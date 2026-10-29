from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_JS = (ROOT / "static" / "index.js").read_text(encoding="utf-8")
FILES_JS = (ROOT / "static" / "files.js").read_text(encoding="utf-8")
FILES_HTML = (ROOT / "static" / "files.html").read_text(encoding="utf-8")
INDEX_CSS = (ROOT / "static" / "index.css").read_text(encoding="utf-8")


class UploadBatchUiContractTests(unittest.TestCase):
    def test_chat_uses_one_batch_card_and_poller(self) -> None:
        for value in (
            "BATCH_THRESHOLD",
            "createUploadBatch",
            "ensureBatchItem",
            "batchRefresh",
            "/ingest/batch/",
            "batch_id",
            "batchAfterAction",
            "__deferCompanion",
            "资料批次",
        ):
            self.assertIn(value, INDEX_JS, value)
        self.assertIn(".upload-batch", INDEX_CSS)

    def test_files_page_exposes_batch_filter_and_retry(self) -> None:
        self.assertIn('id="batchFilter"', FILES_HTML)
        self.assertIn('id="retryFailedBtn"', FILES_HTML)
        for value in (
            "filterLocalRows",
            "populateBatchFilter",
            "applyBatchFilter",
            "retryFailedRows",
            "batch_id",
            "重试失败",
        ):
            self.assertIn(value, FILES_JS, value)


if __name__ == "__main__":
    unittest.main()
