from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.adapters.lightrag.errors import KnowledgeUnavailable
from app.features.ingest import service, storage


FILE_ID = "20261029123456-abcdef12"
IMAGE_ID = "20261029123500-1234abcd"
LONG_TEXT = (
    "本地知识库会保存用户上传的资料，并在解析、质量校验与人工确认之后提交给知识服务建立索引。"
    "整个流程保留原件与解析结果，失败时可以重试或查看原因。文档管理页会显示待确认、"
    "需修复、入库中与已跳过状态，用户据此决定继续入库或修改正文。"
)


def _settings(root: Path):
    return SimpleNamespace(
        parsed_dir=root / "parsed",
        upload_dir=root / "uploads",
        quality_profile="standard",
        quality_ocr_routes=("pdf", "image"),
        quality_min_chars=50,
        quality_max_unocr_images=0,
    )


def _result(file_id: str = FILE_ID, **overrides):
    payload = {
        "file_id": file_id,
        "filename": "alpha.txt",
        "doc_name": "alpha",
        "state": "review",
        "detail": "已通过本地质量校验，等待你确认入库",
        "parse": {"route": "text", "pages": 1},
        "quality": {},
        "knowledge": {"state": "not_submitted", "track_id": ""},
        "lightrag": {"state": "not_submitted", "track_id": ""},
        "timeline": [],
        "updated_at": storage.utc_now(),
    }
    payload.update(overrides)
    return payload


class _Gateway:
    def __init__(self, *, fail: bool = False, status: dict | None = None) -> None:
        self.fail = fail
        self.status = status or {}
        self.uploads: list[tuple[str, bytes, str]] = []
        self.status_calls: list[str] = []

    def upload_document(self, name: str, content: bytes, mime: str):
        self.uploads.append((name, content, mime))
        if self.fail:
            raise KnowledgeUnavailable("offline")
        return {"track_id": "track-1"}

    def document_status(self, track_id: str):
        self.status_calls.append(track_id)
        return self.status


class UploadChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = _settings(Path(self.temp_dir.name))
        self.settings.parsed_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write(self, payload) -> None:
        storage.write_result(self.settings, payload["file_id"], payload)

    def _patch(self):
        return patch.object(service, "get_ingest_settings", return_value=self.settings)

    def test_content_for_edit_exposes_image_caption_fields(self) -> None:
        self._write(_result(
            IMAGE_ID,
            filename="photo.png",
            doc_name="photo",
            parse={"route": "image", "pages": 1},
            is_image=True,
            image_total=1,
        ))
        storage.write_content(self.settings, IMAGE_ID, LONG_TEXT)
        with self._patch():
            payload = service.content_for_edit(IMAGE_ID)
        self.assertEqual(payload["doc_name_stem"], "photo")
        self.assertEqual(payload["doc_id"], "1234")
        self.assertTrue(payload["is_image"])
        self.assertTrue(payload["editable"])
        self.assertEqual(payload["body_chars"], len(LONG_TEXT))

    def test_caption_save_appends_and_then_replaces_without_losing_ocr_text(self) -> None:
        self._write(_result(
            IMAGE_ID,
            filename="photo.png",
            doc_name="photo",
            parse={"route": "image", "pages": 1},
            is_image=True,
            image_total=1,
        ))
        storage.write_content(self.settings, IMAGE_ID, LONG_TEXT)
        with self._patch():
            first = service.edit_content(IMAGE_ID, "第一段描述", caption=True)
            content = storage.read_content(self.settings, IMAGE_ID)
            second = service.edit_content(IMAGE_ID, "第二段描述", caption=True)
            updated = storage.read_content(self.settings, IMAGE_ID)
            edit_payload = service.content_for_edit(IMAGE_ID)
        self.assertTrue(first["ok"])
        self.assertIn(LONG_TEXT, content)
        self.assertIn("第一段描述", content)
        self.assertTrue(second["ok"])
        self.assertIn(LONG_TEXT, updated)
        self.assertIn("第二段描述", updated)
        self.assertNotIn("第一段描述", updated)
        self.assertEqual(edit_payload["caption_text"], "第二段描述")
        self.assertEqual(edit_payload["edit"]["count"], 2)

    def test_save_and_ingest_submits_and_returns_ingesting_status(self) -> None:
        self._write(_result())
        storage.write_content(self.settings, FILE_ID, LONG_TEXT)
        gateway = _Gateway()
        with self._patch(), patch.object(service, "get_knowledge_gateway", return_value=gateway):
            outcome = service.edit_content(FILE_ID, LONG_TEXT, ingest_now=True)
        self.assertTrue(outcome["ok"])
        self.assertTrue(outcome["saved"])
        self.assertTrue(outcome["ingested"])
        self.assertEqual(outcome["state"], "ingesting")
        self.assertEqual(outcome["status"]["knowledge"]["state"], "submitted")
        self.assertEqual(gateway.uploads[0][0], "alpha.md")

    def test_image_ingest_uses_short_id_document_name(self) -> None:
        self._write(_result(
            IMAGE_ID,
            filename="photo.png",
            doc_name="photo",
            parse={"route": "image", "pages": 1},
            is_image=True,
            image_total=1,
        ))
        storage.write_content(self.settings, IMAGE_ID, LONG_TEXT)
        gateway = _Gateway()
        with self._patch(), patch.object(service, "get_knowledge_gateway", return_value=gateway):
            outcome = service.edit_content(IMAGE_ID, LONG_TEXT, ingest_now=True)
        self.assertTrue(outcome["ingested"])
        self.assertEqual(gateway.uploads[0][0], "photo_1234.md")

    def test_save_and_ingest_failure_keeps_saved_content_and_reports_reason(self) -> None:
        self._write(_result())
        storage.write_content(self.settings, FILE_ID, LONG_TEXT)
        gateway = _Gateway(fail=True)
        with self._patch(), patch.object(service, "get_knowledge_gateway", return_value=gateway):
            outcome = service.edit_content(FILE_ID, LONG_TEXT, ingest_now=True)
        self.assertTrue(outcome["ok"])
        self.assertTrue(outcome["saved"])
        self.assertFalse(outcome["ingested"])
        self.assertIn("知识库服务不可用", outcome["ingest_error"])
        self.assertEqual(outcome["state"], "review")
        self.assertEqual(outcome["status"]["knowledge"]["state"], "failed")
        self.assertIn("知识库服务不可用", outcome["status"]["knowledge_error"])
        self.assertEqual(storage.read_content(self.settings, FILE_ID), LONG_TEXT)

    def test_restore_contract_groups_actionable_states(self) -> None:
        self._write(_result())
        self._write(_result("20261029123457-bbbbbbbb", state="blocked", filename="blocked.txt"))
        self._write(_result("20261029123458-cccccccc", state="skipped", filename="skipped.txt"))
        self._write(_result("20261029123459-dddddddd", state="ingesting", filename="busy.txt"))
        self._write(_result("20261029123501-eeeeeeee", state="done", filename="done.txt"))
        self._write(_result(
            "20261029123502-ffffffff",
            state="skipped",
            filename="old.txt",
            updated_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
        ))
        with self._patch():
            pending = service.pending_reviews()
            actionable = service.actionable_jobs()
            active = service.active_jobs()
            recent_skipped, skipped_total = service.skipped_jobs()
        self.assertEqual([item["file_id"] for item in pending], [FILE_ID])
        self.assertEqual([item["state"] for item in actionable], ["blocked"])
        self.assertEqual([item["state"] for item in active], ["ingesting"])
        self.assertEqual([item["file_id"] for item in recent_skipped], ["20261029123458-cccccccc"])
        self.assertEqual(skipped_total, 2)

    def test_knowledge_status_refresh_moves_ingesting_to_done(self) -> None:
        self._write(_result(
            state="ingesting",
            knowledge={"state": "submitted", "track_id": "track-1"},
            lightrag={"state": "submitted", "track_id": "track-1"},
        ))
        gateway = _Gateway(status={
            "documents": [{"status": "processed"}],
            "total_count": 1,
        })
        with self._patch(), patch.object(service, "get_knowledge_gateway", return_value=gateway):
            outcome = service.refresh_knowledge_status(FILE_ID)
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["state"], "done")
        self.assertEqual(outcome["status"]["progress"]["processed"], 1)
        self.assertEqual(outcome["status"]["state"], "done")

    def test_session_allow_all_releases_only_review_jobs(self) -> None:
        self._write(_result())
        blocked_id = "20261029123457-bbbbbbbb"
        self._write(_result(blocked_id, state="blocked", filename="blocked.txt"))
        storage.write_content(self.settings, FILE_ID, LONG_TEXT)
        storage.write_content(self.settings, blocked_id, LONG_TEXT)
        gateway = _Gateway()
        with self._patch(), patch.object(service, "get_knowledge_gateway", return_value=gateway):
            service._release_review_jobs()
        self.assertEqual([name for name, _, _ in gateway.uploads], ["alpha.md"])
        self.assertEqual(storage.read_result(self.settings, FILE_ID)["state"], "ingesting")
        self.assertEqual(storage.read_result(self.settings, blocked_id)["state"], "blocked")

    def test_force_submits_blocked_content_and_records_audit_step(self) -> None:
        self._write(_result(state="blocked", detail="未通过质检"))
        storage.write_content(self.settings, FILE_ID, LONG_TEXT)
        gateway = _Gateway()
        with self._patch(), patch.object(service, "get_knowledge_gateway", return_value=gateway):
            outcome = service.activate(FILE_ID, "force")
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["state"], "ingesting")
        self.assertEqual(gateway.uploads[0][0], "alpha.md")
        stored = storage.read_result(self.settings, FILE_ID)
        self.assertTrue(stored["forced"])
        self.assertIn("force", [row["step"] for row in stored["timeline"]])

    def test_old_track_without_documents_becomes_actionable(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        self._write(_result(
            state="ingesting",
            created_at=old,
            knowledge={"state": "submitted", "track_id": "track-lost"},
            lightrag={"state": "submitted", "track_id": "track-lost"},
        ))
        gateway = _Gateway(status={"documents": [], "total_count": 0})
        with self._patch(), patch.object(service, "get_knowledge_gateway", return_value=gateway):
            outcome = service.refresh_knowledge_status(FILE_ID)
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["state"], "review")
        self.assertEqual(outcome["status"]["knowledge"]["code"], "track_not_found")
        self.assertTrue(outcome["status"]["can"]["ingest"])

    def test_recent_track_without_documents_stays_ingesting(self) -> None:
        self._write(_result(
            state="ingesting",
            created_at=storage.utc_now(),
            knowledge={"state": "submitted", "track_id": "track-new"},
            lightrag={"state": "submitted", "track_id": "track-new"},
        ))
        gateway = _Gateway(status={"documents": [], "total_count": 0})
        with self._patch(), patch.object(service, "get_knowledge_gateway", return_value=gateway):
            outcome = service.refresh_knowledge_status(FILE_ID)
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["state"], "ingesting")

    def test_duplicate_ingest_is_idempotent_while_submission_is_active(self) -> None:
        self._write(_result(
            state="ingesting",
            knowledge={"state": "submitted", "track_id": "track-1"},
            lightrag={"state": "submitted", "track_id": "track-1"},
        ))
        gateway = _Gateway()
        with self._patch(), patch.object(service, "get_knowledge_gateway", return_value=gateway):
            outcome = service.activate(FILE_ID, "ingest")
        self.assertTrue(outcome["ok"])
        self.assertTrue(outcome["duplicate_submit"])
        self.assertEqual(gateway.uploads, [])


if __name__ == "__main__":
    unittest.main()
