from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.features.ingest import service, storage


FILE_ID = "20261029123456-abcdef12"


class SkippedIngestResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = SimpleNamespace(parsed_dir=Path(self.temp_dir.name))
        storage.write_result(
            self.settings,
            FILE_ID,
            {
                "file_id": FILE_ID,
                "filename": "alpha.txt",
                "doc_name": "alpha",
                "state": "skipped",
                "detail": "Skipped by the user",
                "knowledge": {"state": "not_submitted", "track_id": ""},
                "timeline": [],
                "updated_at": "2026-10-29T00:00:00Z",
            },
        )
        storage.write_content(self.settings, FILE_ID, "reviewed content")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_resume_moves_skipped_content_into_existing_submit_flow(self) -> None:
        with patch.object(
            service,
            "get_ingest_settings",
            return_value=self.settings,
        ), patch.object(
            service,
            "_submit_to_knowledge",
            return_value={"ok": True, "state": "ingesting"},
        ) as submit:
            outcome = service.activate(FILE_ID, "resume")
        self.assertTrue(outcome["ok"])
        submitted = submit.call_args.args[1]
        self.assertEqual(submitted["state"], "review")
        self.assertEqual(submitted["timeline"][-1]["step"], "resume")

    def test_resume_rejects_non_skipped_job(self) -> None:
        result = storage.read_result(self.settings, FILE_ID)
        result["state"] = "review"
        storage.write_result(self.settings, FILE_ID, result)
        with patch.object(service, "get_ingest_settings", return_value=self.settings):
            outcome = service.activate(FILE_ID, "resume")
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["http_status"], 409)

    def test_resume_rejects_empty_parsed_content(self) -> None:
        storage.write_content(self.settings, FILE_ID, "")
        with patch.object(service, "get_ingest_settings", return_value=self.settings):
            outcome = service.activate(FILE_ID, "resume")
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["http_status"], 409)

    def test_public_status_reports_resume_capability(self) -> None:
        result = storage.read_result(self.settings, FILE_ID)
        payload = service._public_status(result)
        self.assertTrue(payload["can"]["resume"])


if __name__ == "__main__":
    unittest.main()
