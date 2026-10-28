"""Listener port selection and runtime port publication."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Iterable, Iterator

MAX_PORT = 65535


def _valid_port(value: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_PORT


def candidate_ports(
    preferred: int,
    *,
    explicit: Iterable[int] = (),
    scan_limit: int = 50,
) -> Iterator[int]:
    """Yield unique candidates in deterministic order.

    Order: preferred port, configured candidates, then consecutive ports
    beginning immediately after the preferred port.
    """
    if not _valid_port(preferred):
        raise ValueError("preferred port must be between 1 and 65535")
    if not isinstance(scan_limit, int) or isinstance(scan_limit, bool) or scan_limit < 0:
        raise ValueError("scan_limit must be a non-negative integer")

    yielded: set[int] = set()

    def emit(value: int) -> Iterator[int]:
        if _valid_port(value) and value not in yielded:
            yielded.add(value)
            yield value

    yield from emit(preferred)
    for value in explicit:
        if isinstance(value, int) and not isinstance(value, bool):
            yield from emit(value)

    stop = min(MAX_PORT, preferred + scan_limit)
    for value in range(preferred + 1, stop + 1):
        yield from emit(value)


def can_bind(host: str, port: int) -> bool:
    """Return whether the host can bind the port with IPv4/IPv6 resolution."""
    try:
        addresses = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError:
        return False
    for family, socktype, protocol, _, address in addresses:
        probe = socket.socket(family, socktype, protocol)
        try:
            probe.bind(address)
            return True
        except OSError:
            continue
        finally:
            probe.close()
    return False


def select_port(
    host: str,
    preferred: int,
    *,
    explicit: Iterable[int] = (),
    scan_limit: int = 50,
) -> tuple[int, tuple[int, ...]]:
    """Return the first bindable port and the ports skipped before it."""
    skipped: list[int] = []
    for port in candidate_ports(
        preferred,
        explicit=explicit,
        scan_limit=scan_limit,
    ):
        if can_bind(host, port):
            return port, tuple(skipped)
        skipped.append(port)
    raise RuntimeError(
        "No available listener port found for "
        f"host {host!r} across {preferred} plus {scan_limit} subsequent ports"
    )


def publish_runtime_port(log_dir: Path, host: str, port: int) -> Path:
    """Atomically publish the selected port for local tooling."""
    log_dir.mkdir(parents=True, exist_ok=True)
    target = log_dir / "app.port"
    temporary = log_dir / f".app.port.{os.getpid()}.tmp"
    temporary.write_text(f"{port}\n", encoding="ascii")
    os.replace(temporary, target)
    return target