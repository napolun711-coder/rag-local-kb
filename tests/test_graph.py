from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.adapters.lightrag.client import LightRagHttpClient
from app.adapters.lightrag.config import LightRagSettings
from app.adapters.lightrag.errors import KnowledgeUnavailable
from app.features.knowledge import api as knowledge_api
from app.platform.errors import AppError
from services.lightrag_shell import main as shell


class _Response:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self) -> object:
        return self.payload


class _Session:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return _Response({"nodes": [], "edges": [], "is_truncated": False})


def _client() -> tuple[LightRagHttpClient, _Session]:
    settings = LightRagSettings(
        url="http://127.0.0.1:10261",
        timeout_seconds=10.0,
        connect_timeout_seconds=2.0,
        verify_tls=True,
    )
    client = LightRagHttpClient(settings)
    session = _Session()
    client.session = session  # type: ignore[assignment]
    return client, session


class GraphAdapterTests(unittest.TestCase):
    def test_subgraph_uses_shell_read_only_contract(self) -> None:
        client, session = _client()
        payload = client.graph_subgraph("Entity A", depth=2, max_nodes=60)
        self.assertEqual(payload["nodes"], [])
        call = session.calls[-1]
        self.assertTrue(str(call["url"]).endswith("/graph/subgraph"))
        self.assertEqual(
            call["params"],
            {"label": "Entity A", "depth": 2, "max_nodes": 60},
        )

    def test_popular_and_search_are_bounded(self) -> None:
        client, session = _client()
        client.graph_popular(limit=999)
        self.assertEqual(session.calls[-1]["params"], {"limit": 100})
        client.graph_search(" Entity A ", limit=999)
        self.assertEqual(
            session.calls[-1]["params"],
            {"q": "Entity A", "limit": 50},
        )


class GraphApplicationTests(unittest.TestCase):
    def test_application_exposes_only_read_only_graph_routes(self) -> None:
        paths = {
            route.path
            for route in knowledge_api.graph_router.routes
        }
        self.assertEqual(
            paths,
            {"/api/graph/popular", "/api/graph/search", "/api/graph/global", "/api/graph/subgraph"},
        )

    def test_subgraph_route_trims_label_and_returns_payload(self) -> None:
        gateway = Mock()
        gateway.graph_subgraph.return_value = {"nodes": [{"id": "Entity A"}]}
        with patch.object(
            knowledge_api,
            "LightRagHttpClient",
            return_value=gateway,
        ):
            result = knowledge_api.graph_subgraph(" Entity A ", depth=2, max_nodes=60)
        self.assertEqual(result["nodes"][0]["id"], "Entity A")
        gateway.graph_subgraph.assert_called_once_with(
            "Entity A",
            depth=2,
            max_nodes=60,
        )

    def test_unavailable_graph_maps_to_app_error(self) -> None:
        gateway = Mock()
        gateway.graph_global.side_effect = KnowledgeUnavailable("offline")
        with patch.object(
            knowledge_api,
            "LightRagHttpClient",
            return_value=gateway,
        ):
            with self.assertRaises(AppError) as raised:
                knowledge_api.graph_global(max_nodes=400)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.code, "knowledge_unavailable")


class GraphShellTests(unittest.TestCase):
    def test_label_endpoints_normalize_list_payloads(self) -> None:
        with patch.object(
            shell,
            "_request_json",
            return_value={"data": ["Entity A", "Entity B"]},
        ) as request:
            self.assertEqual(
                shell.graph_popular(16),
                {"items": ["Entity A", "Entity B"]},
            )
            request.assert_called_with(
                "GET",
                "/graph/label/popular",
                params={"limit": 16},
            )

    def test_global_graph_uses_official_full_graph_label(self) -> None:
        with patch.object(
            shell,
            "_request_json",
            return_value={"nodes": [], "edges": [], "is_truncated": False},
        ) as request:
            shell.graph_global(400)
        request.assert_called_once_with(
            "GET",
            "/graphs",
            params={"label": "*", "max_depth": 3, "max_nodes": 400},
        )

    def test_blank_subgraph_label_is_rejected_before_upstream(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            shell.graph_subgraph("   ", depth=2, max_nodes=60)
        self.assertEqual(raised.exception.status_code, 422)

    def test_shell_exposes_no_graph_mutation_routes(self) -> None:
        graph_paths = {
            route.path
            for route in shell.app.routes
            if getattr(route, "path", "").startswith("/graph")
        }
        self.assertEqual(
            graph_paths,
            {"/graph/popular", "/graph/search", "/graph/global", "/graph/subgraph"},
        )


if __name__ == "__main__":
    unittest.main()
