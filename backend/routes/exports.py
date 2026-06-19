"""CSV export endpoints for project data."""
from __future__ import annotations
import csv
import io
import logging
import psycopg2
import time
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

try:
    from ..auth import project_user
    from ..db import qall
except ImportError:
    from auth import project_user
    from db import qall

router = APIRouter(prefix="/api/v1/projects", tags=["exports"])

# ---------------------------------------------------------------------------
# Simple in-memory rate limiter: 10 requests per 60 seconds per user
# ---------------------------------------------------------------------------
_EXPORT_RATE_LIMIT = 10
_EXPORT_RATE_WINDOW = 60  # seconds

_export_hits: dict[str, list[float]] = defaultdict(list)


def _check_export_rate(user_id: str) -> None:
    """Raise 429 if the user has exceeded the export rate limit."""
    now = time.time()
    window_start = now - _EXPORT_RATE_WINDOW
    # Prune old entries
    _export_hits[user_id] = [t for t in _export_hits[user_id] if t > window_start]
    if len(_export_hits[user_id]) >= _EXPORT_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Export rate limit exceeded. Please wait before exporting again.",
        )
    _export_hits[user_id].append(now)

_RESOURCE_CONFIG = {
    "samples": {
        "query": (
            "SELECT sample_id_display, phase, sample_type, lithology, provenance, mass_kg, "
            "representativity, waste_rock_dilution_pct, created_at "
            "FROM lims_samples WHERE project_id=%s ORDER BY created_at"
        ),
        "columns": ["sample_id_display", "phase", "sample_type", "lithology", "provenance",
                    "mass_kg", "representativity", "waste_rock_dilution_pct", "created_at"],
    },
    "equipment": {
        "query": (
            "SELECT equipment_tag, equipment_type, power_installed_kw, design_capacity_t_h, is_long_lead "
            "FROM equipment WHERE project_id=%s ORDER BY equipment_tag"
        ),
        "columns": ["equipment_tag", "equipment_type", "power_installed_kw", "design_capacity_t_h", "is_long_lead"],
    },
    "capex": {
        "query": (
            "SELECT cli.category, cli.subcategory, cli.description, cli.quantity, cli.unit, "
            "cli.unit_cost_usd, cli.total_cost_usd, cm.currency "
            "FROM cost_line_items cli JOIN cost_models cm ON cm.id = cli.model_id "
            "WHERE cm.project_id=%s AND cm.model_type='CAPEX' ORDER BY cli.category, cli.subcategory"
        ),
        "columns": ["category", "subcategory", "description", "quantity", "unit",
                    "unit_cost_usd", "total_cost_usd", "currency"],
    },
    "opex": {
        "query": (
            "SELECT cli.category, cli.subcategory, cli.description, cli.quantity, cli.unit, "
            "cli.unit_cost_usd, cli.total_cost_usd, cm.currency "
            "FROM cost_line_items cli JOIN cost_models cm ON cm.id = cli.model_id "
            "WHERE cm.project_id=%s AND cm.model_type='OPEX' ORDER BY cli.category, cli.subcategory"
        ),
        "columns": ["category", "subcategory", "description", "quantity", "unit",
                    "unit_cost_usd", "total_cost_usd", "currency"],
    },
    "risks": {
        "query": (
            "SELECT title, category, probability, impact, risk_score, status, mitigation, owner "
            "FROM risks WHERE project_id=%s ORDER BY risk_score DESC"
        ),
        "columns": ["title", "category", "probability", "impact", "risk_score", "status", "mitigation", "owner"],
    },
}


@router.get("/{pid}/export/csv/{resource}")
def export_csv(pid: str, resource: str, user=Depends(project_user)):
    try:
        _check_export_rate(str(user["id"]))
        config = _RESOURCE_CONFIG.get(resource)
        if not config:
            raise HTTPException(404, f"Resource inconnue: {resource}. Valeurs acceptées: {list(_RESOURCE_CONFIG)}")

        rows = qall(config["query"], (pid,)) or []
        columns = config["columns"]

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            serialized = {k: (str(row[k]) if row.get(k) is not None else "") for k in columns}
            writer.writerow(serialized)

        filename = f"{resource}_{str(pid)[:8]}.csv"
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
