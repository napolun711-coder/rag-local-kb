from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.features.ingest import parse as parse_service
from app.features.ingest import service as ingest_service
from app.features.ocr.config import OcrSettings
from app.features.ocr import service as ocr_service


def _ocr_settings() -> OcrSettings:
    return OcrSettings(
        mode="auto",
        provider="windows",
        image_ocr=True,
        language="zh-Hans-CN",
        max_file_mb=50,
        timeout_seconds=30,
    )


class OcrTests(unittest.TestCase):
    def test_language_alias_resolves_available_pack(self) -> None:
        self.assertEqual(
            ocr_service._language_tag("ch", ["zh-Hans-CN"]),
            "zh-Hans-CN",
        )
        self.assertEqual(
            ocr_service._language_tag("en", ["zh-Hans-CN", "en-US"]),
            "en-US",
        )

    def test_image_parse_keeps_original_bytes_for_local_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scan.png"
            path.write_bytes(b"fake-png")
            result = parse_service.parse_file(path)

        self.assertTrue(result.ok)
        self.assertEqual(result.route, "image")
        self.assertEqual(len(result.assets), 1)
        self.assertEqual(result.assets[0].data, b"fake-png")

    def test_recognize_assets_merges_nonempty_text_only(self) -> None:
        settings = _ocr_settings()
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch(
                    "app.features.ocr.service.get_settings",
                    return_value=SimpleNamespace(data_dir=Path(temp)),
                ),
                patch(
                    "app.features.ocr.service.status",
                    return_value={"enabled": True, "language_tag": "zh-Hans-CN"},
                ),
                patch(
                    "app.features.ocr.service._run",
                    return_value={"ok": True, "text": "识别结果"},
                ),
            ):
                result = ocr_service.recognize_assets(
                    [SimpleNamespace(name="a.png", source="第 1 页", data=b"image")],
                    settings=settings,
                )

        self.assertTrue(result["used"])
        self.assertEqual(result["recognized"], 1)
        self.assertIn("识别结果", result["text"])

    def test_apply_ocr_appends_text_to_parsed_content(self) -> None:
        parsed = parse_service.ParseResult(
            ok=True,
            route="image",
            pages=1,
            images=1,
            text="",
            assets=[
                parse_service.Asset(
                    name="scan.png",
                    source="原图",
                    data=b"image",
                )
            ],
        )
        settings = SimpleNamespace(parse_max_chars=10000)
        with (
            patch("app.features.ingest.service.get_ocr_settings", return_value=_ocr_settings()),
            patch(
                "app.features.ingest.service.ocr_service.recognize_assets",
                return_value={
                    "used": True,
                    "assets_total": 1,
                    "recognized": 1,
                    "failed": 0,
                    "errors": [],
                    "text": "## OCR：原图\n\n识别后的正文",
                },
            ),
        ):
            meta = ingest_service._apply_ocr(parsed, settings)

        self.assertTrue(meta["used"])
        self.assertIn("识别后的正文", parsed.text)

    def test_disabled_ocr_returns_explicit_error(self) -> None:
        disabled = OcrSettings(
            mode="off",
            provider="windows",
            image_ocr=True,
            language="zh-Hans-CN",
            max_file_mb=50,
            timeout_seconds=30,
        )
        result = ocr_service.recognize_bytes(
            b"image",
            filename="image.png",
            settings=disabled,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "ocr_disabled")

    def test_status_does_not_claim_ocr_when_engine_is_unavailable(self) -> None:
        job = {
            "state": "needs_ocr",
            "image_total": 1,
            "ocr": {},
            "knowledge": {"state": "not_submitted"},
        }
        with (
            patch(
                "app.features.ingest.service.get_ocr_settings",
                return_value=_ocr_settings(),
            ),
            patch(
                "app.features.ingest.service.ocr_service.status",
                return_value={
                    "enabled": False,
                    "reason": "language pack missing",
                },
            ),
        ):
            payload = ingest_service._public_status(job)

        self.assertFalse(payload["ocr"]["enabled"])
        self.assertFalse(payload["can"]["ocr"])

    def test_auto_provider_priority(self) -> None:
        cloud = OcrSettings(
            mode="auto",
            provider="auto",
            image_ocr=True,
            language="zh-Hans-CN",
            max_file_mb=50,
            timeout_seconds=30,
            base_url="https://ocr.example.com/v1/ocr",
            model="cloud-model",
            api_key="secret",
        )
        local_model = OcrSettings(
            mode="auto",
            provider="auto",
            image_ocr=True,
            language="zh-Hans-CN",
            max_file_mb=50,
            timeout_seconds=30,
            base_url="http://127.0.0.1:9001/ocr",
            model="local-model",
        )
        self.assertEqual(
            ocr_service._resolve_provider(cloud, windows_available=True),
            "cloud",
        )
        self.assertEqual(
            ocr_service._resolve_provider(local_model, windows_available=True),
            "local_model",
        )
        self.assertEqual(
            ocr_service._resolve_provider(
                replace(_ocr_settings(), provider="auto"),
                windows_available=True,
            ),
            "windows",
        )

    def test_remote_ocr_contract_returns_text(self) -> None:
        cfg = OcrSettings(
            mode="auto",
            provider="local_model",
            image_ocr=True,
            language="zh-Hans-CN",
            max_file_mb=50,
            timeout_seconds=30,
            base_url="http://127.0.0.1:9001/ocr",
            model="local-model",
        )

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b'{"text":"remote result"}'

        with patch("app.features.ocr.service.urllib.request.urlopen", return_value=Response()):
            result = ocr_service._remote_recognize(
                b"image",
                filename="test.png",
                cfg=cfg,
                provider="local_model",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "remote result")

    def test_cloud_provider_is_marked_external(self) -> None:
        cfg = OcrSettings(
            mode="auto",
            provider="cloud",
            image_ocr=True,
            language="zh-Hans-CN",
            max_file_mb=50,
            timeout_seconds=30,
            base_url="https://ocr.example.com/v1/ocr",
            model="cloud-model",
            api_key="secret",
        )
        with patch(
            "app.features.ocr.service.probe",
            return_value={"available": True, "languages": ["zh-Hans-CN"]},
        ):
            result = ocr_service.status(cfg)

        self.assertTrue(result["enabled"])
        self.assertEqual(result["provider"], "cloud")
        self.assertTrue(result["external"])

    def test_local_model_rejects_public_endpoint(self) -> None:
        cfg = OcrSettings(
            mode="auto",
            provider="local_model",
            image_ocr=True,
            language="zh-Hans-CN",
            max_file_mb=50,
            timeout_seconds=30,
            base_url="https://public.example.com/ocr",
            model="local-model",
        )
        self.assertFalse(ocr_service._remote_configured(cfg, "local_model"))


if __name__ == "__main__":
    unittest.main()
