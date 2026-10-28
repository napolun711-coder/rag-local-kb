"""聊天路由与 SQLite 持久化之间的薄桥接层。

只做四件事：
- 开始一轮：确保会话、写用户消息、创建助手占位（status=streaming）；
- 非流式结束：把上游/本机返回的 response/references 写入助手消息；
- 流式结束：边转发事件边累计，周期落盘，结束时写 complete/partial/error；
- 任何持久化失败都只记日志，绝不影响聊天主流程。

不做：检索、记忆提取和提示词装配，这些由其他模块负责。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from uuid import uuid4

from app.features.memory.config import get_memory_settings
from app.features.memory import fastmem, runtime, session_state
from app.features.memory.context import ChatContext, build_chat_context
from app.features.memory.store import MemoryStore

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_S = 1.5
FLUSH_CHARS = 400

#: LightRAG 检索为空时的两种固定输出：
#: - 1.5.6 的 PROMPTS["fail_response"] 末尾带 [no-context]；
#: - /query 路由在 content 为空时替换成下面这句。
NO_CONTEXT_ROUTE_TEXT = "No relevant context found for the query."
NO_CONTEXT_MARKER = "[no-context]"


def is_no_context_response(text: Any) -> bool:
    """只认固定信号，避免把用户内容里恰好出现的词误判成检索失败。"""
    value = " ".join(str(text or "").split()).strip()
    if not value:
        return False
    if value.endswith(NO_CONTEXT_MARKER) and len(value) <= 200:
        return True
    return value == NO_CONTEXT_ROUTE_TEXT


def _context_meta(context_status: Optional[Dict[str, str]]) -> Dict[str, str]:
    if not context_status:
        return {}
    return {
        "context_status": str(context_status.get("status") or ""),
        "context_reason": str(context_status.get("reason") or ""),
    }


@dataclass
class ChatPersistence:
    store: MemoryStore
    session_id: str
    user_message_id: str
    assistant_message_id: str
    request_id: str
    source: str = ""
    topic_id: Optional[str] = None
    question: str = ""
    finished: bool = False

    def finish(
        self,
        *,
        content: str,
        status: str,
        references: Optional[Iterable[Dict[str, Any]]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self.finished:
            return
        self.finished = True
        merged = dict(meta or {})
        merged.setdefault("source", self.source)
        try:
            self.store.update_message(
                self.assistant_message_id,
                content=content,
                status=status,
                references=list(references or []),
                meta=merged,
                completed=True,
            )
        except Exception as exc:  # noqa: BLE001 - 持久化失败不能反噬流
            logger.warning("助手消息落盘失败（忽略）：%s", type(exc).__name__)


def _text(value: Any) -> str:
    return str(value or "").strip()


def begin_chat(data: Any, *, source: str = "") -> Optional[ChatPersistence]:
    """为一次聊天准备持久化；RAGKB_MEMORY_ENABLED=off 或初始化失败返回 None。"""
    store = runtime.get_store()
    if store is None:
        return None

    session_id = _text(getattr(data, "session_id", ""))
    client_message_id = _text(getattr(data, "client_message_id", "")) or f"cmsg_{uuid4().hex}"
    topic_id = _text(getattr(data, "topic_id", ""))
    message = str(getattr(data, "message", "") or "")

    try:
        session = store.get_session(session_id) if session_id else None
        if session is None:
            session = store.create_session(
                topic_id=topic_id or None,
                client_session_id=session_id or None,
            )
        user = store.add_user_message(
            session["id"],
            message,
            client_message_id=client_message_id,
        )
        # Tier 1 必须在上下文装配前运行：用户刚说出的稳定信息，本轮就应该生效。
        try:
            cfg = get_memory_settings()
            if cfg.memory_enabled and cfg.memory_fast_memory:
                fastmem.apply(
                    store.memories,
                    message,
                    message_id=user["id"],
                    session_id=session["id"],
                    topic_id=session.get("topic_id"),
                )
        except Exception as exc:  # noqa: BLE001 - 快记忆失败不能阻断聊天
            logger.warning("Tier 1 快记忆失败（忽略）：%s", type(exc).__name__)

        # 约束/最近指令/抽取式摘要只存会话元数据；失败不阻断聊天。
        try:
            session_state.refresh(
                store,
                session["id"],
                message,
                int(user.get("seq") or 0),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("会话状态更新失败（忽略）：%s", type(exc).__name__)

        assistant = store.add_assistant_message(
            session["id"],
            status="streaming",
            source=source,
            request_id=client_message_id,
            reply_to_id=user["id"],
        )
        return ChatPersistence(
            store=store,
            session_id=session["id"],
            user_message_id=user["id"],
            assistant_message_id=assistant["id"],
            request_id=client_message_id,
            source=source,
            topic_id=session.get("topic_id"),
            question=str(message or ""),
        )
    except Exception as exc:  # noqa: BLE001 - 聊天不能被持久化拖垮
        logger.warning("对话持久化准备失败（忽略，聊天继续）：%s", type(exc).__name__)
        return None


def build_context(
    persistence: Optional[ChatPersistence], question: str
) -> Tuple[Optional[ChatContext], Dict[str, str]]:
    """装配本轮上下文，并返回可展示的状态。

    状态：
    - ok：带入了上下文；
    - empty：本轮没有可注入的历史/记忆（正常，如首轮）；
    - disabled：开关关闭或会话库不可用（用户主动关闭，不弹错误）；
    - failed：装配异常或会话缺失（聊天继续，但界面要明确提示，不能静默）。
    """
    try:
        cfg = get_memory_settings()
    except Exception:  # noqa: BLE001 - 配置坏了也不能阻断聊天
        return None, {"status": "failed", "reason": "settings_unavailable"}
    if not cfg.memory_enabled:
        return None, {"status": "disabled", "reason": "memory_disabled"}
    if not cfg.memory_context_enabled:
        return None, {"status": "disabled", "reason": "context_disabled"}
    if persistence is None:
        # 开关已开但会话库没准备好：这是失败，不是用户主动关闭，必须提示。
        return None, {"status": "failed", "reason": "persistence_unavailable"}

    try:
        context = build_chat_context(
            persistence.store,
            persistence.session_id,
            question,
            exclude_message_id=persistence.user_message_id,
        )
    except Exception as exc:  # noqa: BLE001 - 上下文失败不能阻断聊天
        logger.warning(" 上下文装配失败（忽略）：%s", type(exc).__name__)
        return None, {"status": "failed", "reason": "assembly_failed"}

    if context is None:
        try:
            session = persistence.store.get_session(persistence.session_id)
        except Exception:  # noqa: BLE001 - 读不到会话按缺失处理
            session = None
        if session is None:
            return None, {"status": "failed", "reason": "session_missing"}
        return None, {"status": "empty", "reason": "no_context"}

    context.trace["context_status"] = "ok"
    context.trace["context_reason"] = ""
    return context, {"status": "ok", "reason": ""}


def enqueue_extraction(persistence: Optional[ChatPersistence]) -> bool:
    """回答完成后入队记忆提取；开关关闭/失败返回 False，绝不影响聊天。

    分级：
    - 明确记忆/偏好/目标/决定 → priority=1，立即；
    - 未处理用户消息达到阈值 → priority=3，立即；
    - 其他 → priority=7，去抖（默认 5 分钟）。
    """
    if persistence is None:
        return False
    try:
        cfg = get_memory_settings()
        if not (cfg.memory_enabled and cfg.memory_auto_extract):
            return False
        store = persistence.store
        pending = store.jobs.count_user_messages_since_watermark(
            persistence.session_id
        )
        if fastmem.has_explicit_memory_marker(persistence.question):
            priority, delay, reason = 1, 0, "explicit"
        elif pending >= int(cfg.memory_extract_every_turns or 6):
            priority, delay, reason = 3, 0, "turn_threshold"
        else:
            priority = 7
            delay = int(cfg.memory_extract_debounce_seconds or 300)
            reason = "debounce"
        store.jobs.enqueue(
            kind="extract_memory",
            session_id=persistence.session_id,
            dedupe_key=f"extract_memory:{persistence.session_id}",
            priority=priority,
            delay_seconds=delay,
            payload={"session_id": persistence.session_id, "reason": reason},
        )
        return True
    except Exception as exc:  # noqa: BLE001 - 入队失败不能阻断回答
        logger.warning("记忆提取入队失败（忽略）：%s", type(exc).__name__)
        return False


def _payload_parts(payload: Any) -> Dict[str, Any]:
    response = ""
    references: List[Dict[str, Any]] = []
    model = ""
    if isinstance(payload, dict):
        value = payload.get("response")
        if not isinstance(value, str) or not value:
            value = payload.get("answer")
        if isinstance(value, str):
            response = value
        refs = payload.get("references")
        if not isinstance(refs, list):
            data = payload.get("data")
            refs = data.get("references") if isinstance(data, dict) else []
        if isinstance(refs, list):
            references = [item for item in refs if isinstance(item, dict)]
        model = str(payload.get("model") or "")
    return {"response": response, "references": references, "model": model}


def finish_from_payload(
    persistence: Optional[ChatPersistence],
    payload: Any,
    *,
    source: str = "",
    trace: Optional[Dict[str, Any]] = None,
    context_status: Optional[Dict[str, str]] = None,
    bypass: bool = False,
) -> None:
    """非流式结束：把最终结果写入助手消息。"""
    if persistence is None:
        return
    if source:
        persistence.source = source
    parts = _payload_parts(payload)
    response = parts["response"]
    no_context = is_no_context_response(response)
    status = "error" if (no_context or not response.strip()) else "complete"
    meta: Dict[str, Any] = {
        "source": persistence.source,
        **_context_meta(context_status),
    }
    if bypass:
        meta["bypass"] = True
    if trace:
        meta["memory_trace"] = trace
    skill_routing = (
        payload.get("skill_routing")
        if isinstance(payload, dict)
        else None
    )
    if isinstance(skill_routing, dict):
        meta["skill_routing"] = skill_routing
    if no_context:
        # 固定“无检索”文案不是答案：不能以 complete 进入下一轮历史。
        meta["no_context"] = True
        meta["error"] = "no_context"
    elif not response.strip():
        meta["empty_response"] = True
    persistence.finish(
        content=response,
        status=status,
        references=parts["references"],
        meta=meta,
    )
    enqueue_extraction(persistence)


def fail(
    persistence: Optional[ChatPersistence],
    exc: BaseException,
    *,
    source: str = "",
    context_status: Optional[Dict[str, str]] = None,
) -> None:
    """非流式异常：把助手消息标成 error；不写异常正文。"""
    if persistence is None:
        return
    if source:
        persistence.source = source
    persistence.finish(
        content="",
        status="error",
        meta={
            "source": persistence.source,
            "exception": type(exc).__name__,
            **_context_meta(context_status),
        },
    )


def wrap_stream(
    events: Iterable[Dict[str, Any]],
    persistence: Optional[ChatPersistence],
    *,
    source: str = "",
    trace: Optional[Dict[str, Any]] = None,
    context_status: Optional[Dict[str, str]] = None,
    bypass: bool = False,
) -> Iterator[Dict[str, Any]]:
    """原样转发事件，同时累计并落盘；事件形状保持兼容。"""
    if persistence is None:
        yield from events
        return
    if source:
        persistence.source = source

    parts: List[str] = []
    char_count = 0
    references: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {
        "source": persistence.source,
        **_context_meta(context_status),
    }
    if bypass:
        meta["bypass"] = True
    if trace:
        meta["memory_trace"] = trace
    status = "complete"
    last_flush = time.monotonic()
    last_flush_chars = 0

    def flush(current_status: str = "streaming") -> None:
        nonlocal last_flush, last_flush_chars
        try:
            persistence.store.update_message(
                persistence.assistant_message_id,
                content="".join(parts),
                status=current_status,
                references=references,
                meta=meta,
            )
            last_flush = time.monotonic()
            last_flush_chars = char_count
        except Exception as exc:  # noqa: BLE001 - 落盘失败不能打断流
            logger.warning("流式内容落盘失败（忽略）：%s", type(exc).__name__)

    try:
        for event in events:
            if not isinstance(event, dict):
                yield event
                continue
            kind = str(event.get("type") or "")
            if kind == "chunk":
                text = event.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
                    char_count += len(text)
                    now = time.monotonic()
                    if (
                        now - last_flush >= FLUSH_INTERVAL_S
                        or char_count - last_flush_chars >= FLUSH_CHARS
                    ):
                        flush()
            elif kind == "references":
                items = event.get("items")
                if isinstance(items, list):
                    references = [item for item in items if isinstance(item, dict)]
            elif kind == "done":
                for key in (
                    "response_time",
                    "verified",
                    "warnings",
                    "usage",
                    "skill_routing",
                ):
                    if key in event:
                        meta[key] = event.get(key)
            elif kind == "error":
                message = str(event.get("message") or "上游返回错误")
                parts.append(("\n\n" if parts else "") + "⚠️ " + message)
                char_count += len(message) + 4
                meta["error"] = message
                if event.get("retry_upstream") is True:
                    meta["retry_upstream"] = True
                status = "error"
            elif kind == "end":
                if is_no_context_response("".join(parts)):
                    # 给前端一个可操作信号：显示“不查资料继续”入口。
                    yield {"type": "no_context"}
            # 先落账再交给调用方：客户端在收到最后一个 chunk 后立刻断开时，
            # 已发出去的内容也必须已经进入内存缓冲（finally 会写 partial）。
            yield event
    except GeneratorExit:
        status = "partial"
        raise
    except Exception as exc:  # noqa: BLE001 - 先把状态写好，再让上层按原逻辑处理
        status = "error"
        meta["exception"] = type(exc).__name__
        raise
    finally:
        content = "".join(parts)
        if is_no_context_response(content):
            # 同非流式：固定“无检索”文案不能污染下一轮历史。
            status = "error"
            meta["no_context"] = True
            meta["error"] = "no_context"
        elif status == "complete" and not parts:
            status = "error"
            meta["empty_response"] = True
        persistence.finish(
            content=content,
            status=status,
            references=references,
            meta=meta,
        )
        enqueue_extraction(persistence)


__all__ = [
    "ChatPersistence",
    "begin_chat",
    "fail",
    "finish_from_payload",
    "is_no_context_response",
    "wrap_stream",
]
