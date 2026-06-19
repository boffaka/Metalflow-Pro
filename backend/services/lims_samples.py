"""LIMS samples service — pilote for the services/ layer.

Encapsulates the business logic for creating LIMS samples: persistence
through a single transaction, plus deferred pipeline cascade signal that
only fires after a successful commit.
"""
from __future__ import annotations

import logging
from typing import Any

try:
    from services.transaction import register_after_commit, transaction
except ImportError:  # pragma: no cover
    from backend.services.transaction import register_after_commit, transaction

logger = logging.getLogger("mpdpms.services.lims_samples")


_INSERT_SAMPLE_SQL = """
INSERT INTO lims_samples (
    project_id, sample_id_display, phase, sample_type, lithology,
    provenance, mass_kg, representativity, waste_rock_dilution_pct,
    source_horizon, depth_interval, total_mass_kg, sent_mass_kg,
    collection_date, reception_date, collection_method, qaqc_protocol,
    crm_standard, duplicate_freq, blank_freq, packaging,
    oxidation_state, domain, status, observations, sort_order
) VALUES (
    %(project_id)s, %(sample_id_display)s, %(phase)s, %(sample_type)s, %(lithology)s,
    %(provenance)s, %(mass_kg)s, %(representativity)s, %(waste_rock_dilution_pct)s,
    %(source_horizon)s, %(depth_interval)s, %(total_mass_kg)s, %(sent_mass_kg)s,
    %(collection_date)s, %(reception_date)s, %(collection_method)s, %(qaqc_protocol)s,
    %(crm_standard)s, %(duplicate_freq)s, %(blank_freq)s, %(packaging)s,
    %(oxidation_state)s, %(domain)s, %(status)s, %(observations)s, %(sort_order)s
) RETURNING *
"""

_SAMPLE_FIELDS = (
    "sample_id_display", "phase", "sample_type", "lithology",
    "provenance", "mass_kg", "representativity", "waste_rock_dilution_pct",
    "source_horizon", "depth_interval", "total_mass_kg", "sent_mass_kg",
    "collection_date", "reception_date", "collection_method", "qaqc_protocol",
    "crm_standard", "duplicate_freq", "blank_freq", "packaging",
    "oxidation_state", "domain", "status", "observations",
)


def _signal_pipeline(pid: str, user_id: str | None) -> None:
    """Best-effort cascade signal — never blocks the caller."""
    try:
        try:
            from routes.pipeline import set_status, mark_stale_cascade
        except ImportError:
            from backend.routes.pipeline import set_status, mark_stale_cascade
        set_status(pid, "lims", "complete", user_id=user_id, triggered_by="lims_write")
        mark_stale_cascade(pid, "lims", user_id=user_id)
    except Exception:
        logger.exception("pipeline cascade signal failed")


def create_sample(
    project_id: str,
    sample: Any,
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Create a LIMS sample and signal the pipeline cascade post-commit.

    `sample` is a pydantic SampleIn (or any object with attribute access for
    every field in _SAMPLE_FIELDS). Returns the inserted row as a dict.
    """
    params: dict[str, Any] = {"project_id": project_id}
    for field in _SAMPLE_FIELDS:
        params[field] = getattr(sample, field, None)

    with transaction() as cur:
        cur.execute(_INSERT_SAMPLE_SQL, params)
        cols = [d[0] for d in cur.description]
        row = dict(zip(cols, cur.fetchone()))

        register_after_commit(lambda: _signal_pipeline(project_id, user_id))
        return row


def create_samples_bulk(
    project_id: str,
    samples: list[Any],
    *,
    user_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create many LIMS samples in a single transaction and fire the cascade
    signal ONCE post-commit. Per-row failures (bad dates, constraint violations)
    are isolated via SAVEPOINT so one bad row doesn't kill the whole batch.

    Returns (accepted_rows, rejected_rows). Each rejected row carries
    {"index": int, "error": str}.
    """
    if not samples:
        return [], []

    # Find the current max sort_order so this batch appends after existing
    # samples instead of restarting at 0.
    from db import qone as _qone  # late import for both module/script paths
    base_offset = 0
    try:
        row = _qone("SELECT COALESCE(MAX(sort_order), -1) AS m FROM lims_samples WHERE project_id=%s", (project_id,))
        if row and row.get("m") is not None:
            base_offset = int(row["m"]) + 1
    except Exception:
        try:
            from backend.db import qone as _qone2
            row = _qone2("SELECT COALESCE(MAX(sort_order), -1) AS m FROM lims_samples WHERE project_id=%s", (project_id,))
            if row and row.get("m") is not None:
                base_offset = int(row["m"]) + 1
        except Exception:
            pass

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    with transaction() as cur:
        for idx, sample in enumerate(samples):
            params: dict[str, Any] = {"project_id": project_id, "sort_order": base_offset + idx}
            for field in _SAMPLE_FIELDS:
                params[field] = getattr(sample, field, None)
            cur.execute("SAVEPOINT row")
            try:
                cur.execute(_INSERT_SAMPLE_SQL, params)
                cols = [d[0] for d in cur.description]
                accepted.append(dict(zip(cols, cur.fetchone())))
                cur.execute("RELEASE SAVEPOINT row")
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT row")
                msg = str(e).splitlines()[0][:200]
                # Log first 3 rejections so we can debug template mismatches
                # without forcing users to copy/paste toasts.
                if idx < 3:
                    logger.warning("sample row %d rejected: %s | display=%r",
                                   idx, msg, params.get("sample_id_display"))
                rejected.append({"index": idx, "error": msg})

        if accepted:
            register_after_commit(lambda: _signal_pipeline(project_id, user_id))
    return accepted, rejected
