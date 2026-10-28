"""Start, stop, and inspect the local RAG service group.

The lifecycle owns only processes it started itself. It never stops a process
merely because that process happens to listen on a configured port.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.platform.ports import can_bind, select_port  # noqa: E402
from app.features.settings.envfile import parse_env_text  # noqa: E402

RUNTIME_DIR = ROOT / "logs" / "runtime"
STATE_FILE = RUNTIME_DIR / "services.json"
SERVICE_ORDER = ("engine", "shell", "app")


@dataclass
class ServiceState:
    name: str
    pid: int
    process_marker: str
    host: str
    port: int
    started_at: str
    log_stdout: str
    log_stderr: str

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_root_env() -> Dict[str, str]:
    path = ROOT / ".env"
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


ROOT_ENV = _load_root_env()


def _setting(name: str, default: str) -> str:
    if name in os.environ:
        return str(os.environ[name]).strip()
    return str(ROOT_ENV.get(name, default)).strip()


def _setting_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(_setting(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _load_state() -> Dict[str, ServiceState]:
    if not STATE_FILE.is_file():
        return {}
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    services = payload.get("services") if isinstance(payload, dict) else None
    if not isinstance(services, dict):
        return {}
    result: Dict[str, ServiceState] = {}
    for name, item in services.items():
        if name not in SERVICE_ORDER or not isinstance(item, dict):
            continue
        try:
            result[name] = ServiceState(
                name=name,
                pid=int(item["pid"]),
                process_marker=str(item.get("process_marker") or ""),
                host=str(item.get("host") or "127.0.0.1"),
                port=int(item["port"]),
                started_at=str(item.get("started_at") or ""),
                log_stdout=str(item.get("log_stdout") or ""),
                log_stderr=str(item.get("log_stderr") or ""),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _save_state(services: Dict[str, ServiceState]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    ordered = {
        name: asdict(services[name])
        for name in SERVICE_ORDER
        if name in services
    }
    payload = {
        "version": 1,
        "updated_at": _utc_now(),
        "services": ordered,
    }
    temporary = STATE_FILE.with_name(f".{STATE_FILE.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, STATE_FILE)


def _clear_state() -> None:
    try:
        STATE_FILE.unlink()
    except FileNotFoundError:
        pass


if os.name == "nt":
    from ctypes import wintypes

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    _KERNEL32.OpenProcess.restype = wintypes.HANDLE
    _KERNEL32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _KERNEL32.GetExitCodeProcess.restype = wintypes.BOOL
    _KERNEL32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    _KERNEL32.GetProcessTimes.restype = wintypes.BOOL
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _KERNEL32.CloseHandle.restype = wintypes.BOOL

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259


def _process_marker(pid: int) -> str:
    """Return a creation-time marker so a reused PID is never mistaken."""
    if pid <= 0:
        return ""
    if os.name != "nt":
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
            return stat.split()[21]
        except (OSError, IndexError):
            return ""
    handle = _KERNEL32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if not handle:
        return ""
    try:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not _KERNEL32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return ""
        value = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
        return str(value)
    finally:
        _KERNEL32.CloseHandle(handle)


def _process_alive(pid: int, marker: str = "") -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        current = _process_marker(pid)
        return not marker or not current or current == marker

    handle = _KERNEL32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not _KERNEL32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        if int(code.value) != _STILL_ACTIVE:
            return False
    finally:
        _KERNEL32.CloseHandle(handle)
    current = _process_marker(pid)
    return not marker or not current or current == marker


def _remove_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _publish_port(name: str, port: int) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    target = RUNTIME_DIR / f"{name}.port"
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(f"{port}\n", encoding="ascii")
    os.replace(temporary, target)


def _read_port(path: Path, *, timeout: float, process_pid: int) -> int:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        if not _process_alive(process_pid):
            raise RuntimeError(f"Service exited before publishing {path.name}")
        try:
            text = path.read_text(encoding="ascii").strip()
            port = int(text)
        except (OSError, ValueError) as exc:
            last_error = type(exc).__name__
        else:
            if 1 <= port <= 65535:
                return port
            last_error = "invalid port"
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {path.name} ({last_error or 'no file'})")


def _wait_http(
    url: str,
    *,
    timeout: float,
    process_pid: int,
    ready: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        if not _process_alive(process_pid):
            raise RuntimeError("Service exited before becoming healthy")
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                body = response.read(512 * 1024)
                payload = json.loads(body.decode("utf-8")) if body else {}
            if response.status == 200 and isinstance(payload, dict):
                if ready is None or ready(payload):
                    return payload
                last_error = "health predicate not satisfied"
            else:
                last_error = f"HTTP {response.status}"
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = type(exc).__name__
        time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for health at {url} ({last_error})")


def _tail(path: Path, lines: int = 12) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def _startup_timeout() -> float:
    return float(_setting_int("RAGKB_LIFECYCLE_START_TIMEOUT", 120, 10, 1800))


def _stop_timeout() -> float:
    return float(_setting_int("RAGKB_LIFECYCLE_STOP_TIMEOUT", 20, 3, 300))


def _main_python() -> Path:
    path = ROOT / ".venv" / "Scripts" / "python.exe"
    if os.name != "nt":
        path = ROOT / ".venv" / "bin" / "python"
    if not path.is_file():
        raise RuntimeError(f"Application virtual environment is missing: {path}")
    return path


def _engine_python() -> Path:
    base = ROOT / "services" / "lightrag_engine" / ".venv"
    path = base / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not path.is_file():
        raise RuntimeError(f"LightRAG engine virtual environment is missing: {path}")
    return path


def _process_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    if extra:
        env.update(extra)
    return env


def _launch(
    name: str,
    command: Iterable[str],
    *,
    cwd: Path,
    env: Dict[str, str],
) -> subprocess.Popen[Any]:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    stdout_path = RUNTIME_DIR / f"{name}.stdout.log"
    stderr_path = RUNTIME_DIR / f"{name}.stderr.log"
    stdout = stdout_path.open("ab", buffering=0)
    stderr = stderr_path.open("ab", buffering=0)
    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        start_new_session = True
    try:
        return subprocess.Popen(
            list(command),
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
            start_new_session=start_new_session,
            close_fds=True,
        )
    finally:
        stdout.close()
        stderr.close()


def _state_for(
    name: str,
    process: subprocess.Popen[Any],
    *,
    host: str,
    port: int,
) -> ServiceState:
    return ServiceState(
        name=name,
        pid=int(process.pid),
        process_marker=_process_marker(int(process.pid)),
        host=host,
        port=port,
        started_at=_utc_now(),
        log_stdout=str(RUNTIME_DIR / f"{name}.stdout.log"),
        log_stderr=str(RUNTIME_DIR / f"{name}.stderr.log"),
    )


def _request_shutdown(pid: int) -> None:
    if os.name == "nt":
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
            return
        except (OSError, ValueError, AttributeError):
            pass
    else:
        try:
            os.kill(pid, signal.SIGTERM)
            return
        except OSError:
            pass
    os.kill(pid, signal.SIGTERM)


def _force_stop(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _stop_service(service: ServiceState) -> str:
    if not _process_alive(service.pid, service.process_marker):
        return "already stopped"
    try:
        _request_shutdown(service.pid)
    except OSError:
        _force_stop(service.pid)
        return "force stopped"
    deadline = time.monotonic() + _stop_timeout()
    while time.monotonic() < deadline:
        if not _process_alive(service.pid, service.process_marker):
            return "stopped"
        time.sleep(0.2)
    _force_stop(service.pid)
    return "force stopped after timeout"


def _cleanup_runtime_files() -> None:
    for name in ("app", "shell", "engine"):
        _remove_if_exists(RUNTIME_DIR / f"{name}.port")
    for path in (ROOT / "logs" / "app.port", ROOT / "logs" / "lightrag-shell.port"):
        _remove_if_exists(path)


def _running_services() -> Dict[str, ServiceState]:
    return {
        name: service
        for name, service in _load_state().items()
        if _process_alive(service.pid, service.process_marker)
    }


def _stop_all(*, quiet: bool = False) -> None:
    services = _load_state()
    for name in reversed(SERVICE_ORDER):
        service = services.get(name)
        if service is None:
            continue
        result = _stop_service(service)
        if not quiet:
            print(f"{name}: {result}")
    _cleanup_runtime_files()
    _clear_state()


def start(*, open_browser: bool, reuse_running: bool = False) -> int:
    running = _running_services()
    if running:
        names = ", ".join(name for name in SERVICE_ORDER if name in running)
        if reuse_running and all(name in running for name in SERVICE_ORDER):
            print(f"Services already running: {names}. Opening the existing web UI.")
            if open_browser:
                return open_web()
            return 0
        print(f"Services already running: {names}. Use restart or stop first.")
        return 1

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_runtime_files()
    _clear_state()

    main_python = _main_python()
    engine_python = _engine_python()
    engine_root = ROOT / "services" / "lightrag_engine"
    if not (engine_root / ".env").is_file():
        print(f"Missing engine configuration: {engine_root / '.env'}")
        return 2

    timeout = _startup_timeout()
    engine_host = _setting("RAGKB_LIGHTRAG_ENGINE_HOST", "127.0.0.1")
    engine_preferred = _setting_int(
        "RAGKB_LIGHTRAG_ENGINE_PORT",
        10262,
        1,
        65535,
    )
    engine_scan_limit = _setting_int(
        "RAGKB_LIGHTRAG_ENGINE_PORT_SCAN_LIMIT",
        20,
        0,
        500,
    )
    if not can_bind(engine_host, engine_preferred):
        print(f"Engine preferred port {engine_preferred} is busy; scanning forward.")
    try:
        engine_port, _ = select_port(
            engine_host,
            engine_preferred,
            scan_limit=engine_scan_limit,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Unable to select an engine port: {exc}")
        return 2

    engine_env_values: Dict[str, str] = {}
    try:
        engine_env_values = parse_env_text(
            (engine_root / ".env").read_text(encoding="utf-8-sig")
        )
    except OSError as exc:
        print(f"Unable to read engine configuration: {type(exc).__name__}")
        return 2

    explicit_data_root = _setting("RAGKB_LIGHTRAG_ENGINE_DATA_DIR", "")
    data_root = Path(
        explicit_data_root or str(ROOT / "data" / "lightrag")
    ).resolve()

    def engine_path(setting_name: str, env_key: str, fallback: Path) -> Path:
        override = _setting(setting_name, "")
        if override:
            path = Path(override).expanduser()
        else:
            saved = str(engine_env_values.get(env_key) or "").strip()
            path = Path(saved).expanduser() if saved else fallback
        if not path.is_absolute():
            path = engine_root / path
        return path.resolve()

    working_dir = engine_path(
        "RAGKB_LIGHTRAG_ENGINE_WORKING_DIR",
        "WORKING_DIR",
        data_root / "storage",
    )
    input_dir = engine_path(
        "RAGKB_LIGHTRAG_ENGINE_INPUT_DIR",
        "INPUT_DIR",
        data_root / "input",
    )
    working_dir.mkdir(parents=True, exist_ok=True)
    input_dir.mkdir(parents=True, exist_ok=True)

    services: Dict[str, ServiceState] = {}
    engine_process: Optional[subprocess.Popen[Any]] = None
    try:
        engine_process = _launch(
            "engine",
            [
                str(engine_python),
                "-B",
                "-m",
                "lightrag.api.lightrag_server",
                "--host",
                engine_host,
                "--port",
                str(engine_port),
                "--working-dir",
                str(working_dir),
                "--input-dir",
                str(input_dir),
            ],
            cwd=engine_root,
            env=_process_env(),
        )
        engine_state = _state_for(
            "engine",
            engine_process,
            host=engine_host,
            port=engine_port,
        )
        services["engine"] = engine_state
        _save_state(services)
        _publish_port("engine", engine_port)
        _wait_http(
            engine_state.url + "/health",
            timeout=timeout,
            process_pid=engine_state.pid,
        )
        print(f"engine: started at {engine_state.url}")

        shell_host = _setting("RAGKB_LIGHTRAG_SHELL_HOST", "127.0.0.1")
        shell_preferred = _setting_int(
            "RAGKB_LIGHTRAG_SHELL_PORT",
            10261,
            1,
            65535,
        )
        shell_scan_limit = _setting_int(
            "RAGKB_LIGHTRAG_SHELL_PORT_SCAN_LIMIT",
            20,
            0,
            500,
        )
        shell_port_file = ROOT / "logs" / "lightrag-shell.port"
        _remove_if_exists(shell_port_file)
        shell_process = _launch(
            "shell",
            [str(main_python), "-B", "-m", "services.lightrag_shell.main"],
            cwd=ROOT,
            env=_process_env(
                {
                    "RAGKB_LIGHTRAG_BACKEND_URL": engine_state.url,
                    "RAGKB_LIGHTRAG_SHELL_HOST": shell_host,
                    "RAGKB_LIGHTRAG_SHELL_PORT": str(shell_preferred),
                    "RAGKB_LIGHTRAG_SHELL_PORT_SCAN_LIMIT": str(shell_scan_limit),
                }
            ),
        )
        shell_port = _read_port(
            shell_port_file,
            timeout=timeout,
            process_pid=shell_process.pid,
        )
        shell_state = _state_for(
            "shell",
            shell_process,
            host=shell_host,
            port=shell_port,
        )
        services["shell"] = shell_state
        _save_state(services)
        _publish_port("shell", shell_port)
        _wait_http(
            shell_state.url + "/health",
            timeout=timeout,
            process_pid=shell_state.pid,
            ready=lambda payload: bool(payload.get("engine_installed")),
        )
        print(f"shell: started at {shell_state.url}")

        app_host = _setting("RAGKB_HOST", "127.0.0.1")
        app_port_file = ROOT / "logs" / "app.port"
        _remove_if_exists(app_port_file)
        app_process = _launch(
            "app",
            [str(main_python), "-B", "main.py"],
            cwd=ROOT,
            env=_process_env(
                {
                    "RAGKB_LIGHTRAG_URL": shell_state.url,
                }
            ),
        )
        app_port = _read_port(
            app_port_file,
            timeout=timeout,
            process_pid=app_process.pid,
        )
        app_state = _state_for(
            "app",
            app_process,
            host=app_host,
            port=app_port,
        )
        services["app"] = app_state
        _save_state(services)
        _publish_port("app", app_port)
        _wait_http(
            app_state.url + "/health",
            timeout=timeout,
            process_pid=app_state.pid,
        )
        print(f"app: started at {app_state.url}/web")
    except Exception as exc:
        print(f"Startup failed: {exc}")
        for name in reversed(SERVICE_ORDER):
            service = services.get(name)
            if service is not None:
                _stop_service(service)
        _cleanup_runtime_files()
        _clear_state()
        for name in ("engine", "shell", "app"):
            stderr_path = RUNTIME_DIR / f"{name}.stderr.log"
            tail = _tail(stderr_path)
            if tail:
                print(f"Last stderr lines from {name} ({stderr_path}):")
                print(tail)
        return 1

    if open_browser:
        webbrowser.open(services["app"].url + "/web")
    return 0


def _health(service: ServiceState, name: str) -> tuple[bool, Dict[str, Any]]:
    try:
        with urllib.request.urlopen(service.url + "/health", timeout=3.0) as response:
            status_code = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return False, {}
    if not isinstance(payload, dict):
        return False, {}
    if name == "shell":
        return status_code == 200 and bool(payload.get("engine_installed")), payload
    return status_code == 200, payload


def status(*, as_json: bool) -> int:
    services = _load_state()
    rows: list[Dict[str, Any]] = []
    for name in SERVICE_ORDER:
        service = services.get(name)
        if service is None:
            rows.append(
                {
                    "service": name,
                    "state": "stopped",
                    "pid": None,
                    "url": None,
                    "healthy": False,
                }
            )
            continue
        alive = _process_alive(service.pid, service.process_marker)
        healthy, payload = _health(service, name) if alive else (False, {})
        rows.append(
            {
                "service": name,
                "state": "running" if alive and healthy else ("unhealthy" if alive else "stopped"),
                "pid": service.pid if alive else None,
                "url": service.url,
                "healthy": healthy,
                "started_at": service.started_at,
                "health": payload,
            }
        )
    if as_json:
        print(json.dumps({"services": rows}, ensure_ascii=False, indent=2))
    else:
        print(f"{'SERVICE':<8} {'STATE':<10} {'PID':>8}  URL")
        for row in rows:
            pid = str(row["pid"]) if row["pid"] is not None else "-"
            print(
                f"{row['service']:<8} {row['state']:<10} {pid:>8}  "
                f"{row['url'] or '-'}"
            )
    return 0


def restart(*, open_browser: bool) -> int:
    _stop_all()
    return start(open_browser=open_browser)


def open_web() -> int:
    services = _load_state()
    app = services.get("app")
    if app is None or not _process_alive(app.pid, app.process_marker):
        print("Application is not running.")
        return 1
    healthy, _ = _health(app, "app")
    if not healthy:
        print("Application is running but its health check failed. Use status or restart.")
        return 1
    webbrowser.open(app.url + "/web")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the local RAG application, LightRAG shell, and engine."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start all services")
    start_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the web UI after startup",
    )
    start_parser.add_argument(
        "--reuse-running",
        action="store_true",
        help="Open the existing web UI when all services are already running",
    )
    subparsers.add_parser("stop", help="Stop services started by this lifecycle")
    subparsers.add_parser("status", help="Show process and health status")
    status_parser = subparsers.choices["status"]
    status_parser.add_argument("--json", action="store_true", help="Print JSON")
    restart_parser = subparsers.add_parser("restart", help="Restart all services")
    restart_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the web UI after startup",
    )
    subparsers.add_parser("open", help="Open the running web UI")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "start":
            return start(
                open_browser=not args.no_browser,
                reuse_running=args.reuse_running,
            )
        if args.command == "stop":
            _stop_all()
            return 0
        if args.command == "status":
            return status(as_json=bool(args.json))
        if args.command == "restart":
            return restart(open_browser=not args.no_browser)
        if args.command == "open":
            return open_web()
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Lifecycle error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
