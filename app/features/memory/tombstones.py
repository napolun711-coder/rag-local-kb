"""删除墓碑（append-only JSONL）。

为什么需要它：
- 备份是整库快照；如果只恢复旧备份，已删除的数据会“复活”；
- 每次删除写一行墓碑，恢复流程必须保留当前墓碑文件并重放，旧快照中的已删数据仍会被抑制；
- 文件独立于主库，且只存 ID/哈希，不存正文。

 只写墓碑；备份与恢复重放在  实现。文件先落盘，后续可直接使用。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict

from app.features.memory.util import utc_now

logger = logging.getLogger(__name__)


class TombstoneLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, *, kind: str, item_id: str, mode: str = "soft", **extra: Any) -> None:
        record: Dict[str, Any] = {
            "ts": utc_now(),
            "kind": str(kind),
            "id": str(item_id),
            "mode": str(mode),
        }
        for key, value in extra.items():
            if value is None or value == "" or value == []:
                continue
            record[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            with self._lock:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError:
            # 墓碑写失败不能反噬删除主流程；只记类型，不记正文。
            logger.warning("删除墓碑写入失败（忽略）：%s", self.path.name)


__all__ = ["TombstoneLog"]
