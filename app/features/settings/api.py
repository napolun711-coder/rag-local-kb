"""Local-only settings API."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from app.features.settings import service
from app.features.access.dependencies import require_authorized, require_local
from app.platform.config import get_settings as get_platform_settings

router = APIRouter(tags=["settings"])


@router.get("/api/settings", dependencies=[Depends(require_authorized)])
def get_settings() -> Dict[str, Any]:
    return service.settings_payload()


@router.post("/api/settings", dependencies=[Depends(require_local)])
async def save_settings(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"messages": ["请求体不是有效 JSON"]},
        ) from exc
    try:
        return service.save_settings(payload)
    except service.SettingsError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"messages": exc.messages},
        ) from exc


@router.post("/api/settings/test", dependencies=[Depends(require_local)])
def test_settings() -> Dict[str, Any]:
    return service.test_connections()


@router.get("/api/tts/options", dependencies=[Depends(require_authorized)])
def get_tts_options() -> Dict[str, Any]:
    return service.tts_options()


def _is_private_ip(ip: str) -> bool:
    """True for RFC-1918 private addresses (10/172.16-31/192.168)."""
    if ip.startswith("10."):
        return True
    if ip.startswith("192.168."):
        return True
    if ip.startswith("172."):
        second = int(ip.split(".")[1])
        if 16 <= second <= 31:
            return True
    return False


def _default_route_interface_ip() -> str:
    """Interface IP of the best default-route whose gateway is a private LAN.

    On multi-homed hosts (Wi-Fi + VirtualBox + WSL2), UDP-connect picks the
    lowest-metric route *regardless* of whether it reaches the LAN.  We parse
    the routing table instead, skip virtual/carrier-grade gateways, and return
    the interface IP of the first private default route (lowest metric wins).
    """
    import platform
    import re
    import subprocess

    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.check_output(
                ["route", "print", "-4"],
                text=True,
                encoding="mbcs",
                errors="replace",
                timeout=5,
            )
            best_metric: int | None = None
            best_ip = ""
            for line in out.splitlines():
                parts = line.split()
                if len(parts) < 5 or parts[0] != "0.0.0.0" or parts[1] != "0.0.0.0":
                    continue
                gateway, iface = parts[2], parts[3]
                if not _is_private_ip(gateway) or not _is_private_ip(iface):
                    continue
                try:
                    metric = int(parts[4])
                except ValueError:
                    metric = 9999
                if best_metric is None or metric < best_metric:
                    best_metric = metric
                    best_ip = iface
            return best_ip

        # Linux / macOS
        cmd = ["ip", "route", "show", "default"] if system == "Linux" else ["route", "-n", "get", "default"]
        out = subprocess.check_output(cmd, text=True, timeout=5)
        if system == "Linux":
            m = re.search(r"via\s+(\S+)", out)
            if m and _is_private_ip(m.group(1)):
                dev_m = re.search(r"dev\s+(\S+)", out)
                if dev_m:
                    addr_out = subprocess.check_output(
                        ["ip", "addr", "show", dev_m.group(1)], text=True, timeout=5
                    )
                    addr_m = re.search(r"inet\s+(\S+)", addr_out)
                    if addr_m:
                        return addr_m.group(1).split("/")[0]
        return ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _lan_ip_candidates() -> list[str]:
    """All private IPv4 addresses, ordered by likelihood of being the LAN IP.

    Strategy:
      1. Ask the routing table which interface carries the private default route
         (this wins over UDP-connect on multi-homed hosts with virtual NICs).
      2. Enumerate all private IPs via getaddrinfo as fallback.
    No packets are sent to external hosts.
    """
    import socket

    route_ip = _default_route_interface_ip()

    enumerated: list[str] = []
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        pass
    else:
        for info in infos:
            ip = str(info[4][0])
            if ip in enumerated or not _is_private_ip(ip):
                continue
            enumerated.append(ip)

    candidates: list[str] = []
    if route_ip:
        candidates.append(route_ip)
    for ip in enumerated:
        if ip not in candidates:
            candidates.append(ip)
    return candidates

@router.get("/api/app/access/connect-info", dependencies=[Depends(require_local)])
def access_connect_info(request: Request) -> Dict[str, Any]:
    cfg = get_platform_settings()
    port = request.url.port or cfg.port
    ips = _lan_ip_candidates()
    if ips:
        url = f"http://{ips[0]}:{port}/web"
    else:
        # No private adapter found (offline): fall back to the request host.
        url = str(request.base_url).rstrip("/") + "/web"
    return {
        "url": url,
        "ips": ips,
        "port": port,
        "available": False,
    }


__all__ = ["router"]

