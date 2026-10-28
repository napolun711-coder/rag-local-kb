"""Chat use cases backed by the knowledge gateway and memory bridge."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Generator, Iterator, List, Optional, Tuple

from app.adapters.lightrag.errors import KnowledgeEngineNotInstalled, KnowledgeUnavailable
from app.features.chat.contracts import ChatRequest
from app.features.chat.streaming import frontend_events
from app.features.knowledge.contracts import KnowledgeGateway
from app.features.knowledge.service import get_knowledge_gateway
from app.features.memory import chat_bridge
from app.features.memory.config import get_memory_settings
from app.features.memory.context import ChatContext

logger = logging.getLogger(__name__)


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
        options = self._upstream_options(context)
        started = time.perf_counter()

        def events() -> Iterator[Dict[str, Any]]:
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
            except Exception as exc:  # noqa: BLE001 - streaming errors become protocol events
                logger.warning("Chat stream failed: %s", type(exc).__name__)
                yield {"type": "error", "message": f"知识服务返回错误（{type(exc).__name__}）。"}
                yield {"type": "done", "response_time": round(time.perf_counter() - started, 3)}
                yield {"type": "end"}

        return persistence, context_status, source, events()


__all__ = ["ChatService"]