"""
Innovations — **readiness ingénierie** & **fidélité jumeau numérique** (heuristiques).

Ces scores sont des indicateurs *product* (0–100) basés sur la richesse des
données projet : ils ne remplacent pas un audit métier. Utilisés par
``GET /api/v1/projects/{pid}/insights/…``.

``WEIGHTS_VERSION`` : incrémenter lors d'un changement de logique pour comparer
les séries temporelles côté analytics.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

WEIGHTS_VERSION = "2026-05-14-v2"

# Readiness — poids (somme = 100)
W_ACTIVE_CIRCUIT = 18
W_DC_CRITERIA = 18
W_MASS_BALANCE = 22
W_MER = 12
W_SIM_PARAMS = 12
W_LIMS = 13
W_FLOWSHEET = 5

# Seuils « plein score » pour les parties progressives
DC_CRITERIA_FULL = 12
MB_SECTIONS_FULL = 6
MER_ITEMS_FULL = 25
SIM_PARAMS_FULL = 30

LIMS_RECENT_DAYS = 90

# Fidélité — poids des composantes (somme = 1.0)
FID_W_MB = 0.30
FID_W_SIM = 0.22
FID_W_EQ = 0.18
FID_W_RUNS = 0.15
FID_W_LIMS = 0.15

try:
    from ..db import qone
except ImportError:  # pragma: no cover
    from db import qone


def _regclass_exists(qualified: str) -> bool:
    row = qone("SELECT to_regclass(%s) AS t", (qualified,))
    return bool(row and row.get("t"))


def _safe_int(row: dict | None, key: str) -> int:
    if not row or row.get(key) is None:
        return 0
    try:
        return int(row[key])
    except (TypeError, ValueError):
        return 0


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _smooth_cap(value: int, half_sat: float) -> float:
    """Croissance saturante vers 1 ; *half_sat* = valeur où score ≈ 0,5."""
    if value <= 0:
        return 0.0
    return _clamp01(1.0 - math.exp(-value / half_sat))


def compute_engineering_readiness(pid: str) -> dict[str, Any]:
    """Score 0–100 + gates avec **crédit partiel** (fraction 0–1) sur la chaîne DC→MB."""
    gates: list[dict[str, Any]] = []
    earned = 0.0
    possible = 0.0

    def add_gate(
        gid: str,
        label: str,
        fraction: float,
        weight: float,
        hint: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        nonlocal earned, possible
        frac = _clamp01(fraction)
        possible += weight
        pts = weight * frac
        earned += pts
        gates.append(
            {
                "id": gid,
                "label": label,
                "fraction": round(frac, 4),
                "weight": weight,
                "points": round(pts, 2),
                "ok": frac >= 0.95,
                "hint": hint,
                "detail": detail or {},
            }
        )

    tpl = None
    if _regclass_exists("public.circuit_templates"):
        tpl = qone(
            "SELECT id::text FROM circuit_templates "
            "WHERE project_id=%s AND COALESCE(is_active, FALSE)=TRUE ORDER BY created_at DESC LIMIT 1",
            (pid,),
        )
    add_gate(
        "active_circuit",
        "Gabarit de circuit actif",
        1.0 if tpl else 0.0,
        W_ACTIVE_CIRCUIT,
        "Activez un template dans Critères de conception.",
        {"template_id": tpl["id"] if tpl else None},
    )

    crit_n = 0
    if tpl and _regclass_exists("public.circuit_criteria"):
        crit_n = _safe_int(
            qone(
                "SELECT COUNT(*)::int AS n FROM circuit_criteria WHERE template_id=%s",
                (tpl["id"],),
            ),
            "n",
        )
    crit_frac = (_clamp01(crit_n / DC_CRITERIA_FULL) if tpl else 0.0)
    add_gate(
        "circuit_criteria",
        "Critères DC peuplés",
        crit_frac,
        W_DC_CRITERIA,
        f"Viser ≥{DC_CRITERIA_FULL} critères pour couvrir le gabarit.",
        {"criteria_count": crit_n, "target": DC_CRITERIA_FULL},
    )

    mb_sec = 0
    if _regclass_exists("public.mass_balance_sections_v2"):
        mb_sec = _safe_int(
            qone("SELECT COUNT(*)::int AS n FROM mass_balance_sections_v2 WHERE project_id=%s", (pid,)),
            "n",
        )
    mb_frac = _clamp01(mb_sec / MB_SECTIONS_FULL) if mb_sec else 0.0
    add_gate(
        "mass_balance_v2",
        "Bilan massique v2 (sections)",
        mb_frac,
        W_MASS_BALANCE,
        "Générez le bilan ; ajoutez des sections au besoin pour refléter le circuit.",
        {"sections": mb_sec, "target_sections": MB_SECTIONS_FULL},
    )

    mer_n = 0
    if _regclass_exists("public.equipment_v2"):
        mer_n = _safe_int(
            qone(
                "SELECT COUNT(*)::int AS n FROM equipment_v2 WHERE project_id=%s AND COALESCE(enabled, TRUE)",
                (pid,),
            ),
            "n",
        )
    mer_frac = _clamp01(mer_n / MER_ITEMS_FULL) if mer_n else 0.0
    add_gate(
        "mer",
        "Registre équipements (MER)",
        mer_frac,
        W_MER,
        "Enrichissez le MER (auto-génération puis compléments vendor).",
        {"items": mer_n, "target_items": MER_ITEMS_FULL},
    )

    sim_n = 0
    if _regclass_exists("public.simulation_params"):
        sim_n = _safe_int(
            qone("SELECT COUNT(*)::int AS n FROM simulation_params WHERE project_id=%s", (pid,)),
            "n",
        )
    sim_frac = _clamp01(sim_n / SIM_PARAMS_FULL) if sim_n else 0.0
    add_gate(
        "simulation_params",
        "Paramètres simulation",
        sim_frac,
        W_SIM_PARAMS,
        "Importez / complétez les groupes procédé & financier.",
        {"params": sim_n, "target_params": SIM_PARAMS_FULL},
    )

    lims_total = 0
    lims_recent = 0
    lims_since = datetime.now(timezone.utc) - timedelta(days=LIMS_RECENT_DAYS)
    if _regclass_exists("public.lims_samples"):
        row = qone(
            "SELECT COUNT(*)::int AS n, "
            "COUNT(*) FILTER (WHERE created_at >= %s)::int AS n_recent "
            "FROM lims_samples WHERE project_id=%s",
            (lims_since, pid),
        ) or {}
        lims_total = _safe_int(row, "n")
        lims_recent = _safe_int(row, "n_recent")
    # Fraction LIMS : densité + fraîcheur (50 % volume cible, 50 % récence)
    vol_frac = _clamp01(lims_total / 20.0) if lims_total else 0.0
    rec_frac = _clamp01(lims_recent / max(lims_total, 1)) if lims_total else 0.0
    lims_combo = 0.6 * vol_frac + 0.4 * rec_frac if lims_total else 0.0
    add_gate(
        "lims",
        "LIMS — volume & récence",
        lims_combo,
        W_LIMS,
        f"Importez des échantillons ; idéalement des créations < {LIMS_RECENT_DAYS} j.",
        {
            "samples_total": lims_total,
            "samples_recent_90d": lims_recent,
            "recent_window_days": LIMS_RECENT_DAYS,
        },
    )

    fs = False
    table = "flowsheets"
    if _regclass_exists("public.flowsheets"):
        fs = bool(qone("SELECT 1 AS ok FROM flowsheets WHERE project_id=%s LIMIT 1", (pid,)))
    elif _regclass_exists("public.flowshheets"):
        table = "flowshheets"
        fs = bool(qone("SELECT 1 AS ok FROM flowshheets WHERE project_id=%s LIMIT 1", (pid,)))
    add_gate(
        "flowsheet",
        "Flowsheet généré",
        1.0 if fs else 0.0,
        W_FLOWSHEET,
        f"Auto-générez le flowsheet ({table}).",
        {"has_flowsheet": fs},
    )

    score = round(100.0 * earned / possible) if possible else 0
    missing = [g["id"] for g in gates if not g["ok"]]
    return {
        "kind": "engineering_readiness",
        "weights_version": WEIGHTS_VERSION,
        "score": score,
        "earned": round(earned, 2),
        "possible": round(possible, 2),
        "gates": gates,
        "missing_gate_ids": missing,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def compute_digital_twin_fidelity(pid: str) -> dict[str, Any]:
    """Heuristique 0–100 : densité de données + **moyenne pondérée** + chaîne LIMS."""
    components: dict[str, Any] = {}

    mb_streams = 0
    if _regclass_exists("public.mass_balance_streams_v2"):
        mb_streams = _safe_int(
            qone("SELECT COUNT(*)::int AS n FROM mass_balance_streams_v2 WHERE project_id=%s", (pid,)),
            "n",
        )
    mb_score = round(100 * _smooth_cap(mb_streams, 12.0))
    components["mass_balance_streams"] = {
        "raw": mb_streams,
        "score": mb_score,
        "note": "Courbe saturante : les premiers flux apportent le plus de valeur.",
    }

    sim_n = 0
    if _regclass_exists("public.simulation_params"):
        sim_n = _safe_int(
            qone("SELECT COUNT(*)::int AS n FROM simulation_params WHERE project_id=%s", (pid,)),
            "n",
        )
    sim_score = round(100 * _smooth_cap(sim_n, 18.0))
    components["simulation_params"] = {
        "raw": sim_n,
        "score": sim_score,
        "note": "Paramètres procédé / financiers pour calage modèle.",
    }

    eq_total = eq_kw = 0
    if _regclass_exists("public.equipment_v2"):
        row = qone(
            "SELECT COUNT(*)::int AS n, "
            "COUNT(*) FILTER (WHERE COALESCE(installed_kw,0) > 0)::int AS nk "
            "FROM equipment_v2 WHERE project_id=%s AND COALESCE(enabled, TRUE)",
            (pid,),
        ) or {}
        eq_total = _safe_int(row, "n")
        eq_kw = _safe_int(row, "nk")
    kw_ratio = (eq_kw / eq_total) if eq_total else 0.0
    density = _smooth_cap(eq_total, 15.0)
    eq_score = round(100 * (0.55 * density + 0.45 * kw_ratio))
    components["equipment_energy_tags"] = {
        "items": eq_total,
        "with_power_kw": eq_kw,
        "score": eq_score,
        "note": "Combine volumétrie MER et tags énergie (kW).",
    }

    recent_runs = 0
    if _regclass_exists("public.simulation_runs"):
        since = datetime.now(timezone.utc) - timedelta(days=30)
        recent_runs = _safe_int(
            qone(
                "SELECT COUNT(*)::int AS n FROM simulation_runs "
                "WHERE project_id=%s AND created_at >= %s AND status = 'done'",
                (pid, since),
            ),
            "n",
        )
    run_score = round(100 * _smooth_cap(recent_runs, 2.0))
    components["recent_simulation_runs"] = {
        "done_last_30d": recent_runs,
        "score": run_score,
        "note": "Runs récents = boucle calibration plus crédible.",
    }

    lims_chain = 0
    if _regclass_exists("public.lims_a1") and _regclass_exists("public.lims_b1"):
        lims_chain = _safe_int(
            qone(
                "SELECT (SELECT COUNT(*)::int FROM lims_a1 WHERE project_id=%s) + "
                "(SELECT COUNT(*)::int FROM lims_b1 WHERE project_id=%s) AS n",
                (pid, pid),
            ),
            "n",
        )
    lims_score = round(100 * _smooth_cap(lims_chain, 20.0))
    components["lims_test_chain"] = {
        "a1_b1_rows": lims_chain,
        "score": lims_score,
        "note": "Densité MIN-01a + BWi (chaîne essais → modèle).",
    }

    overall = round(
        FID_W_MB * components["mass_balance_streams"]["score"]
        + FID_W_SIM * components["simulation_params"]["score"]
        + FID_W_EQ * components["equipment_energy_tags"]["score"]
        + FID_W_RUNS * components["recent_simulation_runs"]["score"]
        + FID_W_LIMS * components["lims_test_chain"]["score"],
        1,
    )

    return {
        "kind": "digital_twin_fidelity",
        "weights_version": WEIGHTS_VERSION,
        "score": overall,
        "weights": {
            "mass_balance_streams": FID_W_MB,
            "simulation_params": FID_W_SIM,
            "equipment_energy_tags": FID_W_EQ,
            "recent_simulation_runs": FID_W_RUNS,
            "lims_test_chain": FID_W_LIMS,
        },
        "components": components,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
