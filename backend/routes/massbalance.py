"""
MPDPMS — Mass Balance routes.
Handles computed mass balance retrieval and auto-generation from LIMS/Design data.
"""

from __future__ import annotations

import logging

import psycopg2.extras
from fastapi import APIRouter, HTTPException, Depends

try:
    from ..auth import project_user
    from ..db import qone, qall, execute, conn, release
    from ..helpers import (
        get_ore_sg,
        compute_annual_t,
        get_mill_circuit_pct_solids,
        select_leach_circuit,
        get_circuit_flags,
    )
    from ..constants import TROY_OZ_PER_GRAM
    from ..deprecation import deprecated_endpoint
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user
    from db import qone, qall, execute, conn, release
    from helpers import (
        get_ore_sg,
        compute_annual_t,
        get_mill_circuit_pct_solids,
        select_leach_circuit,
        get_circuit_flags,
    )
    from constants import TROY_OZ_PER_GRAM
    from deprecation import deprecated_endpoint

# Successor for the deprecated v1 mass-balance endpoints (Lot B consolidation).
# Audit (2026-06-03) found zero runtime callers of /mass-balance/computed and
# /mass-balance/auto-generate across the SPA, the legacy page and the monolith
# UI — all clients use /mass-balance-v2. Marked deprecated (non-breaking) so
# production logs confirm zero traffic before removal.
_MB_SUCCESSOR = "/api/v1/projects/{pid}/mass-balance-v2"

router = APIRouter(prefix="/api/v1/projects", tags=["massbalance"])
logger = logging.getLogger("mpdpms")


def _signal_pipeline(pid: str, module: str, status: str, user_id: str = None) -> None:
    """Signal pipeline status — never blocks the route."""
    try:
        from .pipeline import set_status, mark_stale_cascade
    except ImportError:
        from pipeline import set_status, mark_stale_cascade
    try:
        set_status(pid, module, status, user_id=user_id, triggered_by="auto_generate")
        if status == "complete":
            mark_stale_cascade(pid, module, user_id=user_id)
    except Exception:  # intentional: ignore optional lookup failure
        pass


@router.get("/{pid}/mass-balance/computed", deprecated=True)
def get_mb_computed(pid: str, user=Depends(project_user), _dep=Depends(deprecated_endpoint(_MB_SUCCESSOR))):
    try:
        streams = qall("SELECT * FROM mass_balance_streams WHERE project_id=%s ORDER BY sort_order", (pid,))
        water = qall("SELECT * FROM water_balance_nodes WHERE project_id=%s ORDER BY sort_order", (pid,))
        if not streams:
            raise HTTPException(404, "Aucun bilan massique — utilisez auto-generate")
        rom = next((s for s in streams if s["stream"] == "ROM Feed"), None)
        tails = next((s for s in streams if s["stream"] == "Tailings Final"), None)
        tph = float(rom["solids_tph"]) if rom else 0
        grade = float(rom["au_gt"]) if rom else 0
        tails_grade = float(tails["au_gt"]) if tails else 0
        recovery = (1 - tails_grade / grade) * 100 if grade > 0 else 0
        proj = qone("SELECT operating_hours_day, availability_pct FROM projects WHERE id=%s", (pid,)) or {}
        op_h = float(proj.get("operating_hours_day") or 24.0)
        avail = float(proj.get("availability_pct") or 92.0)
        annual_t = compute_annual_t(tph, op_h, avail)
        annual_oz = annual_t * grade * (recovery / 100) * TROY_OZ_PER_GRAM
        return {
            "streams": streams,
            "water_balance": water,
            "summary": {
                "feed_tph": tph,
                "feed_grade_au_gt": grade,
                "overall_recovery_pct": round(recovery, 2),
                "annual_gold_oz": round(annual_oz, 0),
            },
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.post("/{pid}/mass-balance/auto-generate", deprecated=True)
def auto_generate_mb(pid: str, user=Depends(project_user), _dep=Depends(deprecated_endpoint(_MB_SUCCESSOR))):
    try:
        return _auto_generate_mb_impl(pid, user)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _auto_generate_mb_impl(pid: str, user):
    _signal_pipeline(pid, "mass_balance", "generating", user_id=str(user["id"]))
    p = qone("SELECT * FROM projects WHERE id=%s", (pid,))
    if not p:
        raise HTTPException(404)

    tph = float(p["target_tph"] or 100)
    grade = float(p["gold_grade_g_t"] or 1.0)

    b1 = qall("SELECT * FROM lims_b1 WHERE project_id=%s", (pid,))
    a1 = qall(
        "SELECT c_organic_pct, s_sulfide_pct, s_total_pct, as_ppm, sb_ppm, cu_pct FROM lims_a1 WHERE project_id=%s",
        (pid,),
    )
    d1 = qall("SELECT * FROM lims_d1 WHERE project_id=%s", (pid,))
    c2 = qall("SELECT * FROM lims_c2 WHERE project_id=%s", (pid,))
    e1 = qall("SELECT * FROM lims_e1 WHERE project_id=%s", (pid,))
    try:
        g1 = qall("SELECT au_recovery_pct FROM lims_flotation WHERE project_id=%s", (pid,))
    except Exception:  # intentional: fallback to empty/default on optional data
        g1 = []
    dc_tph_row = qone(
        "SELECT design FROM design_criteria WHERE project_id=%s AND item='Design ore processing rate - Plant'", (pid,)
    )
    dc_au_row = qone("SELECT design FROM design_criteria WHERE project_id=%s AND item='Gold'", (pid,))

    if dc_tph_row and dc_tph_row["design"]:
        tph = float(dc_tph_row["design"])
    if dc_au_row and dc_au_row["design"]:
        grade = float(dc_au_row["design"])

    avg_rec = (
        float(sum(float(r["au_recovery_pct"]) for r in d1 if r["au_recovery_pct"]) / max(len(d1), 1)) if d1 else 91.0
    )
    avg_grg_lims = (
        float(sum(float(r["au_recovery_pct"]) for r in c2 if r["au_recovery_pct"]) / max(len(c2), 1)) if c2 else 0
    )
    avg_ud = (
        float(
            sum(float(r["underflow_density_pct_solids"]) for r in e1 if r["underflow_density_pct_solids"])
            / max(len(e1), 1)
        )
        if e1
        else 55
    )
    cil_rec = min(avg_rec / 100, 0.96)

    # Connexion Modèle IA / Simulation (loaded early so select_leach_circuit can use it)
    sim_params = {
        r["param_key"]: float(r["param_value"])
        for r in qall("SELECT param_key, param_value FROM simulation_params WHERE project_id=%s", (pid,))
        if r["param_value"] is not None
    }

    try:
        from ..engines.gravity_model import plant_gravity_recovery_pct, resolve_gravity_params
    except ImportError:
        from engines.gravity_model import plant_gravity_recovery_pct, resolve_gravity_params

    grav_inputs = dict(sim_params)
    if avg_grg_lims > 0 and "gravity_grg" not in grav_inputs:
        grav_inputs["gravity_grg"] = avg_grg_lims
    gp_grav = resolve_gravity_params(grav_inputs)
    grav_rec = min(plant_gravity_recovery_pct(gp_grav) / 100.0, 0.50)
    avg_grg = gp_grav.grg_pct
    total_rec = grav_rec + (1 - grav_rec) * cil_rec

    leach_decision = select_leach_circuit(pid, a1_rows=a1, d1_rows=d1, project=p, sim_params=sim_params)
    circuit_type = leach_decision["circuit_type"]  # "CIL" or "CIP"

    # Circuit topology — same flags as flowsheet and design criteria
    flags = get_circuit_flags(pid, a1_rows=a1, b1_rows=b1, c2_rows=c2, g1_rows=g1)
    has_gravity = flags["has_gravity"]  # GRG >= 10 %
    has_flotation = flags["has_flotation"]  # S > 2.5% or flotation rec > 50%
    has_isamill = flags["has_isamill"]  # IsaMill regrind (= has_flotation)
    avg_flot_rec = flags["avg_flot"]  # average flotation Au recovery %

    tails_grade = grade * (1 - total_rec)

    # Streams calculation with full mass balance (solides, liquid, pulp, %S, SG)
    SG_ORE = get_ore_sg(pid, sim_params)  # reads sim_params['ore_sg'] → 2.75 default
    SG_WATER = 1.0

    def mb_stream(name, solids_tph, au_gt, pct_solids, sort_order):
        """Calcule un flux complet : solides, liquide, pulpe, %solides, SG."""
        pct_s = max(0.01, min(pct_solids / 100, 0.99))
        liquid_tph = solids_tph * (1 - pct_s) / pct_s
        liquid_m3h = liquid_tph / SG_WATER
        vol_solids = solids_tph / SG_ORE
        pulp_m3h = vol_solids + liquid_m3h
        sg = (solids_tph + liquid_tph) / pulp_m3h if pulp_m3h > 0 else 1.0
        return (name, solids_tph, liquid_m3h, pulp_m3h, pct_solids, sg, au_gt, sort_order)

    # ── Stream mass balance ──────────────────────────────────────────────────
    sag_disch_pct_s = float(sim_params.get("sag_disch_pct_solids", 70.0))
    bm_of_pct_s = float(sim_params.get("bm_of_pct_solids", 35.0))
    flot_mass_pull = float(sim_params.get("flot_mass_pull", 5.0)) / 100.0  # default 5%

    # Gravity streams (aligned with gravity_model slip + mass pull)
    slip_frac = gp_grav.slip_frac if has_gravity else 0.0
    grav_feed = tph * slip_frac if has_gravity else 0.0
    grav_conc = grav_feed * gp_grav.mass_pull_frac if has_gravity else 0.0
    _grav_tails = grav_feed - grav_conc
    conc_grade = (grade * tph * grav_rec) / max(grav_conc, 1e-9) if grav_conc > 0 else 0.0

    # Flotation streams (applied to BM O/F minus gravity feed)
    flot_feed_t = (tph - grav_feed) if has_gravity else tph
    flot_conc_t = flot_feed_t * flot_mass_pull if has_flotation else 0.0
    flot_tails_t = flot_feed_t - flot_conc_t if has_flotation else flot_feed_t
    flot_rec_frac = avg_flot_rec / 100.0 if avg_flot_rec > 0 else 0.65
    flot_conc_grade = (flot_rec_frac * grade * flot_feed_t) / max(flot_conc_t, 0.001) if has_flotation else 0.0
    flot_tails_grade = (
        ((1 - flot_rec_frac) * grade * flot_feed_t) / max(flot_tails_t, 0.001) if has_flotation else grade
    )

    # CIL/CIP feed comes from: gravity tails + flotation tails (if flot) OR direct BM O/F
    if has_flotation:
        # Both flotation concentrate AND tails typically feed CIL (combined circuit)
        cil_feed = tph - grav_conc  # all ore minus what was extracted by gravity ICR
        cil_feed_gr = (grade * tph - conc_grade * grav_conc) / max(cil_feed, 0.001)
    else:
        cil_feed = tph - grav_conc if has_gravity else tph
        cil_feed_gr = (grade * tph - conc_grade * grav_conc) / max(cil_feed, 0.001)

    cil_disch = cil_feed
    cil_disch_gr = cil_feed_gr * (1 - cil_rec)
    thick_uf = cil_feed * 0.999
    tails_solids = thick_uf

    # ── Build ordered stream list ──
    idx = 0
    streams_data = [
        mb_stream("ROM Feed", tph, grade, 100, idx := idx + 1),
        mb_stream("Crusher Product", tph, grade, 95, idx := idx + 1),
        mb_stream("SAG Mill Discharge", tph, grade, sag_disch_pct_s, idx := idx + 1),
        mb_stream("Ball Mill Cyclone O/F", tph, grade, bm_of_pct_s, idx := idx + 1),
    ]

    if has_gravity:
        streams_data += [
            mb_stream("Gravity Feed", grav_feed, grade, 35, idx := idx + 1),
            mb_stream("Gravity Concentrate", grav_conc, conc_grade, 80, idx := idx + 1),
            mb_stream("ICR Leach Feed", grav_conc, conc_grade, 50, idx := idx + 1),
        ]

    if has_flotation:
        streams_data += [
            mb_stream("Flotation Feed", flot_feed_t, grade, 25, idx := idx + 1),
            mb_stream("Flotation Concentrate", flot_conc_t, flot_conc_grade, 70, idx := idx + 1),
            mb_stream("Flotation Tails", flot_tails_t, flot_tails_grade, 25, idx := idx + 1),
        ]

    if has_isamill:
        streams_data += [
            mb_stream("IsaMill Feed", flot_conc_t, flot_conc_grade, 60, idx := idx + 1),
            mb_stream("IsaMill Product", flot_conc_t, flot_conc_grade, 60, idx := idx + 1),
        ]

    streams_data += [
        mb_stream(f"{circuit_type} Feed", cil_feed, cil_feed_gr, 45, idx := idx + 1),
        mb_stream(f"{circuit_type} Discharge", cil_disch, cil_disch_gr, 45, idx := idx + 1),
        mb_stream("Pre-Leach Thickener UF", thick_uf, cil_disch_gr, avg_ud, idx := idx + 1),
        mb_stream("Final Tailings", tails_solids, tails_grade, avg_ud, idx := idx + 1),
    ]

    # Water balance (m³/h)
    water_factor = (100 - avg_ud) / avg_ud if avg_ud > 0 else 1.5
    # cil_pct_solids = leach/mill circuit slurry density (NOT bm_filling which is ball charge %)
    mill_pct_solids = get_mill_circuit_pct_solids(sim_params)
    process_water = tph * ((100 - mill_pct_solids) / mill_pct_solids)

    # Pertes
    tailings_loss = tph * water_factor  # Perte d'eau réelle due à la densité des rejets
    evap_loss = process_water * 0.015

    # Équilibre — l'appoint frais compense exactement les pertes
    fresh_in = tailings_loss + evap_loss
    thickener_rec = process_water * 0.60
    reclaim = max(0.0, process_water - fresh_in - thickener_rec)

    water_data = [
        ("Fresh Water Supply", fresh_in, 0, 0, 0, 0),
        ("Process Water Circuit", process_water, process_water * 0.95, thickener_rec, evap_loss, 1),
        ("Thickener Overflow", thickener_rec, 0, thickener_rec, 0, 2),
        ("Tailings Return Water", reclaim, 0, reclaim, 0, 3),
        ("Evaporation Losses", 0, evap_loss, 0, evap_loss, 4),
        ("Tailings Entrainment", 0, tailings_loss, 0, tailings_loss, 5),
    ]

    execute("DELETE FROM mass_balance_streams WHERE project_id=%s", (pid,))
    execute("DELETE FROM water_balance_nodes   WHERE project_id=%s", (pid,))

    # Batch-insert streams in one transaction + fetch all rows with RETURNING *
    _mb_c = conn()
    try:
        _mb_cur = _mb_c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _mb_cur.executemany(
            "INSERT INTO mass_balance_streams "
            "(project_id, stream, solids_tph, liquid_m3h, pulp_m3h, pct_solids, sg, au_gt, sort_order) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [
                (
                    pid,
                    s_name,
                    round(s_tph, 3),
                    round(s_liq, 1),
                    round(s_pulp, 1),
                    round(s_pcts, 1),
                    round(s_sg, 3),
                    round(s_au, 6),
                    s_ord,
                )
                for s_name, s_tph, s_liq, s_pulp, s_pcts, s_sg, s_au, s_ord in streams_data
            ],
        )
        _mb_cur.executemany(
            "INSERT INTO water_balance_nodes "
            "(project_id, node, inflow, outflow, recycle, loss, sort_order) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            [
                (pid, w_node, round(w_in, 2), round(w_out, 2), round(w_rec, 2), round(w_loss, 2), w_ord)
                for w_node, w_in, w_out, w_rec, w_loss, w_ord in water_data
            ],
        )
        _mb_c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        _mb_c.rollback()
        raise
    finally:
        _mb_cur.close()
        release(_mb_c)

    # Re-fetch inserted rows (needed for downstream calculations)
    saved_streams = qall("SELECT * FROM mass_balance_streams WHERE project_id=%s ORDER BY sort_order", (pid,))
    saved_water = qall("SELECT * FROM water_balance_nodes WHERE project_id=%s ORDER BY sort_order", (pid,))

    # Auto-generate equipment from MB
    equipment_data = build_equipment(
        tph, avg_grg, sim_params, has_gravity=has_gravity, has_flotation=has_flotation, has_isamill=has_isamill
    )
    execute("DELETE FROM equipment WHERE project_id=%s", (pid,))
    _eq_c = conn()
    try:
        _eq_cur = _eq_c.cursor()
        _eq_cur.executemany(
            "INSERT INTO equipment "
            "(project_id, equipment_tag, equipment_type, power_installed_kw, design_capacity_t_h, is_long_lead) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            [(pid, eq["tag"], eq["type"], eq.get("pwr"), eq.get("cap"), eq.get("ll", False)) for eq in equipment_data],
        )
        _eq_c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        _eq_c.rollback()
        raise
    finally:
        _eq_cur.close()
        release(_eq_c)
    saved_eq = [
        {
            "equipment_tag": eq["tag"],
            "equipment_type": eq["type"],
            "is_long_lead": eq.get("ll", False),
            "power_installed_kw": eq.get("pwr"),
            "design_capacity_t_h": eq.get("cap"),
        }
        for eq in equipment_data
    ]

    rom = saved_streams[0]
    tails_s = next((s for s in saved_streams if s["stream"] == "Tailings Final"), saved_streams[-1])
    grade_f = float(rom["au_gt"]) if rom else grade
    tails_gf = float(tails_s["au_gt"]) if tails_s else tails_grade
    rec_f = (1 - tails_gf / grade_f) * 100 if grade_f > 0 else 0
    proj = qone("SELECT operating_hours_day, availability_pct FROM projects WHERE id=%s", (pid,)) or {}
    op_h = float(proj.get("operating_hours_day") or 24.0)
    avail = float(proj.get("availability_pct") or 92.0)
    annual_t = compute_annual_t(tph, op_h, avail)
    annual_oz = annual_t * grade_f * (rec_f / 100) * TROY_OZ_PER_GRAM

    _signal_pipeline(pid, "mass_balance", "complete", user_id=str(user["id"]))
    return {
        "ok": True,
        "streams": saved_streams,
        "water_balance": saved_water,
        "summary": {
            "feed_tph": tph,
            "feed_grade_au_gt": grade_f,
            "overall_recovery_pct": round(rec_f, 2),
            "annual_gold_oz": round(annual_oz, 0),
            "leach_circuit": circuit_type,
            "leach_circuit_confidence": leach_decision["confidence"],
            "leach_circuit_reasons": leach_decision["reasons"],
        },
        "equipment": saved_eq,
        "equipment_count": len(saved_eq),
        "streams_saved": len(saved_streams),
        "water_nodes_saved": len(saved_water),
    }


def build_equipment(
    tph: float,
    avg_grg: float,
    sim: dict = None,
    has_gravity: bool = None,
    has_flotation: bool = None,
    has_isamill: bool = None,
) -> list:
    if sim is None:
        sim = {}
    # Allow caller to pass explicit flags; fall back to inline threshold if not provided
    if has_gravity is None:
        has_gravity = avg_grg >= 10.0
    if has_flotation is None:
        has_flotation = False
    if has_isamill is None:
        has_isamill = False

    eq = []
    sag_pwr = round(tph * sim.get("sag_specific_energy", 8.0), 0)
    bm_pwr = round(tph * sim.get("bm_specific_energy", 7.0), 0)
    eq += [
        {"tag": "CRUS-01", "type": "Gyratory Crusher", "pwr": round(tph * 0.8), "cap": round(tph * 1.1, 1), "ll": True},
        {"tag": "SAG-01", "type": "SAG Mill", "pwr": sag_pwr, "cap": tph, "ll": True},
        {"tag": "BM-01", "type": "Ball Mill", "pwr": bm_pwr, "cap": tph, "ll": True},
        {"tag": "CYC-01", "type": "Hydrocyclone Cluster", "pwr": None, "cap": tph * 1.2, "ll": False},
    ]
    if has_gravity:
        eq += [
            {
                "tag": "GRA-01",
                "type": "Falcon Centrifuge",
                "pwr": round(tph * 0.05),
                "cap": round(tph * 0.35, 1),
                "ll": False,
            },
            {"tag": "ICR-01", "type": "ICR", "pwr": 22, "cap": round(tph * 0.02, 1), "ll": False},
        ]
    if has_flotation:
        eq += [
            {"tag": "FLT-01", "type": "Flotation Cell (Rougher)", "pwr": round(tph * 0.6), "cap": tph, "ll": True},
            {
                "tag": "FLT-02",
                "type": "Flotation Cell (Scavenger)",
                "pwr": round(tph * 0.4),
                "cap": tph * 0.5,
                "ll": False,
            },
            {
                "tag": "FLT-03",
                "type": "Flotation Cell (Cleaner)",
                "pwr": round(tph * 0.2),
                "cap": tph * 0.1,
                "ll": False,
            },
        ]
    if has_isamill:
        eq += [
            {
                "tag": "ISA-01",
                "type": "IsaMill (Concentrate Regrind)",
                "pwr": round(tph * 0.15),
                "cap": tph * 0.05,
                "ll": False,
            },
        ]

    n_tanks = int(sim.get("cil_tanks", 6))
    _pct_s_cil = sim.get("cil_pct_solids", 45.0) / 100.0
    _sg_ore_cil = sim.get("ore_sg", 2.75)
    _sg_slurry_cil = 1.0 / (_pct_s_cil / _sg_ore_cil + (1.0 - _pct_s_cil) / 1.0)
    _vol_flow_cil = tph / max(_sg_slurry_cil * _pct_s_cil, 0.01)
    cil_vol = round(_vol_flow_cil * sim.get("cil_srt", 24) / max(n_tanks, 1), 0)

    eq.append({"tag": "THK-01", "type": "Pre-Leach Thickener", "pwr": round(tph * 0.15), "cap": tph, "ll": True})

    for i in range(1, n_tanks + 1):
        eq.append({"tag": f"CIL-{i:02d}", "type": "CIL Tank", "pwr": round(tph * 0.25), "cap": cil_vol, "ll": False})

    eq += [
        {"tag": "STR-01", "type": "AARL Stripping Vessel", "pwr": 45, "cap": None, "ll": False},
        {"tag": "EW-01", "type": "Electrowinning Cell", "pwr": 60, "cap": None, "ll": False},
        {"tag": "REF-01", "type": "Induction Furnace", "pwr": 35, "cap": None, "ll": False},
        {"tag": "CND-01", "type": "CN Destruction Reactor", "pwr": 18, "cap": tph, "ll": False},
    ]
    return eq
