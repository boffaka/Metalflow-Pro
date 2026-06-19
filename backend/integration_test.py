"""
PostgreSQL-backed integration test runner for MPDPMS.

This script assumes a reachable PostgreSQL database and runs:
1. Alembic migrations
2. Uvicorn server startup
3. End-to-end smoke test

Usage:
    python integration_test.py
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(ROOT, "backend")
ENV = {
    **os.environ,
    "PYTHONPATH": ROOT,
    "AUTO_MIGRATE": "0",
    "BOOTSTRAP_SCHEMA": "0",
    "BASE_URL": os.getenv("BASE_URL", "http://127.0.0.1:8000"),
}


def _run(cmd: list[str], *, cwd: str | None = None) -> None:
    subprocess.run(cmd, cwd=cwd or ROOT, env=ENV, check=True)


def _wait_for_tcp(url: str, timeout_s: int = 60) -> None:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout_s
    last_error = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/v1/health", timeout=5) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # pragma: no cover - runtime safeguard
            last_error = exc
        time.sleep(1)
    raise RuntimeError(f"Backend did not become healthy in {timeout_s}s: {last_error}")


def main() -> int:
    _run([sys.executable, os.path.join(BACKEND_DIR, "manage_db.py"), "upgrade"])

    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=ROOT,
        env=ENV,
    )

    try:
        _wait_for_tcp(ENV["BASE_URL"])
        _run([sys.executable, os.path.join(BACKEND_DIR, "smoke_test.py")])
    finally:
        if server.poll() is None:
            server.send_signal(signal.SIGTERM)
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
