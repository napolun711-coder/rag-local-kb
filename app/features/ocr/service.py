"""Windows built-in OCR adapter with explicit availability reporting."""

from __future__ import annotations

import base64
import ipaddress
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse

from app.features.ocr.config import OcrSettings, get_ocr_settings
from app.platform.config import get_settings
from app.platform.paths import BASE_DIR

SCRIPT = BASE_DIR / "scripts" / "windows_ocr.ps1"
_STATUS_LOCK = threading.RLock()
_STATUS_CACHE: Optional[Dict[str, Any]] = None
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAusB9Wl2nWQAAAAASUVORK5CYII="
)


def _powershell() -> str:
    return shutil.which("powershell.exe") or ""


def _language_tag(requested: str, available: Iterable[str]) -> str:
    values = [str(item) for item in available if str(item)]
    text = str(requested or "").strip()
    lowered = text.casefold()
    aliases = {
        "ch": "zh-hans",
        "chi_sim": "zh-hans",
        "zh": "zh-hans",
        "zh-cn": "zh-hans",
        "en": "en-us",
    }
    wanted = aliases.get(lowered, lowered)
    for item in values:
        if item.casefold() == wanted:
            return item
    for item in values:
        if item.casefold().startswith(wanted):
            return item
    return ""


def _private_or_local_url(value: str) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.strip().lower()
    if host in {"localhost", "::1"} or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def _http_url(value: str) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _remote_configured(cfg: OcrSettings, provider: str) -> bool:
    if not _http_url(cfg.base_url) or not cfg.model:
        return False
    if provider == "local_model":
        return _private_or_local_url(cfg.base_url)
    return bool(cfg.api_key)


def _resolve_provider(
    cfg: OcrSettings,
    *,
    windows_available: bool,
) -> str:
    if not cfg.configured:
        return "none"
    if cfg.provider != "auto":
        if cfg.provider == "windows":
            return "windows" if windows_available else "none"
        if cfg.provider in {"local_model", "cloud"}:
            return cfg.provider if _remote_configured(cfg, cfg.provider) else "none"
        return "none"
    if _remote_configured(cfg, "cloud"):
        return "cloud"
    if _remote_configured(cfg, "local_model"):
        return "local_model"
    if windows_available:
        return "windows"
    return "none"


def _extract_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("text", "content"):
        value = payload.get(key)
        if isinstance(value, str):
            return value.strip()
    for key in ("data", "result", "output"):
        value = payload.get(key)
        if isinstance(value, dict):
            text = _extract_text(value)
            if text:
                return text
    return ""


def _remote_recognize(
    content: bytes,
    *,
    filename: str,
    cfg: OcrSettings,
    provider: str,
) -> Dict[str, Any]:
    if not _remote_configured(cfg, provider):
        reason = (
            "自定义本地 OCR 必须使用本机或局域网地址，并填写模型名"
            if provider == "local_model"
            else "云端 OCR 必须填写接口地址、模型名和 API Key"
        )
        return {"ok": False, "error": "ocr_not_configured", "message": reason}
    body = json.dumps(
        {
            "model": cfg.model,
            "language": cfg.language,
            "mime_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
            "image_base64": base64.b64encode(content).decode("ascii"),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    request = urllib.request.Request(
        cfg.base_url,
        data=body,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=cfg.timeout_seconds) as response:
            raw = response.read(2 * 1024 * 1024)
            status = response.status
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": "http_error", "message": f"HTTP {exc.code}"}
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"ok": False, "error": "invalid_response", "message": "OCR 返回不是 JSON"}
    text = _extract_text(payload)
    return {
        "ok": 200 <= status < 300,
        "text": text,
        "provider": provider,
        "external": provider == "cloud",
        "language": cfg.language,
        "model": cfg.model,
    }


def _run(args: list[str], timeout: float) -> Dict[str, Any]:
    executable = _powershell()
    if not executable:
        return {
            "ok": False,
            "error": "powershell_unavailable",
            "message": "Windows PowerShell is unavailable.",
        }
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    temp_dir = get_settings().data_dir / "ocr-temp"
    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
        handle, raw_output = tempfile.mkstemp(prefix="ocr-result-", suffix=".json", dir=temp_dir)
        os.close(handle)
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}
    output_path = Path(raw_output)
    try:
        try:
            completed = subprocess.run(
                [
                    executable,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    *args,
                    "-OutputPath",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                creationflags=flags,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout", "message": "Local OCR timed out."}
        except OSError as exc:
            return {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        try:
            output = output_path.read_text(encoding="utf-8")
            payload = json.loads(output) if output else {}
        except (OSError, UnicodeDecodeError, ValueError):
            payload = {
                "ok": False,
                "error": "invalid_ocr_output",
                "message": (completed.stderr or completed.stdout or "Invalid OCR output")[:300],
            }
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "error": "invalid_ocr_output",
                "message": "OCR output was not a JSON object.",
            }
        return payload
    finally:
        try:
            output_path.unlink()
        except OSError:
            pass


def probe(*, force: bool = False) -> Dict[str, Any]:
    global _STATUS_CACHE
    with _STATUS_LOCK:
        if _STATUS_CACHE is not None and not force:
            return dict(_STATUS_CACHE)
        if os.name != "nt":
            result = {
                "available": False,
                "languages": [],
                "error": "unsupported_platform",
                "detail": "Windows built-in OCR is available on Windows only.",
            }
        elif not SCRIPT.is_file():
            result = {
                "available": False,
                "languages": [],
                "error": "helper_missing",
                "detail": "The Windows OCR helper script is missing.",
            }
        else:
            payload = _run(
                ["-File", str(SCRIPT), "-ListLanguages"],
                timeout=10,
            )
            languages = payload.get("languages") if isinstance(payload.get("languages"), list) else []
            result = {
                "available": bool(payload.get("ok") and languages),
                "languages": [str(item) for item in languages],
                "error": str(payload.get("error") or ""),
                "detail": str(payload.get("message") or ""),
            }
        _STATUS_CACHE = dict(result)
        return result


def status(settings: Optional[OcrSettings] = None) -> Dict[str, Any]:
    cfg = settings or get_ocr_settings()
    probed = probe()
    language = _language_tag(cfg.language, probed.get("languages") or [])
    windows_available = bool(probed.get("available") and language)
    cloud_configured = _remote_configured(cfg, "cloud")
    local_model_configured = _remote_configured(cfg, "local_model")
    provider = _resolve_provider(cfg, windows_available=windows_available)
    engine_available = provider != "none"
    available = bool(cfg.configured and engine_available)
    reason = ""
    if cfg.mode == "off":
        reason = "OCR 当前关闭。"
    elif provider == "none" and cfg.provider == "cloud":
        reason = "云端 OCR 未配置完整。"
    elif provider == "none" and cfg.provider == "local_model":
        reason = "自定义本地 OCR 未配置完整，或地址不是本机/局域网。"
    elif provider == "none" and cfg.provider == "windows" and not probed.get("available"):
        reason = probed.get("detail") or "Windows 本机 OCR 不可用。"
    elif provider == "none" and cfg.provider == "windows" and not language:
        reason = "请求的 OCR 语言包未安装。"
    elif provider == "none":
        reason = "没有可用的 OCR 提供方。"
    return {
        "enabled": available,
        "configured": cfg.configured,
        "engine_available": engine_available,
        "mode": cfg.mode,
        "provider": provider,
        "configured_provider": cfg.provider,
        "external": provider == "cloud",
        "windows_available": windows_available,
        "image_ocr": cfg.image_ocr,
        "lang": cfg.language,
        "language_tag": language,
        "available_languages": probed.get("languages") or [],
        "max_file_mb": cfg.max_file_mb,
        "timeout_seconds": cfg.timeout_seconds,
        "base_url": cfg.base_url,
        "model": cfg.model,
        "token_set": bool(cfg.api_key),
        "cloud_configured": cloud_configured,
        "local_model_configured": local_model_configured,
        "reason": reason,
        "engine": (
            "windows-media-ocr"
            if provider == "windows"
            else ("custom-local-ocr" if provider == "local_model" else ("cloud-ocr" if provider == "cloud" else ""))
        ),
    }


def recognize_bytes(
    content: bytes,
    *,
    filename: str,
    settings: Optional[OcrSettings] = None,
) -> Dict[str, Any]:
    cfg = settings or get_ocr_settings()
    if not cfg.configured:
        return {"ok": False, "error": "ocr_disabled", "message": "OCR is disabled."}
    if len(content) > cfg.max_file_mb * 1024 * 1024:
        return {"ok": False, "error": "file_too_large", "message": "Image exceeds OCR size limit."}
    available = status(cfg)
    if not available["enabled"]:
        return {
            "ok": False,
            "error": "ocr_unavailable",
            "message": available["reason"],
        }
    provider = str(available.get("provider") or "")
    if provider in {"local_model", "cloud"}:
        return _remote_recognize(
            content,
            filename=filename,
            cfg=cfg,
            provider=provider,
        )
    suffix = Path(filename).suffix or ".png"
    temp_dir = get_settings().data_dir / "ocr-temp"
    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
        handle, raw_path = tempfile.mkstemp(prefix="ocr-", suffix=suffix, dir=temp_dir)
        os.close(handle)
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}
    path = Path(raw_path)
    try:
        try:
            path.write_bytes(content)
        except OSError as exc:
            return {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        payload = _run(
            [
                "-File",
                str(SCRIPT),
                "-Path",
                str(path),
                "-Language",
                available["language_tag"],
            ],
            timeout=cfg.timeout_seconds,
        )
        payload["language_tag"] = available["language_tag"]
        return payload
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def recognize_assets(
    assets: Iterable[Any],
    *,
    settings: Optional[OcrSettings] = None,
) -> Dict[str, Any]:
    cfg = settings or get_ocr_settings()
    runtime = status(cfg)
    recognized = 0
    failed = 0
    sections: list[str] = []
    errors: list[str] = []
    assets_total = 0
    for asset in assets:
        data = getattr(asset, "data", b"") or b""
        if not data:
            continue
        assets_total += 1
        name = str(getattr(asset, "name", "") or "image")
        source = str(getattr(asset, "source", "") or name)
        try:
            result = recognize_bytes(data, filename=name, settings=cfg)
        except Exception as exc:
            result = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        text = str(result.get("text") or "").strip()
        if result.get("ok") and text:
            recognized += 1
            sections.append(f"## OCR：{source}\n\n{text}")
        else:
            failed += 1
            message = str(result.get("message") or result.get("error") or "OCR failed")
            errors.append(f"{source}: {message[:200]}")
    return {
        "used": recognized > 0,
        "provider": str(runtime.get("provider") or ""),
        "engine": str(runtime.get("engine") or ""),
        "external": bool(runtime.get("external")),
        "language": str(runtime.get("language_tag") or cfg.language),
        "assets_total": assets_total,
        "recognized": recognized,
        "failed": failed,
        "chars": sum(len(item) for item in sections),
        "text": "\n\n".join(sections),
        "errors": errors[:8],
    }


def test_connection(settings: Optional[OcrSettings] = None) -> Dict[str, Any]:
    cfg = settings or get_ocr_settings()
    reset_status_cache()
    runtime = status(cfg)
    provider = str(runtime.get("provider") or "")
    if provider == "none":
        return {
            "ok": False,
            "status": runtime,
            "message": runtime.get("reason") or "OCR 不可用",
        }
    if provider == "windows":
        return {
            "ok": bool(runtime.get("engine_available")),
            "status": runtime,
            "message": "Windows 本机 OCR 可用" if runtime.get("engine_available") else runtime.get("reason"),
        }
    result = _remote_recognize(
        base64.b64decode(TINY_PNG_B64),
        filename="ocr-test.png",
        cfg=cfg,
        provider=provider,
    )
    return {
        "ok": bool(result.get("ok")),
        "status": runtime,
        "message": (
            f"{provider} OCR 接口可用"
            if result.get("ok")
            else result.get("message") or "OCR 接口不可用"
        ),
    }


def reset_status_cache() -> None:
    global _STATUS_CACHE
    with _STATUS_LOCK:
        _STATUS_CACHE = None


__all__ = [
    "probe",
    "recognize_assets",
    "recognize_bytes",
    "reset_status_cache",
    "status",
    "test_connection",
]
