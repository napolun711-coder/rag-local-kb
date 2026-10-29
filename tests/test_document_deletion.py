from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.features.documents import api as documents_api
from app.features.documents import deletion


FILE_ID = "20261029123456-abcdef12"


class DocumentDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.uploads = self.root / "uploads"
        self.parsed = self.root / "parsed"
        self.app_data = self.root / "app-data"
        self.uploads.mkdir()
        self.parsed.mkdir()
        self.app_data.mkdir()
        self.settings = SimpleNamespace(
            upload_dir=self.uploads,
            parsed_dir=self.parsed,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_app_copy(self) -> tuple[Path, Path]:
        archive = self.uploads / "20261029" / "120000_alpha.txt"
        archive.parent.mkdir(parents=True)
        archive.write_text("app archive", encoding="utf-8")
        job_dir = self.parsed / FILE_ID
        source = job_dir / "source" / "alpha.txt"
        source.parent.mkdir(parents=True)
        source.write_text("parsed source copy", encoding="utf-8")
        return archive, job_dir

    def test_external_source_is_never_deleted(self) -> None:
        source = self.root / "user-source.txt"
        source.write_text("original source", encoding="utf-8")
        with patch.object(
            deletion.storage,
            "read_result",
            return_value={"saved_path": str(source)},
        ), patch.object(deletion.storage, "remove_job"):
            result = deletion._delete_app_copy(
                file_id=FILE_ID,
                settings=self.settings,
            )
        self.assertTrue(source.is_file())
        self.assertFalse(result["archive_removed"])

    def test_delete_app_copy_removes_archive_and_parsed_directory(self) -> None:
        archive, job_dir = self._write_app_copy()
        external = self.root / "user-source.txt"
        external.write_text("original source", encoding="utf-8")
        with patch.object(
            deletion.storage,
            "read_result",
            return_value={
                "saved_path": str(archive),
                "source_path": str(external),
            },
        ), patch.object(deletion, "DATA_DIR", self.app_data):
            result = deletion._delete_app_copy(
                file_id=FILE_ID,
                settings=self.settings,
            )
        self.assertTrue(result["ok"])
        self.assertTrue(result["archive_removed"])
        self.assertFalse(archive.exists())
        self.assertFalse(job_dir.exists())
        self.assertTrue(external.is_file())

    def test_local_only_file_does_not_touch_knowledge(self) -> None:
        with patch.object(deletion, "_delete_knowledge") as knowledge, patch.object(
            deletion,
            "_delete_app_copy",
            return_value={"ok": True, "message": "app copy removed"},
        ) as app_copy:
            result = deletion._process_item(
                {"file_id": FILE_ID},
                delete_knowledge_upload_copy=False,
                settings=self.settings,
                gateway=Mock(),
            )
        self.assertTrue(result["ok"])
        knowledge.assert_not_called()
        app_copy.assert_called_once()

    def test_knowledge_document_can_be_deleted_without_app_copy(self) -> None:
        with patch.object(
            deletion,
            "_delete_knowledge",
            return_value={"ok": True, "message": "knowledge removed"},
        ) as knowledge, patch.object(deletion, "_delete_app_copy") as app_copy:
            result = deletion._process_item(
                {"doc_id": "doc-1"},
                delete_knowledge_upload_copy=False,
                settings=self.settings,
                gateway=Mock(),
            )
        self.assertTrue(result["ok"])
        knowledge.assert_called_once()
        app_copy.assert_not_called()

    def test_selected_file_removes_app_copy_and_knowledge_record(self) -> None:
        gateway = Mock()
        with patch.object(
            deletion,
            "_delete_knowledge",
            return_value={"ok": True, "message": "knowledge removed"},
        ) as knowledge, patch.object(
            deletion,
            "_delete_app_copy",
            return_value={"ok": True, "message": "app copy removed"},
        ) as app_copy:
            result = deletion._process_item(
                {"doc_id": "doc-1", "file_id": FILE_ID},
                delete_knowledge_upload_copy=True,
                settings=self.settings,
                gateway=gateway,
            )
        self.assertTrue(result["ok"])
        knowledge.assert_called_once_with(gateway, "doc-1", delete_file=True)
        app_copy.assert_called_once_with(file_id=FILE_ID, settings=self.settings)

    def test_knowledge_upload_copy_option_controls_lightrag_delete_file(self) -> None:
        gateway = Mock()
        gateway.delete_document.return_value = {
            "status": "deletion_started",
            "message": "Deletion started",
        }
        result = deletion._delete_knowledge(
            gateway,
            "doc-1",
            delete_file=False,
        )
        gateway.delete_document.assert_called_once_with(
            "doc-1",
            delete_file=False,
            delete_llm_cache=False,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["submitted"])

    def test_knowledge_failure_preserves_app_copy(self) -> None:
        with patch.object(
            deletion,
            "_delete_knowledge",
            return_value={"ok": False, "message": "knowledge unavailable"},
        ), patch.object(deletion, "_delete_app_copy") as app_copy:
            result = deletion._process_item(
                {"doc_id": "doc-1", "file_id": FILE_ID},
                delete_knowledge_upload_copy=False,
                settings=self.settings,
                gateway=Mock(),
            )
        self.assertFalse(result["ok"])
        app_copy.assert_not_called()

    def test_submitted_knowledge_removal_is_reported_as_pending(self) -> None:
        with patch.object(
            deletion,
            "_delete_knowledge",
            return_value={
                "ok": True,
                "message": "Knowledge deletion submitted",
                "submitted": True,
            },
        ), patch.object(
            deletion,
            "_delete_app_copy",
            return_value={"ok": True, "message": "app copy removed"},
        ):
            result = deletion._process_item(
                {"doc_id": "doc-1", "file_id": FILE_ID},
                delete_knowledge_upload_copy=False,
                settings=self.settings,
                gateway=Mock(),
            )
        self.assertTrue(result["ok"])
        self.assertTrue(result["pending"])

    def test_duplicate_items_are_collapsed_before_job_creation(self) -> None:
        item = {"doc_id": "doc-1", "file_id": FILE_ID}
        with patch.object(deletion.threading, "Thread") as thread:
            job = deletion.start_deletion(
                [item, dict(item)],
                delete_knowledge_upload_copy=True,
            )
        self.assertEqual(job["total"], 1)
        self.assertTrue(job["delete_knowledge_upload_copy"])
        thread.return_value.start.assert_called_once()

    def test_item_without_identifiers_is_rejected(self) -> None:
        with self.assertRaises(deletion.DeleteValidationError):
            deletion.start_deletion([{"name": "missing ids"}])


class DocumentDeletionApiTests(unittest.TestCase):
    def test_yes_confirmation_and_option_are_forwarded(self) -> None:
        data = documents_api.DeleteRequest(
            confirmation="Yes",
            delete_knowledge_upload_copy=True,
            items=[
                documents_api.DeleteItem(
                    doc_id="doc-1",
                    file_id=FILE_ID,
                    name="alpha",
                )
            ],
        )
        with patch.object(
            documents_api,
            "start_deletion",
            return_value={"id": "del_test"},
        ) as start:
            payload = documents_api.files_delete(data)
        self.assertTrue(payload["success"])
        start.assert_called_once_with(
            [{"doc_id": "doc-1", "file_id": FILE_ID, "name": "alpha"}],
            delete_knowledge_upload_copy=True,
        )

    def test_confirmation_is_required(self) -> None:
        data = documents_api.DeleteRequest(
            confirmation="yes please",
            items=[documents_api.DeleteItem(doc_id="doc-1")],
        )
        with self.assertRaises(HTTPException) as raised:
            documents_api.files_delete(data)
        self.assertEqual(raised.exception.status_code, 400)

    def test_empty_items_are_rejected(self) -> None:
        data = documents_api.DeleteRequest(confirmation="Yes")
        with self.assertRaises(HTTPException) as raised:
            documents_api.files_delete(data)
        self.assertEqual(raised.exception.status_code, 400)

    def test_invalid_item_is_rejected_before_job_start(self) -> None:
        data = documents_api.DeleteRequest(
            confirmation="Yes",
            items=[documents_api.DeleteItem(name="missing ids")],
        )
        with patch.object(deletion.threading, "Thread") as thread:
            with self.assertRaises(HTTPException) as raised:
                documents_api.files_delete(data)
        self.assertEqual(raised.exception.status_code, 400)
        thread.assert_not_called()


if __name__ == "__main__":
    unittest.main()
