"""OpenAI-compatible cloud TTS proxy with device fallback metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

from app.features.tts.config import TtsSettings, get_tts_settings

MAX_TEXT_CHARS = 1000
MAX_AUDIO_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class TtsAudio:
    content: bytes
    media_type: str
    provider: str


class TtsError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


def _speech_url(host: str) -> str:
    base = str(host or "").rstrip("/")
    path = urlparse(base).path.rstrip("/")
    if path.endswith("/audio/speech"):
        return base
    if path.endswith("/v1"):
        return base + "/audio/speech"
    return base + "/v1/audio/speech"


def status(settings: Optional[TtsSettings] = None) -> Dict[str, Any]:
    cfg = settings or get_tts_settings()
    cloud_ready = cfg.cloud_enabled
    reason = ""
    if not cfg.cloud_configured:
        reason = "云端 TTS 未配置完整，使用本设备语音"
    elif not cloud_ready:
        reason = "当前朗读方式为仅设备语音"
    chain = (
        [
            {"provider": "cloud", "ready": True, "label": "云端 TTS"},
            {"provider": "device", "ready": True, "label": "本设备语音"},
        ]
        if cloud_ready
        else [{"provider": "device", "ready": True, "label": "本设备语音"}]
    )
    return {
        "ready": cloud_ready,
        "engine": "openai-compatible" if cloud_ready else "",
        "reason": reason,
        "hint": "",
        "chain": chain,
        "active_provider": "cloud" if cloud_ready else "device",
        "chain_order": [item["provider"] for item in chain],
        "browser_fallback": True,
        "max_chars": 800,
        "external": cloud_ready,
        "configured": cfg.cloud_configured,
        "model": cfg.cloud_model,
        "voice": cfg.cloud_voice,
    }


def synthesize(
    text: str,
    *,
    settings: Optional[TtsSettings] = None,
) -> TtsAudio:
    cfg = settings or get_tts_settings()
    normalized = str(text or "").strip()
    if not normalized:
        raise TtsError("朗读文本不能为空", status_code=400)
    if len(normalized) > MAX_TEXT_CHARS:
        raise TtsError(f"朗读文本不能超过 {MAX_TEXT_CHARS} 字", status_code=413)
    if not cfg.cloud_enabled:
        reason = (
            "云端 TTS 未配置完整"
            if not cfg.cloud_configured
            else "当前朗读方式未启用云端 TTS"
        )
        raise TtsError(reason)
    body: Dict[str, Any] = {
        "model": cfg.cloud_model,
        "input": normalized,
        "response_format": "mp3",
    }
    if cfg.cloud_voice:
        body["voice"] = cfg.cloud_voice
    try:
        response = requests.post(
            _speech_url(cfg.cloud_host),
            headers={
                "Authorization": f"Bearer {cfg.cloud_api_key}",
                "Accept": "audio/*",
            },
            json=body,
            timeout=cfg.cloud_timeout,
        )
    except requests.RequestException as exc:
        raise TtsError(f"云端 TTS 不可达：{type(exc).__name__}") from exc
    if response.status_code >= 400:
        detail = response.text[:300]
        raise TtsError(f"云端 TTS 返回 HTTP {response.status_code}：{detail}", status_code=502)
    content = response.content
    if not content:
        raise TtsError("云端 TTS 返回空音频", status_code=502)
    if len(content) > MAX_AUDIO_BYTES:
        raise TtsError("云端 TTS 音频超过大小限制", status_code=502)
    media_type = str(response.headers.get("Content-Type") or "audio/mpeg").split(";", 1)[0]
    if not media_type.startswith("audio/"):
        media_type = "audio/mpeg"
    return TtsAudio(content=content, media_type=media_type, provider="cloud")


def test_connection(settings: Optional[TtsSettings] = None) -> Dict[str, Any]:
    cfg = settings or get_tts_settings()
    current = status(cfg)
    if not cfg.cloud_configured:
        return {"ok": False, "status": current, "message": "云端 TTS 未配置完整"}
    if not cfg.cloud_enabled:
        return {"ok": False, "status": current, "message": "当前朗读方式为仅设备语音"}
    try:
        audio = synthesize("语音测试", settings=cfg)
    except TtsError as exc:
        return {"ok": False, "status": current, "message": str(exc)}
    return {
        "ok": True,
        "status": current,
        "message": f"云端 TTS 可用，返回 {len(audio.content)} 字节音频",
        "media_type": audio.media_type,
    }


__all__ = [
    "MAX_TEXT_CHARS",
    "TtsAudio",
    "TtsError",
    "status",
    "synthesize",
    "test_connection",
]
