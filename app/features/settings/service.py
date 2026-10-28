"""Read, validate, persist, and test runtime configuration."""

from __future__ import annotations

import json
import os
import shutil
import ipaddress
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

from app.adapters.lightrag.config import get_lightrag_settings
from app.features.inbox.config import get_inbox_settings
from app.features.ingest.config import get_ingest_settings
from app.features.knowledge.service import get_knowledge_gateway
from app.features.memory.config import get_memory_settings
from app.features.ocr.config import get_ocr_settings
from app.features.ocr import service as ocr_service
from app.features.tts.config import get_tts_settings
from app.features.tts import service as tts_service
from app.features.settings.envfile import EnvFileError, read_env_file, update_env_file
from app.features.settings.schema import (
    FIELD_BY_PAIR,
    FIELDS,
    FieldSpec,
    UNSUPPORTED,
    UNSUPPORTED_SECRETS,
    group_defaults,
)
from app.platform.config import ENV_FILE, get_settings
from app.platform.paths import BASE_DIR

ROOT = BASE_DIR
ENGINE_ENV_FILE = ROOT / "services" / "lightrag_engine" / ".env"
DEFAULT_BACKUP_DIR = ROOT / "config" / "settings-backups"
SERVICE_NAMES = ("engine", "shell", "app")


def _runtime_path(name: str, default: Path) -> Path:
    override = os.environ.get(name, "").strip()
    if not override:
        return default
    path = Path(override).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


LOG_ROOT = _runtime_path("RAGKB_LOG_DIR", ROOT / "logs")
RUNTIME_DIR = _runtime_path("RAGKB_RUNTIME_DIR", LOG_ROOT / "runtime")
RUNTIME_STATE_FILE = _runtime_path(
    "RAGKB_RUNTIME_STATE_FILE", RUNTIME_DIR / "services.json"
)


class SettingsError(RuntimeError):
    status_code = 400

    def __init__(self, messages: Sequence[str]) -> None:
        self.messages = [str(item) for item in messages if str(item).strip()]
        super().__init__("; ".join(self.messages) or "Invalid settings")


def app_env_path() -> Path:
    override = os.environ.get("RAGKB_SETTINGS_APP_ENV_FILE", "").strip()
    return Path(override).resolve() if override else ENV_FILE


def engine_env_path() -> Path:
    override = os.environ.get("RAGKB_SETTINGS_ENGINE_ENV_FILE", "").strip()
    return Path(override).resolve() if override else ENGINE_ENV_FILE


def backup_dir() -> Path:
    override = os.environ.get("RAGKB_SETTINGS_BACKUP_DIR", "").strip()
    return Path(override).resolve() if override else DEFAULT_BACKUP_DIR


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mtime_iso(path: Path) -> str:
    try:
        value = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return ""
    return value.isoformat()


def _mask_secret(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    return "••••" + text[-4:] if len(text) > 4 else "••••"


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


def _rerank_binding_for(mode: str, provider: str) -> str:
    normalized = str(mode or "off").lower()
    if normalized == "off":
        return "null"
    if normalized == "local":
        return "cohere"
    return str(provider or "cohere").lower()


def _engine_defaults() -> Dict[str, str]:
    return {
        "WORKING_DIR": str(ROOT / "data" / "lightrag" / "storage"),
        "INPUT_DIR": str(ROOT / "data" / "lightrag" / "input"),
        "LIGHTRAG_VECTOR_STORAGE": "NanoVectorDBStorage",
        "LLM_BINDING": "openai",
        "EMBEDDING_BINDING": "ollama",
        "EMBEDDING_MODEL": "bge-m3:latest",
        "EMBEDDING_BINDING_HOST": "http://127.0.0.1:11434",
        "EMBEDDING_DIM": "1024",
        "EMBEDDING_SEND_DIM": "false",
        "RERANK_BINDING": "null",
    }


def _app_defaults() -> Dict[str, str]:
    app = get_settings()
    ingest = get_ingest_settings()
    memory = get_memory_settings()
    return {
        "RAGKB_PORT": str(app.port),
        "RAGKB_PORT_SCAN_LIMIT": str(app.port_scan_limit),
        "RAGKB_LIGHTRAG_SHELL_PORT": "10261",
        "RAGKB_LIGHTRAG_ENGINE_PORT": "10262",
        "RAGKB_LOG_LEVEL": app.log_level,
        "RAGKB_INGEST_MAX_UPLOAD_SIZE": str(ingest.max_upload_size),
        "RAGKB_INGEST_ALLOWED_EXTENSIONS": ",".join(ingest.allowed_extensions),
        "RAGKB_INGEST_QUALITY_PROFILE": ingest.quality_profile,
        "RAGKB_INGEST_QUALITY_MIN_CHARS": str(ingest.quality_min_chars),
        "RAGKB_INGEST_QUALITY_MIN_READABLE_RATIO": str(ingest.quality_min_readable_ratio),
        "RAGKB_INGEST_QUALITY_MAX_GARBLED_RATIO": str(ingest.quality_max_garbled_ratio),
        "RAGKB_INGEST_QUALITY_MAX_REPEAT_RATIO": str(ingest.quality_max_repeat_ratio),
        "RAGKB_INGEST_QUALITY_MIN_CHARS_PER_PAGE": str(ingest.quality_min_chars_per_page),
        "RAGKB_INGEST_QUALITY_PASS_SCORE": str(ingest.quality_pass_score),
        "RAGKB_INGEST_QUALITY_MAX_UNOCR_IMAGES": str(ingest.quality_max_unocr_images),
        "RAGKB_MEMORY_ENABLED": str(memory.memory_enabled).lower(),
        "RAGKB_MEMORY_CONTEXT_ENABLED": str(memory.memory_context_enabled).lower(),
        "RAGKB_MEMORY_FAST_MEMORY": str(memory.memory_fast_memory).lower(),
        "RAGKB_MEMORY_CONTEXT_BUDGET": str(memory.memory_context_budget),
        "RAGKB_MEMORY_RECENT_HISTORY_MESSAGES": str(memory.memory_recent_history_messages),
        "RAGKB_MEMORY_RECENT_HISTORY_TOKENS": str(memory.memory_recent_history_tokens),
        "RAGKB_MEMORY_UPSTREAM_HISTORY": memory.memory_upstream_history,
        "RAGKB_MEMORY_UPSTREAM_MEMORY": memory.memory_upstream_memory,
        "RAGKB_MEMORY_RECALL_ENABLED": str(memory.memory_recall_enabled).lower(),
        "RAGKB_MEMORY_RECALL_BUDGET": str(memory.memory_recall_budget),
        "RAGKB_MEMORY_RECALL_MAX_ITEMS": str(memory.memory_recall_max_items),
        "RAGKB_MEMORY_AUTO_EXTRACT": str(memory.memory_auto_extract).lower(),
        "RAGKB_MEMORY_WORKER": str(memory.memory_worker).lower(),
        "RAGKB_MEMORY_EXTRACT_EVERY_TURNS": str(memory.memory_extract_every_turns),
        "RAGKB_MEMORY_EXTRACT_MAX_MESSAGES": str(memory.memory_extract_max_messages),
        "RAGKB_MEMORY_EXTRACT_MAX_INPUT_TOKENS": str(memory.memory_extract_max_input_tokens),
        "RAGKB_MEMORY_EXTRACT_DEBOUNCE_SECONDS": str(
            memory.memory_extract_debounce_seconds
        ),
    }


def _read_sources() -> Tuple[Dict[str, str], Dict[str, str]]:
    return read_env_file(app_env_path()), read_env_file(engine_env_path())


def _display_value(spec: FieldSpec, raw: str) -> str:
    text = str(raw or "").strip()
    if spec.kind == "booltext":
        lowered = text.lower()
        if lowered in {"1", "true", "yes", "on"}:
            return "true"
        if lowered in {"0", "false", "no", "off"}:
            return "false"
        return ""
    if spec.kind == "choice":
        for choice in spec.choices:
            if text.lower() == choice.lower():
                return choice
        return text
    return text


def _spec_value(
    spec: FieldSpec,
    app_values: Mapping[str, str],
    engine_values: Mapping[str, str],
) -> str:
    source = engine_values if spec.scope == "engine" else app_values
    raw = source.get(spec.env_key)
    if raw is None:
        raw = _engine_defaults().get(spec.env_key, "") if spec.scope == "engine" else _app_defaults().get(spec.env_key, spec.default)
    if spec.secret:
        return _mask_secret(raw)
    raw = _display_value(spec, raw)
    if spec.pair == ("vector_store", "type"):
        lowered = str(raw or "").lower()
        if "qdrant" in lowered:
            return "qdrant"
        if "nano" in lowered or "local" in lowered:
            return "local"
    return str(raw or "")


def _settings_groups(
    app_values: Mapping[str, str],
    engine_values: Mapping[str, str],
) -> Dict[str, Dict[str, str]]:
    result: Dict[str, Dict[str, str]] = {}
    for pair, default in group_defaults().items():
        result.setdefault(pair[0], {})[pair[1]] = default
    for spec in FIELDS:
        result.setdefault(spec.group, {})[spec.key] = _spec_value(
            spec,
            app_values,
            engine_values,
        )
    if "RAGKB_RERANK_MODE" not in app_values:
        binding = str(engine_values.get("RERANK_BINDING", "null") or "null").lower()
        if binding in {"", "null", "none"}:
            result.setdefault("rerank", {})["mode"] = "off"
        else:
            host = str(engine_values.get("RERANK_BINDING_HOST", "") or "")
            result.setdefault("rerank", {})["mode"] = (
                "local" if host and _private_or_local_url(host) else "cloud"
            )
        if binding in {"cohere", "jina", "aliyun"}:
            result.setdefault("rerank", {})["provider"] = binding
    return result


def _parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _runtime_state() -> List[Dict[str, Any]]:
    try:
        payload = json.loads(RUNTIME_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    saved = payload.get("services") if isinstance(payload, dict) else {}
    if not isinstance(saved, dict):
        saved = {}
    rows: List[Dict[str, Any]] = []
    for name in SERVICE_NAMES:
        item = saved.get(name) if isinstance(saved.get(name), dict) else {}
        host = str(item.get("host") or "127.0.0.1")
        try:
            port = int(item.get("port") or 0)
        except (TypeError, ValueError):
            port = 0
        if not port:
            port_file = {
                "engine": RUNTIME_DIR / "engine.port",
                "shell": LOG_ROOT / "lightrag-shell.port",
                "app": LOG_ROOT / "app.port",
            }[name]
            try:
                port = int(port_file.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                port = 0
        url = f"http://{host}:{port}" if port else ""
        healthy = False
        if url:
            try:
                with urllib.request.urlopen(url + "/health", timeout=1.5) as response:
                    healthy = response.status == 200
            except (OSError, urllib.error.URLError):
                healthy = False
        rows.append(
            {
                "service": name,
                "url": url,
                "port": port or None,
                "running": healthy,
                "started_at": str(item.get("started_at") or ""),
            }
        )
    return rows


def _restart_needed() -> bool:
    rows = {row["service"]: row for row in _runtime_state()}
    app_started = _parse_time(rows.get("app", {}).get("started_at"))
    engine_started = _parse_time(rows.get("engine", {}).get("started_at"))
    for path, started in (
        (app_env_path(), app_started),
        (engine_env_path(), engine_started),
    ):
        if started is None or not path.is_file():
            continue
        try:
            changed = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if changed > started:
            return True
    return False


def _vector_type_for_effective(value: str) -> str:
    lowered = str(value or "").lower()
    if "qdrant" in lowered:
        return "qdrant"
    if "nano" in lowered or "local" in lowered:
        return "local"
    return value


def _lightrag_effective(engine_values: Mapping[str, str]) -> Dict[str, str]:
    defaults = _engine_defaults()

    def value(key: str) -> str:
        return str(engine_values.get(key, defaults.get(key, "")) or "")

    return {
        "LLM_BINDING": value("LLM_BINDING"),
        "LLM_MODEL": value("LLM_MODEL"),
        "LLM_BINDING_HOST": value("LLM_BINDING_HOST"),
        "EMBEDDING_BINDING": value("EMBEDDING_BINDING"),
        "EMBEDDING_MODEL": value("EMBEDDING_MODEL"),
        "EMBEDDING_BINDING_HOST": value("EMBEDDING_BINDING_HOST"),
        "EMBEDDING_DIM": value("EMBEDDING_DIM"),
        "EMBEDDING_SEND_DIM": value("EMBEDDING_SEND_DIM"),
        "RERANK_BINDING": value("RERANK_BINDING"),
        "RERANK_MODEL": value("RERANK_MODEL"),
        "RERANK_BINDING_HOST": value("RERANK_BINDING_HOST"),
        "RERANK_TIMEOUT": value("RERANK_TIMEOUT"),
        "LIGHTRAG_VECTOR_STORAGE": _vector_type_for_effective(
            value("LIGHTRAG_VECTOR_STORAGE")
        ),
        "WORKING_DIR": value("WORKING_DIR"),
        "INPUT_DIR": value("INPUT_DIR"),
    }


def _quality_effective() -> Dict[str, Any]:
    settings = get_ingest_settings()
    return {
        "profile": settings.quality_profile,
        "min_chars": settings.quality_min_chars,
        "min_readable_ratio": settings.quality_min_readable_ratio,
        "max_garbled_ratio": settings.quality_max_garbled_ratio,
        "max_repeat_ratio": settings.quality_max_repeat_ratio,
        "min_chars_per_page": settings.quality_min_chars_per_page,
        "pass_score": settings.quality_pass_score,
        "max_unocr_images": settings.quality_max_unocr_images,
    }


def settings_payload() -> Dict[str, Any]:
    app_values, engine_values = _read_sources()
    app = get_settings()
    ingest = get_ingest_settings()
    inbox = get_inbox_settings()
    ocr = get_ocr_settings()
    ocr_runtime = ocr_service.status(ocr)
    runtime_rows = _runtime_state()
    unsupported_values = dict(UNSUPPORTED)
    app_env = app_env_path()
    engine_env = engine_env_path()
    updated_candidates = [
        _mtime_iso(path)
        for path in (app_env, engine_env)
        if path.is_file()
    ]
    return {
        "settings": _settings_groups(app_values, engine_values),
        "settings_file": str(app_env),
        "settings_files": {
            "application": str(app_env),
            "engine": str(engine_env),
        },
        "settings_exists": app_env.is_file() or engine_env.is_file(),
        "updated_at": max(updated_candidates) if updated_candidates else "",
        "restart_needed": _restart_needed(),
        "lightrag_effective": _lightrag_effective(engine_values),
        "app_effective": {
            "lightrag_url": get_lightrag_settings().url,
            "max_upload_size": ingest.max_upload_size,
            "allowed_extensions": list(ingest.allowed_extensions),
            "log_level": app.log_level,
            "skill_routing": unsupported_values[("app", "skill_routing")],
            "skill_routing_saved": unsupported_values[("app", "skill_routing")],
            "skill_routing_restart_needed": False,
        },
        "quality_effective": _quality_effective(),
        "ingest_confirm_effective": {
            "mode": "always",
            "pending_review_ttl_hours": 24,
        },
        "inbox_effective": {
            "enabled": inbox.enabled,
            "poll_seconds": inbox.poll_seconds,
            "settle_seconds": inbox.settle_seconds,
            "confirm": "review",
            "dirs": {
                "inbox": str(inbox.directory),
                "state": str(inbox.state_path),
            },
        },
        "ocr_effective": ocr_runtime,
        "ocr_preview": {
            "enabled_after_restart": False,
            "configured_mode": ocr.mode,
            "configured_provider": ocr.provider,
            "reason": ocr_runtime["reason"],
            "pending_env": [],
        },
        "ocr_providers": [
            {
                "id": "windows",
                "label": "Windows 本机 OCR",
                "available": bool(ocr_runtime.get("windows_available")),
                "selected": ocr_runtime.get("provider") == "windows",
                "detail": "" if ocr_runtime.get("windows_available") else "Windows OCR 或语言包不可用",
            },
            {
                "id": "local_model",
                "label": "自定义本地 OCR 模型",
                "available": bool(ocr_runtime.get("local_model_configured")),
                "selected": ocr_runtime.get("provider") == "local_model",
                "detail": "需要本机/局域网接口、模型名",
            },
            {
                "id": "cloud",
                "label": "云端 OCR",
                "available": bool(ocr_runtime.get("cloud_configured")),
                "selected": ocr_runtime.get("provider") == "cloud",
                "detail": "需要接口地址、模型名和 API Key；启用后会外发图片",
            },
        ],
        "tts_effective": tts_service.status(),
        "runtime_effective": {"services": runtime_rows},
        "injected_env": {},
        "note": "配置已保存。运行中的服务需重启后使用新配置。",
    }


def _normalize_spec(spec: FieldSpec, value: Any) -> str:
    text = str(value or "").strip()
    if spec.secret:
        if len(text) > 4096 or "\n" in text or "\r" in text:
            raise SettingsError([f"{spec.group}.{spec.key} 不是有效密钥"])
        return text
    if not text:
        return ""
    if len(text) > 4000 or "\n" in text or "\r" in text:
        raise SettingsError([f"{spec.group}.{spec.key} 无效"])
    if spec.kind == "choice":
        normalized = text.upper() if spec.env_key == "RAGKB_LOG_LEVEL" else text.lower()
        if spec.choices and normalized not in spec.choices:
            raise SettingsError(
                [f"{spec.group}.{spec.key} 必须是 {', '.join(spec.choices)} 之一"]
            )
        return normalized
    if spec.kind == "integer":
        try:
            number = int(text)
        except ValueError as exc:
            raise SettingsError([f"{spec.group}.{spec.key} 必须是整数"]) from exc
        if spec.minimum is not None and number < spec.minimum:
            raise SettingsError([f"{spec.group}.{spec.key} 不能小于 {int(spec.minimum)}"])
        if spec.maximum is not None and number > spec.maximum:
            raise SettingsError([f"{spec.group}.{spec.key} 不能大于 {int(spec.maximum)}"])
        return str(number)
    if spec.kind == "number":
        try:
            number = float(text)
        except ValueError as exc:
            raise SettingsError([f"{spec.group}.{spec.key} 必须是数字"]) from exc
        if spec.minimum is not None and number < spec.minimum:
            raise SettingsError([f"{spec.group}.{spec.key} 不能小于 {spec.minimum}"])
        if spec.maximum is not None and number > spec.maximum:
            raise SettingsError([f"{spec.group}.{spec.key} 不能大于 {spec.maximum}"])
        return f"{number:g}"
    if spec.kind == "booltext":
        normalized = text.lower()
        if normalized not in {"true", "false"}:
            raise SettingsError([f"{spec.group}.{spec.key} 必须是 true 或 false"])
        return normalized
    if spec.kind in {"url", "path"}:
        if spec.kind == "url":
            parsed = urlparse(text)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise SettingsError([f"{spec.group}.{spec.key} 必须是 http(s) 地址"])
        return text
    if spec.kind == "list":
        result: List[str] = []
        for item in text.split(","):
            token = item.strip()
            if not token:
                continue
            if not token.startswith("."):
                token = "." + token
            token = token.lower()
            if token not in result:
                result.append(token)
        return ",".join(result)
    return text


def _resolved_value(
    spec: FieldSpec,
    app_values: Mapping[str, str],
    engine_values: Mapping[str, str],
    updates: Mapping[str, Optional[str]],
) -> str:
    if spec.env_key in updates:
        value = updates[spec.env_key]
        return "" if value is None else _display_value(spec, str(value))
    source = engine_values if spec.scope == "engine" else app_values
    raw = source.get(spec.env_key)
    if raw is not None:
        return _display_value(spec, raw)
    default = (
        _engine_defaults().get(spec.env_key, spec.default)
        if spec.scope == "engine"
        else _app_defaults().get(spec.env_key, spec.default)
    )
    return _display_value(spec, default)


def _validate_resolved(
    app_values: Mapping[str, str],
    engine_values: Mapping[str, str],
    updates: Mapping[str, Optional[str]],
) -> None:
    messages: List[str] = []
    for spec in FIELDS:
        value = _resolved_value(spec, app_values, engine_values, updates)
        if spec.required and not value:
            messages.append(f"{spec.group}.{spec.key} 不能为空")
    ports = {
        pair: _resolved_value(FIELD_BY_PAIR[pair], app_values, engine_values, updates)
        for pair in (
            ("runtime", "app_port"),
            ("runtime", "shell_port"),
            ("runtime", "engine_port"),
        )
    }
    if len(set(ports.values())) != len(ports):
        messages.append("应用、外壳与引擎端口必须互不相同")
    working = _resolved_value(
        FIELD_BY_PAIR[("graph", "working_dir")],
        app_values,
        engine_values,
        updates,
    )
    inputs = _resolved_value(
        FIELD_BY_PAIR[("graph", "input_dir")],
        app_values,
        engine_values,
        updates,
    )
    if working and inputs and Path(working).resolve() == Path(inputs).resolve():
        messages.append("知识库数据目录与文档输入目录不能相同")
    if messages:
        raise SettingsError(messages)


def save_settings(payload: Mapping[str, Any], *, remote: bool = False) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise SettingsError(["请求体必须是 JSON 对象"])
    app_values, engine_values = _read_sources()
    app_updates: Dict[str, Optional[str]] = {}
    engine_updates: Dict[str, Optional[str]] = {}
    messages: List[str] = []

    clear_values = payload.get("clear_secrets", [])
    clear_pairs: set[Tuple[str, str]] = set()
    if clear_values:
        if not isinstance(clear_values, list):
            raise SettingsError(["clear_secrets 必须是数组"])
        for item in clear_values:
            if (
                isinstance(item, (list, tuple))
                and len(item) == 2
                and (str(item[0]), str(item[1])) in FIELD_BY_PAIR
            ):
                clear_pairs.add((str(item[0]), str(item[1])))
            else:
                messages.append("clear_secrets 含有未知字段")

    for group, values in payload.items():
        if group == "clear_secrets":
            continue
        if not isinstance(values, Mapping):
            messages.append(f"{group} 必须是对象")
            continue
        for key, value in values.items():
            pair = (str(group), str(key))
            spec = FIELD_BY_PAIR.get(pair)
            if spec is None:
                if pair in UNSUPPORTED:
                    current = UNSUPPORTED[pair]
                    incoming = str(value or "").strip()
                    if pair in UNSUPPORTED_SECRETS:
                        if incoming or pair in clear_pairs:
                            messages.append(f"{group}.{key} 在当前版本尚未实现")
                    elif incoming.lower() != current.lower():
                        messages.append(f"{group}.{key} 在当前版本尚未实现")
                else:
                    messages.append(f"未知设置项：{group}.{key}")
                continue
            update_target = engine_updates if spec.scope == "engine" else app_updates
            if spec.secret:
                if pair in clear_pairs:
                    update_target[spec.env_key] = None
                    continue
                text = str(value or "").strip()
                if not text:
                    continue
                try:
                    update_target[spec.env_key] = _normalize_spec(spec, text)
                except SettingsError as exc:
                    messages.extend(exc.messages)
                continue
            text = str(value or "").strip()
            if not text:
                update_target[spec.env_key] = None
                continue
            try:
                normalized = _normalize_spec(spec, text)
            except SettingsError as exc:
                messages.extend(exc.messages)
                continue
            if pair == ("vector_store", "type"):
                normalized = (
                    "QdrantVectorDBStorage"
                    if normalized == "qdrant"
                    else "NanoVectorDBStorage"
                )
            update_target[spec.env_key] = normalized

    for pair in clear_pairs:
        spec = FIELD_BY_PAIR.get(pair)
        if spec is None:
            continue
        if not spec.secret:
            messages.append(f"{pair[0]}.{pair[1]} 不是密钥字段")
            continue
        target = engine_updates if spec.scope == "engine" else app_updates
        target[spec.env_key] = None

    rerank_mode = str(
        app_updates.get(
            "RAGKB_RERANK_MODE",
            app_values.get("RAGKB_RERANK_MODE", "off"),
        )
        or "off"
    ).lower()
    rerank_provider = str(
        app_updates.get(
            "RAGKB_RERANK_PROVIDER",
            app_values.get("RAGKB_RERANK_PROVIDER", "cohere"),
        )
        or "cohere"
    ).lower()
    rerank_ui_changed = (
        "RAGKB_RERANK_MODE" in app_updates
        or "RAGKB_RERANK_PROVIDER" in app_updates
    )
    if rerank_ui_changed:
        engine_updates["RERANK_BINDING"] = _rerank_binding_for(
            rerank_mode,
            rerank_provider,
        )
    if rerank_mode == "local":
        rerank_host = _resolved_value(
            FIELD_BY_PAIR[("rerank", "host")],
            app_values,
            engine_values,
            {**app_updates, **engine_updates},
        )
        rerank_model = _resolved_value(
            FIELD_BY_PAIR[("rerank", "model")],
            app_values,
            engine_values,
            {**app_updates, **engine_updates},
        )
        if not rerank_host or not _private_or_local_url(rerank_host):
            messages.append("本地重排必须填写本机或局域网接口地址")
        if not rerank_model:
            messages.append("本地重排必须填写模型名称")
    elif rerank_mode == "cloud":
        rerank_key = _resolved_value(
            FIELD_BY_PAIR[("rerank", "api_key")],
            app_values,
            engine_values,
            {**app_updates, **engine_updates},
        )
        if not rerank_key:
            messages.append("云端重排必须填写 API Key")

    ocr_mode = _resolved_value(
        FIELD_BY_PAIR[("ocr", "mode")],
        app_values,
        engine_values,
        {**app_updates, **engine_updates},
    )
    ocr_provider = _resolved_value(
        FIELD_BY_PAIR[("ocr", "provider")],
        app_values,
        engine_values,
        {**app_updates, **engine_updates},
    )
    if ocr_mode in {"manual", "auto"}:
        ocr_base_url = _resolved_value(
            FIELD_BY_PAIR[("ocr", "base_url")],
            app_values,
            engine_values,
            {**app_updates, **engine_updates},
        )
        ocr_model = _resolved_value(
            FIELD_BY_PAIR[("ocr", "model")],
            app_values,
            engine_values,
            {**app_updates, **engine_updates},
        )
        ocr_token = _resolved_value(
            FIELD_BY_PAIR[("ocr", "token")],
            app_values,
            engine_values,
            {**app_updates, **engine_updates},
        )
        if ocr_provider == "local_model":
            if not ocr_base_url or not _private_or_local_url(ocr_base_url):
                messages.append("自定义本地 OCR 必须填写本机或局域网接口地址")
            if not ocr_model:
                messages.append("自定义本地 OCR 必须填写模型名称")
        elif ocr_provider == "cloud":
            if not ocr_base_url:
                messages.append("云端 OCR 必须填写兼容接口地址")
            if not ocr_model:
                messages.append("云端 OCR 必须填写模型名称")
            if not ocr_token:
                messages.append("云端 OCR 必须填写 Token")

    if messages:
        raise SettingsError(messages)
    _validate_resolved(app_values, engine_values, {**app_updates, **engine_updates})

    if remote and (app_updates or engine_updates):
        raise SettingsError(["设置只能从本机修改"])

    engine_backup: Optional[Path] = None
    app_backup: Optional[Path] = None
    engine_existed = engine_env_path().is_file()
    app_existed = app_env_path().is_file()
    try:
        engine_backup = update_env_file(
            engine_env_path(),
            engine_updates,
            backup_dir=backup_dir(),
        )
        app_backup = update_env_file(
            app_env_path(),
            app_updates,
            backup_dir=backup_dir(),
        )
    except (OSError, EnvFileError) as exc:
        if engine_existed and engine_backup is not None:
            shutil.copy2(engine_backup, engine_env_path())
        elif not engine_existed:
            try:
                engine_env_path().unlink()
            except OSError:
                pass
        raise SettingsError([f"写入配置失败：{type(exc).__name__}"]) from exc

    backups = [str(item) for item in (app_backup, engine_backup) if item is not None]
    resolved_updates = {**app_updates, **engine_updates}
    memory_values = {
        spec.key: _resolved_value(spec, app_values, engine_values, resolved_updates)
        for spec in FIELDS
        if spec.group == "memory"
    }
    warnings: List[str] = []
    if memory_values.get("context_enabled") == "true" and memory_values.get("enabled") != "true":
        warnings.append("记忆未启用，因此上下文注入不会生效。")
    if memory_values.get("worker") == "true" and not (
        memory_values.get("enabled") == "true"
        and memory_values.get("auto_extract") == "true"
    ):
        warnings.append("后台 worker 已保存为开启，但记忆或自动提取未同时开启，worker 不会启动。")
    base_note = (
        "配置已保存。"
        if not (app_updates or engine_updates)
        else "配置已保存；重启服务后生效。"
    )
    ocr_mode = _resolved_value(
        FIELD_BY_PAIR[("ocr", "mode")],
        app_values,
        engine_values,
        resolved_updates,
    )
    ocr_provider = _resolved_value(
        FIELD_BY_PAIR[("ocr", "provider")],
        app_values,
        engine_values,
        resolved_updates,
    )
    ocr_changed = any(key.startswith("RAGKB_OCR_") for key in resolved_updates)
    ocr_desired = ocr_mode in {"manual", "auto"} and ocr_provider != "none"
    return {
        "ok": True,
        "note": " ".join([base_note, *warnings]),
        "warnings": warnings,
        "updated_at": _utc_now(),
        "settings_file": str(app_env_path()),
        "settings_files": {
            "application": str(app_env_path()),
            "engine": str(engine_env_path()),
        },
        "restart_needed": True if (app_updates or engine_updates) else _restart_needed(),
        "backups": backups,
        "settings": _settings_groups(
            read_env_file(app_env_path()),
            read_env_file(engine_env_path()),
        ),
        "ocr_preview": {
            "enabled_after_restart": bool(ocr_changed and ocr_desired),
            "configured_mode": ocr_mode,
            "configured_provider": ocr_provider,
            "reason": "" if ocr_desired else "OCR 当前关闭或提供方不是 local",
            "pending_env": ["OCR"] if ocr_changed else [],
        },
        "ingest_confirm_effective": {"mode": "always", "pending_review_ttl_hours": 24},
    }


def _http_json(
    method: str,
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    body: Optional[Mapping[str, Any]] = None,
    timeout: float = 20.0,
) -> Tuple[bool, str, Dict[str, Any]]:
    data = None
    request_headers = {"Accept": "application/json", **dict(headers or {})}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=request_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(256 * 1024)
            status = response.status
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}", {}
    except (OSError, urllib.error.URLError) as exc:
        return False, type(exc).__name__, {}
    payload: Dict[str, Any] = {}
    if raw:
        try:
            value = json.loads(raw.decode("utf-8"))
            if isinstance(value, dict):
                payload = value
        except (UnicodeDecodeError, ValueError):
            payload = {}
    return 200 <= status < 300, f"HTTP {status}", payload


def test_connections() -> Dict[str, Any]:
    app_values = read_env_file(app_env_path())
    engine_values = read_env_file(engine_env_path())
    defaults = _engine_defaults()

    def value(key: str) -> str:
        return str(engine_values.get(key, defaults.get(key, "")) or "").strip()

    checks: List[Dict[str, Any]] = []
    llm_host = value("LLM_BINDING_HOST").rstrip("/")
    llm_model = value("LLM_MODEL")
    llm_key = value("LLM_BINDING_API_KEY")
    if llm_host and llm_model:
        headers = {"Authorization": f"Bearer {llm_key}"} if llm_key else {}
        ok, detail, payload = _http_json(
            "POST",
            llm_host + "/chat/completions",
            headers=headers,
            body={
                "model": llm_model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 1,
                "temperature": 0,
                "stream": False,
            },
            timeout=30.0,
        )
        checks.append(
            {
                "name": "生成模型（OpenAI 兼容）",
                "ok": ok,
                "detail": (
                    f"{llm_model} 可调用"
                    if ok
                    else f"{llm_model} 调用失败（{detail}）"
                ),
            }
        )
    else:
        checks.append(
            {
                "name": "生成模型（OpenAI 兼容）",
                "ok": False,
                "detail": "尚未配置接口地址或模型名称",
            }
        )

    embedding_binding = value("EMBEDDING_BINDING").lower()
    embedding_host = value("EMBEDDING_BINDING_HOST").rstrip("/")
    embedding_model = value("EMBEDDING_MODEL")
    if embedding_binding == "ollama" and embedding_host and embedding_model:
        ok, detail, payload = _http_json(
            "GET",
            embedding_host + "/api/tags",
            timeout=8.0,
        )
        names = {
            str(item.get("name") or "")
            for item in payload.get("models", [])
            if isinstance(item, dict)
        }
        model_ok = embedding_model in names
        checks.append(
            {
                "name": "嵌入模型（Ollama）",
                "ok": ok and model_ok,
                "detail": (
                    f"{embedding_model} 已安装"
                    if ok and model_ok
                    else (
                        "Ollama 可达但未找到指定模型"
                        if ok
                        else f"Ollama 不可达（{detail}）"
                    )
                ),
            }
        )
    elif embedding_binding == "openai" and embedding_host:
        headers = (
            {"Authorization": f"Bearer {value('EMBEDDING_BINDING_API_KEY')}"}
            if value("EMBEDDING_BINDING_API_KEY")
            else {}
        )
        ok, detail, _ = _http_json(
            "GET",
            embedding_host + "/models",
            headers=headers,
            timeout=15.0,
        )
        checks.append(
            {
                "name": "嵌入模型（OpenAI 兼容）",
                "ok": ok,
                "detail": "接口可访问" if ok else f"接口不可用（{detail}）",
            }
        )
    else:
        checks.append(
            {
                "name": "嵌入模型",
                "ok": False,
                "detail": "尚未配置可用的嵌入服务",
            }
        )

    rerank_binding = value("RERANK_BINDING").lower()
    if rerank_binding and rerank_binding != "null":
        defaults = {
            "cohere": (
                "https://api.cohere.com/v2/rerank",
                "rerank-v3.5",
            ),
            "jina": (
                "https://api.jina.ai/v1/rerank",
                "jina-reranker-v2-base-multilingual",
            ),
            "aliyun": (
                "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
                "gte-rerank-v2",
            ),
        }
        default_url, default_model = defaults.get(
            rerank_binding,
            ("", ""),
        )
        rerank_host = value("RERANK_BINDING_HOST") or default_url
        rerank_model = value("RERANK_MODEL") or default_model
        rerank_key = value("RERANK_BINDING_API_KEY")
        headers = {"Authorization": f"Bearer {rerank_key}"} if rerank_key else {}
        documents = [
            "本地知识库中的检索结果排序说明。",
            "与查询无关的天气和旅行信息。",
        ]
        if rerank_binding == "aliyun":
            body: Dict[str, Any] = {
                "model": rerank_model,
                "input": {"query": "本地 RAG 检索", "documents": documents},
                "parameters": {"top_n": 2},
            }
        else:
            body = {
                "model": rerank_model,
                "query": "本地 RAG 检索",
                "documents": documents,
                "top_n": 2,
            }
        try:
            rerank_timeout = float(value("RERANK_TIMEOUT") or 30)
        except ValueError:
            rerank_timeout = 30.0
        ok, detail, payload = _http_json(
            "POST",
            rerank_host,
            headers=headers,
            body=body,
            timeout=max(5.0, min(60.0, rerank_timeout)),
        )
        if rerank_binding == "aliyun":
            output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
            results = output.get("results") if isinstance(output.get("results"), list) else []
        else:
            results = payload.get("results") if isinstance(payload.get("results"), list) else []
        valid = [
            item for item in results
            if isinstance(item, dict)
            and isinstance(item.get("index"), int)
            and isinstance(item.get("relevance_score"), (int, float))
        ]
        mode = str(app_values.get("RAGKB_RERANK_MODE", "off")).lower()
        label = "本地重排" if mode == "local" else "云端重排"
        checks.append(
            {
                "name": f"重排模型（{label}）",
                "ok": bool(ok and valid),
                "detail": (
                    f"{rerank_model or rerank_binding} 可用，返回 {len(valid)} 条排序"
                    if ok and valid
                    else (
                        f"接口可达但返回格式不符合 /rerank"
                        if ok
                        else f"{rerank_binding} 不可用（{detail}）"
                    )
                ),
            }
        )

    health = get_knowledge_gateway().health()
    checks.append(
        {
            "name": "知识服务（外壳与引擎）",
            "ok": health.reachable and health.engine_installed,
            "detail": (
                "外壳与 LightRAG 引擎均可用"
                if health.reachable and health.engine_installed
                else health.detail or f"当前状态：{health.state}"
            ),
        }
    )
    return {
        "checks": checks,
        "all_ok": all(bool(item["ok"]) for item in checks),
    }


def tts_options() -> Dict[str, Any]:
    runtime = tts_service.status()
    cfg = get_tts_settings()
    return {
        "available": bool(runtime["ready"]),
        "cloud": {
            "configured": bool(runtime["configured"]),
            "configured_ready": bool(runtime["ready"]),
            "reason": runtime["reason"],
            "host": cfg.cloud_host,
            "model": runtime["model"],
            "voice": runtime["voice"],
        },
        "runtime": runtime,
    }


__all__ = [
    "SettingsError",
    "app_env_path",
    "engine_env_path",
    "save_settings",
    "settings_payload",
    "test_connections",
    "tts_options",
]
