from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional, Tuple
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

_TEMP_ROOT = Path(tempfile.mkdtemp(prefix="ragkb-access-"))
_DATA = _TEMP_ROOT / "data"
_KNOWLEDGE = _TEMP_ROOT / "knowledge"
_LOGS = _TEMP_ROOT / "logs"
_UPLOADS = _DATA / "uploads"
_PARSED = _DATA / "parsed"
_APP_ENV = _TEMP_ROOT / "app.env"
_ENGINE_ENV = _TEMP_ROOT / "engine.env"
_APP_ENV.write_text("", encoding="utf-8")
_ENGINE_ENV.write_text("", encoding="utf-8")
for path in (_DATA, _KNOWLEDGE, _LOGS, _UPLOADS, _PARSED):
    path.mkdir(parents=True, exist_ok=True)
os.environ.update(
    {
        "RAGKB_DATA_DIR": str(_DATA),
        "RAGKB_KNOWLEDGE_DIR": str(_KNOWLEDGE),
        "RAGKB_LOG_DIR": str(_LOGS),
        "RAGKB_RUNTIME_DIR": str(_LOGS / "runtime"),
        "RAGKB_ENV_FILE": str(_APP_ENV),
        "RAGKB_SETTINGS_APP_ENV_FILE": str(_APP_ENV),
        "RAGKB_SETTINGS_ENGINE_ENV_FILE": str(_ENGINE_ENV),
        "RAGKB_INGEST_UPLOAD_DIR": str(_UPLOADS),
        "RAGKB_INGEST_PARSED_DIR": str(_PARSED),
        "RAGKB_MEMORY_DB_PATH": str(_DATA / "memory" / "ragkb.db"),
        "RAGKB_LIGHTRAG_URL": "http://127.0.0.1:9",
        "RAGKB_OCR_MODE": "off",
    }
)

from app.platform.config import reload_settings  # noqa: E402

reload_settings()

from app.features.access import service as access_service  # noqa: E402
from app.features.access.dependencies import require_authorized, require_local  # noqa: E402
from app.features.documents import api as documents_api  # noqa: E402
from app.features.ingest import api as ingest_api  # noqa: E402
from app.features.ingest import limits  # noqa: E402
from app.main import app as application  # noqa: E402


def _scope(
    method: str,
    path: str,
    *,
    host: str,
    query: str = "",
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    raw_headers = {"host": "testserver"}
    if headers:
        raw_headers.update(headers)
    if cookies:
        raw_headers["cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items())
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query.encode("utf-8"),
        "headers": [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in raw_headers.items()
        ],
        "client": (host, 12345),
        "server": ("testserver", 80),
        "root_path": "",
    }


async def _call(
    method: str,
    path: str,
    *,
    host: str,
    query: str = "",
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    body: bytes = b"",
) -> Tuple[int, Dict[str, str], bytes]:
    all_headers = dict(headers or {})
    if body:
        all_headers.setdefault("content-length", str(len(body)))
    scope = _scope(
        method,
        path,
        host=host,
        query=query,
        headers=all_headers,
        cookies=cookies,
    )
    messages: list[Dict[str, Any]] = []

    async def receive() -> Dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Dict[str, Any]) -> None:
        messages.append(message)

    await application(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in start.get("headers", [])
    }
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return int(start["status"]), response_headers, response_body


def _request(
    method: str,
    path: str,
    *,
    host: str,
    query: str = "",
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Dict[str, str], bytes]:
    body = b""
    all_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        all_headers.setdefault("content-type", "application/json")
    return asyncio.run(
        _call(
            method,
            path,
            host=host,
            query=query,
            headers=all_headers,
            cookies=cookies,
            body=body,
        )
    )


def _multipart_file(filename: str, content: bytes) -> Tuple[bytes, str]:
    boundary = "----ragkb-boundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


class AccessBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        access_service._DEVICES = None
        access_service._PENDING.clear()
        limits.reset()
        self.csrf = "csrf-test-token"
        request = access_service.create_request("boundary-test", "192.168.1.50")
        access_service.approve_request(request["request_id"])
        claimed = access_service.claim_request(
            request["request_id"],
            request["request_secret"],
            "192.168.1.50",
        )
        assert claimed is not None
        self.access_cookie = access_service.cookie_value(
            claimed["device_id"],
            claimed["device_token"],
        )
        self.lan_cookies = {
            access_service.COOKIE_NAME: self.access_cookie,
            access_service.CSRF_COOKIE_NAME: self.csrf,
        }
        self.lan_headers = {access_service.CSRF_HEADER_NAME: self.csrf}

    def tearDown(self) -> None:
        access_service._DEVICES = None
        access_service._PENDING.clear()
        limits.reset()

    def test_unpaired_lan_is_denied(self) -> None:
        status, _, body = _request("GET", "/api/settings", host="192.168.1.50")
        self.assertEqual(status, 401)
        self.assertIn("device_auth_required", body.decode("utf-8"))

        status, headers, _ = _request("GET", "/web", host="192.168.1.50")
        self.assertEqual(status, 307)
        self.assertEqual(headers.get("location"), "/pair")

    def test_paired_lan_can_read_settings_and_memory_status(self) -> None:
        status, _, _ = _request(
            "GET", "/api/settings", host="192.168.1.50", cookies=self.lan_cookies
        )
        self.assertEqual(status, 200)
        status, _, _ = _request(
            "GET", "/api/memory/status", host="192.168.1.50", cookies=self.lan_cookies
        )
        self.assertEqual(status, 200)
        status, _, _ = _request(
            "GET", "/api/tts/status", host="192.168.1.50", cookies=self.lan_cookies
        )
        self.assertEqual(status, 200)

    def test_paired_lan_can_read_documents(self) -> None:
        class _Result:
            items = []
            local_only = []
            upstream_ok = True
            upstream_message = ""

            def to_dict(self):
                return {}

        class _Catalog:
            def list_documents(self, **kwargs):
                return _Result()

        with patch.object(documents_api, "_service", return_value=_Catalog()):
            status, _, _ = _request(
                "GET",
                "/api/files/list",
                host="192.168.1.50",
                cookies=self.lan_cookies,
            )
            self.assertEqual(status, 200)
        status, _, _ = _request(
            "GET", "/api/topics", host="192.168.1.50", cookies=self.lan_cookies
        )
        self.assertEqual(status, 200)

    def test_lan_writes_require_csrf(self) -> None:
        status, _, body = _request(
            "POST",
            "/api/app/keepalive",
            host="192.168.1.50",
            cookies=self.lan_cookies,
        )
        self.assertEqual(status, 403)
        self.assertIn("csrf_required", body.decode("utf-8"))

        status, _, _ = _request(
            "POST",
            "/api/app/keepalive",
            host="192.168.1.50",
            cookies=self.lan_cookies,
            headers=self.lan_headers,
        )
        self.assertEqual(status, 200)

    def test_lan_admin_and_settings_writes_are_local_only(self) -> None:
        status, _, _ = _request(
            "GET",
            "/api/memory/admin/status",
            host="192.168.1.50",
            cookies=self.lan_cookies,
        )
        self.assertEqual(status, 403)
        for path, payload in (
            ("/api/settings", {}),
            ("/ingest/queue", {"paused": False}),
            ("/ingest/session", {"allow_all": False}),
            ("/api/settings/test", None),
            ("/api/knowledge/index-compatibility/accept", {}),
            ("/api/topics", {"name": "local-only"}),
            ("/api/memory/items/nope/confirm", None),
            ("/api/memory/import", {}),
            ("/api/memory/clear", {"confirmation": "CLEAR ALL"}),
            ("/api/tts/test", None),
            ("/api/files/delete", {"items": [], "confirmation": "yes"}),
        ):
            status, _, _ = _request(
                "POST",
                path,
                host="192.168.1.50",
                cookies=self.lan_cookies,
                headers=self.lan_headers,
                payload=payload,
            )
            self.assertEqual(status, 403, path)

    def test_local_client_keeps_full_access(self) -> None:
        status, _, _ = _request("GET", "/api/settings", host="127.0.0.1")
        self.assertEqual(status, 200)
        status, _, _ = _request("POST", "/api/app/keepalive", host="127.0.0.1")
        self.assertEqual(status, 200)
        status, _, _ = _request("GET", "/api/memory/admin/status", host="127.0.0.1")
        self.assertEqual(status, 200)

    def test_lan_upload_quota_is_enforced(self) -> None:
        body, content_type = _multipart_file("alpha.txt", b"hello")
        settings = SimpleNamespace(
            max_upload_size=1024,
            lan_upload_max_files=1,
            lan_upload_max_bytes=1024,
            lan_upload_window_seconds=60,
        )
        accepted = {
            "file_id": "file-1",
            "state": "queued",
            "batch_id": "",
            "duplicate": False,
        }
        headers = {
            **self.lan_headers,
            "content-type": content_type,
        }
        with patch.object(ingest_api, "get_ingest_settings", return_value=settings), patch.object(
            ingest_api.service, "process_upload", return_value=accepted
        ):
            status, _, _ = asyncio.run(
                _call(
                    "POST",
                    "/upload",
                    host="192.168.1.50",
                    cookies=self.lan_cookies,
                    headers=headers,
                    body=body,
                )
            )
            self.assertEqual(status, 202)
            status, _, response = asyncio.run(
                _call(
                    "POST",
                    "/upload",
                    host="192.168.1.50",
                    cookies=self.lan_cookies,
                    headers=headers,
                    body=body,
                )
            )
            self.assertEqual(status, 429)
            self.assertIn("upload_quota_exceeded", response.decode("utf-8"))

    def test_authorized_status_issues_csrf_cookie(self) -> None:
        status, headers, _ = _request(
            "GET",
            "/api/app/access/status",
            host="192.168.1.50",
            cookies={access_service.COOKIE_NAME: self.access_cookie},
        )
        self.assertEqual(status, 200)
        set_cookie = headers.get("set-cookie", "")
        self.assertIn(access_service.CSRF_COOKIE_NAME, set_cookie)

    def test_dependency_helpers(self) -> None:
        local = Request(_scope("GET", "/api/settings", host="127.0.0.1"))
        lan = Request(_scope("GET", "/api/settings", host="192.168.1.50"))
        require_local(local)
        require_authorized(local)
        with self.assertRaises(HTTPException):
            require_local(lan)
        with self.assertRaises(HTTPException):
            require_authorized(lan)

    def test_csrf_validation(self) -> None:
        local_post = Request(_scope("POST", "/x", host="127.0.0.1"))
        self.assertTrue(access_service.csrf_valid(local_post))

        safe_lan_get = Request(_scope("GET", "/x", host="192.168.1.50"))
        self.assertTrue(access_service.csrf_valid(safe_lan_get))

        missing = Request(_scope("POST", "/x", host="192.168.1.50"))
        self.assertFalse(access_service.csrf_valid(missing))

        valid = Request(
            _scope(
                "POST",
                "/x",
                host="192.168.1.50",
                cookies={access_service.CSRF_COOKIE_NAME: "abc"},
                headers={access_service.CSRF_HEADER_NAME: "abc"},
            )
        )
        self.assertTrue(access_service.csrf_valid(valid))

        invalid = Request(
            _scope(
                "POST",
                "/x",
                host="192.168.1.50",
                cookies={access_service.CSRF_COOKIE_NAME: "abc"},
                headers={access_service.CSRF_HEADER_NAME: "other"},
            )
        )
        self.assertFalse(access_service.csrf_valid(invalid))


class UploadLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        limits.reset()

    def tearDown(self) -> None:
        limits.reset()

    def test_file_and_byte_quota(self) -> None:
        allowed, _ = limits.reserve("device:a", 10, max_files=2, max_bytes=20, window_seconds=60)
        self.assertTrue(allowed)
        allowed, _ = limits.reserve("device:a", 10, max_files=2, max_bytes=20, window_seconds=60)
        self.assertTrue(allowed)
        allowed, retry = limits.reserve(
            "device:a", 1, max_files=2, max_bytes=20, window_seconds=60
        )
        self.assertFalse(allowed)
        self.assertGreater(retry, 0)

        limits.reset()
        allowed, _ = limits.reserve("device:b", 21, max_files=10, max_bytes=20, window_seconds=60)
        self.assertFalse(allowed)

    def test_quota_is_per_key(self) -> None:
        self.assertTrue(
            limits.reserve("device:a", 10, max_files=1, max_bytes=100, window_seconds=60)[0]
        )
        self.assertTrue(
            limits.reserve("device:b", 10, max_files=1, max_bytes=100, window_seconds=60)[0]
        )


if __name__ == "__main__":
    unittest.main()
