"""Windows built-in OCR adapter with explicit availability reporting."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from app.features.ocr.config import OcrSettings, get_ocr_settings
from app.platform.config import get_settings
from app.platform.paths import BASE_DIR

SCRIPT = BASE_DIR / "scripts" / "windows_ocr.ps1"
_STATUS_LOCK = threading.RLock()
_STATUS_CACHE: Optional[Dict[str, Any]] = None


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
    engine_available = bool(probed.get("available") and language)
    available = bool(cfg.enabled and engine_available)
    reason = ""
    if cfg.provider != "local":
        reason = "本地 OCR 提供方未选择。"
    elif cfg.mode == "off":
        reason = "OCR 当前关闭。"
    elif not probed.get("available"):
        reason = probed.get("detail") or "Windows 本机 OCR 不可用。"
    elif not language:
        reason = "请求的 OCR 语言包未安装。"
    return {
        "enabled": available,
        "configured": cfg.enabled,
        "engine_available": engine_available,
        "mode": cfg.mode,
        "provider": cfg.provider,
        "external": False,
        "image_ocr": cfg.image_ocr,
        "lang": cfg.language,
        "language_tag": language,
        "available_languages": probed.get("languages") or [],
        "max_file_mb": cfg.max_file_mb,
        "timeout_seconds": cfg.timeout_seconds,
        "reason": reason,
        "engine": "windows-media-ocr",
    }


def recognize_bytes(
    content: bytes,
    *,
    filename: str,
    settings: Optional[OcrSettings] = None,
) -> Dict[str, Any]:
    cfg = settings or get_ocr_settings()
    if not cfg.enabled:
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
        "provider": "local",
        "engine": "windows-media-ocr",
        "language": str(status(cfg).get("language_tag") or cfg.language),
        "assets_total": assets_total,
        "recognized": recognized,
        "failed": failed,
        "chars": sum(len(item) for item in sections),
        "text": "\n\n".join(sections),
        "errors": errors[:8],
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
]
