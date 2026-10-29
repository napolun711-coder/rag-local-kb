from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.features.documents.service import DocumentCatalogService


class _LocalService(DocumentCatalogService):
    def __init__(self, results, settings) -> None:
        self.gateway = None
        self.settings = settings
        self._results = results

    def _local_results(self):
        return self._results


class DocumentOriginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.upload_dir = root / "uploads"
        self.parsed_dir = root / "parsed"
        self.upload_dir.mkdir()
        self.parsed_dir.mkdir()
        self.source = self.parsed_dir / "original.txt"
        self.source.write_text("original", encoding="utf-8")
        self.settings = SimpleNamespace(
            upload_dir=self.upload_dir,
            parsed_dir=self.parsed_dir,
        )
        self.results = [{
            "file_id": "20261029123456-abcdef12",
            "filename": "original.txt",
            "doc_name": "original",
            "state": "done",
            "quality": {},
            "knowledge": {
                "state": "done",
                "document_name": "original.md",
            },
            "source_path": str(self.source),
            "saved_path": "",
            "updated_at": "2026-10-29T00:00:00Z",
        }]
        self.service = _LocalService(self.results, self.settings)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_knowledge_document_name_resolves_to_local_original(self) -> None:
        payload = self.service.resolve_origin("original.md")
        self.assertTrue(payload["available"])
        self.assertEqual(payload["kind"], "source")
        self.assertEqual(payload["file_id"], "20261029123456-abcdef12")

    def test_unknown_document_returns_a_localized_reason(self) -> None:
        payload = self.service.resolve_origin("missing.md")
        self.assertFalse(payload["available"])
        self.assertIn("本机没有找到", payload["reason"])


if __name__ == "__main__":
    unittest.main()
