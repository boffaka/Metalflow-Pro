"""
MPDPMS -- Cost Models routes (CAPEX / OPEX).
CRUD for cost_models and cost_line_items.
Auto-initialises default CAPEX and OPEX models with standard mining line items
on first access for a project.
"""
from __future__ import annotations
import logging

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional


try:
    from ..auth import project_user, require_project_role
    from ..db import qone, qall, execute, conn, release, build_update_sets
    from ..helpers import get_opex_defaults
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user, require_project_role
    from db import qone, qall, execute, conn, release, build_update_sets
    from helpers import get_opex_defaults

import psycopg2.extras

logger = logging.getLogger("mpdpms.costs")

router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["costs"])

# ─── Default line items for auto-init ──────────────────────────────────────────

DEFAULT_CAPEX = [
    {"category": "Comminution (Broyage/Concassage)", "description": "SAG, broyeurs à boulets, concasseurs", "quantity": 1, "unit": "lot", "unit_cost_usd": 0, "wbs_code": "4100"},
    {"category": "Lixiviation & Adsorption CIL", "description": "Cuves CIL, pompes, tuyauterie procédé", "quantity": 1, "unit": "lot", "unit_cost_usd": 0, "wbs_code": "4200"},
    {"category": "Circuit de Gravité", "description": "Concentrateurs centrifuges, tables", "quantity": 1, "unit": "lot", "unit_cost_usd": 0, "wbs_code": "4300"},
    {"category": "Épaississement & Rejets (TSF)", "description": "Épaississeurs, parc à résidus", "quantity": 1, "unit": "lot", "unit_cost_usd": 0, "wbs_code": "4400"},
    {"category": "ADR (Élution & Électrolyse)", "description": "Colonnes élution, cellules EW, fonderie", "quantity": 1, "unit": "lot", "unit_cost_usd": 0, "wbs_code": "4500"},
    {"category": "Infrastructures & Camp", "description": "Bâtiments, routes, alimentation eau", "quantity": 1, "unit": "lot", "unit_cost_usd": 0, "wbs_code": "5000"},
    {"category": "Électricité & Distribution", "description": "Ligne HT, sous-station, distribution", "quantity": 1, "unit": "lot", "unit_cost_usd": 0, "wbs_code": "5100"},
    {"category": "Préparation Réactifs & Services", "description": "Stockage NaCN, chaux, floculant", "quantity": 1, "unit": "lot", "unit_cost_usd": 0, "wbs_code": "5200"},
    {"category": "EPCM", "description": "Ingénierie, approvisionnement, gestion", "quantity": 1, "unit": "lot", "unit_cost_usd": 0, "wbs_code": "6000"},
    {"category": "Imprévus (Contingence)", "description": "Contingence projet selon classe AACE", "quantity": 1, "unit": "lot", "unit_cost_usd": 0, "wbs_code": "7000"},
]


def _ensure_model(pid: str, model_type: str) -> dict:
    """Get or create a cost model for the project.

    Uses a single transaction for model creation + seed line items to prevent
    race-condition duplicates and partial initialization.
    """
    model = qone(
        "SELECT * FROM cost_models WHERE project_id = %s AND model_type = %s "
        "ORDER BY version DESC LIMIT 1",
        (pid, model_type),
    )
    if model:
        return model

    # Build defaults before entering the transaction
    sim = {
        r["param_key"]: float(r["param_value"])
        for r in qall(
            "SELECT param_key, param_value FROM simulation_params WHERE project_id=%s",
            (pid,),
        )
        if r["param_value"] is not None
    }

    if model_type == "CAPEX":
        defaults = DEFAULT_CAPEX
    else:
        defaults_cfg = get_opex_defaults(sim)
        p_energy = defaults_cfg["energy_rate"]
        p_nacn   = defaults_cfg["nacn_price"]
        p_cao    = defaults_cfg["cao_price"]

        aux_kwh_t = defaults_cfg["aux_energy_kwh_t"]
        c_energy  = defaults_cfg["sag_specific_energy"] + defaults_cfg["bm_specific_energy"] + aux_kwh_t
        if "energy_kwh_t" in sim: c_energy = sim["energy_kwh_t"]  # Master override
        c_nacn = defaults_cfg["nacn_kg_t"]
        c_cao  = defaults_cfg["cao_kg_t"]

        # Calculs OPEX dynamique ($/t) — tous depuis simulation_params
        cost_energy         = round(c_energy * p_energy, 2)
        cost_nacn           = round(c_nacn * p_nacn, 2)
        cost_cao            = round(c_cao * p_cao, 2)
        cost_other_reagents = defaults_cfg["opex_other_reag_usd_t"]
        cost_media          = defaults_cfg["opex_media_usd_t"]
        cost_liners         = defaults_cfg["opex_liners_usd_t"]
        cost_labor          = defaults_cfg["opex_labor_usd_t"]
        cost_maint          = defaults_cfg["opex_maint_usd_t"]
        cost_lab            = defaults_cfg["opex_lab_usd_t"]
        cost_ga             = defaults_cfg["opex_ga_usd_t"]

        defaults = [
            {"category": "Énergie électrique", "description": f"{c_energy} kWh/t @ ${p_energy}/kWh", "quantity": 1, "unit": "$/t", "unit_cost_usd": cost_energy, "wbs_code": "OP-100"},
            {"category": "Réactifs (NaCN)", "description": f"{c_nacn} kg/t @ ${p_nacn}/kg", "quantity": 1, "unit": "$/t", "unit_cost_usd": cost_nacn, "wbs_code": "OP-201"},
            {"category": "Réactifs (CaO / Chaux)", "description": f"{c_cao} kg/t @ ${p_cao}/kg", "quantity": 1, "unit": "$/t", "unit_cost_usd": cost_cao, "wbs_code": "OP-202"},
            {"category": "Réactifs (Autres)", "description": "Floculant, acide, charbon actif", "quantity": 1, "unit": "$/t", "unit_cost_usd": cost_other_reagents, "wbs_code": "OP-203"},
            {"category": "Consommables (Boulets)", "description": "Boulets de broyage acier", "quantity": 1, "unit": "$/t", "unit_cost_usd": cost_media, "wbs_code": "OP-301"},
            {"category": "Consommables (Blindages)", "description": "Blindages broyeur, grilles", "quantity": 1, "unit": "$/t", "unit_cost_usd": cost_liners, "wbs_code": "OP-302"},
            {"category": "Main d'œuvre (Opérations)", "description": "Personnel opérations usine", "quantity": 1, "unit": "$/t", "unit_cost_usd": cost_labor, "wbs_code": "OP-400"},
            {"category": "Maintenance (Pièces)", "description": "Pièces de rechange et maintenance", "quantity": 1, "unit": "$/t", "unit_cost_usd": cost_maint, "wbs_code": "OP-500"},
            {"category": "Laboratoire", "description": "Analyses chimiques, contrôle qualité", "quantity": 1, "unit": "$/t", "unit_cost_usd": cost_lab, "wbs_code": "OP-600"},
            {"category": "G&A (Frais généraux)", "description": "Administration, assurances, permis", "quantity": 1, "unit": "$/t", "unit_cost_usd": cost_ga, "wbs_code": "OP-700"},
        ]

    c = conn()
    cur = None
    try:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        for item in defaults:
            cur.execute(
                "INSERT INTO cost_line_items (model_id, category, description, quantity, unit, unit_cost_usd, source, wbs_code) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (model["id"], item["category"], item["description"], item["quantity"],
                 item["unit"], item["unit_cost_usd"], "Auto-g\u00e9n\u00e9r\u00e9 (Param\u00e8tres globaux)", item["wbs_code"]),
            )
        c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        c.rollback()
        logger.exception("cost_line_items seed failed for project=%s model_type=%s", pid, model_type)
        raise
    finally:
        if cur is not None:
            cur.close()
        release(c)
    return model


# ─── Pydantic models ──────────────────────────────────────────────────────────

class CostLineIn(BaseModel):
    category: str
    description: Optional[str] = None
    quantity: float = Field(default=1, ge=0)
    unit: Optional[str] = None
    unit_cost_usd: float = Field(default=0, ge=0)
    source: Optional[str] = None
    wbs_code: Optional[str] = None

class CostLinePatch(BaseModel):
    category: Optional[str] = None
    description: Optional[str] = None
    quantity: Optional[float] = Field(default=None, ge=0)
    unit: Optional[str] = None
    unit_cost_usd: Optional[float] = Field(default=None, ge=0)
    source: Optional[str] = None
    wbs_code: Optional[str] = None


# ─── ENDPOINTS: Cost Models ───────────────────────────────────────────────────

@router.get("/costs/{model_type}")
def get_cost_model(pid: str, model_type: str, user=Depends(project_user)):
    """Get a cost model (CAPEX or OPEX) with all its line items."""
    try:
        if model_type not in ("CAPEX", "OPEX"):
            raise HTTPException(400, "model_type doit être CAPEX ou OPEX")
        model = _ensure_model(pid, model_type)
        items = qall(
            "SELECT * FROM cost_line_items WHERE model_id = %s ORDER BY wbs_code, created_at",
            (model["id"],),
        )
        # Convert Decimal to float for JSON
        for it in items:
            for k in ("quantity", "unit_cost_usd", "total_cost_usd"):
                if it.get(k) is not None:
                    it[k] = float(it[k])
        total = sum(it["total_cost_usd"] or 0 for it in items)
        return {
            "model": {
                "id": model["id"],
                "project_id": model["project_id"],
                "model_type": model["model_type"],
                "version": model["version"],
                "notes": model["notes"],
                "updated_at": model["updated_at"],
            },
            "items": items,
            "total_usd": total,
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.post("/costs/{model_type}/lines")
def add_cost_line(pid: str, model_type: str, body: CostLineIn, user=Depends(require_project_role("Project Manager"))):
    """Add a line item to a cost model."""
    try:
        if model_type not in ("CAPEX", "OPEX"):
            raise HTTPException(400, "model_type doit être CAPEX ou OPEX")
        model = _ensure_model(pid, model_type)
        row = execute(
            "INSERT INTO cost_line_items (model_id, category, description, quantity, unit, unit_cost_usd, source, wbs_code) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (model["id"], body.category, body.description, body.quantity,
             body.unit, body.unit_cost_usd, body.source, body.wbs_code),
        )
        execute("UPDATE cost_models SET updated_at = NOW() WHERE id = %s", (model["id"],))
        for k in ("quantity", "unit_cost_usd", "total_cost_usd"):
            if row.get(k) is not None:
                row[k] = float(row[k])
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.patch("/costs/lines/{line_id}")
def patch_cost_line(pid: str, line_id: str, body: CostLinePatch, user=Depends(require_project_role("Project Manager"))):
    """Update a cost line item."""
    try:
        existing = qone(
            "SELECT cli.*, cm.project_id FROM cost_line_items cli "
            "JOIN cost_models cm ON cm.id = cli.model_id "
            "WHERE cli.id = %s AND cm.project_id = %s",
            (line_id, pid),
        )
        if not existing:
            raise HTTPException(404, "Ligne introuvable")
        fields, vals = build_update_sets(body.model_dump(exclude_none=True), allowed=frozenset(type(body).model_fields.keys()))
        if not fields:
            raise HTTPException(400, "Aucun champ à mettre à jour")
        vals.append(line_id)
        execute(f"UPDATE cost_line_items SET {', '.join(fields)} WHERE id = %s", tuple(vals))
        execute("UPDATE cost_models SET updated_at = NOW() WHERE id = %s", (existing["model_id"],))
        row = qone("SELECT * FROM cost_line_items WHERE id = %s", (line_id,))
        for k in ("quantity", "unit_cost_usd", "total_cost_usd"):
            if row.get(k) is not None:
                row[k] = float(row[k])
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.delete("/costs/lines/{line_id}")
def delete_cost_line(pid: str, line_id: str, user=Depends(require_project_role("Project Manager"))):
    """Delete a cost line item."""
    try:
        existing = qone(
            "SELECT cli.*, cm.project_id FROM cost_line_items cli "
            "JOIN cost_models cm ON cm.id = cli.model_id "
            "WHERE cli.id = %s AND cm.project_id = %s",
            (line_id, pid),
        )
        if not existing:
            raise HTTPException(404, "Ligne introuvable")
        execute("DELETE FROM cost_line_items WHERE id = %s", (line_id,))
        execute("UPDATE cost_models SET updated_at = NOW() WHERE id = %s", (existing["model_id"],))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.post("/costs/{model_type}/reset")
def reset_cost_model(pid: str, model_type: str, user=Depends(require_project_role("Project Manager"))):
    """Reset a cost model to defaults (deletes all lines and re-seeds)."""
    try:
        if model_type not in ("CAPEX", "OPEX"):
            raise HTTPException(400, "model_type doit être CAPEX ou OPEX")
        model = qone(
            "SELECT * FROM cost_models WHERE project_id = %s AND model_type = %s ORDER BY version DESC LIMIT 1",
            (pid, model_type),
        )
        if model:
            execute("DELETE FROM cost_line_items WHERE model_id = %s", (model["id"],))
            execute("DELETE FROM cost_models WHERE id = %s", (model["id"],))
        # Re-create with defaults
        return get_cost_model(pid, model_type, user)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
