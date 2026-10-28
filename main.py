"""ASGI entrypoint and resilient local launcher."""

from __future__ import annotations

import sys

from app.main import app

__all__ = ["app"]


def run() -> None:
    import uvicorn

    from app.platform.config import get_settings
    from app.platform.logging import setup_logging
    from app.platform.paths import ensure_runtime_dirs
    from app.platform.ports import publish_runtime_port, select_port

    settings = get_settings()
    ensure_runtime_dirs(
        (settings.data_dir, settings.knowledge_dir, settings.log_dir)
    )
    logger = setup_logging(settings.log_level, settings.log_dir)

    try:
        port, skipped = select_port(
            settings.host,
            settings.port,
            explicit=settings.port_candidates,
            scan_limit=settings.port_scan_limit,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        logger.error("Unable to select a listener port: %s", exc)
        raise SystemExit(2) from exc

    if skipped:
        logger.warning(
            "Preferred port unavailable; skipped %s and selected %d",
            ", ".join(str(value) for value in skipped),
            port,
        )
    port_file = publish_runtime_port(settings.log_dir, settings.host, port)
    logger.info("Serving on http://%s:%d (runtime port file: %s)", settings.host, port, port_file)

    uvicorn.run(app, host=settings.host, port=port)


if __name__ == "__main__":
    run()