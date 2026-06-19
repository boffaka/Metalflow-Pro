"""
MPDPMS — Ore to Bullion Simulator API routes.
Du Minerai au Lingot : simulation circuit par circuit.
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

try:
    from ..auth import project_user
    from ..db import qone, qall, execute
    from ..audit import record_event
    from ..logging_config import log_user_action
    from ..engines.ore_to_bullion import simulate_ore_to_bullion, FeedParameters, CircuitConfig
except ImportError:
    from auth import project_user
    from db import qone, qall, execute
    from audit import record_event
    from logging_config import log_user_action
    from engines.ore_to_bullion import simulate_ore_to_bullion, FeedParameters, CircuitConfig

router = APIRouter(prefix="/api/v1/projects/{pid}/ore-to-bullion", tags=["ore-to-bullion"])
logger = logging.getLogger("mpdpms.ore_to_bullion")


# ── Request/Response models ──────────────────────────────────────────────────

class CreateRunRequest(BaseModel):
    name: str = "Simulation sans nom"
    feed_params: dict
    circuit_config: dict = {}
    overrides: dict | None = None

class CompareRequest(BaseModel):
    run_ids: list[str] = Field(..., min_length=2, max_length=4)


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("/runs", status_code=201)
def create_run(pid: str, body: CreateRunRequest, user=Depends(project_user)):
    """Create and execute a new simulation run (synchronous — typically < 5s)."""
    try:
        # Validate inputs
        fp = FeedParameters(**body.feed_params)
        cc = CircuitConfig(**body.circuit_config)
    except Exception as e:
        raise HTTPException(422, detail=f"Invalid parameters: {e}")

    # Execute simulation (pure engine — no DB)
    t0 = time.perf_counter()
    try:
        result = simulate_ore_to_bullion(fp, cc, body.overrides)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    except Exception as e:
        logger.exception("Simulation engine error for project %s", pid)
        raise HTTPException(500, detail=f"Simulation engine error: {e}")

    computation_time = time.perf_counter() - t0

    # Persist run
    run_id = str(uuid.uuid4())
    results_json = result.model_dump()

    execute(
        """INSERT INTO ore_to_bullion_runs
           (id, project_id, name, feed_params, circuit_config, overrides, results, status, created_by, computation_time_s)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'done', %s, %s)""",
        (run_id, pid, body.name, json.dumps(fp.model_dump()), json.dumps(cc.model_dump()),
         json.dumps(body.overrides) if body.overrides else None,
         json.dumps(results_json), user.get("id"), computation_time),
    )

    # Persist circuit results (normalized)
    for i, cr in enumerate(result.circuit_results):
        execute(
            """INSERT INTO ore_to_bullion_circuit_results
               (run_id, circuit_name, circuit_order, input_stream, output_stream,
                mass_balance, equipment, energy_kwh_t, power_kw, reagents, alerts, metadata)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (run_id, cr.circuit_name, i, json.dumps(cr.input_stream), json.dumps(cr.output_stream),
             json.dumps(cr.mass_balance), json.dumps(cr.equipment),
             cr.energy_kwh_t, cr.power_kw, json.dumps(cr.reagents),
             json.dumps(cr.alerts), json.dumps(cr.metadata)),
        )

    # Audit trail
    record_event(user_id=user["id"], project_id=pid, entity_type="ore_to_bullion",
                 entity_id=run_id, action="simulation_run", new_value={"name": body.name}, source="web")
    log_user_action("ore_to_bullion.run", user_id=str(user["id"]),
                    entity_type="simulation_run", entity_id=run_id,
                    details={"name": body.name, "circuits": len(result.circuit_results),
                             "recovery": result.overall_recovery_pct, "time_ms": round(computation_time * 1000)})

    return {"run_id": run_id, "status": "done", "results": results_json}


@router.get("/runs")
def list_runs(pid: str, user=Depends(project_user)):
    """List all simulation runs for a project."""
    rows = qall(
        """SELECT id, name, status, created_at, computation_time_s,
                  feed_params->>'feed_rate_tph' AS feed_rate,
                  feed_params->>'gold_grade_g_t' AS gold_grade,
                  results->>'overall_recovery_pct' AS recovery,
                  results->>'annual_gold_oz' AS gold_oz
           FROM ore_to_bullion_runs
           WHERE project_id = %s
           ORDER BY created_at DESC""",
        (pid,),
    )
    return rows


@router.get("/runs/{run_id}")
def get_run(pid: str, run_id: str, user=Depends(project_user)):
    """Get complete results for a simulation run."""
    run = qone(
        "SELECT * FROM ore_to_bullion_runs WHERE id = %s AND project_id = %s",
        (run_id, pid),
    )
    if not run:
        raise HTTPException(404, "Simulation run not found")

    circuits = qall(
        "SELECT * FROM ore_to_bullion_circuit_results WHERE run_id = %s ORDER BY circuit_order",
        (run_id,),
    )
    run["circuit_results"] = circuits
    return run


@router.post("/runs/{run_id}/duplicate", status_code=201)
def duplicate_run(pid: str, run_id: str, user=Depends(project_user)):
    """Duplicate an existing run with a new name."""
    original = qone(
        "SELECT feed_params, circuit_config, overrides, name FROM ore_to_bullion_runs WHERE id = %s AND project_id = %s",
        (run_id, pid),
    )
    if not original:
        raise HTTPException(404, "Run not found")

    new_name = f"{original['name']} (copie)"
    body = CreateRunRequest(
        name=new_name,
        feed_params=original["feed_params"] if isinstance(original["feed_params"], dict) else json.loads(original["feed_params"]),
        circuit_config=original["circuit_config"] if isinstance(original["circuit_config"], dict) else json.loads(original["circuit_config"]),
        overrides=original.get("overrides"),
    )
    return create_run(pid, body, user)


@router.delete("/runs/{run_id}")
def delete_run(pid: str, run_id: str, user=Depends(project_user)):
    """Delete a simulation run and all its circuit results."""
    run = qone("SELECT id FROM ore_to_bullion_runs WHERE id = %s AND project_id = %s", (run_id, pid))
    if not run:
        raise HTTPException(404, "Run not found")
    execute("DELETE FROM ore_to_bullion_runs WHERE id = %s", (run_id,))
    return {"ok": True, "deleted": run_id}


@router.post("/runs/compare")
def compare_runs(pid: str, body: CompareRequest, user=Depends(project_user)):
    """Compare up to 4 runs side-by-side."""
    if len(body.run_ids) < 2:
        raise HTTPException(400, "At least 2 runs required for comparison")

    runs = []
    for rid in body.run_ids[:4]:
        run = qone(
            """SELECT id, name, results->>'overall_recovery_pct' AS recovery,
                      results->>'annual_gold_oz' AS gold_oz,
                      results->>'total_energy_kwh_t' AS energy,
                      results->>'total_power_kw' AS power,
                      results->>'total_reagent_opex_usd_t' AS reagent_opex,
                      results->>'co2_kg_per_oz' AS co2,
                      results->'alerts_summary' AS alerts
               FROM ore_to_bullion_runs WHERE id = %s AND project_id = %s""",
            (rid, pid),
        )
        if run:
            runs.append(run)

    return {"runs": runs}


@router.get("/lims-defaults")
def get_lims_defaults(pid: str, user=Depends(project_user)):
    """Return LIMS-derived default parameters for the simulation wizard.

    Aggregates averages from LIMS B1 (comminution), A1 (ore characterization),
    D1 (leach tests), and C2 (gravity) to pre-populate the wizard inputs.
    """
    try:
        return _get_lims_defaults_impl(pid)
    except Exception as e:
        logger.warning("LIMS defaults failed for project %s: %s", pid, e)
        return {"source": "defaults", "params": {}}


def _get_lims_defaults_impl(pid: str):
    """Internal: compute LIMS averages for wizard pre-population."""
    def _avg(rows, field):
        vals = [float(r[field]) for r in rows if r.get(field) not in (None, '', 0)]
        return round(sum(vals) / len(vals), 3) if vals else None

    def _max_val(rows, field):
        vals = [float(r[field]) for r in rows if r.get(field) not in (None, '', 0)]
        return round(max(vals), 3) if vals else None

    # B1 — Comminution indices
    b1 = qall("SELECT bwi_kwh_t, cwi_kwh_t, a_x_b, axb, crushing_wi_kwh_t, abrasion_index_ai, p80_target_um, f80_um, sg, bulk_density_t_m3, mia_kwh_t, mih_kwh_t FROM lims_b1 WHERE project_id=%s", (pid,))

    # A1 — Ore characterization
    a1 = qall("SELECT au_g_t, ag_g_t, s_total_pct, s_sulfide_pct, c_organic_pct, as_ppm, cu_pct, fe_pct FROM lims_a1 WHERE project_id=%s", (pid,))

    # D1 — Leach tests
    d1 = qall("SELECT au_recovery_pct, nacn_consumption_kg_t, cao_consumption_kg_t, duree_h, leach_rec_24h_pct, leach_rec_48h_pct, solides_pulpe_pct, p80_alim_um FROM lims_d1 WHERE project_id=%s", (pid,))

    # C2 — Gravity (GRG)
    try:
        c2 = qall("SELECT au_recovery_pct, grg_rec_pct FROM lims_c2 WHERE project_id=%s", (pid,))
    except Exception:
        c2 = []

    # Aggregate
    params = {}
    lims_source = {}

    # BWi
    bwi = _avg(b1, "bwi_kwh_t")
    if bwi:
        params["bwi_kwh_t"] = bwi
        lims_source["bwi_kwh_t"] = f"LIMS B1 avg ({len([r for r in b1 if r.get('bwi_kwh_t')])} tests)"

    # CWi
    cwi = _avg(b1, "cwi_kwh_t") or _avg(b1, "crushing_wi_kwh_t")
    if cwi:
        params["cwi_kwh_t"] = cwi
        lims_source["cwi_kwh_t"] = "LIMS B1"

    # Axb
    axb = _avg(b1, "a_x_b") or _avg(b1, "axb")
    if axb:
        params["axb"] = axb
        lims_source["axb"] = "LIMS B1"

    # Ore SG
    sg = _avg(b1, "bulk_density_t_m3") or _avg(b1, "sg")
    if sg:
        params["ore_sg"] = sg
        lims_source["ore_sg"] = "LIMS B1"

    # Gold grade
    au = _avg(a1, "au_g_t")
    if au:
        params["gold_grade_g_t"] = au
        lims_source["gold_grade_g_t"] = f"LIMS A1 avg ({len([r for r in a1 if r.get('au_g_t')])} samples)"

    # Sulphur
    s_total = _avg(a1, "s_total_pct")
    if s_total:
        params["s_total_pct"] = s_total
        lims_source["s_total_pct"] = "LIMS A1"

    # Organic carbon
    c_org = _avg(a1, "c_organic_pct")
    if c_org:
        params["c_organic_pct"] = c_org
        lims_source["c_organic_pct"] = "LIMS A1"

    # Leach recovery
    rec_24h = _avg(d1, "leach_rec_24h_pct")
    rec_48h = _avg(d1, "leach_rec_48h_pct")
    rec = _avg(d1, "au_recovery_pct") or rec_48h or rec_24h
    if rec:
        params["leaching_recovery_pct"] = rec
        lims_source["leaching_recovery_pct"] = f"LIMS D1 ({len([r for r in d1 if r.get('au_recovery_pct') or r.get('leach_rec_48h_pct')])} tests)"
    if rec_24h:
        params["leach_rec_24h_pct"] = rec_24h
    if rec_48h:
        params["leach_rec_48h_pct"] = rec_48h

    # NaCN consumption
    nacn = _avg(d1, "nacn_consumption_kg_t")
    if nacn:
        params["leaching_nacn_kg_t"] = nacn
        lims_source["leaching_nacn_kg_t"] = "LIMS D1"

    # CaO consumption
    cao = _avg(d1, "cao_consumption_kg_t")
    if cao:
        params["leaching_cao_kg_t"] = cao
        lims_source["leaching_cao_kg_t"] = "LIMS D1"

    # Leach SRT (from test duration)
    srt = _avg(d1, "duree_h")
    if srt:
        params["leaching_srt_h"] = min(48, max(8, srt))
        lims_source["leaching_srt_h"] = "LIMS D1 (durée test)"

    # P80 target
    p80 = _avg(b1, "p80_target_um") or _avg(d1, "p80_alim_um")
    if p80:
        params["grinding_target_p80_um"] = p80
        lims_source["grinding_target_p80_um"] = "LIMS B1/D1"

    # GRG
    grg = _avg(c2, "grg_rec_pct") or _avg(c2, "au_recovery_pct")
    if grg:
        params["grg_pct"] = grg
        lims_source["grg_pct"] = "LIMS C2"

    # Abrasion index
    ai = _avg(b1, "abrasion_index_ai")
    if ai:
        params["abrasion_index_ai"] = ai
        lims_source["abrasion_index_ai"] = "LIMS B1"

    # Mia/Mih (Morrell indices)
    mia = _avg(b1, "mia_kwh_t")
    mih = _avg(b1, "mih_kwh_t")
    if mia:
        params["mia_kwh_t"] = mia
        lims_source["mia_kwh_t"] = "LIMS B1"
    if mih:
        params["mih_kwh_t"] = mih
        lims_source["mih_kwh_t"] = "LIMS B1"

    # Recommendations based on LIMS
    recommendations = []
    if s_total and s_total > 2.5:
        recommendations.append("Sulfures élevés (>2.5%) — considérer flottation ou pré-oxydation")
    if c_org and c_org > 0.3:
        recommendations.append("Carbone organique élevé (>0.3%) — risque preg-robbing, considérer CIL")
    if bwi and bwi > 18:
        recommendations.append(f"BWi élevé ({bwi} kWh/t) — minerai dur, considérer HPGR")
    if grg and grg > 20:
        recommendations.append(f"GRG élevé ({grg}%) — circuit gravité fortement recommandé")
    if nacn and nacn > 1.5:
        recommendations.append(f"Consommation NaCN élevée ({nacn} kg/t) — vérifier cyanicides")

    return {
        "source": "lims",
        "params": params,
        "lims_source": lims_source,
        "recommendations": recommendations,
        "test_counts": {
            "b1_comminution": len(b1),
            "a1_characterization": len(a1),
            "d1_leaching": len(d1),
            "c2_gravity": len(c2),
        },
    }
