"""候选记忆的校验、去重与冲突策略。

边界：
- 只处理已通过提取器校验的候选（content/subject/kind/sensitivity）；
- 没有可验证来源（message_id + quote 原文子串）的候选直接跳过；
- **Tier 2 永不静默覆盖** user_explicit / user_confirmed / user_edited；
- 低置信旧记忆遇到更高置信号候选时，两者都进入 conflicted，等用户选择。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Sequence

from app.features.memory.memories import (
    KINDS,
    SENSITIVITIES,
    SOURCE_TYPES,
    canonical_key,
    content_key,
)

logger = logging.getLogger(__name__)

ALLOWED_STATUSES = ("active", "pending", "conflicted")
PROTECTED_SOURCE_TYPES = ("user_explicit", "user_confirmed")
CONFLICT_CONFIDENCE_DELTA = 0.1


def apply_candidates(
    store: Any,
    candidates: Sequence[Dict[str, Any]],
    *,
    force_pending: bool = False,
    actor: str = "system",
) -> Dict[str, Any]:
    """把候选写成 memories/sources/events；返回统计。"""
    written: List[Dict[str, Any]] = []
    skipped = 0
    unverified = 0
    duplicate_pending = 0
    mutated_existing = 0
    seen: set[tuple[str, str]] = set()

    for raw in candidates:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        content = str(raw.get("content") or "").strip()[:300]
        subject = str(raw.get("subject") or "").strip()[:40]
        if not content or not subject:
            skipped += 1
            continue

        kind = str(raw.get("kind") or "fact").strip().lower()
        if kind not in KINDS:
            kind = "fact"
        sensitivity = str(raw.get("sensitivity") or "normal").strip().lower()
        if sensitivity not in SENSITIVITIES:
            sensitivity = "normal"
        if sensitivity == "secret":
            skipped += 1
            continue

        source_type = str(raw.get("source_type") or "model_inferred").strip().lower()
        if source_type not in SOURCE_TYPES:
            source_type = "model_inferred"
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            importance = max(1, min(5, int(raw.get("importance") or 3)))
        except (TypeError, ValueError):
            importance = 3

        scope = str(raw.get("scope") or "global").strip().lower()
        if scope not in ("global", "topic"):
            scope = "global"
        topic_id = str(raw.get("topic_id") or "").strip() or None
        if scope == "topic" and not topic_id:
            scope = "global"
        scope_key = "global" if scope == "global" else f"topic:{topic_id}"

        message_id = str(raw.get("message_id") or "").strip()
        session_id = str(raw.get("session_id") or "").strip() or None
        quote = str(raw.get("quote") or "").strip()[:500]
        if not message_id or not quote:
            unverified += 1
            continue
        message = store.get_message(message_id)
        if message is None or quote not in str(message.get("content") or ""):
            unverified += 1
            continue

        canonical = canonical_key(scope_key, kind, subject)
        content_fingerprint = content_key(content)
        dedupe_key = (canonical, content_fingerprint)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        status = str(raw.get("status") or "").strip().lower()
        if force_pending:
            status = "pending"
        elif status not in ALLOWED_STATUSES:
            status = (
                "active"
                if confidence >= 0.8
                and sensitivity == "normal"
                and source_type != "model_inferred"
                else "pending"
            )
        if status == "active" and (
            sensitivity != "normal" or source_type == "model_inferred"
        ):
            status = "pending"

        existing_active = store.memories.active_by_canonical(canonical)
        meta = dict(raw.get("meta") or {}) if isinstance(raw.get("meta"), dict) else {}
        meta.setdefault("extractor", str(raw.get("extractor") or "unknown"))
        reason = str(raw.get("reason") or "")[:200]
        if reason:
            meta.setdefault("reason", reason)

        if existing_active is not None:
            if content_key(existing_active["content"]) == content_fingerprint:
                # 同内容：交给 create() 合并来源/提升置信度，不新增 active。
                status = "active"
                meta.setdefault("merged_from", existing_active["id"])
            else:
                status = "conflicted"
                meta.setdefault("conflict_with", existing_active["id"])
                if (
                    str(existing_active.get("source_type")) not in PROTECTED_SOURCE_TYPES
                    and not existing_active.get("user_edited")
                    and confidence
                    > float(existing_active.get("confidence") or 0.0)
                    + CONFLICT_CONFIDENCE_DELTA
                ):
                    try:
                        store.memories.mark_conflicted(
                            existing_active["id"],
                            reason="tier2_higher_confidence",
                            meta={"conflict_with_candidate": canonical},
                            actor=actor,
                        )
                        mutated_existing += 1
                    except Exception as exc:  # noqa: BLE001 - 单条冲突标记失败不影响其它候选
                        logger.warning(
                            "Tier 2 标记旧记忆冲突失败（忽略）：%s",
                            type(exc).__name__,
                        )
        else:
            # 同 canonical + 同内容的 pending 已存在：
            # - 新候选也是 pending → 跳过重复；
            # - 新候选是 active → 先把 pending 提升为 active，再走 create 合并来源。
            duplicate_pending_flag = False
            for pending in store.memories.find_by_canonical(
                canonical, status="pending", limit=10
            ):
                if content_key(pending["content"]) != content_fingerprint:
                    continue
                if status == "active":
                    try:
                        store.memories.promote(
                            pending["id"],
                            reason="tier2_reextract",
                            actor=actor,
                        )
                    except Exception as exc:  # noqa: BLE001 - 单条提升失败不影响其它候选
                        logger.warning(
                            "Tier 2 提升 pending 失败（忽略）：%s",
                            type(exc).__name__,
                        )
                        # 不跳过：继续创建 active，pending 留待用户处理。
                else:
                    duplicate_pending += 1
                    duplicate_pending_flag = True
                break
            if duplicate_pending_flag:
                continue

        try:
            memory = store.memories.create(
                content=content,
                subject=subject,
                kind=kind,
                scope=scope,
                topic_id=topic_id,
                source_type=source_type,
                sensitivity=sensitivity,
                confidence=confidence,
                importance=importance,
                status=status,
                meta=meta,
                source={
                    "message_id": message_id,
                    "session_id": session_id,
                    "quote": quote,
                },
                actor=actor,
            )
        except Exception as exc:  # noqa: BLE001 - 单条候选失败不影响其它候选
            logger.warning("Tier 2 候选写入失败（忽略）：%s", type(exc).__name__)
            skipped += 1
            continue

        written.append(memory)

    statuses = [str(item.get("status")) for item in written]
    return {
        "written": len(written),
        "active": statuses.count("active"),
        "pending": statuses.count("pending"),
        "conflicted": statuses.count("conflicted"),
        "skipped": skipped,
        "unverified": unverified,
        "duplicate_pending": duplicate_pending,
        "mutated_existing": mutated_existing,
        "memory_ids": [str(item.get("id")) for item in written],
    }


__all__ = ["apply_candidates"]
