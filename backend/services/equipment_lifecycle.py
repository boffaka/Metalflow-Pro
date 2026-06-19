"""Equipment lifecycle staleness propagation.

When equipment_v2 items are mutated (PATCH, POST, DELETE, auto-generate, purge),
this helper invokes the existing pipeline cascade so downstream modules
(opex, economics, risks per `STALE_CASCADE`) are flagged as stale.

Why: NI 43-101 §5.5 requires that derived computations are auto-flagged when
their sources change. The cascade infrastructure exists in
`routes.pipeline.mark_stale_cascade` and is invoked from
design_criteria / lims / mass_balance / flowsheet endpoints, but
equipment_v2 endpoints currently bypass it — leaving downstream modules
silently out-of-date after every motor_kw / price_cad / specs edit.

Failure modes (cascade error, audit error) are swallowed so the originating
HTTP request is never blocked by traceability bookkeeping.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("mpdpms.services.equipment_lifecycle")


# Defense-in-depth: bound the change_summary length to keep audit_events
# rows small even if a future caller passes an unbounded string.
_CHANGE_SUMMARY_MAX_LEN = 500


def trigger_equipment_cascade(
    *,
    project_id: str,
    user_id: Optional[str],
    change_summary: str,
) -> list[str]:
    """Mark downstream modules stale after an equipment change.

    Returns the list of downstream module codes that were marked stale.
    Returns [] (and logs a warning) on cascade failure.
    """
    if change_summary and len(change_summary) > _CHANGE_SUMMARY_MAX_LEN:
        change_summary = change_summary[: _CHANGE_SUMMARY_MAX_LEN - 1] + "…"

    cascaded: list[str] = []
    try:
        try:
            from routes.pipeline import mark_stale_cascade
        except ImportError:  # pragma: no cover - relative-import fallback
            from ..routes.pipeline import mark_stale_cascade  # type: ignore[no-redef]
        cascaded = mark_stale_cascade(project_id, "equipment", user_id=user_id)
    except Exception as e:  # never block the originating request
        logger.warning(
            "equipment cascade failed for project %s (change=%s): %s",
            project_id, change_summary, e,
        )
        return []

    if cascaded:
        try:
            try:
                from audit import record_event
            except ImportError:  # pragma: no cover - relative-import fallback
                from ..audit import record_event  # type: ignore[no-redef]
            record_event(
                user_id=user_id,
                project_id=project_id,
                entity_type="staleness",
                entity_id=None,
                action="mark_stale_cascade",
                field_name="equipment",
                new_value={
                    "cascaded_to": cascaded,
                    "change_summary": change_summary,
                },
                source="equipment_lifecycle",
            )
        except Exception as e:
            # Audit-chain break is more serious than a failed mutation for
            # NI 43-101 conformance: the next audit row's previous_checksum
            # will diverge from what's expected. Surface the break loudly
            # so operators can detect it (grep `audit_chain_break=True`).
            logger.error(
                "equipment cascade audit-event failed for project %s: %s",
                project_id, e,
                extra={
                    "audit_chain_break": True,
                    "project_id": project_id,
                    "cascaded_to": cascaded,
                    "change_summary": change_summary,
                },
            )

    return cascaded
