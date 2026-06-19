"""
Simple end-to-end smoke test for a running MPDPMS backend.

Usage:
    python smoke_test.py

Environment variables:
    BASE_URL        Default: http://127.0.0.1:8000
    ADMIN_EMAIL     Required
    ADMIN_PASSWORD  Required
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")


def request(method: str, path: str, payload: dict | None = None, token: str | None = None) -> tuple[int, dict | str]:
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(f"{BASE_URL}{path}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def wait_for_health() -> None:
    deadline = time.time() + 60
    last_error = None
    while time.time() < deadline:
        try:
            status, data = request("GET", "/api/v1/health")
            if status == 200 and isinstance(data, dict) and data.get("status") == "ok":
                return
            last_error = f"health returned {status}: {data}"
        except Exception as exc:  # pragma: no cover - runtime safeguard
            last_error = str(exc)
        time.sleep(2)
    raise RuntimeError(f"Backend not healthy after 60s: {last_error}")


def assert_status(actual: int, expected: int, context: str) -> None:
    if actual != expected:
        raise AssertionError(f"{context}: expected {expected}, got {actual}")


def main() -> int:
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        raise RuntimeError("Set ADMIN_EMAIL and ADMIN_PASSWORD before running smoke_test.py")

    wait_for_health()

    status, login = request(
        "POST",
        "/api/v1/auth/login",
        {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert_status(status, 200, "login")
    token = login["access_token"]

    project_code = f"SMOKE-{int(time.time())}"
    status, project = request(
        "POST",
        "/api/v1/projects",
        {
            "project_name": "Smoke Test Project",
            "project_code": project_code,
            "status": "SCOPING",
            "availability_pct": 91,
        },
        token,
    )
    assert_status(status, 201, "create project")
    pid = project["id"]

    status, me = request("GET", "/api/v1/auth/me", token=token)
    assert_status(status, 200, "auth me")
    if me.get("email") != ADMIN_EMAIL:
        raise AssertionError("auth/me returned unexpected user")

    status, _ = request("POST", f"/api/v1/projects/{pid}/simulation/params/init", token=token)
    assert_status(status, 200, "init simulation params")

    status, stage_gates = request("GET", f"/api/v1/projects/{pid}/stage-gates", token=token)
    assert_status(status, 200, "list stage gates")
    if not isinstance(stage_gates, list) or len(stage_gates) != 6:
        raise AssertionError("stage gate initialization did not create 6 stages")

    status, costs = request("GET", f"/api/v1/projects/{pid}/costs/OPEX", token=token)
    assert_status(status, 200, "get opex model")
    if "items" not in costs or "total_usd" not in costs:
        raise AssertionError("cost model response missing expected keys")

    status, block_model = request(
        "POST",
        f"/api/v1/projects/{pid}/blockmodels/",
        {"name": "Smoke Model", "x_block_size": 20, "y_block_size": 20, "z_block_size": 10},
        token,
    )
    assert_status(status, 200, "create block model")

    status, upload = request(
        "POST",
        f"/api/v1/projects/{pid}/blockmodels/{block_model['id']}/blocks",
        [
            {
                "i_index": 0,
                "j_index": 0,
                "k_index": 0,
                "x_center": 10,
                "y_center": 10,
                "z_center": 5,
                "density": 2.75,
                "volume": 1000,
                "grade_au": 1.2,
                "attributes": {"domain": "ore"},
            }
        ],
        token,
    )
    assert_status(status, 201, "upload block")
    if upload.get("count") != 1:
        raise AssertionError("block upload count mismatch")

    status, summary = request("GET", f"/api/v1/projects/{pid}/blockmodels/{block_model['id']}/summary", token=token)
    assert_status(status, 200, "block model summary")
    if int(summary.get("total_blocks", 0)) != 1:
        raise AssertionError("unexpected block summary")

    status, _ = request("DELETE", f"/api/v1/projects/{pid}", token=token)
    assert_status(status, 200, "delete project")

    print("Smoke test passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
