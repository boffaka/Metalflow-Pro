"""
Circuit Optimizer — Expert rules + surrogate scoring + Pareto ranking.

Given a project's LIMS ore characterization data, recommends the optimal
process circuit by:
1. Filtering incompatible circuits via expert metallurgical rules
2. Scoring remaining candidates via analytical process models
3. Ranking by NPV proxy and identifying Pareto-optimal circuits
"""

from __future__ import annotations

import logging

logger = logging.getLogger("mpdpms.circuit_optimizer")

try:
    from ..constants import TROY_OZ_PER_GRAM
    from .. import config as cfg
except ImportError:  # pragma: no cover - supports direct script imports
    from constants import TROY_OZ_PER_GRAM
    import config as cfg

# ─── Circuit Library ─────────────────────────────────────────────────────────

CIRCUITS = [
    {
        "id": "C01",
        "name": "Gravity + CIL direct",
        "ops": ["GRAVITY", "CIL"],
        "has_gravity": True,
        "has_flotation": False,
        "has_hpgr": False,
        "has_pretreat": False,
        "is_heap": False,
        "base_recovery": 0.65,
        "energy_factor": 0.4,
        "opex_base": 8.0,
        "capex_factor": 0.3,
    },
    {
        "id": "C02",
        "name": "SAG/BM + CIL",
        "ops": ["SAG_MILL", "BALL_MILL", "CIL"],
        "has_gravity": False,
        "has_flotation": False,
        "has_hpgr": False,
        "has_pretreat": False,
        "is_heap": False,
        "base_recovery": 0.91,
        "energy_factor": 1.0,
        "opex_base": 16.0,  # was 0.88, raised per Marsden & House
        "capex_factor": 0.7,
    },
    {
        "id": "C03",
        "name": "SAG/BM + Gravity + CIL",
        "ops": ["SAG_MILL", "BALL_MILL", "GRAVITY", "CIL"],
        "has_gravity": True,
        "has_flotation": False,
        "has_hpgr": False,
        "has_pretreat": False,
        "is_heap": False,
        "base_recovery": 0.91,
        "energy_factor": 1.0,
        "opex_base": 17.0,
        "capex_factor": 0.75,
    },
    {
        "id": "C04",
        "name": "SABC + Gravity + CIL",
        "ops": ["SAG_MILL", "PEBBLE_CRUSHER", "BALL_MILL", "GRAVITY", "CIL"],
        "has_gravity": True,
        "has_flotation": False,
        "has_hpgr": False,
        "has_pretreat": False,
        "is_heap": False,
        "base_recovery": 0.90,
        "energy_factor": 1.15,
        "opex_base": 18.5,
        "capex_factor": 0.85,
    },
    {
        "id": "C05",
        "name": "SAG/BM + Flotation + Regrind + CIL",
        "ops": ["SAG_MILL", "BALL_MILL", "FLOTATION", "REGRIND", "CIL"],
        "has_gravity": False,
        "has_flotation": True,
        "has_hpgr": False,
        "has_pretreat": True,
        "is_heap": False,
        "base_recovery": 0.93,
        "energy_factor": 1.1,
        "opex_base": 22.0,
        "capex_factor": 0.9,
    },
    {
        "id": "C06",
        "name": "SAG/BM + Gravity + Flotation + CIL",
        "ops": ["SAG_MILL", "BALL_MILL", "GRAVITY", "FLOTATION", "CIL"],
        "has_gravity": True,
        "has_flotation": True,
        "has_hpgr": False,
        "has_pretreat": True,
        "is_heap": False,
        "base_recovery": 0.94,
        "energy_factor": 1.1,
        "opex_base": 21.0,
        "capex_factor": 0.92,
    },
    {
        "id": "C07",
        "name": "HPGR + BM + Flotation + CIL",
        "ops": ["HPGR", "BALL_MILL", "FLOTATION", "CIL"],
        "has_gravity": False,
        "has_flotation": True,
        "has_hpgr": True,
        "has_pretreat": True,
        "is_heap": False,
        "base_recovery": 0.92,
        "energy_factor": 0.85,
        "opex_base": 20.0,
        "capex_factor": 1.0,
    },
    {
        "id": "C08",
        "name": "SAG/BM + POX + CIL (refractory)",
        "ops": ["SAG_MILL", "BALL_MILL", "POX", "CIL"],
        "has_gravity": False,
        "has_flotation": False,
        "has_hpgr": False,
        "has_pretreat": True,
        "is_heap": False,
        "base_recovery": 0.95,
        "energy_factor": 1.3,
        "opex_base": 35.0,
        "capex_factor": 1.5,
    },
    {
        "id": "C09",
        "name": "SAG/BM + BIOX + CIL (refractory)",
        "ops": ["SAG_MILL", "BALL_MILL", "BIOX", "CIL"],
        "has_gravity": False,
        "has_flotation": False,
        "has_hpgr": False,
        "has_pretreat": True,
        "is_heap": False,
        "base_recovery": 0.93,
        "energy_factor": 1.2,
        "opex_base": 30.0,
        "capex_factor": 1.3,
    },
    {
        "id": "C10",
        "name": "Heap Leach (low grade)",
        "ops": ["CRUSH", "HEAP_LEACH"],
        "has_gravity": False,
        "has_flotation": False,
        "has_hpgr": False,
        "has_pretreat": False,
        "is_heap": True,
        "base_recovery": 0.55,
        "energy_factor": 0.2,
        "opex_base": 6.0,
        "capex_factor": 0.25,
    },
    {
        "id": "C11",
        "name": "Flotation + UF Regrind + CIL",
        "ops": ["SAG_MILL", "BALL_MILL", "FLOTATION", "ISAMILL", "CIL"],
        "has_gravity": False,
        "has_flotation": True,
        "has_hpgr": False,
        "has_pretreat": True,
        "is_heap": False,
        "base_recovery": 0.95,
        "energy_factor": 1.15,
        "opex_base": 24.0,
        "capex_factor": 1.0,
    },
    {
        "id": "C12",
        "name": "SABC + Gravity + Flot + Regrind + CIL",
        "ops": ["SAG_MILL", "PEBBLE_CRUSHER", "BALL_MILL", "GRAVITY", "FLOTATION", "REGRIND", "CIL"],
        "has_gravity": True,
        "has_flotation": True,
        "has_hpgr": False,
        "has_pretreat": True,
        "is_heap": False,
        "base_recovery": 0.96,
        "energy_factor": 1.2,
        "opex_base": 25.0,
        "capex_factor": 1.1,
    },
    # ── CIP variants (for non preg-robbing ores, C org < 0.3%) ──
    {
        "id": "C13",
        "name": "SAG/BM + CIP",
        "ops": ["SAG_MILL", "BALL_MILL", "CIP"],
        "has_gravity": False,
        "has_flotation": False,
        "has_hpgr": False,
        "has_pretreat": False,
        "is_heap": False,
        "is_cip": True,
        "base_recovery": 0.92,
        "energy_factor": 1.0,
        "opex_base": 15.0,  # CIP slightly higher than CIL (no preg-rob)
        "capex_factor": 0.72,
    },
    {
        "id": "C14",
        "name": "SAG/BM + Gravity + CIP",
        "ops": ["SAG_MILL", "BALL_MILL", "GRAVITY", "CIP"],
        "has_gravity": True,
        "has_flotation": False,
        "has_hpgr": False,
        "has_pretreat": False,
        "is_heap": False,
        "is_cip": True,
        "base_recovery": 0.92,
        "energy_factor": 1.0,
        "opex_base": 16.0,
        "capex_factor": 0.77,
    },
    {
        "id": "C15",
        "name": "SAG/BM + Gravity + Flotation + CIP",
        "ops": ["SAG_MILL", "BALL_MILL", "GRAVITY", "FLOTATION", "CIP"],
        "has_gravity": True,
        "has_flotation": True,
        "has_hpgr": False,
        "has_pretreat": True,
        "is_heap": False,
        "is_cip": True,
        "base_recovery": 0.95,
        "energy_factor": 1.1,
        "opex_base": 20.0,
        "capex_factor": 0.94,
    },
    {
        "id": "C16",
        "name": "Gravity + CIP direct",
        "ops": ["GRAVITY", "CIP"],
        "has_gravity": True,
        "has_flotation": False,
        "has_hpgr": False,
        "has_pretreat": False,
        "is_heap": False,
        "is_cip": True,
        "base_recovery": 0.68,
        "energy_factor": 0.45,
        "opex_base": 9.0,
        "capex_factor": 0.32,
    },
    # ── HPGR trade-off variants (PFS / flowsheet Gosselin) ──
    {
        "id": "C17",
        "name": "HPGR + BM + Gravité + Vertimill + Flottation + CIP",
        "ops": ["HPGR", "BALL_MILL", "GRAVITY", "VERTIMILL", "FLOTATION", "CIP"],
        "has_gravity": True,
        "has_flotation": True,
        "has_hpgr": True,
        "has_pretreat": True,
        "is_heap": False,
        "is_cip": True,
        "tradeoff_group": "hpgr",
        "base_recovery": 0.93,
        "energy_factor": 0.88,
        "opex_base": 22.5,
        "capex_factor": 1.05,
    },
    {
        "id": "C18",
        "name": "HPGR + BM + Gravité + Vertimill + Flottation + Regrind + CIP",
        "ops": ["HPGR", "BALL_MILL", "GRAVITY", "VERTIMILL", "FLOTATION", "VERTIMILL_REGRIND", "CIP"],
        "has_gravity": True,
        "has_flotation": True,
        "has_hpgr": True,
        "has_pretreat": True,
        "is_heap": False,
        "is_cip": True,
        "tradeoff_group": "hpgr",
        "base_recovery": 0.945,
        "energy_factor": 0.92,
        "opex_base": 24.0,
        "capex_factor": 1.12,
    },
    {
        "id": "C19",
        "name": "HPGR + BM + Gravité + Flottation + Regrind Vertimill + CIP",
        "ops": ["HPGR", "BALL_MILL", "GRAVITY", "FLOTATION", "VERTIMILL_REGRIND", "CIP"],
        "has_gravity": True,
        "has_flotation": True,
        "has_hpgr": True,
        "has_pretreat": True,
        "is_heap": False,
        "is_cip": True,
        "tradeoff_group": "hpgr",
        "base_recovery": 0.95,
        "energy_factor": 0.90,
        "opex_base": 23.0,
        "capex_factor": 1.08,
    },
    {
        "id": "C20",
        "name": "HPGR + BM + Gravité + Vertimill + Lixiviation + CIP (flowsheet actuel)",
        "ops": ["HPGR", "BALL_MILL", "GRAVITY", "VERTIMILL", "LEACH_CUVES", "CIP"],
        "has_gravity": True,
        "has_flotation": False,
        "has_hpgr": True,
        "has_pretreat": False,
        "is_heap": False,
        "is_cip": True,
        "tradeoff_group": "flowsheet_actuel",
        "flowsheet_actuel": True,
        "base_recovery": 0.91,
        "energy_factor": 0.88,
        "opex_base": 18.0,
        "capex_factor": 0.98,
    },
]


# ─── Ore Profile Extraction ─────────────────────────────────────────────────


def extract_ore_profile(pid: str, db_qall, db_qone) -> dict:
    """Extract ore characterization from LIMS data."""

    def _avg(rows, field, default=None):
        vals = [float(r[field]) for r in rows if r.get(field) not in (None, "", 0)]
        return sum(vals) / len(vals) if vals else default

    a1 = db_qall("SELECT * FROM lims_a1 WHERE project_id=%s", (pid,))
    b1 = db_qall("SELECT * FROM lims_b1 WHERE project_id=%s", (pid,))
    c2 = db_qall("SELECT * FROM lims_c2 WHERE project_id=%s", (pid,))
    d1 = db_qall("SELECT * FROM lims_d1 WHERE project_id=%s", (pid,))

    try:
        g1 = db_qall("SELECT * FROM lims_flotation WHERE project_id=%s", (pid,))
    except Exception:
        g1 = []

    project = db_qone("SELECT * FROM projects WHERE id=%s", (pid,)) or {}

    # ── Block Model Analysis ─────────────────────────────────────────────
    bm_lithotypes = []
    bm_total_tonnage = 0
    bm_dominant_litho = None
    bm_grade_by_litho = {}
    bm_oxide_pct = 0
    bm_sulfide_pct = 0
    bm_transition_pct = 0
    try:
        bm_data = db_qall(
            "SELECT rock_type, COUNT(*) as n_blocks, "
            "COALESCE(SUM(tonnage), SUM(volume * density)) as total_tonnage, "
            "AVG(grade_au) as avg_grade "
            "FROM blocks WHERE config_id IN "
            "(SELECT id FROM block_model_configs WHERE project_id=%s) "
            "GROUP BY rock_type ORDER BY total_tonnage DESC",
            (pid,),
        )
        for row in bm_data:
            rt = (row.get("rock_type") or "Unknown").lower()
            tonnage = float(row.get("total_tonnage") or 0)
            grade = float(row.get("avg_grade") or 0)
            bm_lithotypes.append(
                {
                    "name": row.get("rock_type", "Unknown"),
                    "tonnage": tonnage,
                    "grade_au": round(grade, 3),
                    "n_blocks": int(row.get("n_blocks") or 0),
                }
            )
            bm_total_tonnage += tonnage
            bm_grade_by_litho[rt] = grade

        if bm_total_tonnage > 0:
            bm_dominant_litho = bm_lithotypes[0]["name"] if bm_lithotypes else None
            for lt in bm_lithotypes:
                pct = lt["tonnage"] / bm_total_tonnage * 100
                lt["pct"] = round(pct, 1)
                name_lower = lt["name"].lower()
                if any(k in name_lower for k in ("oxide", "oxyde", "oxid", "saprolite", "laterite")):
                    bm_oxide_pct += pct
                elif any(k in name_lower for k in ("sulphide", "sulfide", "sulfure", "fresh", "frais")):
                    bm_sulfide_pct += pct
                elif any(k in name_lower for k in ("transition", "mixed", "mixte")):
                    bm_transition_pct += pct
    except Exception as e:
        logger.debug("Block model query failed (may not exist): %s", e)

    profile = {
        "grade_au": _avg(a1, "au_g_t", float(project.get("gold_grade_g_t") or 1.0)),
        "c_organic_pct": _avg(a1, "c_organic_pct", 0.1),
        "s_total_pct": _avg(a1, "s_total_pct", 1.0),
        "as_ppm": _avg(a1, "as_ppm", 50),
        "bwi": _avg(b1, "bwi_kwh_t", 14.0),
        "grg_pct": _avg(c2, "gravity_au_recovery_pct") or _avg(c2, "au_recovery_pct", 15.0),
        "leach_recovery_pct": _avg(d1, "au_recovery_pct", 85.0),
        "nacn_kg_t": _avg(d1, "nacn_consumption_kg_t", 0.5),
        "flot_recovery_pct": _avg(g1, "recovery_pct", 0.0),
        "throughput_tph": float(project.get("target_tph") or 913),
        "gold_price": float(project.get("gold_price_usd_oz") or cfg.DEFAULT_GOLD_PRICE_USD_OZ),
        "availability_pct": float(project.get("availability_pct") or 92),
        "op_hours_day": float(project.get("operating_hours_day") or 22),
        "mine_life_years": int(project.get("mine_life_years") or 14),
        "discount_rate_pct": float(project.get("discount_rate_pct") or 5),
        # Block model data
        "bm_available": bm_total_tonnage > 0,
        "bm_total_tonnage": round(bm_total_tonnage, 0),
        "bm_dominant_litho": bm_dominant_litho,
        "bm_lithotypes": bm_lithotypes,
        "bm_oxide_pct": round(bm_oxide_pct, 1),
        "bm_sulfide_pct": round(bm_sulfide_pct, 1),
        "bm_transition_pct": round(bm_transition_pct, 1),
    }
    _enrich_dc_economics(profile, pid, db_qall, db_qone)
    return profile


def _enrich_dc_economics(ore: dict, pid: str, db_qall, db_qone) -> None:
    """Merge design criteria and economics into the ore profile for scoring."""
    ore["dc_available"] = False
    ore["economics_available"] = False
    ore["capex_usd"] = None
    ore["opex_usd_t"] = None

    dc_rows: list = []
    try:
        dc_rows = (
            db_qall(
                "SELECT item, design, unit FROM design_criteria WHERE project_id=%s",
                (pid,),
            )
            or []
        )
    except Exception:
        logger.debug("design_criteria read skipped for %s", pid, exc_info=True)
    if dc_rows:
        ore["dc_available"] = True
        for row in dc_rows:
            item = (row.get("item") or "").lower()
            try:
                val = float(row.get("design"))
            except (TypeError, ValueError):
                continue
            if "processing rate" in item and "plant" in item:
                ore["throughput_tph"] = val
            elif item.strip() == "gold" or "grade" in item and "au" in item:
                ore["grade_au"] = val
            elif "recovery" in item and "overall" in item:
                ore["dc_target_recovery_pct"] = val

    try:
        capex_row = db_qone(
            "SELECT COALESCE(SUM(price_cad), 0) AS total FROM equipment_v2 WHERE project_id=%s AND enabled=TRUE",
            (pid,),
        )
        if capex_row and float(capex_row.get("total") or 0) > 0:
            ore["capex_usd"] = float(capex_row["total"])
            ore["economics_available"] = True
    except Exception:
        pass

    try:
        opex_rows = db_qall("SELECT installed_kw FROM opex_power WHERE project_id=%s", (pid,))
        kw = sum(float(r["installed_kw"]) for r in opex_rows if r.get("installed_kw"))
        tph = float(ore.get("throughput_tph") or 100)
        op_h = float(ore.get("op_hours_day") or 22)
        avail = float(ore.get("availability_pct") or 92) / 100
        annual_t = tph * op_h * 365 * avail
        if kw > 0 and annual_t > 0:
            ore["opex_usd_t"] = (kw * 8760 * 0.09) / annual_t + 12.0
            ore["economics_available"] = True
    except Exception:
        pass


# ─── Four-circuit evaluation sets (metallurgical archetypes) ─────────────────

_CIRCUIT_BY_ID = {c["id"]: c for c in CIRCUITS}

_CIP_CIRCUIT_IDS: list[str] = [c["id"] for c in CIRCUITS if c.get("is_cip")]

# When Stange/LIMS recommends CIP, swap CIL archetype ids for CIP equivalents before scoring.
_CIL_TO_CIP_ID: dict[str, str] = {
    "C01": "C16",
    "C02": "C13",
    "C03": "C14",
    "C04": "C14",
    "C05": "C15",
    "C06": "C15",
    "C07": "C15",
    "C11": "C15",
    "C12": "C15",
}


def _apply_leach_type_to_circuit_ids(ids: list[str], leach_type: str | None) -> list[str]:
    """Map evaluation-set ids to CIP variants when advisor says CIP (always len(ids) entries)."""
    if leach_type != "CIP":
        return ids
    target_n = len(ids)
    out: list[str] = []
    seen: set[str] = set()
    for cid in ids:
        nid = _CIL_TO_CIP_ID.get(cid, cid)
        if nid in seen:
            for alt in _CIP_CIRCUIT_IDS:
                if alt not in seen:
                    nid = alt
                    break
        if nid in _CIRCUIT_BY_ID and nid not in seen:
            out.append(nid)
            seen.add(nid)
        elif cid in _CIRCUIT_BY_ID and cid not in seen and _CIRCUIT_BY_ID[cid].get("is_cip"):
            out.append(cid)
            seen.add(cid)
    for alt in _CIP_CIRCUIT_IDS:
        if len(out) >= target_n:
            break
        if alt not in seen:
            out.append(alt)
            seen.add(alt)
    for cid in ids:
        if len(out) >= target_n:
            break
        if cid in _CIRCUIT_BY_ID and cid not in seen:
            out.append(cid)
            seen.add(cid)
    for cid in _CIP_CIRCUIT_IDS:
        if len(out) >= target_n:
            break
        if cid not in seen:
            out.append(cid)
            seen.add(cid)
    return out[:target_n]


_EVALUATION_SETS = {
    "refractory": ["C08", "C09", "C06", "C05"],
    "high_grg": ["C03", "C06", "C02", "C01"],
    "low_grade": ["C10", "C02", "C01", "C03"],
    "standard": ["C02", "C03", "C05", "C06"],
}


def select_four_circuits(
    ore: dict,
    leach_type: str | None = None,
) -> tuple[list[dict], str]:
    """Pick exactly four circuits to compare for this ore profile."""
    if ore.get("s_total_pct", 0) > 5:
        key = "refractory"
    elif ore.get("grg_pct", 0) > 25:
        key = "high_grg"
    elif ore.get("grade_au", 1) < 0.8:
        key = "low_grade"
    else:
        key = "standard"
    base_ids = list(_EVALUATION_SETS[key])
    ids = _apply_leach_type_to_circuit_ids(base_ids, leach_type)
    circuits = [_CIRCUIT_BY_ID[i] for i in ids if i in _CIRCUIT_BY_ID]
    if len(circuits) < 4:
        for cid in base_ids + list(_CIRCUIT_BY_ID.keys()):
            if len(circuits) >= 4:
                break
            c = _CIRCUIT_BY_ID.get(cid)
            if c and c not in circuits:
                circuits.append(c)
    labels = {
        "refractory": "réfractaire (sulfures élevés)",
        "high_grg": "or libre élevé (GRG)",
        "low_grade": "faible teneur",
        "standard": "or non réfractaire conventionnel",
    }
    return circuits, labels.get(key, "standard")


def _compatibility_notes(circuit: dict, ore: dict) -> list[str]:
    """Return warnings if circuit is metallurgically marginal for this ore."""
    _, filtered = filter_circuits(ore)
    eliminated = {f["id"]: f["reasons"] for f in filtered}
    if circuit["id"] in eliminated:
        return eliminated[circuit["id"]]
    return []


# ─── Phase 1: Expert Filtering ──────────────────────────────────────────────


def filter_circuits(ore: dict) -> tuple[list[dict], list[dict]]:
    """Apply expert rules to eliminate incompatible circuits.

    Returns (valid_circuits, filtered_out).
    """
    try:
        valid = []
        filtered = []

        for circuit in CIRCUITS:
            reasons = []

            # Rule 1a: Preg-robbing — no CIP if high organic carbon
            if ore["c_organic_pct"] > 0.3 and circuit.get("is_cip"):
                reasons.append(
                    f"C organique {ore['c_organic_pct']:.2f}% > 0.3% — preg-robbing, CIP elimine (CIL requis)"
                )

            # Rule 1b: Low organic carbon — no CIL when CIP would be better (unless refractory)
            if ore["c_organic_pct"] <= 0.1 and not circuit.get("is_cip") and "CIL" in circuit.get("ops", []):
                if ore["s_total_pct"] < 3 and ore["leach_recovery_pct"] > 80:
                    reasons.append(
                        f"C organique {ore['c_organic_pct']:.2f}% ≤ 0.1% + non-réfractaire — CIP plus efficace que CIL"
                    )

            # Rule 2: Gravity required if GRG significant (SME Handbook: >25% justifies gravity circuit)
            if ore["grg_pct"] > 25 and not circuit["has_gravity"]:
                reasons.append(f"GRG {ore['grg_pct']:.0f}% > 25% — gravite requise")

            # Rule 3: No gravity if GRG negligible
            if ore["grg_pct"] < 5 and circuit["has_gravity"]:
                reasons.append(f"GRG {ore['grg_pct']:.0f}% < 5% — gravite non rentable")

            # Rule 4: Hard ore needs HPGR or SABC
            if ore["bwi"] > 20 and not circuit["has_hpgr"] and "PEBBLE_CRUSHER" not in circuit.get("ops", []):
                if not circuit["is_heap"]:
                    reasons.append(f"BWi {ore['bwi']:.0f} kWh/t > 20 — minerai dur, HPGR/SABC requis")

            # Rule 5: High sulfides need pretreatment
            if ore["s_total_pct"] > 5 and not circuit["has_pretreat"]:
                reasons.append(f"Sulfures {ore['s_total_pct']:.1f}% > 5% — pretraitement requis")

            # Rule 6: Low leach recovery — no direct cyanidation alone
            if ore["leach_recovery_pct"] < 70 and not circuit["has_pretreat"] and not circuit["is_heap"]:
                reasons.append(
                    f"Rec. lixiviation {ore['leach_recovery_pct']:.0f}% < 70% — cyanuration directe insuffisante"
                )

            # Rule 7: Flotation data shows high recovery — require flotation
            if ore["flot_recovery_pct"] > 85 and not circuit["has_flotation"]:
                reasons.append(f"Rec. flottation {ore['flot_recovery_pct']:.0f}% > 85% — flottation recommandee")

            # Rule 8: Low grade — heap leach competitive
            if ore["grade_au"] < 0.5 and not circuit["is_heap"] and circuit["opex_base"] > 15:
                reasons.append(f"Grade {ore['grade_au']:.2f} g/t < 0.5 — OPEX trop eleve pour ce grade")

            # Rule 9: High grade — heap leach wasteful
            if ore["grade_au"] > 2.0 and circuit["is_heap"]:
                reasons.append(f"Grade {ore['grade_au']:.2f} g/t > 2.0 — heap leach gaspille du metal")

            # Rule 10: Block model — sulfide dominant requires pretreatment-ready circuit
            if ore.get("bm_available") and ore.get("bm_sulfide_pct", 0) > 60:
                if not circuit["has_pretreat"] and not circuit["is_heap"] and not circuit.get("is_cip"):
                    reasons.append(
                        f"Block model: {ore['bm_sulfide_pct']:.0f}% sulfure — "
                        f"circuit doit gerer la refractarite (flottation/pretraitement requis)"
                    )

            # Rule 11: Block model — mostly oxide, simple circuit preferred
            if ore.get("bm_available") and ore.get("bm_oxide_pct", 0) > 70:
                if circuit["has_pretreat"] and "POX" in circuit.get("ops", []):
                    reasons.append(
                        f"Block model: {ore['bm_oxide_pct']:.0f}% oxide — "
                        f"POX surdimensionne pour minerai majoritairement oxyde"
                    )

            if reasons:
                filtered.append({"id": circuit["id"], "name": circuit["name"], "reasons": reasons})
            else:
                valid.append(circuit)

        return valid, filtered
    except Exception as e:
        logger.error("filter_circuits failed: %s", e)
        return [], []


# ─── Phase 2: Scoring ────────────────────────────────────────────────────────


def score_circuits(valid: list[dict], ore: dict) -> list[dict]:
    """Score each valid circuit using analytical process models.

    Returns list of scored circuits with recovery, opex, energy, npv.
    """
    results = []

    tph = ore["throughput_tph"]
    grade = ore["grade_au"]
    avail = ore["availability_pct"] / 100
    op_h = ore["op_hours_day"]
    gold_price = ore["gold_price"]
    mine_life = ore["mine_life_years"]
    discount = ore["discount_rate_pct"] / 100

    annual_tonnes = tph * op_h * 365 * avail

    for circuit in valid:
        # Adjust recovery based on ore characteristics
        rec = circuit["base_recovery"]

        # Gravity bonus
        if circuit["has_gravity"] and ore["grg_pct"] > 10:
            rec = min(0.99, rec + (ore["grg_pct"] - 10) * 0.001)

        # Flotation bonus
        if circuit["has_flotation"] and ore["flot_recovery_pct"] > 50:
            rec = min(0.99, rec + (ore["flot_recovery_pct"] - 50) * 0.0005)

        # Sulfide penalty for non-pretreat circuits
        if ore["s_total_pct"] > 2 and not circuit["has_pretreat"]:
            rec *= max(0.7, 1 - (ore["s_total_pct"] - 2) * 0.03)

        # Carbon organic penalty
        if ore["c_organic_pct"] > 0.3:
            rec *= max(0.85, 1 - (ore["c_organic_pct"] - 0.3) * 0.05)

        # Leach recovery cap — strict: circuit cannot exceed LIMS leach recovery
        if not circuit["has_pretreat"] and not circuit["is_heap"]:
            rec = min(rec, ore["leach_recovery_pct"] / 100.0)

        # Block model adjustment — blended LOM recovery
        if ore.get("bm_available"):
            # If significant sulfide proportion, penalize non-pretreat circuits
            sulfide_frac = ore.get("bm_sulfide_pct", 0) / 100
            oxide_frac = ore.get("bm_oxide_pct", 0) / 100

            if sulfide_frac > 0.3 and not circuit["has_pretreat"]:
                # Recovery drops for sulfide ore in non-pretreat circuits
                rec *= 1 - sulfide_frac * 0.15

            if oxide_frac > 0.5 and circuit.get("is_cip"):
                # CIP works well on oxide (no preg-robbing)
                rec = min(0.99, rec * 1.02)

        # Final recovery bounds: physically cannot exceed 100%
        rec = min(rec, 0.98)
        rec = max(rec, 0.0)

        # Energy calculation
        energy = ore["bwi"] * circuit["energy_factor"]
        if circuit["has_hpgr"]:
            energy *= 0.85  # HPGR energy savings
        if "VERTIMILL" in circuit.get("ops", []):
            energy += ore["bwi"] * 0.12
        if "VERTIMILL_REGRIND" in circuit.get("ops", []):
            energy += ore["bwi"] * 0.08
            rec = min(0.99, rec + 0.015)

        # OPEX calculation
        opex = circuit["opex_base"]
        opex += energy * 0.092  # electricity cost
        opex += ore["nacn_kg_t"] * 3.5  # NaCN cost
        if circuit["has_flotation"]:
            opex += 2.5  # flotation reagents
        if circuit["has_gravity"]:
            opex += 0.5  # gravity circuit maintenance

        # NPV proxy
        annual_oz = annual_tonnes * grade * rec * TROY_OZ_PER_GRAM
        revenue = annual_oz * gold_price
        opex_annual = opex * annual_tonnes
        annual_cf = revenue - opex_annual

        if discount > 0 and mine_life > 0:
            pv_factor = (1 - (1 + discount) ** (-mine_life)) / discount
        else:
            pv_factor = mine_life

        npv = annual_cf * pv_factor

        results.append(
            {
                "id": circuit["id"],
                "name": circuit["name"],
                "ops": circuit["ops"],
                "recovery_pct": round(rec * 100, 1),
                "energy_kwh_t": round(energy, 1),
                "opex_usd_t": round(opex, 2),
                "annual_gold_oz": round(annual_oz, 0),
                "npv_musd": round(npv / 1e6, 1),
                "is_pareto": False,  # computed below
            }
        )

    # Identify Pareto-optimal circuits (max recovery, min OPEX)
    for r in results:
        is_dominated = False
        for other in results:
            if other["id"] == r["id"]:
                continue
            if other["recovery_pct"] >= r["recovery_pct"] and other["opex_usd_t"] <= r["opex_usd_t"]:
                if other["recovery_pct"] > r["recovery_pct"] or other["opex_usd_t"] < r["opex_usd_t"]:
                    is_dominated = True
                    break
        r["is_pareto"] = not is_dominated

    # Sort by NPV descending
    results.sort(key=lambda x: x["npv_musd"], reverse=True)

    return results


# ─── Phase 3: Justification ─────────────────────────────────────────────────


def generate_comparison_summary(recommended: dict, evaluated: list[dict], ore: dict) -> str:
    """Explain why the recommended circuit wins vs the three other evaluated options."""
    if not recommended or not evaluated:
        return ""
    lines = [
        f"**{recommended['name']}** est recommandé après comparaison de 4 circuits candidats.",
        "",
        "Comparaison synthétique (NPV proxy @ paramètres projet) :",
    ]
    for i, c in enumerate(evaluated, 1):
        marker = " ← recommandé" if c["id"] == recommended["id"] else ""
        warn = ""
        if c.get("warnings"):
            warn = f" — attention : {c['warnings'][0]}"
        lines.append(
            f"{i}. **{c['name']}** : récup. {c['recovery_pct']} %, "
            f"OPEX {c['opex_usd_t']:.1f} $/t, NPV ~${c['npv_musd']:.0f} M{marker}{warn}"
        )
    lines.append("")
    lines.append(
        f"Le circuit retenu maximise le NPV estimé ({recommended['npv_musd']:.0f} M$) "
        f"tout en restant compatible avec le profil LIMS"
        + (" et le modèle de blocs" if ore.get("bm_available") else "")
        + ("." if not ore.get("dc_available") else ", les critères de conception et l'enveloppe économique du projet.")
    )
    return "\n".join(lines)


def generate_justification(recommended: dict, ore: dict) -> str:
    """Generate a textual justification for the recommended circuit."""
    name = recommended["name"]
    rec = recommended["recovery_pct"]
    opex = recommended["opex_usd_t"]
    npv = recommended["npv_musd"]
    mine_life = ore["mine_life_years"]

    parts = [f"Circuit recommande : **{name}**"]
    parts.append(f"Votre minerai presente :")

    # BWi
    bwi = ore["bwi"]
    if bwi > 20:
        parts.append(f"- BWi de {bwi:.0f} kWh/t (minerai dur — broyage intensif requis)")
    elif bwi > 14:
        parts.append(f"- BWi de {bwi:.0f} kWh/t (durete moderee)")
    else:
        parts.append(f"- BWi de {bwi:.0f} kWh/t (minerai tendre)")

    # GRG
    grg = ore["grg_pct"]
    if grg > 30:
        parts.append(f"- GRG de {grg:.0f}% (gravite tres rentable)")
    elif grg > 10:
        parts.append(f"- GRG de {grg:.0f}% (gravite benefique)")
    else:
        parts.append(f"- GRG de {grg:.0f}% (gravite non significative)")

    # Sulfides
    s = ore["s_total_pct"]
    if s > 5:
        parts.append(f"- Sulfures a {s:.1f}% (pretraitement necessaire)")
    elif s > 2:
        parts.append(f"- Sulfures a {s:.1f}% (moderement sulfure)")
    else:
        parts.append(f"- Sulfures a {s:.1f}% (non refractory)")

    # Carbon organic — CIP vs CIL (Stange 1999)
    c = ore["c_organic_pct"]
    if c > 0.5:
        parts.append(
            f"- Carbone organique a {c:.2f}% — **CIL obligatoire** "
            f"(gangue carbonée adsorbe Au(CN)2, charbon en pulpe — Stange 1999)"
        )
    elif c > 0.3:
        parts.append(f"- Carbone organique a {c:.2f}% — **CIL recommande** (preg-robbing, Stange §CIL)")
    elif c <= 0.1:
        parts.append(
            f"- Carbone organique a {c:.2f}% — **CIP privilegie** "
            f"(lixiviation puis adsorption en cascade — Stange §CIP)"
        )
    else:
        parts.append(f"- Carbone organique a {c:.2f}% — arbitrage CIP/CIL selon essais d'adsorption")

    # Block model insights
    if ore.get("bm_available"):
        parts.append("")
        parts.append("**Modele de blocs (LOM) :**")
        total_t = ore.get("bm_total_tonnage", 0)
        if total_t > 0:
            parts.append(f"- Reserve totale : {total_t:,.0f} t")
        dom = ore.get("bm_dominant_litho")
        if dom:
            parts.append(f"- Lithologie dominante : **{dom}**")
        ox = ore.get("bm_oxide_pct", 0)
        su = ore.get("bm_sulfide_pct", 0)
        tr = ore.get("bm_transition_pct", 0)
        if ox > 0 or su > 0:
            parts.append(f"- Repartition : Oxyde {ox:.0f}% | Sulfure {su:.0f}% | Transition {tr:.0f}%")
        # Metallurgical implications
        if su > 60:
            parts.append(f"- ⚠ Majorite sulfure ({su:.0f}%) — le circuit doit gerer la refractarite sur le long terme")
        elif ox > 60:
            parts.append(
                f"- ✓ Majorite oxyde ({ox:.0f}%) — lixiviation directe favorable pour la majorite de la reserve"
            )
        elif tr > 30:
            parts.append(
                f"- ⚡ Transition significative ({tr:.0f}%) — prevoir flexibilite du circuit (transition oxide→sulfure)"
            )
        # Lithotype table
        for lt in ore.get("bm_lithotypes", [])[:5]:
            parts.append(
                f"  · {lt['name']}: {lt.get('pct', 0):.0f}% ({lt['tonnage']:,.0f} t) @ {lt['grade_au']:.2f} g/t"
            )
    else:
        parts.append("")
        parts.append("_Modele de blocs non disponible — recommandation basee uniquement sur les donnees LIMS._")

    if ore.get("dc_available"):
        parts.append("")
        parts.append("**Critères de conception :** débit et paramètres procédé intégrés au calcul.")
    else:
        parts.append("")
        parts.append("_Critères de conception non renseignés — hypothèses projet par défaut._")

    if ore.get("economics_available"):
        parts.append("**Économie :** CAPEX équipements et OPEX énergie intégrés au proxy NPV.")
    else:
        parts.append("_Module économique partiel — NPV basé sur prix de l'or et coûts unitaires types._")

    parts.append("")
    parts.append(
        f"Ce circuit offre une recuperation estimee de **{rec}%** pour un OPEX de **{opex:.2f} $/t**, generant un NPV de **${npv:.0f}M** sur {mine_life} ans."
    )

    return "\n".join(parts)


# ─── Main Entry Point ────────────────────────────────────────────────────────


def recommend_circuit(pid: str, db_qall, db_qone) -> dict:
    """Main entry point: evaluate 4 circuits and recommend the best for this project.

    Returns: {recommended, candidates (4), filtered_out, ore_profile, justification, ...}
    """
    ore = extract_ore_profile(pid, db_qall, db_qone)
    logger.info("ore profile for %s: %s", pid, {k: v for k, v in ore.items() if k not in ("throughput_tph",)})

    try:
        from engines.cip_cil_advisor import recommend_cip_cil_from_lims
    except ImportError:
        from .cip_cil_advisor import recommend_cip_cil_from_lims

    try:
        cip_cil = recommend_cip_cil_from_lims(pid, db_qall, db_qone)
    except Exception:
        logger.warning("recommend_cip_cil_from_lims failed for %s", pid, exc_info=True)
        cip_cil = None

    leach_type = (cip_cil or {}).get("circuit_type")
    four, profile_label = select_four_circuits(ore, leach_type=leach_type)
    if len(four) < 4:
        four, profile_label = select_four_circuits(ore, leach_type=None)
    if len(four) < 4:
        four = CIRCUITS[:4]
        profile_label = profile_label or "standard"

    # Score exactly the four selected archetypes
    scored = score_circuits(four, ore)
    for s in scored:
        s["warnings"] = _compatibility_notes(_CIRCUIT_BY_ID.get(s["id"], {}), ore)

    # Prefer circuits without hard elimination warnings; else best NPV
    viable = [s for s in scored if not s.get("warnings")]
    ranked = viable if viable else scored
    ranked.sort(key=lambda x: x["npv_musd"], reverse=True)

    recommended = ranked[0]
    recommended["status"] = "Recommandé"
    for s in scored:
        if s["id"] == recommended["id"]:
            continue
        s["status"] = "Alternative"
        if s.get("warnings"):
            s["status"] = "Écarté (règles métallurgiques)"

    # Pareto among the four
    for s in scored:
        if s["id"] != recommended["id"] and s.get("is_pareto"):
            s["status"] = "Pareto"

    justification = generate_justification(recommended, ore)
    comparison = generate_comparison_summary(recommended, scored, ore)

    _, all_filtered = filter_circuits(ore)
    other_filtered = [
        {"id": f["id"], "name": f["name"], "reason": f["reasons"][0]}
        for f in all_filtered
        if f["id"] not in {c["id"] for c in four}
    ]

    return {
        "recommended": recommended,
        "candidates": scored,
        "evaluated_count": len(scored),
        "evaluation_profile": profile_label,
        "filtered_out": other_filtered,
        "ore_profile": ore,
        "justification": justification,
        "comparison_summary": comparison,
        "data_sources": _data_sources_payload(ore),
        "cip_cil": cip_cil,
    }


def _data_sources_payload(ore: dict) -> dict:
    return {
        "lims": True,
        "block_model": bool(ore.get("bm_available")),
        "design_criteria": bool(ore.get("dc_available")),
        "economics": bool(ore.get("economics_available")),
    }
