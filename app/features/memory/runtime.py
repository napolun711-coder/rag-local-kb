"""记忆库运行时单例与降级。

边界：
- `RAGKB_MEMORY_ENABLED=off` 时不创建目录、不打开数据库；
- 初始化失败只记日志并返回 None，聊天路径继续（不落盘）；
- `reset_for_tests()` 是测试钩子，生产请求不会调用。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from app.features.memory.config import get_memory_settings
from app.features.memory.db import MemoryDatabase, resolve_db_path
from app.features.memory.store import MemoryStore

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_store: Optional[MemoryStore] = None
_init_error = ""
_init_failed = False
_test_store: Optional[MemoryStore] = None
_test_enabled: Optional[bool] = None


def enabled() -> bool:
    """测试钩子优先；否则读启动时冻结的配置。"""
    if _test_enabled is not None:
        return bool(_test_enabled)
    try:
        return bool(get_memory_settings().memory_enabled)
    except Exception:  # noqa: BLE001 - 配置坏了不能拖垮聊天
        return False


def initialize() -> bool:
    """显式初始化（lifespan 调用）；返回是否可用。"""
    return get_store() is not None


def get_store() -> Optional[MemoryStore]:
    """懒加载；关闭/失败返回 None。"""
    global _store, _init_error, _init_failed

    if _test_enabled is not None:
        return _test_store
    if not enabled():
        return None
    if _store is not None:
        return _store
    if _init_failed:
        return None

    with _lock:
        if _store is not None:
            return _store
        if _init_failed:
            return None
        try:
            settings = get_memory_settings()
            path = resolve_db_path(settings.memory_db_path)
            store = MemoryStore(MemoryDatabase(path))
            store.initialize()
            _store = store
            _init_error = ""
            _init_failed = False
            logger.info("对话持久化已启用：%s", path)
        except Exception as exc:  # noqa: BLE001 - 初始化失败降级为不落盘
            # 只记类型；路径/异常正文不写日志，避免把用户路径带进日志。
            _init_error = f"{type(exc).__name__}"
            _init_failed = True
            logger.warning("对话持久化初始化失败，已降级为不落盘：%s", type(exc).__name__)
            return None
    return _store


def status() -> Dict[str, Any]:
    if _test_enabled is not None:
        if _test_store is None:
            return {"enabled": bool(_test_enabled), "ready": False, "error": "test_store_missing"}
        return _test_store.status()
    if not enabled():
        return {"enabled": False, "ready": False, "reason": "memory_disabled"}
    store = get_store()
    if store is None:
        return {"enabled": True, "ready": False, "error": _init_error or "init_failed"}
    return store.status()


def error() -> str:
    return _init_error


def reset_for_tests(
    store: Optional[MemoryStore] = None,
    *,
    enabled: Optional[bool] = None,
) -> None:
    """测试专用：注入内存/临时库，或清空单例。"""
    global _store, _init_error, _init_failed, _test_store, _test_enabled
    with _lock:
        _store = None
        _init_error = ""
        _init_failed = False
        _test_store = store
        if enabled is not None:
            _test_enabled = bool(enabled)
        elif store is not None:
            _test_enabled = True
        else:
            _test_enabled = None


__all__ = [
    "enabled",
    "error",
    "get_store",
    "initialize",
    "reset_for_tests",
    "status",
]
