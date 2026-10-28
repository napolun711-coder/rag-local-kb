"""MVP：会话级状态（约束 / 最近指令 / 抽取式摘要）。

边界：
- 只存 `sessions.meta_json`，不建表、不迁移；
- 只服务当前会话，不写入长期记忆，不跨会话注入；
- 摘要只做抽取，不调用模型；不是事实，也不是引用来源；
- 任何失败都不阻断聊天，调用方按“没有状态”处理。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.features.memory.store import MemoryStore
from app.features.memory.util import utc_now
from app.shared.tokens import estimate_tokens

STATE_KEY = "session_state"

MAX_INSTRUCTIONS = 5
MAX_INSTRUCTION_CHARS = 70
MAX_CONSTRAINTS = 6
MAX_FACTS = 4
MAX_FACT_CHARS = 70
SUMMARY_MAX_TOKENS = 220
SESSION_PROMPT_MAX_TOKENS = 600
SUMMARY_MIN_MESSAGES = 10
RECENT_KEEP_MESSAGES = 6

CONSTRAINT_LABELS = {
    "word_count": "字数",
    "format": "格式",
    "tone": "语气",
    "fact_boundary": "事实边界",
    "audience": "对象",
}

_CONSTRAINT_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("word_count", re.compile(r"(?:字数|控制在|不超过|不少于|约|左右)?\s*\d{2,5}\s*字")),
    ("format", re.compile(r"(三段|分三段|四段|分点|条目式|表格式|报告格式|书信格式)")),
    ("tone", re.compile(r"(正式|书面|简洁|克制|口语|详细|严谨)")),
    (
        "fact_boundary",
        re.compile(r"(只使用|只能用|不得新增|不要编造|不得编造|事实范围|不要推测)"),
    ),
    ("audience", re.compile(r"(?:对象|给|面向)[:：]?\s*([^\n，。；;]{2,30})")),
)

_HAS_NUMBER_RE = re.compile(
    r"\d{4}\s*[-年/.]\s*\d{1,2}|\d+\s*[年月日]|第[一二三四五六七八九十\d]+"
)
_CITATION_RE = re.compile(r"\[\s*\d+\s*\]|［\s*\d+\s*］|【\s*\d+\s*】")
_FACT_RE = re.compile(
    r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
    r"|\d{4}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2}"
    r"|\d+\s*(?:天|日|个月|月|年|元|万|%|号|字)"
    r"|[A-Z]{2,}-\d+"
)


def _clip(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if limit > 0 and len(value) > limit:
        return value[:limit].rstrip() + "…"
    return value


def _clip_to_tokens(text: str, max_tokens: int) -> str:
    value = str(text or "")
    if max_tokens <= 0:
        return ""
    if estimate_tokens(value) <= max_tokens:
        return value
    low, high = 0, len(value)
    best = ""
    while low <= high:
        middle = (low + high) // 2
        candidate = value[:middle] + "\n…（摘要已截断）"
        if estimate_tokens(candidate) <= max_tokens:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def load_state(
    store: MemoryStore, session_id: str, *, strict: bool = False
) -> Dict[str, Any]:
    try:
        session = store.get_session(session_id)
    except Exception:  # noqa: BLE001 - 读不到会话等同于没有状态
        if strict:
            raise
        return {}
    meta = (session or {}).get("meta") if isinstance(session, dict) else {}
    state = meta.get(STATE_KEY) if isinstance(meta, dict) else {}
    return state if isinstance(state, dict) else {}


def _extract_constraints(message: str, seq: int) -> List[Dict[str, Any]]:
    text = " ".join(str(message or "").split())
    if not text:
        return []
    found: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()
    for key, pattern in _CONSTRAINT_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0).strip()
            if key == "audience" and match.lastindex:
                value = match.group(1).strip()
            if not value:
                continue
            dedupe = (key, value)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            found.append(
                {
                    "key": key,
                    "label": CONSTRAINT_LABELS.get(key, key),
                    "value": _clip(value, 80),
                    "source_seq": int(seq),
                    "source_quote": _clip(text, 100),
                    "status": "active",
                    "updated_at": utc_now(),
                }
            )
    return found


def _update_constraints(
    state: Dict[str, Any], message: str, seq: int
) -> None:
    existing = state.get("constraints")
    constraints = existing if isinstance(existing, list) else []
    new_items = _extract_constraints(message, seq)
    for item in new_items:
        for old in constraints:
            if (
                isinstance(old, dict)
                and old.get("key") == item["key"]
                and old.get("status") == "active"
            ):
                old["status"] = "superseded"
                old["superseded_by_seq"] = int(seq)
        constraints.append(item)
    # 只保留最近 6 条 active + 少量 superseded，避免元数据无限增长。
    active = [item for item in constraints if item.get("status") == "active"]
    superseded = [item for item in constraints if item.get("status") != "active"]
    state["constraints"] = (superseded[-6:] + active[-MAX_CONSTRAINTS:])[-12:]


def _update_instructions(state: Dict[str, Any], message: str, seq: int) -> None:
    existing = state.get("instructions")
    items = existing if isinstance(existing, list) else []
    text = _clip(message, MAX_INSTRUCTION_CHARS)
    if not text:
        return
    if not any(int(item.get("seq") or 0) == int(seq) for item in items if isinstance(item, dict)):
        items.append({"seq": int(seq), "text": text, "created_at": utc_now()})
    state["instructions"] = items[-MAX_INSTRUCTIONS:]


def _extract_user_facts(message: str, seq: int) -> List[Dict[str, Any]]:
    """只抽取用户明确说出、带日期/数字的短句；供长会话继续引用。"""
    text = " ".join(str(message or "").split())
    if not text:
        return []
    facts: List[Dict[str, Any]] = []
    for part in re.split(r"[。；;\n]", text):
        value = part.strip()
        if not value or not _FACT_RE.search(value):
            continue
        facts.append(
            {
                "seq": int(seq),
                "text": _clip(value, MAX_FACT_CHARS),
                "created_at": utc_now(),
            }
        )
    return facts


def _update_facts(state: Dict[str, Any], message: str, seq: int) -> None:
    existing = state.get("user_facts")
    facts = existing if isinstance(existing, list) else []
    seen = {
        _clip(item.get("text") or "", MAX_FACT_CHARS)
        for item in facts
        if isinstance(item, dict)
    }
    for item in _extract_user_facts(message, seq):
        if item["text"] in seen:
            continue
        seen.add(item["text"])
        facts.append(item)
    state["user_facts"] = facts[-MAX_FACTS:]


def _summary_text(messages: Sequence[Dict[str, Any]], upto_seq: int) -> str:
    users: List[str] = []
    key_lines: List[str] = []
    answers: List[str] = []
    for row in messages:
        content = _CITATION_RE.sub("", _clip(row.get("content") or "", 120))
        content = " ".join(content.split())
        if not content:
            continue
        seq = int(row.get("seq") or 0)
        role = str(row.get("role") or "")
        if role == "user":
            users.append("第%d轮：%s" % (seq, content))
        elif role == "assistant":
            answers.append(content)
        if _HAS_NUMBER_RE.search(content):
            key_lines.append(content)

    lines = [
        "## 会话摘要（截至第 %d 轮；不是事实，不是引用来源）" % upto_seq,
    ]
    if users:
        lines.append("- 用户要求/问题：")
        lines.extend("  - " + item for item in users[-6:])
    if key_lines:
        lines.append("- 关键数字/日期：")
        lines.extend("  - " + item for item in key_lines[-6:])
    if answers:
        lines.append("- 最近结论：")
        lines.extend("  - " + item for item in answers[-2:])
    if len(lines) <= 1:
        return ""
    return _clip_to_tokens("\n".join(lines), SUMMARY_MAX_TOKENS)


def _update_summary(
    store: MemoryStore, state: Dict[str, Any], session_id: str
) -> None:
    try:
        rows = store.list_messages(session_id, limit=200)
    except Exception:  # noqa: BLE001 - 读取失败保持旧摘要
        return
    messages = [
        row
        for row in rows
        if row.get("status") == "complete"
        and row.get("role") in ("user", "assistant")
        and str(row.get("content") or "").strip()
    ]
    if len(messages) < SUMMARY_MIN_MESSAGES:
        return
    boundary = len(messages) - RECENT_KEEP_MESSAGES
    if boundary <= 0:
        return
    upto_seq = int(messages[boundary - 1].get("seq") or 0)
    current = state.get("summary") if isinstance(state.get("summary"), dict) else {}
    previous_upto = int(current.get("upto_seq") or 0)
    if upto_seq <= previous_upto:
        return
    # 每次用“截至边界”的完整旧消息重建摘要，避免第二次更新把第一次的摘要
    # 整段覆盖掉；抽取函数自己会限制条数与 token。
    older = messages[:boundary]
    text = _summary_text(older, upto_seq)
    if not text:
        return
    state["summary"] = {
        "text": text,
        "upto_seq": upto_seq,
        "turns": len(older),
        "updated_at": utc_now(),
    }


def refresh(
    store: MemoryStore,
    session_id: str,
    message: str,
    seq: int,
) -> Dict[str, Any]:
    """用户消息落库后更新会话状态；失败由调用方忽略。"""
    state = load_state(store, session_id)
    if not state:
        state = {"version": 1}
    _update_constraints(state, message, seq)
    _update_instructions(state, message, seq)
    _update_facts(state, message, seq)
    _update_summary(store, state, session_id)
    state["version"] = 1
    state["updated_at"] = utc_now()
    store.update_session_meta(session_id, {STATE_KEY: state})
    return state


def build_session_prompt(state: Dict[str, Any]) -> str:
    if not isinstance(state, dict) or not state:
        return ""
    lines: List[str] = [
        "## 会话状态（本地上下文；不是证据，也不是引用来源）",
        "使用规则：仅用于理解当前任务；当前用户指令优先；历史和摘要不是权威事实。"
        "事实性陈述必须由本轮证据或用户本轮明确提供的信息支持。",
        "信息不足时明确说明不确定或未提供，不得编造标识、日期、金额或文档编号。",
    ]
    constraints = [
        item
        for item in (state.get("constraints") or [])
        if isinstance(item, dict) and item.get("status") == "active"
    ]
    if constraints:
        lines.append("### 当前有效要求")
        for item in constraints[-MAX_CONSTRAINTS:]:
            lines.append(
                "- [%s] %s（第%s轮）"
                % (
                    item.get("label") or item.get("key") or "要求",
                    item.get("value") or "",
                    item.get("source_seq") or "?",
                )
            )
    instructions = [
        item
        for item in (state.get("instructions") or [])
        if isinstance(item, dict) and item.get("text")
    ]
    if instructions:
        lines.append("### 最近指令（按时间；冲突时以当前有效要求和本轮指令为准）")
        for item in instructions[-MAX_INSTRUCTIONS:]:
            lines.append(
                "- 第%s轮：%s" % (item.get("seq") or "?", item.get("text") or "")
            )
    facts = [
        item
        for item in (state.get("user_facts") or [])
        if isinstance(item, dict) and item.get("text")
    ]
    if facts:
        lines.append("### 用户在本会话提供的事实（用户陈述；不是知识库事实）")
        for item in facts[-MAX_FACTS:]:
            lines.append(
                "- 第%s轮：%s" % (item.get("seq") or "?", item.get("text") or "")
            )
    summary = state.get("summary") if isinstance(state.get("summary"), dict) else {}
    if summary.get("text"):
        lines.append(str(summary["text"]))
    if len(lines) <= 4:
        return ""
    return _clip_to_tokens("\n".join(lines), SESSION_PROMPT_MAX_TOKENS)


def prompt_tokens(state: Dict[str, Any]) -> int:
    return estimate_tokens(build_session_prompt(state))


__all__ = [
    "STATE_KEY",
    "build_session_prompt",
    "load_state",
    "prompt_tokens",
    "refresh",
]
