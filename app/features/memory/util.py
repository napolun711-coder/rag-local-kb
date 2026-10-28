"""记忆模块共用的小工具（时间、ID、JSON、标题）。

刻意保持纯函数：不发请求、不读配置、不写日志。
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    """ISO-8601 UTC，毫秒精度，以 Z 结尾（便于排序与前端解析）。"""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def iso_to_ms(value: Any) -> int:
    """把数据库里的 ISO 时间转成 epoch 毫秒；坏值返回 0。"""
    if not isinstance(value, str) or not value.strip():
        return 0
    text = value.strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    except ValueError:
        return 0


def value_to_iso(value: Any, *, fallback: str | None = None) -> str:
    """把 epoch 毫秒/秒、ISO 字符串或空值收敛成 ISO 字符串。"""
    if isinstance(value, bool) or value is None:
        return fallback or utc_now()
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        number = float(value)
        # localStorage 的时间戳是毫秒；也容忍秒级输入。
        seconds = number / 1000.0 if number > 10_000_000_000 else number
        try:
            return (
                datetime.fromtimestamp(seconds, timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
        except (OverflowError, OSError, ValueError):
            return fallback or utc_now()
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            if text.endswith("Z"):
                parsed = datetime.fromisoformat(text[:-1] + "+00:00")
            else:
                parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z")
        except ValueError:
            return fallback or utc_now()
    return fallback or utc_now()


def title_from_text(text: str, *, limit: int = 18) -> str:
    """与前端 titleFromText 一致：压缩空白，超长截断加省略号。"""
    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        return "新对话"
    return clean if len(clean) <= limit else clean[:limit] + "…"


def json_loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        parsed = json.loads(value)
        return parsed if parsed is not None else default
    except (TypeError, ValueError):
        return default


def json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return "{}" if isinstance(value, dict) else "[]"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


__all__ = [
    "iso_to_ms",
    "json_dumps",
    "json_loads",
    "new_id",
    "title_from_text",
    "utc_now",
    "value_to_iso",
]
