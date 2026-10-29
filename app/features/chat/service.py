"""Chat use cases backed by the knowledge gateway and memory bridge."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Generator, Iterator, List, Optional, Tuple

from app.adapters.lightrag.errors import (
    KnowledgeEngineNotInstalled,
    KnowledgeHTTPError,
    KnowledgeIndexMismatch,
    KnowledgeUnavailable,
)
from app.features.chat.contracts import ChatRequest
from app.features.chat.streaming import frontend_events
from app.features.knowledge.contracts import KnowledgeGateway
from app.features.knowledge.service import get_knowledge_gateway
from app.features.memory import chat_bridge
from app.features.memory.config import get_memory_settings
from app.features.memory.context import ChatContext

logger = logging.getLogger(__name__)


def _query_error_event(exc: KnowledgeHTTPError) -> Dict[str, Any]:
    message = str(exc) or "知识服务返回错误"
    lowered = message.lower()
    no_context_markers = (
        "no relevant context",
        "no relevant",
        "no context",
        "no document",
        "not found",
        "empty query",
        "no data",
    )
    if any(marker in lowered for marker in no_context_markers):
        return {
            "type": "error",
            "no_context": True,
            "message": "知识库没有找到与这个问题相关的资料。可以先上传并入库相关资料，"
                       "或选择继续对话（不查资料）。",
        }
    if any(marker in lowered for marker in ("insufficient", "balance", "quota", "401", "402", "403")):
        return {
            "type": "error",
            "message": "模型服务拒绝了本次查询（可能是余额、配额或鉴权问题）："
                       + message[:300] + "。请检查模型配置后重试，或继续对话（不查资料）。",
        }
    if exc.status_code in (400, 404, 422) or "http 4" in lowered:
        return {
            "type": "error",
            "message": "知识库没有完成本次检索：" + message[:300]
                       + "。可以先上传相关资料、换一种问法后重试，或继续对话（不查资料）。",
        }
    return {
        "type": "error",
        "message": "知识服务检索失败：" + message[:300]
                   + "。请检查知识服务/引擎状态后重试，或继续对话（不查资料）。",
    }


_PERSONAL_FACT_RE = re.compile(
    r"(我的|我叫|你还记得|记得我|我之前|我的项目|我的偏好|我的名字|我的代号|"
    r"我的邮箱|我的电话|my\s+(?:project|preference|name|code|email|phone)|"
    r"what\s+is\s+my|do\s+you\s+remember)",
    re.I,
)
_DIRECT_QUESTION_RE = re.compile(
    r"(是什么|叫什么|是多少|哪个|什么|吗|？|\?|what|which|who|remember)",
    re.I,
)
_KB_LOOKUP_RE = re.compile(
    r"(资料库|知识库|文档|文件|资料中|原文|上传|knowledge\s*base|document|file)",
    re.I,
)
_DIRECT_MEMORY_MIN_CONFIDENCE = 0.8


def _direct_memory_answer(context: Any, question: str) -> Optional[Tuple[str, List[str]]]:
    """Answer a direct personal-fact question from high-confidence active memory.

    Only runs when the question explicitly asks about the user's own stored
    information. Knowledge-base lookups and ordinary questions keep the normal
    model path. This keeps small models from inventing a project name when the
    correct memory was already retrieved.
    """
    if context is None:
        return None
    text = str(question or "").strip()
    if not text or _KB_LOOKUP_RE.search(text):
        return None
    if not (_PERSONAL_FACT_RE.search(text) and _DIRECT_QUESTION_RE.search(text)):
        return None
    lines: List[str] = []
    ids: List[str] = []
    for item in getattr(context, "memory_items", []) or []:
        if str(item.get("reason") or "") != "relevant":
            continue
        memory = item.get("memory") if isinstance(item.get("memory"), dict) else {}
        if str(memory.get("status") or "") != "active":
            continue
        if str(memory.get("sensitivity") or "normal") != "normal":
            continue
        try:
            confidence = float(memory.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0
        if confidence < _DIRECT_MEMORY_MIN_CONFIDENCE:
            continue
        content = str(memory.get("content") or "").strip()
        if not content:
            continue
        subject = str(memory.get("subject") or "").strip()
        line = content if (not subject or subject in content) else f"{subject}：{content}"
        if line in lines:
            continue
        lines.append(line)
        memory_id = str(memory.get("id") or "")
        if memory_id:
            ids.append(memory_id)
        if len(lines) >= 3:
            break
    if not lines:
        return None
    return "；".join(lines), ids


class ChatService:
    def __init__(self, gateway: Optional[KnowledgeGateway] = None) -> None:
        self.gateway = gateway or get_knowledge_gateway()

    def ensure_ready(self) -> None:
        health = self.gateway.health()
        if not health.reachable:
            raise KnowledgeUnavailable(health.detail or "knowledge service unreachable")
        if not health.engine_installed:
            raise KnowledgeEngineNotInstalled(health.detail or "knowledge engine not installed")

    def _source(self, request: ChatRequest) -> str:
        return "upstream_bypass" if request.force_bypass else "upstream"

    def _upstream_options(self, context: Optional[ChatContext]) -> Dict[str, Any]:
        if context is None:
            return {}
        cfg = get_memory_settings()
        options: Dict[str, Any] = {}
        history_mode = str(cfg.memory_upstream_history or "off").lower()
        memory_mode = str(cfg.memory_upstream_memory or "off").lower()
        if history_mode != "off":
            if context.history:
                options["conversation_history"] = context.history
            if context.session_prompt:
                options["_session_prompt"] = context.session_prompt
        if memory_mode != "off":
            if context.memory_prompt:
                options["_memory_prompt"] = context.memory_prompt
            if context.recall_prompt:
                options["_recall_prompt"] = context.recall_prompt
        prompts = [
            str(options.pop(key, "") or "")
            for key in ("_session_prompt", "_memory_prompt", "_recall_prompt")
        ]
        prompt = "\n\n".join(item for item in prompts if item)
        if prompt:
            options["user_prompt"] = prompt
        return options

    def _mode(self, request: ChatRequest) -> str:
        return "bypass" if request.force_bypass else "mix"

    def answer(self, request: ChatRequest) -> Tuple[Dict[str, Any], Any, Dict[str, str]]:
        self.ensure_ready()
        source = self._source(request)
        persistence = chat_bridge.begin_chat(request, source=source)
        context, context_status = chat_bridge.build_context(persistence, request.message)
        direct = _direct_memory_answer(context, request.message)
        if direct is not None:
            answer, memory_ids = direct
            payload = {
                "response": answer,
                "references": [],
                "response_time": 0,
                "memory_direct": True,
                "memory_ids": memory_ids,
            }
            chat_bridge.finish_from_payload(
                persistence,
                payload,
                source="memory",
                trace=context.trace if context is not None else None,
                context_status=context_status,
                bypass=request.force_bypass,
            )
            return payload, persistence, context_status
        options = self._upstream_options(context)
        try:
            payload = self.gateway.query(
                request.message,
                mode=self._mode(request),
                include_references=True,
                **options,
            )
        except Exception as exc:
            chat_bridge.fail(persistence, exc, source=source, context_status=context_status)
            raise
        chat_bridge.finish_from_payload(
            persistence,
            payload,
            source=source,
            trace=context.trace if context is not None else None,
            context_status=context_status,
            bypass=request.force_bypass,
        )
        return payload, persistence, context_status

    def stream(self, request: ChatRequest) -> Tuple[Any, Dict[str, str], str, Iterator[Dict[str, Any]]]:
        self.ensure_ready()
        source = self._source(request)
        persistence = chat_bridge.begin_chat(request, source=source)
        context, context_status = chat_bridge.build_context(persistence, request.message)
        direct = _direct_memory_answer(context, request.message)
        if direct is not None:
            source = "memory"
        options = self._upstream_options(context)
        started = time.perf_counter()

        def events() -> Iterator[Dict[str, Any]]:
            if direct is not None:
                answer, memory_ids = direct
                yield {"type": "memory", "items": memory_ids}
                yield {"type": "chunk", "text": answer}
                yield {"type": "done", "response_time": 0, "memory_direct": True}
                yield {"type": "end"}
                return
            if context_status.get("status") == "failed":
                yield {
                    "type": "context",
                    "status": "failed",
                    "reason": str(context_status.get("reason") or ""),
                }
            try:
                chunks = self.gateway.stream_query(
                    request.message,
                    mode=self._mode(request),
                    include_references=True,
                    include_response_time=True,
                    **options,
                )
                yield from frontend_events(chunks, started_at=started)
            except KnowledgeEngineNotInstalled:
                yield {"type": "error", "message": "知识引擎尚未安装或未启用。"}
                yield {"type": "done", "response_time": round(time.perf_counter() - started, 3)}
                yield {"type": "end"}
            except KnowledgeUnavailable:
                yield {"type": "error", "message": "知识服务不可达，请检查引擎与外壳状态。"}
                yield {"type": "done", "response_time": round(time.perf_counter() - started, 3)}
                yield {"type": "end"}
            except KnowledgeIndexMismatch:
                yield {
                    "type": "error",
                    "message": "嵌入模型或向量索引身份已变化。请先重建索引，并在设置页确认兼容性。",
                }
                yield {"type": "done", "response_time": round(time.perf_counter() - started, 3)}
                yield {"type": "end"}
            except KnowledgeHTTPError as exc:
                yield _query_error_event(exc)
                yield {"type": "done", "response_time": round(time.perf_counter() - started, 3)}
                yield {"type": "end"}
            except Exception as exc:  # noqa: BLE001 - streaming errors become protocol events
                logger.warning("Chat stream failed: %s", type(exc).__name__)
                yield {"type": "error", "message": f"知识服务返回错误（{type(exc).__name__}）。"}
                yield {"type": "done", "response_time": round(time.perf_counter() - started, 3)}
                yield {"type": "end"}

        return persistence, context_status, source, events()


__all__ = ["ChatService"]
