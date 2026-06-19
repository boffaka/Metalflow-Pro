"""
LIMS lookup helper — provides per-op_code default values for the flowsheet tree
when a node has not been manually populated.

Single batched call per project: fetch_op_defaults(project_id) returns a dict
keyed by op_code prefix, with sub-keys 'recovery_pct', 'throughput_tph',
'water_m3h', 'grade_au_gt' (only those that LIMS data can provide).

Mapping:
  FLOTATION_*       → recovery_pct = AVG(lims_flotation.au_recovery_pct)
  LEACH_CIL, CIP    → recovery_pct = AVG(lims_kinetics.rec_24h)
  GRAVITY_*         → recovery_pct = AVG(lims_kinetics.rec_24h)   (best available proxy)

Throughput, water and grade are not derived from LIMS in v1 — they remain
manual-entry only. This file is intentionally narrow; extend as new mappings
are validated.
"""
from __future__ import annotations

import logging
from typing import Optional

try:
    from .db import qone
except ImportError:  # pragma: no cover
    from db import qone


logger = logging.getLogger("mpdpms.lims_lookup")


def _avg_or_none(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_op_defaults(project_id: str) -> dict[str, dict[str, float]]:
    """Pre-load LIMS-derived default values for the project, in one call per source.

    Returns a dict whose keys are *op_code prefixes* matched as:
      - exact: 'LEACH_CIL', 'LEACH_CIP'
      - prefix: 'FLOTATION', 'GRAVITY'

    The caller resolves a node's op_code against these via _resolve_op below.
    """
    defaults: dict[str, dict[str, float]] = {}

    # ─── Flotation ────────────────────────────────────────────────────────
    flot = qone(
        "SELECT AVG(au_recovery_pct) AS rec FROM lims_flotation WHERE project_id=%s",
        (project_id,),
    )
    rec = _avg_or_none(flot["rec"]) if flot else None
    if rec is not None:
        defaults["FLOTATION"] = {"recovery_pct": rec}

    # ─── Kinetics (used for CIL, CIP, gravity proxy) ──────────────────────
    kin = qone(
        "SELECT AVG(rec_24h) AS rec FROM lims_kinetics WHERE project_id=%s",
        (project_id,),
    )
    rec24 = _avg_or_none(kin["rec"]) if kin else None
    if rec24 is not None:
        defaults["LEACH_CIL"] = {"recovery_pct": rec24}
        defaults["LEACH_CIP"] = {"recovery_pct": rec24}
        defaults["GRAVITY"] = {"recovery_pct": rec24}

    return _ResolvingDict(defaults)


class _ResolvingDict(dict):
    """Dict subclass: __getitem__/get(op_code) tries exact match first,
    then falls back to longest matching prefix."""

    def get(self, key, default=None):  # type: ignore[override]
        if key in self:
            return super().__getitem__(key)
        # Try longest prefix
        best: Optional[str] = None
        for k in self.keys():
            if isinstance(key, str) and key.startswith(k):
                if best is None or len(k) > len(best):
                    best = k
        if best is not None:
            return super().__getitem__(best)
        return default

    def __getitem__(self, key):  # type: ignore[override]
        v = self.get(key)
        if v is None:
            raise KeyError(key)
        return v
