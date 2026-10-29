from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "files.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "files.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "files.css").read_text(encoding="utf-8")


class FilesDeleteUiContractTests(unittest.TestCase):
    def _require_once(self, value: str) -> None:
        self.assertEqual(len(re.findall(re.escape(value), HTML)), 1, value)

    def test_delete_mode_and_dialog_controls_are_present(self) -> None:
        for element_id in (
            "deleteModeBtn",
            "cancelDeleteModeBtn",
            "deleteDialog",
            "deleteDialogBackdrop",
            "deleteSelectionCount",
            "deleteKnowledgeCopy",
            "deleteConfirmation",
            "confirmDelete",
            "cancelDelete",
        ):
            self._require_once('id="' + element_id + '"')

    def test_old_multi_action_controls_are_removed(self) -> None:
        self.assertNotIn('name="scope"', HTML)
        self.assertNotIn("查看影响", HTML)
        self.assertNotIn("removeKnowledgeBtn", HTML)
        self.assertNotIn("deleteAppCopyBtn", HTML)
        self.assertNotIn("remove_originals:", JS)

    def test_delete_request_uses_yes_and_upload_copy_option(self) -> None:
        self.assertIn('confirmation: "Yes"', JS)
        self.assertIn("delete_knowledge_upload_copy: deleteKnowledgeUploadCopy", JS)
        self.assertIn('value.trim().toLowerCase() === "yes"', JS)
        self.assertIn('$("confirmDelete").classList.toggle("ready", ready)', JS)

    def test_upload_copy_option_is_only_for_knowledge_items(self) -> None:
        self.assertIn('$("deleteKnowledgeCopyLabel").hidden = !items.some', JS)
        self.assertIn("同时删除知识库上传副本", HTML)

    def test_local_table_shows_state_and_recovery_actions(self) -> None:
        self.assertIn('<h2 id="localTitle">本机待处理与已跳过</h2>', HTML)
        self.assertNotIn("处理状态", HTML)
        self.assertNotIn("文件来源", HTML)
        self.assertIn("继续入库", JS)
        self.assertIn("data-resume-local", JS)
        self.assertIn('action: "resume"', JS)
        self.assertIn("local_state_label", JS)
        self.assertIn("local_detail", JS)
        self.assertIn("入库中…", JS)
        self.assertIn("去处理", JS)
        self.assertIn("scheduleIngestRefresh", JS)

    def test_original_file_boundary_remains_explicit(self) -> None:
        self.assertIn("原始文件及其所在文件夹不会被修改或删除", HTML)

    def test_knowledge_documents_are_visually_primary(self) -> None:
        self.assertIn('class="card overview-card"', HTML)
        self.assertIn('class="card primary-card"', HTML)
        self.assertIn('class="tw skip-scroll"', HTML)
        self.assertIn(".overview-card{padding:10px 12px", CSS)
        self.assertIn(".skip-scroll{max-height:270px;overflow:auto}", CSS)
        self.assertIn('$("deleteModeBtn").classList.toggle("danger", state.deleteMode)', JS)


if __name__ == "__main__":
    unittest.main()
