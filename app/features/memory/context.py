"""聊天上下文装配（近期对话 + 长期记忆 + 预算 trace）。

边界：
- 纯装配：读取已在 SQLite 的会话/消息/记忆，不发网络请求；
- 不把历史摘录当事实；不跨 topic 取记忆；
- `RAGKB_MEMORY_CONTEXT_ENABLED=off` 或没有可注入内容时返回 None，
  调用方据此保持改造前的请求体完全不变。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.features.memory.config import MemorySettings, get_memory_settings
from app.features.memory.retrieve import (
    build_history_prompt,
    build_memory_prompt,
    history_for_messages_with_stats,
    load_history_recall,
    load_memories,
    load_recent_messages,
)
from app.features.memory import session_state
from app.features.memory.store import MemoryStore
from app.shared.tokens import estimate_tokens


@dataclass(frozen=True)
class ChatContext:
    history: List[Dict[str, str]] = field(default_factory=list)
    history_items: List[Dict[str, Any]] = field(default_factory=list)
    memory_items: List[Dict[str, Any]] = field(default_factory=list)
    history_recall_items: List[Dict[str, Any]] = field(default_factory=list)
    topic_id: str = ""
    session_prompt: str = ""
    memory_prompt: str = ""
    recall_prompt: str = ""
    user_prompt: str = ""
    local_prefix: str = ""
    memory_tokens: int = 0
    history_tokens: int = 0
    session_tokens: int = 0
    recall_tokens: int = 0
    trace: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_upstream_context(self) -> bool:
        return bool(self.history or self.user_prompt)

    @property
    def has_local_context(self) -> bool:
        return bool(self.history or self.local_prefix)


def build_chat_context(
    store: MemoryStore,
    session_id: str,
    question: str,
    *,
    exclude_message_id: Optional[str] = None,
    settings: Optional[MemorySettings] = None,
) -> Optional[ChatContext]:
    cfg = settings or get_memory_settings()
    if not cfg.memory_enabled or not cfg.memory_context_enabled or not session_id:
        return None

    try:
        session = store.get_session(session_id)
    except Exception:  # noqa: BLE001 - 读不到会话就等同于没有上下文
        return None
    if session is None:
        return None
    topic_id = session.get("topic_id")

    # 装配链路不能把读取失败伪装成“本轮没有历史”：失败交给 chat_bridge
    # 标记 context_status=failed，前端据此明确提示上下文未带入。
    state = session_state.load_state(store, session_id, strict=True)
    session_prompt = session_state.build_session_prompt(state)
    session_tokens = estimate_tokens(session_prompt)
    history_budget = int(cfg.memory_recent_history_tokens or 0)
    recent_budget = (
        max(0, history_budget - session_tokens) if history_budget > 0 else 0
    )
    history_items = load_recent_messages(
        store,
        session_id,
        exclude_message_id=exclude_message_id,
        max_messages=int(cfg.memory_recent_history_messages or 0),
        max_tokens=recent_budget,
        strict=True,
    )
    memory_items = load_memories(
        store,
        question,
        topic_id=topic_id,
        budget_tokens=int(cfg.memory_context_budget or 0),
    )

    history_recall_items: List[Dict[str, Any]] = []
    recall_prompt = ""
    if cfg.memory_recall_enabled:
        history_recall_items = load_history_recall(
            store,
            question,
            session_id=session_id,
            topic_id=topic_id,
            exclude_message_ids=[
                str(item.get("message_id") or "") for item in history_items
            ],
            budget_tokens=int(cfg.memory_recall_budget or 400),
            max_items=int(cfg.memory_recall_max_items or 5),
        )
        recall_prompt = build_history_prompt(history_recall_items)

    memory_prompt = build_memory_prompt(memory_items)
    history, citations_stripped = history_for_messages_with_stats(history_items)
    user_prompt = "\n\n".join(
        part for part in (session_prompt, memory_prompt, recall_prompt) if part
    )
    if not user_prompt and not history:
        return None

    memory_tokens = 0
    for item in memory_items:
        try:
            memory_tokens += int(item.get("tokens") or 0)
        except (TypeError, ValueError):
            pass
    recall_tokens = 0
    for item in history_recall_items:
        try:
            recall_tokens += int(item.get("tokens") or 0)
        except (TypeError, ValueError):
            pass
    # 清洗引用后长度会略变；按实际要注入的内容重算，trace 才是真预算。
    recent_history_tokens = sum(
        estimate_tokens(item["content"]) for item in history
    )
    history_tokens = session_tokens + recent_history_tokens
    summary_meta = (
        state.get("summary") if isinstance(state.get("summary"), dict) else {}
    )

    trace = {
        "memory_ids": [
            str(item.get("memory", {}).get("id") or "")
            for item in memory_items
            if item.get("memory")
        ],
        "memory_reasons": {
            str(item.get("memory", {}).get("id") or ""): str(item.get("reason") or "")
            for item in memory_items
            if item.get("memory")
        },
        "memory_count": len(memory_items),
        "memory_tokens": memory_tokens,
        "recall_message_ids": [
            str(item.get("message_id") or "") for item in history_recall_items
        ],
        "recall_sessions": [
            str(item.get("session_id") or "") for item in history_recall_items
        ],
        "recall_count": len(history_recall_items),
        "recall_tokens": recall_tokens,
        "history_message_ids": [
            str(item.get("message_id") or "") for item in history_items
        ],
        "history_count": len(history_items),
        "history_tokens": history_tokens,
        "session_prompt_tokens": session_tokens,
        "recent_history_tokens": recent_history_tokens,
        "session_constraints": len(
            [
                item
                for item in (state.get("constraints") or [])
                if isinstance(item, dict) and item.get("status") == "active"
            ]
        ),
        "session_summary_tokens": estimate_tokens(
            summary_meta.get("text") or ""
        ),
        "history_truncated": any(
            bool(item.get("truncated")) for item in history_items
        ),
        "history_truncated_count": sum(
            1 for item in history_items if item.get("truncated")
        ),
        "history_citations_stripped": citations_stripped,
        "prompt_chars": len(memory_prompt),
        "topic_id": topic_id,
        # 预算口径：memory 与 history 各自有上限；KB 上下文由生成层
        # （LightRAG max_total_tokens / 本机 ContextBuilder）单独预算。
        "memory_budget": int(cfg.memory_context_budget or 0),
        "history_budget": int(cfg.memory_recent_history_tokens or 0),
        "recall_budget": (
            int(cfg.memory_recall_budget or 0)
            if cfg.memory_recall_enabled
            else 0
        ),
        "injected_tokens": memory_tokens + history_tokens + recall_tokens,
        "combined_budget": int(cfg.memory_context_budget or 0)
        + int(cfg.memory_recent_history_tokens or 0)
        + (
            int(cfg.memory_recall_budget or 0)
            if cfg.memory_recall_enabled
            else 0
        ),
        "budget_scope": "memory+history; knowledge-base context is budgeted by the generation layer",
    }

    return ChatContext(
        history=history,
        history_items=history_items,
        memory_items=memory_items,
        history_recall_items=history_recall_items,
        topic_id=str(topic_id or ""),
        session_prompt=session_prompt,
        memory_prompt=memory_prompt,
        recall_prompt=recall_prompt,
        user_prompt=user_prompt,
        local_prefix=user_prompt,
        memory_tokens=memory_tokens,
        history_tokens=history_tokens,
        session_tokens=session_tokens,
        recall_tokens=recall_tokens,
        trace=trace,
    )


__all__ = ["ChatContext", "build_chat_context"]
