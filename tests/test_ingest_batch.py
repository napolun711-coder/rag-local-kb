from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.features.ingest import service, storage


BATCH = "batch_20261029140000_abcdef12"
OTHER = "batch_20261029140001_12345678"


def _settings(root: Path):
    return SimpleNamespace(
        parsed_dir=root / "parsed",
        upload_dir=root / "uploads",
        allowed_extensions=(".txt", ".md"),
        max_upload_size=1024 * 1024,
        quality_profile="standard",
        quality_ocr_routes=("pdf", "image"),
        quality_min_chars=50,
        quality_max_unocr_images=0,
    )


def _fid(value: int) -> str:
    return "2026102914" + f"{value:04d}" + "-abcdef" + f"{value:02d}"


def _result(file_id: str, state: str, batch_id: str = BATCH, created_offset: int = 0):
    created = (datetime.now(timezone.utc) + timedelta(seconds=created_offset)).isoformat()
    return {
        "file_id": file_id,
        "batch_id": batch_id,
        "filename": file_id + ".md",
        "doc_name": file_id,
        "state": state,
        "detail": "detail-" + state,
        "created_at": created,
        "updated_at": created,
        "knowledge": {
            "state": "failed" if state == "failed" else "not_submitted",
            "message": "knowledge-failed" if state == "failed" else "",
            "track_id": ("track-" + file_id[-8:]) if state == "ingesting" else "",
            "progress": {"state": "processing", "total": 2, "processed": 1},
        },
        "lightrag": {"state": "not_submitted"},
        "timeline": [],
    }


class IngestBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = _settings(Path(self.temp_dir.name))
        self.settings.parsed_dir.mkdir(parents=True, exist_ok=True)
        self.settings.upload_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write(self, payload) -> None:
        storage.write_result(self.settings, payload["file_id"], payload)

    def test_batch_id_is_validated_or_generated(self) -> None:
        self.assertEqual(service.normalize_batch_id(BATCH), BATCH)
        generated = service.normalize_batch_id("bad id")
        self.assertNotEqual(generated, "bad id")
        self.assertTrue(storage.valid_batch_id(generated))
        self.assertTrue(storage.valid_batch_id(service.normalize_batch_id("")))

    def test_upload_stores_the_requested_batch_id(self) -> None:
        def fake_pipeline(_settings, result, **_kwargs):
            return result

        with (
            patch.object(service, "get_ingest_settings", return_value=self.settings),
            patch.object(service, "_run_pipeline", side_effect=fake_pipeline),
        ):
            result = service.process_upload("alpha.txt", b"hello", batch_id=BATCH)
        self.assertEqual(result["batch_id"], BATCH)

    def test_batch_status_aggregates_only_that_batch(self) -> None:
        rows = [
            _result(_fid(1), "review", created_offset=1),
            _result(_fid(2), "needs_ocr", created_offset=2),
            _result(_fid(3), "blocked", created_offset=3),
            _result(_fid(4), "failed", created_offset=4),
            _result(_fid(5), "ingesting", created_offset=5),
            _result(_fid(6), "done", created_offset=6),
            _result(_fid(7), "skipped", created_offset=7),
            _result(_fid(99), "done", batch_id=OTHER, created_offset=8),
        ]
        for row in rows:
            self._write(row)
        with (
            patch.object(service, "get_ingest_settings", return_value=self.settings),
            patch.object(service, "refresh_knowledge_status", return_value={"ok": True}),
        ):
            payload = service.batch_status(BATCH)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["total"], 7)
        self.assertEqual(
            [item["file_id"] for item in payload["items"]],
            [_fid(i) for i in range(1, 8)],
        )
        self.assertEqual(payload["groups"]["review"], 2)
        self.assertEqual(payload["groups"]["actionable"], 2)
        self.assertEqual(payload["groups"]["active"], 1)
        self.assertEqual(payload["groups"]["done"], 1)
        self.assertEqual(payload["groups"]["skipped"], 1)
        self.assertFalse(payload["complete"])
        self.assertEqual(payload["items"][3]["knowledge_error"], "knowledge-failed")
        self.assertEqual(payload["items"][4]["progress"]["processed"], 1)

    def test_terminal_batch_is_complete_and_invalid_id_is_rejected(self) -> None:
        for index, state in enumerate(("done", "failed", "skipped")):
            self._write(_result(_fid(20 + index), state, created_offset=index))
        with patch.object(service, "get_ingest_settings", return_value=self.settings):
            payload = service.batch_status(BATCH)
            invalid = service.batch_status("bad id")
        self.assertTrue(payload["complete"])
        self.assertIsNone(invalid)

    def test_batch_status_refreshes_ingesting_track_before_aggregating(self) -> None:
        file_id = _fid(30)
        self._write(_result(file_id, "ingesting", created_offset=1))

        def fake_refresh(target: str):
            result = storage.read_result(self.settings, target)
            result["state"] = "done"
            storage.write_result(self.settings, target, result)
            return {"ok": True}

        with (
            patch.object(service, "get_ingest_settings", return_value=self.settings),
            patch.object(service, "refresh_knowledge_status", side_effect=fake_refresh) as refresh,
        ):
            payload = service.batch_status(BATCH)
        refresh.assert_called_once_with(file_id)
        self.assertEqual(payload["groups"]["done"], 1)
        self.assertTrue(payload["complete"])

    def test_unknown_valid_batch_returns_empty_progress(self) -> None:
        with patch.object(service, "get_ingest_settings", return_value=self.settings):
            payload = service.batch_status("batch_20261029140002_ffffffff")
        self.assertEqual(payload["total"], 0)
        self.assertFalse(payload["complete"])


if __name__ == "__main__":
    unittest.main()
