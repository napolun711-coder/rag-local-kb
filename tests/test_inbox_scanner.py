from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.features.inbox.config import InboxSettings
from app.features.inbox import service


class InboxScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "inbox"
        self.root.mkdir()
        self.state = Path(self.temp_dir.name) / "state.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _settings(self, *, enabled: bool = True, settle: int = 5) -> InboxSettings:
        return InboxSettings(
            enabled=enabled,
            directory=self.root,
            state_path=self.state,
            poll_seconds=10,
            settle_seconds=settle,
            allowed_extensions=(".txt", ".md"),
            max_file_size=1024,
        )

    def _scan(self, settings: InboxSettings, now: float):
        with (
            patch("app.features.inbox.service._protected_paths", return_value=()),
            patch("app.features.inbox.service.time.time", return_value=now),
        ):
            return service.scan_once(settings, reason="test")

    def test_disabled_scanner_does_not_process_files(self) -> None:
        source = self.root / "note.txt"
        source.write_text("hello", encoding="utf-8")
        with patch("app.features.inbox.service.ingest_service.process_upload") as upload:
            result = self._scan(self._settings(enabled=False), 1000)

        self.assertFalse(result["ok"])
        upload.assert_not_called()
        self.assertTrue(source.is_file())

    def test_file_settles_then_creates_one_review_job(self) -> None:
        source = self.root / "note.txt"
        source.write_text("hello inbox", encoding="utf-8")
        settings = self._settings(settle=5)
        with patch(
            "app.features.inbox.service.ingest_service.process_upload",
            return_value={"file_id": "job-1", "state": "review"},
        ) as upload:
            first = self._scan(settings, 1000)
            second = self._scan(settings, 1006)
            third = self._scan(settings, 1012)

        self.assertTrue(first["ok"])
        self.assertEqual(first["processed"], 0)
        self.assertEqual(second["processed"], 1)
        self.assertEqual(third["processed"], 0)
        self.assertEqual(upload.call_count, 1)
        self.assertTrue(source.is_file())

    def test_same_content_at_another_name_is_duplicate(self) -> None:
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_text("same payload", encoding="utf-8")
        second.write_text("same payload", encoding="utf-8")
        settings = self._settings(settle=5)
        with patch(
            "app.features.inbox.service.ingest_service.process_upload",
            return_value={"file_id": "job-1", "state": "review"},
        ) as upload:
            self._scan(settings, 1000)
            result = self._scan(settings, 1006)

        self.assertEqual(upload.call_count, 1)
        self.assertEqual(result["duplicates"], 1)
        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())
        payload = self.state.read_text(encoding="utf-8")
        digest = hashlib.sha256(b"same payload").hexdigest()
        self.assertIn(digest, payload)

    def test_failure_keeps_source_and_does_not_retry_unchanged_file(self) -> None:
        source = self.root / "broken.md"
        source.write_text("# broken", encoding="utf-8")
        settings = self._settings(settle=0)
        with patch(
            "app.features.inbox.service.ingest_service.process_upload",
            side_effect=RuntimeError("parser failed"),
        ) as upload:
            first = self._scan(settings, 1000)
            second = self._scan(settings, 1001)

        self.assertEqual(first["failed"], 1)
        self.assertEqual(second["processed"], 0)
        self.assertEqual(upload.call_count, 1)
        self.assertTrue(source.is_file())

    def test_hidden_temp_and_unsupported_files_are_ignored(self) -> None:
        for name in (".hidden.txt", "draft.txt.part", "binary.bin"):
            (self.root / name).write_text("ignored", encoding="utf-8")
        with patch("app.features.inbox.service.ingest_service.process_upload") as upload:
            result = self._scan(self._settings(settle=0), 1000)

        self.assertTrue(result["ok"])
        upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
