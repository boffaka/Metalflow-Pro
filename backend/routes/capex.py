"""CAPEX module API. Spec §7. Mutations recompute DCF synchronously."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

try:
    from ..auth import project_user
    from ..db import qone, qall, execute
    from ..services import capex as capex_service
    from ..services.circuit_templates import list_templates
    from .capex_schemas import (
        CapexModuleOut, EquipmentItemOut, FactorsOut, TotalsOut,
        EquipmentIn, EquipmentPatch, FactorsPatch, SeedRequest,
        TemplateListItem,
    )
except ImportError:  # pragma: no cover
    from auth import project_user
    from db import qone, qall, execute
    from services import capex as capex_service
    from services.circuit_templates import list_templates
    from routes.capex_schemas import (
        CapexModuleOut, EquipmentItemOut, FactorsOut, TotalsOut,
        EquipmentIn, EquipmentPatch, FactorsPatch, SeedRequest,
        TemplateListItem,
    )

router = APIRouter(prefix="/api/v1/projects", tags=["capex"])
logger = logging.getLogger("mpdpms")

# ─── Schema-name mapping ─────────────────────────────────────────────────────
# The CAPEX schemas (`category`, `name`, `typical_power_kw`) are the public
# contract. The underlying `equipment_v2` table predates the CAPEX module and
# uses (`eq_type`, `equipment_name`, `installed_kw`). We translate at the
# router boundary so routes stay aligned with the public schema.
_PATCH_COLUMN_MAP = {
    "category": "eq_type",
    "name": "equipment_name",
    "typical_power_kw": "installed_kw",
    "price_cad": "price_cad",
}

# Explicit map from FactorsPatch field -> override-flag column on
# capex_factors. Avoids string-stripping derivations and keeps the set of
# patchable factors auditable in one place.
_FACTOR_OVERRIDE_FLAGS = {
    "indirect_pct": "is_overridden_indirect",
    "epcm_pct": "is_overridden_epcm",
    "contingency_pct": "is_overridden_contingency",
}


def _project_or_404(pid: str) -> dict:
    row = qone("SELECT id, target_tph FROM projects WHERE id=%s", (pid,))
    if not row:
        raise HTTPException(404, "Project not found")
    return row


def _build_module_response(pid: str) -> CapexModuleOut:
    proj = _project_or_404(pid)
    equipment = qall(
        "SELECT id::text AS id, "
        "       eq_type AS category, "
        "       equipment_name AS name, "
        "       template_id AS template_key, "
        "       installed_kw AS typical_power_kw, "
        "       price_cad, is_long_lead AS is_overridden, template_id IS NOT NULL AS seeded_from_template, "
        "       NULL::float AS parametric_alpha, NULL::float AS parametric_beta "
        "FROM equipment_v2 WHERE project_id=%s AND enabled=true "
        "ORDER BY eq_type, equipment_name",
        (pid,),
    )
    totals = capex_service.compute_total(pid)
    return CapexModuleOut(
        circuit_type=proj.get("circuit_type", "WOL"),
        equipment=[EquipmentItemOut(**{
            **e,
            "price_cad": float(e["price_cad"] or 0),
            "typical_power_kw": float(e["typical_power_kw"]) if e.get("typical_power_kw") is not None else None,
            "parametric_alpha": float(e["parametric_alpha"]) if e.get("parametric_alpha") is not None else None,
            "parametric_beta": float(e["parametric_beta"]) if e.get("parametric_beta") is not None else None,
        }) for e in equipment],
        factors=FactorsOut(
            indirect_pct=totals["factor_pcts"]["indirect"],
            epcm_pct=totals["factor_pcts"]["epcm"],
            contingency_pct=totals["factor_pcts"]["contingency"],
            overridden=totals["overridden"],
        ),
        totals=TotalsOut(
            direct_cad=totals["direct_cad"],
            indirect_cad=totals["indirect_cad"],
            epcm_cad=totals["epcm_cad"],
            contingency_cad=totals["contingency_cad"],
            total_cad=totals["total_cad"],
        ),
    )


def _recompute_dcf_or_none(pid: str) -> dict | None:
    """Best-effort sync DCF recompute via `_dcf_core` (Task 3.4).

    Re-raises `HTTPException` so a 400 from `_dcf_core` (e.g. CAPEX-empty)
    bubbles up to the caller. Other exceptions are swallowed so a transient
    DCF failure doesn't roll back the CAPEX mutation."""
    try:
        from .economics import _dcf_core  # type: ignore
    except ImportError:  # pragma: no cover
        try:
            from routes.economics import _dcf_core  # type: ignore
        except ImportError:
            return None
    try:
        return _dcf_core(pid, payload=None)
    except HTTPException:
        raise  # propagate 400 from CAPEX-empty etc.
    except Exception as exc:  # intentional: graceful fallback on optional operation
        logger.warning("DCF recompute failed for %s: %s", pid, exc)
        return None


def _mutating_response(pid: str) -> dict:
    return {
        "capex": _build_module_response(pid).model_dump(),
        "dcf": _recompute_dcf_or_none(pid),
    }


# ─── Read ─────────────────────────────────────────────────────────────────────

@router.get("/{pid}/capex", response_model=CapexModuleOut)
def get_capex(pid: str, user=Depends(project_user)):
    return _build_module_response(pid)


@router.get("/{pid}/capex/templates", response_model=list[TemplateListItem])
def get_templates(pid: str, user=Depends(project_user)):
    _project_or_404(pid)
    return list_templates()


# ─── Equipment write ─────────────────────────────────────────────────────────

@router.post("/{pid}/capex/equipment", status_code=status.HTTP_201_CREATED)
def add_equipment(pid: str, body: EquipmentIn, user=Depends(project_user)):
    _project_or_404(pid)
    new_id = str(uuid.uuid4())
    # Synthesize the equipment_v2 NOT NULL columns the CAPEX schema doesn't
    # expose. Mirror the seed-from-template synthesis pattern in
    # services/capex.py to keep manual rows shape-compatible with seeded ones.
    # NOTE: SELECT MAX+1 then INSERT can race under concurrent adds; there is
    # no UNIQUE(project_id, seq_no) constraint on equipment_v2, so a duplicate
    # seq is non-fatal (only the equipment_tag may collide visually). Leaving
    # as-is per current schema; revisit if a unique constraint is added.
    seq_no = qone(
        "SELECT COALESCE(MAX(CAST(NULLIF(seq_no, '') AS INTEGER)), 0) + 1 AS n "
        "FROM equipment_v2 WHERE project_id=%s",
        (pid,),
    )
    seq = str(int(seq_no["n"])).zfill(3) if seq_no else "001"
    wbs_code = (body.category or "GEN")[:6].upper()
    tag = f"MAN-{seq}"
    execute(
        "INSERT INTO equipment_v2 "
        "(id, project_id, wbs_code, wbs_description, eq_type, seq_no, "
        " equipment_tag, equipment_name, price_cad, installed_kw, "
        " enabled) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)",
        (new_id, pid, wbs_code, body.name, body.category, seq, tag,
         body.name, body.price_cad,
         body.typical_power_kw if body.typical_power_kw is not None else 0),
    )
    return _mutating_response(pid)


@router.patch("/{pid}/capex/equipment/{eid}")
def patch_equipment(pid: str, eid: str, body: EquipmentPatch,
                    user=Depends(project_user)):
    _project_or_404(pid)
    existing = qone(
        "SELECT id FROM equipment_v2 WHERE id=%s AND project_id=%s",
        (eid, pid),
    )
    if not existing:
        raise HTTPException(404, "Equipment not found")
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")

    sets = []
    vals: list[Any] = []
    for k, v in updates.items():
        if k not in _PATCH_COLUMN_MAP:
            raise HTTPException(status_code=400, detail=f"Cannot update field: {k}")
        col = _PATCH_COLUMN_MAP[k]
        sets.append(f"{col} = %s")
        vals.append(v)
        # Mirror equipment_name into wbs_description so the legacy NOT NULL
        # column stays meaningful when users rename a row.
        if k == "name":
            sets.append("wbs_description = %s")
            vals.append(v)
    sets.append("updated_at = now()")
    vals.append(eid)
    execute(f"UPDATE equipment_v2 SET {', '.join(sets)} WHERE id = %s", vals)

    return _mutating_response(pid)


@router.delete("/{pid}/capex/equipment/{eid}")
def delete_equipment(pid: str, eid: str, user=Depends(project_user)):
    _project_or_404(pid)
    row = qone(
        "SELECT seeded_from_template FROM equipment_v2 "
        "WHERE id=%s AND project_id=%s",
        (eid, pid),
    )
    if not row:
        raise HTTPException(404, "Equipment not found")
    if row["seeded_from_template"]:
        raise HTTPException(409, "Cannot hard-delete a seeded equipment row. "
                                 "Disable it via PATCH enabled=false instead, "
                                 "or use POST /seed?force=true to reset the list.")
    execute("DELETE FROM equipment_v2 WHERE id=%s", (eid,))
    return _mutating_response(pid)


@router.post("/{pid}/capex/equipment/{eid}/reset")
def reset_equipment(pid: str, eid: str, user=Depends(project_user)):
    _project_or_404(pid)
    row = qone(
        "SELECT parametric_alpha, parametric_beta, seeded_from_template "
        "FROM equipment_v2 WHERE id=%s AND project_id=%s",
        (eid, pid),
    )
    if not row:
        raise HTTPException(404, "Equipment not found")
    if not row["seeded_from_template"] or row["parametric_alpha"] is None:
        raise HTTPException(400, "Reset only available on parametric (seeded) rows")
    execute(
        "UPDATE equipment_v2 SET is_overridden=false, updated_at=now() WHERE id=%s",
        (eid,),
    )
    capex_service.recompute_for_project(pid)
    return _mutating_response(pid)


# ─── Factors write ───────────────────────────────────────────────────────────

@router.patch("/{pid}/capex/factors")
def patch_factors(pid: str, body: FactorsPatch, user=Depends(project_user)):
    _project_or_404(pid)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No factor to update")
    # Ensure the row exists (newly-inserted projects get one via seed; defensive).
    execute(
        "INSERT INTO capex_factors (project_id) VALUES (%s) "
        "ON CONFLICT (project_id) DO NOTHING",
        (pid,),
    )
    sets = []
    vals: list[Any] = []
    for col, val in updates.items():
        if col not in _FACTOR_OVERRIDE_FLAGS:
            raise HTTPException(status_code=400, detail=f"Cannot update factor: {col}")
        sets.append(f"{col} = %s")
        vals.append(val)
        sets.append(f"{_FACTOR_OVERRIDE_FLAGS[col]} = true")
    sets.append("updated_at = now()")
    vals.append(pid)
    execute(f"UPDATE capex_factors SET {', '.join(sets)} WHERE project_id=%s", vals)
    return _mutating_response(pid)


@router.post("/{pid}/capex/factors/reset")
def reset_factors(pid: str, user=Depends(project_user)):
    proj = _project_or_404(pid)
    try:
        from ..services.circuit_templates import load_template
    except ImportError:  # pragma: no cover
        from services.circuit_templates import load_template
    template = load_template(proj.get("circuit_type", "WOL"))
    defaults = template.get("default_factors", {})
    execute(
        "UPDATE capex_factors SET indirect_pct=%s, epcm_pct=%s, contingency_pct=%s, "
        "  is_overridden_indirect=false, is_overridden_epcm=false, "
        "  is_overridden_contingency=false, updated_at=now() "
        "WHERE project_id=%s",
        (
            float(defaults.get("indirect_pct", 0.30)),
            float(defaults.get("epcm_pct", 0.15)),
            float(defaults.get("contingency_pct", 0.15)),
            pid,
        ),
    )
    return _mutating_response(pid)


# ─── Seed ────────────────────────────────────────────────────────────────────

@router.post("/{pid}/capex/seed")
def seed(pid: str, body: SeedRequest, user=Depends(project_user)):
    _project_or_404(pid)
    try:
        capex_service.seed_from_template(pid, body.circuit_type, force=body.force)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _mutating_response(pid)
