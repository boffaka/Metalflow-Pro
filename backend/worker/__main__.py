"""Entry point: `python -m backend.worker`.

Wires logging, registers SIGTERM/SIGINT handlers that flip the stop event,
and calls loop.run() on the main thread.
"""
from __future__ import annotations

import logging
import signal
import threading

try:
    from worker import loop
    from worker import handlers  # noqa: F401 — populates registry on import
    from observability import configure_logging
    from settings import get_settings
except ImportError:  # pragma: no cover
    from backend.worker import loop
    from backend.worker import handlers  # noqa: F401
    from backend.observability import configure_logging
    from backend.settings import get_settings


def main() -> None:
    configure_logging()
    log = logging.getLogger("mpdpms.worker")
    settings = get_settings()
    if not settings.worker_enabled:
        log.warning("WORKER_ENABLED=false — worker exiting immediately")
        return

    stop_event = threading.Event()

    def _on_signal(signum, frame):
        log.info("[worker] received signal %s — initiating shutdown", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    loop.run(stop_event=stop_event)


if __name__ == "__main__":
    main()
