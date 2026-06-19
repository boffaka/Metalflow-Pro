#!/usr/bin/env python3
"""Dump FastAPI's OpenAPI schema to a JSON file without starting the server.

Used by frontend/scripts/gen-api-types.sh to keep frontend type definitions
in sync with the backend.

Usage
-----
    cd backend && python scripts/export_openapi.py [path]

Default output: backend/openapi.json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Provide minimum env so main.py's _check_required_env_vars passes. We only
# need the spec — no DB or auth wiring is exercised.
os.environ.setdefault("DATABASE_URL", "postgresql://placeholder:placeholder@localhost:5432/placeholder")
os.environ.setdefault("JWT_SECRET", "placeholder-secret-not-used-for-spec-export-32chars")
os.environ.setdefault("ADMIN_EMAIL", "placeholder@example.com")
os.environ.setdefault("ADMIN_PASSWORD", "PlaceholderP@ss1")
os.environ.setdefault("AUTO_MIGRATE", "0")
os.environ.setdefault("BOOTSTRAP_SCHEMA", "0")

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
# Both paths: backend/ for `from routes...` and repo root for `from backend.celery_app...`
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else BACKEND / "openapi.json"

    from main import app  # noqa: E402 — import after env setup

    schema = app.openapi()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"Wrote OpenAPI schema: {out} ({len(schema.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
