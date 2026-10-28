"""单线程记忆任务 worker。

边界：
- 只有 `RAGKB_MEMORY_ENABLED=on` 且 `RAGKB_MEMORY_AUTO_EXTRACT=on` 且 `RAGKB_MEMORY_WORKER=on` 才启动；
- 没有注册任何 handler 时保持空转，不领取任务；
- 单线程、短事务、租约 + 重试；启动时恢复过期租约；
- 日志只记录任务 kind / 异常类型，不记录 payload 正文。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

from app.features.memory.config import get_memory_settings
from app.features.memory import runtime

logger = logging.getLogger(__name__)

JobHandler = Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]

#: 租约必须覆盖一次 LLM 提取的最长耗时，再留出收尾余量；
#: 否则慢模型下任务还在跑，租约已过期，会被另一个 worker 重复领取。
LEASE_MARGIN_SECONDS = 60


class JobRetry(Exception):
    """handler 主动要求重试（可指定延迟）。"""

    def __init__(
        self,
        message: str = "",
        *,
        delay_seconds: Optional[int] = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.delay_seconds = delay_seconds
        self.retryable = retryable


class MemoryJobWorker:
    def __init__(
        self,
        *,
        poll_seconds: float = 1.0,
        lease_seconds: int = 120,
        worker_id: Optional[str] = None,
    ) -> None:
        self.poll_seconds = max(0.05, float(poll_seconds or 1.0))
        self.lease_seconds = max(5, int(lease_seconds or 120))
        self.worker_id = worker_id or f"worker_{id(self):x}"
        self._handlers: Dict[str, JobHandler] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self.last_error = ""

    # ============================================================
    # 生命周期
    # ============================================================

    def register(self, kind: str, handler: JobHandler) -> None:
        clean_kind = str(kind or "").strip()
        if not clean_kind:
            raise ValueError("kind 不能为空")
        self._handlers[clean_kind] = handler

    def effective_lease_seconds(self, cfg: Any = None) -> int:
        """租约至少覆盖一次 LLM 超时 + 余量，避免慢模型被重复领取。"""
        if cfg is None:
            try:
                cfg = get_memory_settings()
            except Exception:  # noqa: BLE001 - 配置坏了沿用构造值
                return max(5, int(self.lease_seconds or 120))
        llm_timeout = int(getattr(cfg, "llm_timeout", 300) or 300)
        return max(
            5,
            int(self.lease_seconds or 120),
            llm_timeout + LEASE_MARGIN_SECONDS,
        )

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            try:
                cfg = get_memory_settings()
            except Exception as exc:  # noqa: BLE001 - 配置坏了不能拖垮服务
                logger.warning("记忆 worker 读取配置失败：%s", type(exc).__name__)
                return False
            if not (
                cfg.memory_enabled
                and cfg.memory_auto_extract
                and cfg.memory_worker
            ):
                logger.info(
                    "记忆 worker 未启动（RAGKB_MEMORY_ENABLED/RAGKB_MEMORY_AUTO_EXTRACT/RAGKB_MEMORY_WORKER 未全开）"
                )
                return False
            store = runtime.get_store()
            if store is None:
                logger.info("记忆 worker 未启动（记忆库未就绪）")
                return False
            self.lease_seconds = self.effective_lease_seconds(cfg)
            try:
                recovered = store.jobs.recover_stale()
                if recovered:
                    logger.info("记忆 worker 启动恢复：%d 个过期租约任务已重新排队", recovered)
            except Exception as exc:  # noqa: BLE001 - 恢复失败不能阻止启动
                logger.warning("记忆任务恢复失败（忽略）：%s", type(exc).__name__)

            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="memory-job-worker",
                daemon=True,
            )
            self._thread.start()
            logger.info("记忆 worker 已启动：%s", self.worker_id)
            return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout or 0.0)))

    def running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def status(self) -> Dict[str, Any]:
        return {
            "running": self.running(),
            "worker_id": self.worker_id,
            "handlers": sorted(self._handlers),
            "last_error": self.last_error,
        }

    # ============================================================
    # 单步执行
    # ============================================================

    def run_once(self) -> bool:
        """领取并执行一个任务；没有可执行任务返回 False。"""
        store = runtime.get_store()
        if store is None:
            return False
        try:
            cfg = get_memory_settings()
        except Exception:  # noqa: BLE001
            return False
        if not (cfg.memory_enabled and cfg.memory_auto_extract):
            return False
        if not self._handlers:
            # 只搭基础设施； 注册 extract_memory handler 后才领取。
            return False

        job = store.jobs.claim(
            worker_id=self.worker_id,
            lease_seconds=self.effective_lease_seconds(cfg),
            kinds=tuple(self._handlers),
        )
        if job is None:
            return False

        handler = self._handlers.get(str(job["kind"]))
        if handler is None:
            store.jobs.fail(
                job["id"],
                "handler_missing",
                retryable=False,
                worker_id=self.worker_id,
            )
            return True

        try:
            result = handler(job)
        except JobRetry as exc:
            self.last_error = type(exc).__name__
            store.jobs.fail(
                job["id"],
                str(exc) or type(exc).__name__,
                retryable=exc.retryable,
                retry_delay_seconds=exc.delay_seconds,
                worker_id=self.worker_id,
            )
            logger.warning("记忆任务要求重试：%s（%s）", job["kind"], type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 - 单任务失败不能杀死 worker
            self.last_error = type(exc).__name__
            store.jobs.fail(
                job["id"],
                type(exc).__name__,
                retryable=True,
                worker_id=self.worker_id,
            )
            logger.warning("记忆任务失败：%s（%s）", job["kind"], type(exc).__name__)
        else:
            done = store.jobs.complete(
                job["id"], result=result, worker_id=self.worker_id
            )
            self._enqueue_follow_up(done)
        return True

    def _enqueue_follow_up(self, job: Optional[Dict[str, Any]]) -> None:
        """任务运行期间又有新入队请求时，完成后补一轮。"""
        if not isinstance(job, dict):
            return
        payload = dict(job.get("payload") or {})
        if not payload.pop("_follow_up_requested", False):
            return
        store = runtime.get_store()
        if store is None:
            return
        try:
            store.jobs.enqueue(
                kind=str(job["kind"]),
                session_id=job.get("session_id"),
                payload=payload,
                priority=int(job.get("priority") or 7),
                delay_seconds=0,
                dedupe_key=str(job.get("dedupe_key") or ""),
                max_attempts=int(job.get("max_attempts") or 5),
            )
        except Exception as exc:  # noqa: BLE001 - 补入队失败不影响主任务
            logger.warning("记忆任务补入队失败（忽略）：%s", type(exc).__name__)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                did_work = self.run_once()
            except Exception as exc:  # noqa: BLE001 - 循环不能因单次异常退出
                self.last_error = type(exc).__name__
                logger.warning("记忆 worker 循环异常（忽略）：%s", type(exc).__name__)
                did_work = False
            if not did_work:
                self._stop.wait(self.poll_seconds)


_worker_lock = threading.RLock()
_worker: Optional[MemoryJobWorker] = None


def get_worker() -> MemoryJobWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = MemoryJobWorker()
        return _worker


def reset_worker_for_tests() -> None:
    global _worker
    with _worker_lock:
        if _worker is not None:
            _worker.stop()
        _worker = None


__all__ = [
    "JobHandler",
    "JobRetry",
    "MemoryJobWorker",
    "get_worker",
    "reset_worker_for_tests",
]
