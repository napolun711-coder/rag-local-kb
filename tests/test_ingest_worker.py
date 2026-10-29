from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.features.ingest import service, storage, worker


BATCH = "batch_20261029170000_abcdef12"


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


class IngestWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = _settings(Path(self.temp_dir.name))
        self.settings.parsed_dir.mkdir(parents=True, exist_ok=True)
        self.settings.upload_dir.mkdir(parents=True, exist_ok=True)
        worker._PAUSED = False
        worker._IN_FLIGHT.clear()

    def tearDown(self) -> None:
        worker._PAUSED = False
        worker._IN_FLIGHT.clear()
        self.temp_dir.cleanup()

    def _patch(self):
        return patch.object(service, "get_ingest_settings", return_value=self.settings)

    def _upload(self, name: str, content: bytes, *, batch_id: str = BATCH):
        with self._patch():
            return service.process_upload(name, content, batch_id=batch_id)

    def test_upload_enqueues_without_running_pipeline(self) -> None:
        with patch.object(service, "_run_pipeline") as pipeline:
            result = self._upload("alpha.txt", b"unique-content-1")
        pipeline.assert_not_called()
        self.assertEqual(result["state"], "queued")
        self.assertEqual(result["batch_id"], BATCH)
        stored = storage.read_result(self.settings, result["file_id"])
        self.assertEqual(stored["state"], "queued")
        self.assertEqual(stored["attempts"], 0)
        self.assertTrue(stored["content_hash"])
        self.assertTrue(stored["allow_session_auto"])

    def test_duplicate_content_reuses_existing_job(self) -> None:
        first = self._upload("alpha.txt", b"same-content")
        second = self._upload("beta.txt", b"same-content")
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["file_id"], second["file_id"])
        self.assertEqual(len(storage.list_results(self.settings)), 1)

    def test_run_once_claims_and_processes_one_job(self) -> None:
        uploaded = self._upload("alpha.txt", b"process-me")

        def fake_pipeline(_settings, result, **_kwargs):
            result["state"] = "review"
            result["detail"] = "processed by test"
            storage.write_result(self.settings, result["file_id"], result)
            return result

        with (
            self._patch(),
            patch.object(service, "_run_pipeline", side_effect=fake_pipeline),
        ):
            claimed = worker.run_once()
        self.assertTrue(claimed)
        stored = storage.read_result(self.settings, uploaded["file_id"])
        self.assertEqual(stored["state"], "review")
        self.assertEqual(stored["attempts"], 1)
        self.assertNotIn(uploaded["file_id"], worker._IN_FLIGHT)
        self.assertFalse(worker.run_once())

    def test_exception_requeues_with_backoff(self) -> None:
        uploaded = self._upload("alpha.txt", b"retry-me")
        with self.assertLogs("app.features.ingest.worker", level="ERROR"):
            with (
                self._patch(),
                patch.object(service, "_run_pipeline", side_effect=RuntimeError("boom")),
                patch.dict(os.environ, {"RAGKB_INGEST_RETRY_BACKOFF_SECONDS": "1"}),
            ):
                worker.run_once()
        stored = storage.read_result(self.settings, uploaded["file_id"])
        self.assertEqual(stored["state"], "queued")
        self.assertEqual(stored["attempts"], 1)
        self.assertTrue(stored["next_attempt_at"])
        self.assertIn("重试", stored["detail"])

    def test_exception_becomes_failed_at_max_attempts(self) -> None:
        uploaded = self._upload("alpha.txt", b"fail-me")
        with self.assertLogs("app.features.ingest.worker", level="ERROR"):
            with (
                self._patch(),
                patch.object(service, "_run_pipeline", side_effect=RuntimeError("boom")),
                patch.dict(os.environ, {"RAGKB_INGEST_MAX_ATTEMPTS": "1"}),
            ):
                worker.run_once()
        stored = storage.read_result(self.settings, uploaded["file_id"])
        self.assertEqual(stored["state"], "failed")
        self.assertIn("处理异常", stored["detail"])

    def test_recover_requeues_interrupted_processing_job(self) -> None:
        uploaded = self._upload("alpha.txt", b"recover-me")
        stored = storage.read_result(self.settings, uploaded["file_id"])
        stored["state"] = "processing"
        storage.write_result(self.settings, uploaded["file_id"], stored)
        with self._patch():
            recovered = worker.recover()
        self.assertEqual(recovered, 1)
        after = storage.read_result(self.settings, uploaded["file_id"])
        self.assertEqual(after["state"], "queued")
        self.assertIn("重启", after["detail"])

    def test_pause_blocks_run_once_and_status_reports_queue(self) -> None:
        self._upload("alpha.txt", b"paused-job")
        with self._patch():
            paused = worker.set_paused(True)
            self.assertTrue(paused["paused"])
            self.assertFalse(worker.run_once())
            status = worker.status()
        self.assertTrue(status["paused"])
        self.assertEqual(status["queued"], 1)
        worker._PAUSED = False


if __name__ == "__main__":
    unittest.main()
