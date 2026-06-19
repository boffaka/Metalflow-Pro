"""Deploy-visible build identifiers (single source of truth).

Override at release time with env ``METALFLOW_BUILD_ID`` (CI/CD, Docker build-args).
"""
from __future__ import annotations

import os

# Default updated when shipping user-visible bundles (monolith HTML + API root headers).
_raw_build = os.getenv("METALFLOW_BUILD_ID", "").strip()
APP_BUILD_ID: str = _raw_build or "2026-05-20-fix-econ-sentinels-v1"
