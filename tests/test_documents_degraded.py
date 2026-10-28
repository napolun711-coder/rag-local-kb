from __future__ import annotations

import unittest
from unittest.mock import patch

from app.adapters.lightrag.errors import KnowledgeHTTPError, KnowledgeUnavailable
from app.features.documents import api as documents_api
from app.features.documents.service import DocumentCatalogService


class _UnavailableGateway:
    def list_documents_paginated(self, **_kwargs):
        raise KnowledgeUnavailable("offline")


class _BadRequestGateway:
    def list_documents_paginated(self, **_kwargs):
        raise KnowledgeHTTPError(400, "bad filter")


class _FailsOnLaterPageGateway:
    def __init__(self) -> None:
        self.calls = 0

    def list_documents_paginated(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return {"documents": [], "pagination": {"has_next": False}}
        if self.calls == 2:
            return {
                "documents": [
                    {
                        "id": "remote-1",
                        "file_path": "remote.md",
                        "status": "processed",
                    }
                ],
                "pagination": {"has_next": True},
            }
        raise KnowledgeUnavailable("dropped on page two")


class _LocalOnlyService(DocumentCatalogService):
    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self.settings = object()

    def _local_results(self):
        return [
            {
                "file_id": "local-1",
                "filename": "alpha.txt",
                "doc_name": "Alpha",
                "state": "review",
                "quality": {"level": "ok", "score": 90},
                "source_path": "",
                "saved_path": "",
                "knowledge": {"state": "not_submitted"},
                "updated_at": "2026-10-29T00:00:00+00:00",
            },
            {
                "file_id": "local-2",
                "filename": "beta.md",
                "doc_name": "Beta",
                "state": "done",
                "quality": {"level": "ok", "score": 95},
                "source_path": "",
                "saved_path": "",
                "knowledge": {"state": "done"},
                "updated_at": "2026-10-29T00:01:00+00:00",
            },
        ]


class DocumentDegradedTests(unittest.TestCase):
    def test_unavailable_service_returns_local_records(self) -> None:
        service = _LocalOnlyService(_UnavailableGateway())
        with patch(
            "app.features.documents.service.topics.topic_maps",
            return_value=({}, {}),
        ):
            result = service.list_documents(page=1, page_size=20)

        self.assertFalse(result.upstream_ok)
        self.assertEqual(result.items, ())
        self.assertEqual(result.local_only_total, 2)
        self.assertEqual({item.file_id for item in result.local_only}, {"local-1", "local-2"})

    def test_local_filter_still_works_while_degraded(self) -> None:
        service = _LocalOnlyService(_UnavailableGateway())
        with patch(
            "app.features.documents.service.topics.topic_maps",
            return_value=({}, {}),
        ):
            result = service.list_documents(page=1, page_size=20, q="alpha")

        self.assertFalse(result.upstream_ok)
        self.assertEqual(result.local_only_total, 1)
        self.assertEqual(result.local_only[0].file_id, "local-1")

    def test_overview_marks_remote_counts_as_unavailable(self) -> None:
        service = _LocalOnlyService(_UnavailableGateway())
        with patch(
            "app.features.documents.service.topics.topic_maps",
            return_value=({}, {}),
        ):
            result = service.overview()

        self.assertFalse(result.upstream_ok)
        self.assertEqual(result.remote_total, 0)
        self.assertEqual(result.local_total, 2)

    def test_client_errors_are_not_degraded(self) -> None:
        service = _LocalOnlyService(_BadRequestGateway())
        with self.assertRaises(KnowledgeHTTPError):
            service.list_documents(page=1, page_size=20)

    def test_filtered_multipage_failure_does_not_return_partial_remote_data(self) -> None:
        service = _LocalOnlyService(_FailsOnLaterPageGateway())
        with patch(
            "app.features.documents.service.topics.topic_maps",
            return_value=({}, {}),
        ):
            result = service.list_documents(page=1, page_size=20, q="a")

        self.assertFalse(result.upstream_ok)
        self.assertEqual(result.items, ())
        self.assertEqual(result.local_only_total, 2)

    def test_api_returns_local_payload_without_claiming_remote_success(self) -> None:
        service = _LocalOnlyService(_UnavailableGateway())
        with (
            patch("app.features.documents.service.topics.topic_maps", return_value=({}, {})),
            patch("app.features.documents.api._service", return_value=service),
        ):
            payload = documents_api.files_list(
                page=1,
                page_size=20,
                status="all",
                q="",
                topic_id="",
            )
            overview = documents_api.files_overview()

        self.assertEqual(payload["documents"], [])
        self.assertEqual(len(payload["local_only"]), 2)
        self.assertFalse(payload["upstream"]["ok"])
        self.assertIn("仅显示本机已有记录", payload["notice"])
        self.assertFalse(overview["upstream"]["ok"])
        self.assertEqual(overview["counts"]["all"], 0)


if __name__ == "__main__":
    unittest.main()
