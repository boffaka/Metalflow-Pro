"""
MPDPMS — Professional OPEX v2 API routes.

6-section OPEX model: manpower, power, reagents/consumables, mobile equipment,
general inputs, and consolidated summary.
"""
from __future__ import annotations

import logging
import unicodedata
import psycopg2

from fastapi import APIRouter, HTTPException, Depends

try:
    from ..auth import project_user
    from ..db import qone, qall, execute, conn, release, build_update_sets
    from ..models import (
        OpexInputPatch, ManpowerIn, ManpowerPatch,
        ReagentIn, ReagentPatch, MobileIn, MobilePatch, PowerPatch,
    )
except ImportError:  # pragma: no cover
    from auth import project_user

try:
    from ..constants import TROY_OZ_PER_GRAM
except ImportError:  # pragma: no cover - supports direct script imports
    from constants import TROY_OZ_PER_GRAM
    from db import qone, qall, execute, conn, release, build_update_sets
    from models import (
        OpexInputPatch, ManpowerIn, ManpowerPatch,
        ReagentIn, ReagentPatch, MobileIn, MobilePatch, PowerPatch,
    )

router = APIRouter(prefix="/api/v1/projects/{pid}/opex-v2", tags=["opex-v2"])
logger = logging.getLogger("mpdpms.opex_v2")


def _ascii_fold(s: str) -> str:
    """Lowercase, strip accents — for matching French `opex_reagents.category` labels."""
    if not s:
        return ""
    nfd = unicodedata.normalize("NFD", str(s).lower())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def _opex_reagent_summary_bucket(category: str | None) -> str:
    """
    Map DB category → buckets used in GET /summary.

    `auto_generate` uses French labels (Réactifs de…, Médias de broyage, Consommables).
    Older rows may use English BALLS, LINERS, REAGENTS, DEWATERING, …
    """
    n = _ascii_fold(category or "")
    if n in ("balls", "liners"):
        return "grinding"
    if "broyage" in n and ("media" in n or "medias" in n):
        return "grinding"
    if n in ("dewatering", "assay", "others") or n.startswith("consommables"):
        return "consumables"
    if n == "reagents":
        return "reagents"
    return "reagents"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OFFICE_HOURS_YEAR = 2080
SHIFT_HOURS_YEAR = 3128  # 10-4 rotation, 365 days

DEFAULT_INPUTS = [
    ("power_cost_kwh", "Coût électricité", 0.092, "CAD/kWh", "Default"),
    ("fuel_gasoline", "Essence", 1.045, "CAD/L", "Default"),
    ("fuel_diesel", "Diesel", 0.930, "CAD/L", "Default"),
    ("labour_benefits_pct", "Avantages sociaux", 20, "%", "Default"),
    ("labour_bonus_pct", "Bonus", 5, "%", "Default"),
    ("annual_throughput", "Débit annuel", 0, "t/an", "Project"),
    ("plant_availability_pct", "Disponibilité usine", 92, "%", "Default"),
    ("crushing_availability_pct", "Disponibilité concassage", 75, "%", "Default"),
    ("recovery_pct", "Récupération Au", 0, "%", "LIMS"),
]

TYPICAL_MANPOWER = [
    # (dept, description, category, schedule, count, hourly_rate)
    ("Mill Administration", "Mill Superintendent", "Staff", "Office", 1, 124),
    ("Mill Administration", "Chief Metallurgist", "Staff", "Office", 1, 102),
    ("Mill Administration", "Safety & Training Officer", "Staff", "Office", 1, 72),
    ("Mill Administration", "Environmental Officer", "Staff", "Office", 1, 72),
    ("Mill Administration", "Mill Clerk / Secretary", "Staff", "Office", 1, 44),
    ("Mill Administration", "Warehouse Supervisor", "Staff", "Office", 1, 56),
    ("Mill Administration", "Warehouse Worker", "Hourly", "Office", 2, 32),
    ("Mill Administration", "Security Guard", "Hourly", "Shift", 4, 28),
    ("Operations", "Chief Operator / Shift Boss", "Staff", "Shift", 4, 62),
    ("Operations", "Crusher & Stockpile Operator", "Hourly", "Shift", 4, 36),
    ("Operations", "Control Room Operator", "Hourly", "Shift", 4, 40),
    ("Operations", "SAG / Ball Mill Operator", "Hourly", "Shift", 4, 38),
    ("Operations", "Gravity Operator", "Hourly", "Shift", 4, 36),
    ("Operations", "Leach / CIL Operator", "Hourly", "Shift", 4, 36),
    ("Operations", "Elution / Goldroom Operator", "Hourly", "Shift", 4, 38),
    ("Operations", "Tailings & Water Operator", "Hourly", "Shift", 4, 34),
    ("Operations", "Reagent Mixing Operator", "Hourly", "Shift", 4, 34),
    ("Operations", "General Helper / Utility", "Hourly", "Shift", 4, 30),
    ("Maintenance", "Maintenance Superintendent", "Staff", "Office", 1, 96),
    ("Maintenance", "Maintenance Planner", "Staff", "Office", 1, 72),
    ("Maintenance", "Mechanical Supervisor", "Staff", "Office", 1, 78),
    ("Maintenance", "Electrical Supervisor", "Staff", "Office", 1, 78),
    ("Maintenance", "Instrumentation Supervisor", "Staff", "Office", 1, 78),
    ("Maintenance", "Millwright (Mechanical)", "Hourly", "Shift", 4, 48),
    ("Maintenance", "Welder / Fabricator", "Hourly", "Office", 2, 46),
    ("Maintenance", "Electrician", "Hourly", "Shift", 4, 48),
    ("Maintenance", "Instrument Technician", "Hourly", "Shift", 4, 48),
    ("Maintenance", "Mechanical Helper", "Hourly", "Shift", 4, 34),
    ("Maintenance", "Crane / Mobile Equip. Operator", "Hourly", "Office", 2, 42),
    ("Met & Lab", "Senior Metallurgist", "Staff", "Office", 1, 88),
    ("Met & Lab", "Process Engineer", "Staff", "Office", 1, 78),
    ("Met & Lab", "Lab Supervisor", "Staff", "Office", 1, 62),
    ("Met & Lab", "Lab Technician", "Hourly", "Shift", 4, 36),
    ("Met & Lab", "Sample Prep Technician", "Hourly", "Shift", 4, 32),
]

MANPOWER_PATCH_FIELDS = {
    "department", "description", "category", "schedule",
    "num_employees", "base_salary_hourly", "bonus_pct",
    "benefits_pct", "overtime_pct", "sort_order",
}

REAGENT_PATCH_FIELDS = {
    "category", "description", "unit_consumption", "consumption_rate",
    "yearly_consumption", "unit_cost_cad", "source", "sort_order",
}

POWER_PATCH_FIELDS = {
    "wbs_code", "wbs_description", "operating_kw", "electrical_efficiency",
    "load_factor", "area_availability", "hours_per_day", "sort_order",
}

# Enabled circuit_operations.op_code values (upper) used to gate OPEX reagent lines
_FLOTATION_OPS = frozenset({
    "FLOTATION_ROUGHER", "FLOTATION_SCAVENGER", "FLOTATION_CLEANER", "FLOTATION_COLONNE",
})
_LEACH_OPS = frozenset({
    "CIL", "CIP", "LEACH_CUVES", "HEAP_LEACH", "VAT_LEACH", "PREAERATION",
})
_CARBON_OPS = frozenset({"CIL", "CIP"})
_THICKENER_OPS = frozenset({"EPAISSISSEUR", "EPAISSISSEUR_HD", "EPAISSISSEUR_CONC"})
_SAG_MEDIA_OPS = frozenset({"SAG_MILL"})
_BALL_MEDIA_OPS = frozenset({"BALL_MILL", "ROD_MILL", "VERTIMILL", "ISAMILL", "VERTIMILL_REGRIND", "SMD", "UFG"})


def _project_enabled_op_codes(pid: str) -> set[str]:
    """Distinct enabled op_codes (upper) for all circuit_templates of this project."""
    try:
        rows = qall(
            "SELECT DISTINCT upper(trim(op_code)) AS op_code "
            "FROM circuit_operations "
            "WHERE template_id IN (SELECT id FROM circuit_templates WHERE project_id=%s) "
            "AND enabled = true AND op_code IS NOT NULL",
            (pid,),
        )
    except Exception:  # pragma: no cover — optional table / permissions
        rows = []
    return {str(r["op_code"]).strip() for r in (rows or []) if r.get("op_code")}


def _build_opex_reagent_defs(sim_params: dict[str, float], ops: set[str]) -> list[tuple[str, str, str, float, float, str]]:
    """
    Build (category, description, unit, rate, unit_cost_cad, source) rows aligned with
    enabled circuit operations. If no operations are recorded, flotation is still omitted
    (avoids inventing a flotation plant); other blocks default to on like before.
    """
    empty = len(ops) == 0
    has_flotation = (not empty) and bool(ops & _FLOTATION_OPS)
    has_leach = empty or bool(ops & _LEACH_OPS)
    has_detox = empty or any(o.startswith("DETOX_") for o in ops)
    has_carbon = empty or bool(ops & _CARBON_OPS)
    has_thickener = empty or bool(ops & _THICKENER_OPS)
    has_sag = empty or bool(ops & _SAG_MEDIA_OPS)
    has_ball = empty or bool(ops & _BALL_MEDIA_OPS)

    defs: list[tuple[str, str, str, float, float, str]] = []

    if has_leach:
        defs.extend(
            [
                (
                    "Réactifs de lixiviation",
                    "Cyanure de sodium (NaCN)",
                    "kg/t",
                    sim_params.get("nacn_kg_t", 0.5),
                    3.50,
                    "DC/LIMS",
                ),
                (
                    "Réactifs de lixiviation",
                    "Chaux vive (CaO)",
                    "kg/t",
                    sim_params.get("cao_kg_t", 1.2),
                    0.12,
                    "DC/LIMS",
                ),
            ]
        )

    if has_flotation:
        defs.extend(
            [
                (
                    "Réactifs de flottation",
                    "Collecteur PAX",
                    "g/t",
                    sim_params.get("pax_dosage", 135) / 1000,
                    4.80,
                    "DC",
                ),
                (
                    "Réactifs de flottation",
                    "Moussant MIBC",
                    "g/t",
                    sim_params.get("mibc_dosage", 71) / 1000,
                    3.20,
                    "DC",
                ),
                (
                    "Réactifs de flottation",
                    "Dépresseur CMC",
                    "g/t",
                    sim_params.get("cmc_dosage", 65) / 1000,
                    2.50,
                    "DC",
                ),
            ]
        )

    if has_detox:
        defs.extend(
            [
                (
                    "Réactifs de détox",
                    "Métabisulfite de sodium (SMBS)",
                    "kg/t",
                    sim_params.get("smbs_kg_t", 0.16),
                    0.85,
                    "DC",
                ),
                (
                    "Réactifs de détox",
                    "Sulfate de cuivre (CuSO4)",
                    "g/t",
                    sim_params.get("cuso4_g_t", 15) / 1000,
                    1.80,
                    "DC",
                ),
            ]
        )

    if has_thickener:
        defs.append(
            (
                "Épaississeur",
                "Floculant",
                "g/t",
                sim_params.get("flocculant_g_t", 35) / 1000,
                3.00,
                "DC/LIMS",
            )
        )

    if has_sag:
        defs.append(
            (
                "Médias de broyage",
                "Boulets de broyage (SAG)",
                "kg/t",
                sim_params.get("sag_media_kg_t", 0.8),
                1.20,
                "Industry",
            )
        )
    if has_ball:
        defs.append(
            (
                "Médias de broyage",
                "Boulets de broyage (Ball Mill)",
                "kg/t",
                sim_params.get("bm_media_kg_t", 1.5),
                1.10,
                "Industry",
            )
        )
    if has_carbon:
        defs.append(
            (
                "Médias de broyage",
                "Charbon actif",
                "kg/t",
                sim_params.get("carbon_makeup_kg_t", 0.04),
                2.80,
                "DC",
            )
        )

    defs.extend(
        [
            ("Consommables", "Revêtements de broyeur", "$/t", 1.0, 1.0, "Industry"),
            ("Consommables", "Filtres & membranes", "$/t", 0.3, 1.0, "Industry"),
            ("Consommables", "Huiles & lubrifiants", "$/t", 0.15, 1.0, "Industry"),
        ]
    )
    return defs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ensure_inputs(pid: str):
    """Seed default opex_inputs for a project if none exist."""
    existing = qone("SELECT count(*) as cnt FROM opex_inputs WHERE project_id=%s", (pid,))
    if existing and existing["cnt"] > 0:
        return
    # Try to get throughput and recovery from project
    proj = qone("SELECT target_tph FROM projects WHERE id=%s", (pid,))
    annual_tp = 0
    if proj and proj.get("target_tph"):
        annual_tp = float(proj["target_tph"]) * 8760 * 0.92  # tph * hours * availability
    recovery = 0
    try:
        lims = qone("""
            SELECT design_value FROM design_criteria_v2
            WHERE project_id=%s AND item ILIKE '%recovery%gold%'
            LIMIT 1
        """, (pid,))
        if lims and lims.get("design_value"):
            recovery = float(lims["design_value"])
    except Exception:  # intentional: fallback to default value
        # Fallback: try simulation_params
        try:
            sp = qone("SELECT param_value FROM simulation_params WHERE project_id=%s AND param_key='overall_rec_au' LIMIT 1", (pid,))
            if sp and sp.get("param_value"):
                recovery = float(sp["param_value"])
        except Exception:  # intentional: fallback to default value
            recovery = 90  # safe default

    c = conn()
    cur = None
    try:
        cur = c.cursor()
        for key, label, val, unit, src in DEFAULT_INPUTS:
            actual_val = val
            if key == "annual_throughput" and annual_tp > 0:
                actual_val = round(annual_tp, 0)
            if key == "recovery_pct" and recovery > 0:
                actual_val = recovery
            cur.execute("""
                INSERT INTO opex_inputs (project_id, param_key, param_label, param_value, param_unit, source)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (project_id, param_key) DO NOTHING
            """, (pid, key, label, actual_val, unit, src))
        c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        c.rollback()
        raise
    finally:
        if cur:
            cur.close()
        release(c)


def _get_input_val(pid: str, key: str, default=0):
    row = qone("SELECT param_value FROM opex_inputs WHERE project_id=%s AND param_key=%s", (pid, key))
    return float(row["param_value"]) if row and row.get("param_value") is not None else default


def _get_all_inputs(pid: str) -> dict:
    """Load all opex_inputs for a project in one query — avoids N+1 when multiple keys needed."""
    rows = qall("SELECT param_key, param_value FROM opex_inputs WHERE project_id=%s", (pid,))
    return {r["param_key"]: float(r["param_value"]) for r in rows if r["param_value"] is not None}


def _calc_manpower_row(row: dict) -> dict:
    """Recalculate total_salary and total_cost for a manpower row."""
    hourly = float(row.get("base_salary_hourly") or 0)
    hrs = SHIFT_HOURS_YEAR if row.get("schedule") == "Shift" else OFFICE_HOURS_YEAR
    base_annual = hourly * hrs
    bonus = base_annual * float(row.get("bonus_pct") or 0) / 100
    benefits = base_annual * float(row.get("benefits_pct") or 0) / 100
    ot = base_annual * float(row.get("overtime_pct") or 0) / 100
    total_salary = base_annual + bonus + benefits + ot
    num = int(row.get("num_employees") or 1)
    return {"total_salary": round(total_salary, 2), "total_cost": round(total_salary * num, 2)}


def _calc_power_row(row: dict, power_cost: float, annual_tp: float) -> dict:
    """Recalculate derived power fields."""
    kw = float(row.get("operating_kw") or 0)
    eff = float(row.get("electrical_efficiency") or 0.92)
    lf = float(row.get("load_factor") or 0.80)
    avail = float(row.get("area_availability") or 0.92)
    hpd = float(row.get("hours_per_day") or 22)
    hours_year = hpd * 365 * avail
    consumption = kw * eff * lf * hours_year
    total_cost = consumption * power_cost
    kwh_mt = consumption / annual_tp if annual_tp > 0 else 0
    cost_mt = total_cost / annual_tp if annual_tp > 0 else 0
    return {
        "hours_per_year": round(hours_year, 1),
        "consumption_kwh_year": round(consumption, 0),
        "consumption_kwh_mt": round(kwh_mt, 2),
        "total_cost": round(total_cost, 2),
        "unit_cost_mt": round(cost_mt, 4),
    }


def _calc_reagent_row(row: dict, annual_tp: float) -> dict:
    """Recalculate derived reagent fields."""
    rate = float(row.get("consumption_rate") or 0)
    yearly = rate * annual_tp if annual_tp > 0 else float(row.get("yearly_consumption") or 0)
    unit_cost = float(row.get("unit_cost_cad") or 0)
    total = yearly * unit_cost
    cost_mt = total / annual_tp if annual_tp > 0 else 0
    return {
        "yearly_consumption": round(yearly, 2),
        "total_cost": round(total, 2),
        "unit_cost_mt": round(cost_mt, 4),
    }


# ============================================================================
# 1. GET /inputs
# ============================================================================
@router.get("/inputs")
def get_inputs(pid: str, user=Depends(project_user)):
    try:
        _ensure_inputs(pid)
        rows = qall("SELECT * FROM opex_inputs WHERE project_id=%s ORDER BY param_key", (pid,))
        return {"items": rows}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ============================================================================
# 2. PATCH /inputs/{iid}
# ============================================================================
@router.patch("/inputs/{iid}")
def patch_input(pid: str, iid: str, body: OpexInputPatch, user=Depends(project_user)):
    try:
        val = body.param_value
        row = execute("""
            UPDATE opex_inputs SET param_value=%s
            WHERE id=%s AND project_id=%s
            RETURNING *
        """, (val, iid, pid))
        if not row:
            raise HTTPException(404, "Input not found")
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


# ============================================================================
# 3. GET /manpower
# ============================================================================
@router.get("/manpower")
def get_manpower(pid: str, user=Depends(project_user)):
    try:
        rows = qall("""
            SELECT * FROM opex_manpower WHERE project_id=%s
            ORDER BY sort_order, department, description
        """, (pid,))
        return {"items": rows}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ============================================================================
# 4. POST /manpower
# ============================================================================
@router.post("/manpower")
def add_manpower(pid: str, body: ManpowerIn, user=Depends(project_user)):
    try:
        calc = _calc_manpower_row(body.model_dump())
        row = execute("""
            INSERT INTO opex_manpower
                (project_id, department, description, category, schedule,
                 num_employees, base_salary_hourly, bonus_pct, benefits_pct,
                 overtime_pct, total_salary, total_cost, sort_order)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING *
        """, (
            pid, body.department, body.description,
            body.category, body.schedule,
            body.num_employees, body.base_salary_hourly,
            body.bonus_pct, body.benefits_pct,
            body.overtime_pct, calc["total_salary"], calc["total_cost"],
            body.sort_order,
        ))
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


# ============================================================================
# 5. PATCH /manpower/{mid}
# ============================================================================
@router.patch("/manpower/{mid}")
def patch_manpower(pid: str, mid: str, body: ManpowerPatch, user=Depends(project_user)):
    try:
        updates = {k: v for k, v in body.model_dump(exclude_none=True).items() if k in MANPOWER_PATCH_FIELDS}
        sets, vals = build_update_sets(updates, allowed=frozenset(MANPOWER_PATCH_FIELDS))
        if not sets:
            raise HTTPException(400, "No valid fields")
        vals.extend([mid, pid])
        execute(f"UPDATE opex_manpower SET {','.join(sets)} WHERE id=%s AND project_id=%s", vals)
        current = qone("SELECT * FROM opex_manpower WHERE id=%s AND project_id=%s", (mid, pid))
        if not current:
            raise HTTPException(404, "Position not found")
        calc = _calc_manpower_row(current)
        row = execute("""
            UPDATE opex_manpower SET total_salary=%s, total_cost=%s
            WHERE id=%s AND project_id=%s RETURNING *
        """, (calc["total_salary"], calc["total_cost"], mid, pid))
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


# ============================================================================
# 6. DELETE /manpower/{mid}
# ============================================================================
@router.delete("/manpower/{mid}")
def delete_manpower(pid: str, mid: str, user=Depends(project_user)):
    try:
        execute("DELETE FROM opex_manpower WHERE id=%s AND project_id=%s", (mid, pid))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ============================================================================
# 7. GET /power
# ============================================================================
@router.get("/power")
def get_power(pid: str, user=Depends(project_user)):
    try:
        rows = qall("""
            SELECT * FROM opex_power WHERE project_id=%s
            ORDER BY sort_order, wbs_code
        """, (pid,))
        return {"items": rows}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ============================================================================
# 7b. GET /reagents  — list rows (UI + export)
# ============================================================================
@router.get("/reagents")
def get_reagents(pid: str, user=Depends(project_user)):
    try:
        rows = qall("""
            SELECT * FROM opex_reagents WHERE project_id=%s
            ORDER BY sort_order, category, description
        """, (pid,))
        return {"items": rows or []}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ============================================================================
# 7c. GET /mobile  — list rows (UI + export)
# ============================================================================
@router.get("/mobile")
def get_mobile(pid: str, user=Depends(project_user)):
    try:
        rows = qall("""
            SELECT * FROM opex_mobile WHERE project_id=%s
            ORDER BY sort_order, description
        """, (pid,))
        return {"items": rows or []}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ============================================================================
# 8. POST /power/auto-generate
# ============================================================================
@router.post("/power/auto-generate")
def auto_generate_power(pid: str, user=Depends(project_user)):
    """Generate power consumption from equipment_v2 (MER), grouped by WBS."""
    try:
        return _auto_generate_power_impl(pid, user)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _auto_generate_power_impl(pid: str, user):
    _ensure_inputs(pid)
    _inputs = _get_all_inputs(pid)  # single query instead of 2 separate qone() calls
    power_cost = _inputs.get("power_cost_kwh") or 0.092
    annual_tp = _inputs.get("annual_throughput") or 0

    # Read equipment grouped by WBS
    wbs_rows = qall("""
        SELECT wbs_code, MIN(eq_type) as wbs_description,
               COALESCE(SUM(installed_kw * quantity), 0) as total_kw
        FROM equipment_v2
        WHERE project_id=%s AND installed_kw > 0
        GROUP BY wbs_code
        ORDER BY wbs_code
    """, (pid,))

    if not wbs_rows:
        raise HTTPException(404, "No equipment found in MER. Generate equipment list first.")

    # Clear existing
    execute("DELETE FROM opex_power WHERE project_id=%s", (pid,))

    c = conn()
    cur = None
    try:
        cur = c.cursor()
        for idx, w in enumerate(wbs_rows):
            row_data = {
                "operating_kw": float(w["total_kw"]),
                "electrical_efficiency": 0.92,
                "load_factor": 0.80,
                "area_availability": 0.92,
                "hours_per_day": 22,
            }
            calc = _calc_power_row(row_data, power_cost, annual_tp)
            cur.execute("""
                INSERT INTO opex_power
                    (project_id, wbs_code, wbs_description, operating_kw,
                     electrical_efficiency, load_factor, area_availability,
                     hours_per_day, hours_per_year, consumption_kwh_year,
                     consumption_kwh_mt, total_cost, unit_cost_mt, sort_order)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                pid, w["wbs_code"], w["wbs_description"] or w["wbs_code"],
                float(w["total_kw"]),
                0.92, 0.80, 0.92, 22,
                calc["hours_per_year"], calc["consumption_kwh_year"],
                calc["consumption_kwh_mt"], calc["total_cost"],
                calc["unit_cost_mt"], idx,
            ))
        c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        c.rollback()
        raise
    finally:
        if cur:
            cur.close()
        release(c)

    return {"ok": True, "count": len(wbs_rows)}

# 8b. PATCH /power/{pwid}
# ============================================================================
@router.patch("/power/{pwid}")
def patch_power(pid: str, pwid: str, body: PowerPatch, user=Depends(project_user)):
    """Update a power row and recalculate derived values."""
    try:
        allowed = {"operating_kw", "electrical_efficiency", "load_factor",
                   "area_availability", "hours_per_day", "wbs_description"}
        sets, vals = [], []
        for k, v in body.model_dump(exclude_none=True).items():
            if k in allowed:
                sets.append(f"{k}=%s")
                vals.append(v)
        if not sets:
            raise HTTPException(400, "No valid fields")
        vals.extend([pwid, pid])
        execute(f"UPDATE opex_power SET {','.join(sets)} WHERE id=%s AND project_id=%s", vals)
        current = qone("SELECT * FROM opex_power WHERE id=%s AND project_id=%s", (pwid, pid))
        if not current:
            raise HTTPException(404, "Power row not found")
        _ensure_inputs(pid)
        power_cost = _get_input_val(pid, "power_cost_kwh", 0.092)
        annual_tp = _get_input_val(pid, "annual_throughput", 0)
        calc = _calc_power_row(current, power_cost, annual_tp)
        row = execute("""
            UPDATE opex_power SET hours_per_year=%s, consumption_kwh_year=%s,
                   consumption_kwh_mt=%s, total_cost=%s, unit_cost_mt=%s
            WHERE id=%s AND project_id=%s RETURNING *
        """, (calc["hours_per_year"], calc["consumption_kwh_year"],
              calc["consumption_kwh_mt"], calc["total_cost"], calc["unit_cost_mt"],
              pwid, pid))
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


# ============================================================================
# 10. POST /reagents
# ============================================================================
@router.post("/reagents")
def add_reagent(pid: str, body: ReagentIn, user=Depends(project_user)):
    try:
        _ensure_inputs(pid)
        annual_tp = _get_input_val(pid, "annual_throughput", 0)
        calc = _calc_reagent_row(body.model_dump(), annual_tp)
        row = execute("""
            INSERT INTO opex_reagents
                (project_id, category, description, unit_consumption,
                 consumption_rate, yearly_consumption, unit_cost_cad, source,
                 total_cost, unit_cost_mt, sort_order)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING *
        """, (
            pid, body.category, body.description,
            body.unit_consumption, body.consumption_rate,
            calc["yearly_consumption"], body.unit_cost_cad,
            body.source, calc["total_cost"], calc["unit_cost_mt"],
            body.sort_order,
        ))
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


# ============================================================================
# 11. PATCH /reagents/{rid}
# ============================================================================
@router.patch("/reagents/{rid}")
def patch_reagent(pid: str, rid: str, body: ReagentPatch, user=Depends(project_user)):
    try:
        sets, vals = [], []
        for k, v in body.model_dump(exclude_none=True).items():
            if k in REAGENT_PATCH_FIELDS:
                sets.append(f"{k}=%s")
                vals.append(v)
        if not sets:
            raise HTTPException(400, "No valid fields")
        vals.extend([rid, pid])
        execute(f"UPDATE opex_reagents SET {','.join(sets)} WHERE id=%s AND project_id=%s", vals)
        current = qone("SELECT * FROM opex_reagents WHERE id=%s AND project_id=%s", (rid, pid))
        if not current:
            raise HTTPException(404, "Reagent not found")
        _ensure_inputs(pid)
        annual_tp = _get_input_val(pid, "annual_throughput", 0)
        calc = _calc_reagent_row(current, annual_tp)
        row = execute("""
            UPDATE opex_reagents SET yearly_consumption=%s, total_cost=%s, unit_cost_mt=%s
            WHERE id=%s AND project_id=%s RETURNING *
        """, (calc["yearly_consumption"], calc["total_cost"], calc["unit_cost_mt"], rid, pid))
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


# ============================================================================
# 13. POST /mobile
# ============================================================================
@router.post("/mobile")
def add_mobile(pid: str, body: MobileIn, user=Depends(project_user)):
    try:
        total = body.quantity * body.operating_hours_year * body.cost_per_hour
        _ensure_inputs(pid)
        annual_tp = _get_input_val(pid, "annual_throughput", 0)
        cost_mt = total / annual_tp if annual_tp > 0 else 0
        row = execute("""
            INSERT INTO opex_mobile
                (project_id, description, equipment_type, quantity,
                 operating_hours_year, cost_per_hour, total_cost, unit_cost_mt, sort_order)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING *
        """, (
            pid, body.description, body.equipment_type,
            body.quantity, body.operating_hours_year, body.cost_per_hour,
            round(total, 2), round(cost_mt, 4),
            body.sort_order,
        ))
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


# ============================================================================
# 14. PATCH /mobile/{mid}
# ============================================================================
@router.patch("/mobile/{mid}")
def patch_mobile(pid: str, mid: str, body: MobilePatch, user=Depends(project_user)):
    try:
        allowed = {"description", "equipment_type", "quantity", "operating_hours_year", "cost_per_hour", "sort_order"}
        sets, vals = [], []
        for k, v in body.model_dump(exclude_none=True).items():
            if k in allowed:
                sets.append(f"{k}=%s")
                vals.append(v)
        if not sets:
            raise HTTPException(400, "No valid fields")
        vals.extend([mid, pid])
        execute(f"UPDATE opex_mobile SET {','.join(sets)} WHERE id=%s AND project_id=%s", vals)
        current = qone("SELECT * FROM opex_mobile WHERE id=%s AND project_id=%s", (mid, pid))
        if not current:
            raise HTTPException(404, "Mobile equipment not found")
        qty = int(current.get("quantity") or 1)
        hrs = float(current.get("operating_hours_year") or 0)
        cph = float(current.get("cost_per_hour") or 0)
        total = qty * hrs * cph
        _ensure_inputs(pid)
        annual_tp = _get_input_val(pid, "annual_throughput", 0)
        cost_mt = total / annual_tp if annual_tp > 0 else 0
        row = execute("""
            UPDATE opex_mobile SET total_cost=%s, unit_cost_mt=%s
            WHERE id=%s AND project_id=%s RETURNING *
        """, (round(total, 2), round(cost_mt, 4), mid, pid))
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


# ============================================================================
# 15. DELETE /mobile/{mid}
# ============================================================================
@router.delete("/mobile/{mid}")
def delete_mobile(pid: str, mid: str, user=Depends(project_user)):
    try:
        execute("DELETE FROM opex_mobile WHERE id=%s AND project_id=%s", (mid, pid))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ============================================================================
# 16. GET /summary
# ============================================================================
@router.get("/summary")
def get_summary(pid: str, user=Depends(project_user)):
    """Total OPEX summary across all categories."""
    try:
        return _get_summary_impl(pid)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _get_summary_impl(pid: str):
    _ensure_inputs(pid)
    annual_tp = _get_input_val(pid, "annual_throughput", 0)
    recovery = _get_input_val(pid, "recovery_pct", 0)

    # Get project gold grade for oz calculation
    proj = qone("SELECT gold_grade_g_t, target_tph FROM projects WHERE id=%s", (pid,))
    grade = float(proj.get("gold_grade_g_t") or 0) if proj else 0
    oz_year = annual_tp * grade * (recovery / 100) * TROY_OZ_PER_GRAM if annual_tp > 0 and grade > 0 else 0

    # Manpower total
    mp = qone("SELECT COALESCE(SUM(total_cost),0) as total FROM opex_manpower WHERE project_id=%s", (pid,))
    manpower_total = float(mp["total"]) if mp else 0

    # Power total
    pw = qone("SELECT COALESCE(SUM(total_cost),0) as total FROM opex_power WHERE project_id=%s", (pid,))
    power_total = float(pw["total"]) if pw else 0

    # Reagents: split into grinding media vs reagents vs consumables
    rg = qall("""
        SELECT category, COALESCE(SUM(total_cost),0) as total
        FROM opex_reagents WHERE project_id=%s GROUP BY category
    """, (pid,))
    grinding_total = 0.0
    reagents_total = 0.0
    consumables_total = 0.0
    for r in rg or []:
        val = float(r.get("total") or 0)
        bucket = _opex_reagent_summary_bucket(r.get("category"))
        if bucket == "grinding":
            grinding_total += val
        elif bucket == "consumables":
            consumables_total += val
        else:
            reagents_total += val

    # Mobile / material handling
    mb = qone("SELECT COALESCE(SUM(total_cost),0) as total FROM opex_mobile WHERE project_id=%s", (pid,))
    mobile_total = float(mb["total"]) if mb else 0

    # Spares estimate (2% of CAPEX if available, else 0)
    capex = qone("""
        SELECT COALESCE(SUM(price_cad),0) as total FROM equipment_v2
        WHERE project_id=%s AND enabled=true
    """, (pid,))
    spares_total = float(capex["total"]) * 0.02 if capex and capex["total"] else 0

    grand = manpower_total + power_total + grinding_total + reagents_total + consumables_total + mobile_total + spares_total

    def _row(label, val):
        return {
            "category": label,
            "total_cad_year": round(val, 2),
            "cad_per_t": round(val / annual_tp, 4) if annual_tp > 0 else 0,
            "cad_per_oz": round(val / oz_year, 2) if oz_year > 0 else 0,
            "pct_total": round(val / grand * 100, 1) if grand > 0 else 0,
        }

    rows = [
        _row("Main d'oeuvre", manpower_total),
        _row("Puissance électrique", power_total),
        _row("Média de broyage & réactifs", grinding_total + reagents_total),
        _row("Consommables & pièces d'usure", consumables_total),
        _row("Manutention matériel", mobile_total),
        _row("Pièces de rechange", spares_total),
    ]

    return {
        "rows": rows,
        "grand_total": _row("Total coût opératoire", grand),
        "annual_throughput": annual_tp,
        "oz_per_year": round(oz_year, 0),
    }


# ============================================================================
# 17. POST /auto-generate
# ============================================================================
@router.post("/auto-generate")
def auto_generate(pid: str, user=Depends(project_user)):
    """Auto-generate full OPEX from MER + DC + LIMS."""
    try:
        return _auto_generate_impl(pid, user)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _auto_generate_impl(pid: str, user):
    _ensure_inputs(pid)

    # 1. Generate manpower
    execute("DELETE FROM opex_manpower WHERE project_id=%s", (pid,))
    c = conn()
    cur = None
    try:
        cur = c.cursor()
        for idx, (dept, desc, cat, sched, count, rate) in enumerate(TYPICAL_MANPOWER):
            row_data = {
                "base_salary_hourly": rate,
                "schedule": sched,
                "bonus_pct": 5,
                "benefits_pct": 20,
                "overtime_pct": 0,
                "num_employees": count,
            }
            calc = _calc_manpower_row(row_data)
            cur.execute("""
                INSERT INTO opex_manpower
                    (project_id, department, description, category, schedule,
                     num_employees, base_salary_hourly, bonus_pct, benefits_pct,
                     overtime_pct, total_salary, total_cost, sort_order)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                pid, dept, desc, cat, sched, count, rate,
                5, 20, 0, calc["total_salary"], calc["total_cost"], idx,
            ))
        c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        c.rollback()
        raise
    finally:
        if cur:
            cur.close()
        release(c)

    # 2. Generate power (delegates to existing endpoint logic)
    try:
        auto_generate_power(pid, user=user)
    except HTTPException:
        pass  # No equipment yet is OK

    # 3. Generate reagents from design criteria / simulation params
    execute("DELETE FROM opex_reagents WHERE project_id=%s", (pid,))
    project = qone("SELECT * FROM projects WHERE id=%s", (pid,))
    target_tph = float(project.get("target_tph") or 0) if project else 0
    avail_pct = float(project.get("availability_pct") or 92) if project else 92
    op_h = float(project.get("operating_hours_day") or 24.0) if project else 24.0
    annual_tp = target_tph * op_h * 365 * (avail_pct / 100)

    # Update annual_throughput input
    execute(
        "UPDATE opex_inputs SET param_value=%s WHERE project_id=%s AND param_key='annual_throughput'",
        (annual_tp, pid),
    )

    # Read reagent consumption from DC / sim_params
    sim_params = {r["param_key"]: float(r["param_value"]) for r in qall(
        "SELECT param_key, param_value FROM simulation_params WHERE project_id=%s", (pid,)
    ) if r.get("param_value") is not None}

    enabled_ops = _project_enabled_op_codes(pid)
    reagent_defs = _build_opex_reagent_defs(sim_params, enabled_ops)

    # Batch-insert reagents in a single transaction (replaces 14 individual execute() auto-commits)
    reagent_rows = []
    for idx, (cat, desc, unit, rate, unit_cost, src) in enumerate(reagent_defs):
        yearly = rate * annual_tp if annual_tp > 0 else 0
        total_cost = yearly * unit_cost
        cost_mt = total_cost / annual_tp if annual_tp > 0 else 0
        reagent_rows.append((pid, cat, desc, unit, rate, round(yearly, 2), unit_cost, src,
                             round(total_cost, 2), round(cost_mt, 4), idx))
    reagent_count = len(reagent_rows)
    _batch_c = conn()
    try:
        _batch_cur = _batch_c.cursor()
        _batch_cur.executemany("""
            INSERT INTO opex_reagents
                (project_id, category, description, unit_consumption,
                 consumption_rate, yearly_consumption, unit_cost_cad, source,
                 total_cost, unit_cost_mt, sort_order)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, reagent_rows)
        _batch_c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        _batch_c.rollback()
        raise
    finally:
        _batch_cur.close()
        release(_batch_c)

    # 4. Generate mobile equipment (typical mill fleet)
    execute("DELETE FROM opex_mobile WHERE project_id=%s", (pid,))
    mobile_defs = [
        # (description, equipment_type, qty, cost_per_hour, hours_yr)
        ("Chargeuse frontale CAT 966", "Manutention", 1, 185, 8760),
        ("Chariot élévateur 5t", "Manutention", 2, 65, 4160),
        ("Camion-benne 30t", "Transport", 1, 120, 4160),
        ("Grue mobile 25t", "Maintenance", 1, 250, 2080),
        ("Nacelle élévatrice", "Maintenance", 1, 45, 2080),
        ("Véhicule de service (pickup)", "Général", 4, 35, 4160),
        ("Ambulance / véhicule urgence", "Sécurité", 1, 55, 8760),
    ]

    # Batch-insert mobile equipment in a single transaction (replaces 7 individual auto-commits)
    mobile_rows = []
    for idx, (desc, eq_type, qty, cph, hours_yr) in enumerate(mobile_defs):
        total_cost = cph * hours_yr * qty
        cost_mt = total_cost / annual_tp if annual_tp > 0 else 0
        mobile_rows.append((pid, desc, eq_type, qty, hours_yr, cph,
                            round(total_cost, 2), round(cost_mt, 4), idx))
    mobile_count = len(mobile_rows)
    _mob_c = conn()
    try:
        _mob_cur = _mob_c.cursor()
        _mob_cur.executemany("""
            INSERT INTO opex_mobile
                (project_id, description, equipment_type, quantity,
                 operating_hours_year, cost_per_hour,
                 total_cost, unit_cost_mt, sort_order)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, mobile_rows)
        _mob_c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        _mob_c.rollback()
        raise
    finally:
        _mob_cur.close()
        release(_mob_c)

    return {
        "ok": True,
        "manpower_positions": len(TYPICAL_MANPOWER),
        "reagents_generated": reagent_count,
        "mobile_generated": mobile_count,
        "enabled_circuit_ops": len(enabled_ops),
        "reagents_gated_by_circuit": True,
    }


# ============================================================================
# 18. POST /export
# ============================================================================
@router.post("/export")
def export_opex(pid: str, user=Depends(project_user)):
    """Return structured JSON for Excel export (6 sheets)."""
    try:
        summary = get_summary(pid, user=user)
        inputs = get_inputs(pid, user=user)
        manpower = get_manpower(pid, user=user)
        power = get_power(pid, user=user)
        reagents = {
            "items": qall(
                "SELECT * FROM opex_reagents WHERE project_id=%s ORDER BY category, sort_order, description",
                (pid,),
            )
        }
        mobile = {
            "items": qall(
                "SELECT * FROM opex_mobile WHERE project_id=%s ORDER BY sort_order, description",
                (pid,),
            )
        }

        proj = qone("SELECT project_name, project_code FROM projects WHERE id=%s", (pid,))

        return {
            "project": proj,
            "sheets": {
                "summary": summary,
                "inputs": inputs,
                "manpower": manpower,
                "power": power,
                "reagents": reagents,
                "mobile": mobile,
            },
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
