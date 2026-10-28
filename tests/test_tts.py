from __future__ import annotations

import unittest
from unittest.mock import patch

from app.features.tts import service
from app.features.tts.config import TtsSettings


def _device_settings() -> TtsSettings:
    return TtsSettings(
        cloud_host="",
        cloud_model="",
        cloud_voice="",
        cloud_api_key="",
        cloud_timeout=60,
        provider_chain="cloud",
    )


def _cloud_settings(chain: str = "cloud") -> TtsSettings:
    return TtsSettings(
        cloud_host="https://tts.example.com/v1",
        cloud_model="tts-model",
        cloud_voice="alloy",
        cloud_api_key="secret",
        cloud_timeout=60,
        provider_chain=chain,
    )


class TtsTests(unittest.TestCase):
    def test_no_cloud_configuration_defaults_to_device(self) -> None:
        runtime = service.status(_device_settings())
        self.assertFalse(runtime["ready"])
        self.assertEqual(runtime["active_provider"], "device")
        self.assertEqual(runtime["chain_order"], ["device"])
        self.assertTrue(runtime["browser_fallback"])

    def test_complete_cloud_configuration_is_cloud_first(self) -> None:
        runtime = service.status(_cloud_settings())
        self.assertTrue(runtime["ready"])
        self.assertEqual(runtime["active_provider"], "cloud")
        self.assertEqual(runtime["chain_order"], ["cloud", "device"])

    def test_browser_only_chain_disables_cloud(self) -> None:
        runtime = service.status(_cloud_settings("browser"))
        self.assertFalse(runtime["ready"])
        self.assertEqual(runtime["active_provider"], "device")

    def test_speech_url_normalization(self) -> None:
        self.assertEqual(
            service._speech_url("https://api.openai.com/v1"),
            "https://api.openai.com/v1/audio/speech",
        )
        self.assertEqual(
            service._speech_url("https://api.openai.com/v1/audio/speech"),
            "https://api.openai.com/v1/audio/speech",
        )
        self.assertEqual(
            service._speech_url("http://127.0.0.1:9000"),
            "http://127.0.0.1:9000/v1/audio/speech",
        )

    def test_synthesis_posts_expected_cloud_request(self) -> None:
        class Response:
            status_code = 200
            content = b"fake-mp3"
            headers = {"Content-Type": "audio/mpeg"}
            text = ""

        with patch("app.features.tts.service.requests.post", return_value=Response()) as post:
            audio = service.synthesize("你好", settings=_cloud_settings())

        self.assertEqual(audio.content, b"fake-mp3")
        self.assertEqual(audio.media_type, "audio/mpeg")
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["json"]["model"], "tts-model")
        self.assertEqual(kwargs["json"]["input"], "你好")
        self.assertEqual(kwargs["json"]["voice"], "alloy")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret")

    def test_synthesis_without_cloud_raises_explicit_error(self) -> None:
        with self.assertRaises(service.TtsError) as raised:
            service.synthesize("你好", settings=_device_settings())
        self.assertIn("未配置", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
