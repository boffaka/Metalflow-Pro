"""
CIP vs CIL selection — metallurgical rules aligned with Stange (1999),
*The process design of gold leaching and carbon-in-pulp circuits*,
SAIMM, JANUARY/FEBRUARY 1999.

Summary (Stange):
- **CIP**: separate leach (20–40 h, 6–12 reactors) then adsorption cascade (6–8 tanks);
  countercurrent carbon transfer; preferred when ore is clean and preg-robbing is absent.
  Davidson (1988): leach–CIP can yield higher recovery than CIL on non-carbonaceous ores.
- **CIL**: carbon in leach — simultaneous leaching and adsorption; lower CAPEX (one agitator train)
  but larger tanks, flattened adsorption profile. **Required** when carbonaceous material
  adsorbs/precipitates Au(CN)₂⁻ — activated carbon in pulp captures gold immediately.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("mpdpms.cip_cil")

REFERENCE = (
    "W. Stange (1999), The process design of gold leaching and carbon-in-pulp circuits, "
    "SAIMM — CIP: lixiviation puis adsorption en cascade; CIL: charbon en cuve de lixiviation "
    "pour minerais carbonés / preg-robbing."
)


def _avg(rows: list[dict], field: str, default: Optional[float] = None) -> Optional[float]:
    vals = [float(r[field]) for r in rows if r.get(field) not in (None, "", 0)]
    return sum(vals) / len(vals) if vals else default


def recommend_cip_cil(
    *,
    c_organic_pct: Optional[float] = None,
    s_total_pct: Optional[float] = None,
    s_sulfide_pct: Optional[float] = None,
    as_ppm: Optional[float] = None,
    sb_ppm: Optional[float] = None,
    cu_pct: Optional[float] = None,
    nacn_kg_t: Optional[float] = None,
    leach_recovery_pct: Optional[float] = None,
    preg_rob_index: Optional[float] = None,
    gold_grade_g_t: Optional[float] = None,
    leach_recovery_cv: Optional[float] = None,
    has_lims_a1: bool = False,
    has_lims_d1: bool = False,
) -> dict[str, Any]:
    """
    Score > 0 → favour CIL; score <= -1 → CIP; else CIL (conservative default).

    Returns structured recommendation for API and UI.
    """
    score = 0
    reasons: list[str] = []
    c_org = float(c_organic_pct) if c_organic_pct is not None else None
    s_sulf = float(s_sulfide_pct if s_sulfide_pct is not None else s_total_pct or 0)
    as_avg = float(as_ppm or 0)
    sb_avg = float(sb_ppm or 0)
    cu_ppm = float(cu_pct or 0) * 10_000
    nacn = float(nacn_kg_t or 0)
    grade = float(gold_grade_g_t or 1.5)
    leach_rec = float(leach_recovery_pct) if leach_recovery_pct is not None else None

    # ── Stange: carbonaceous / preg-robbing → CIL ─────────────────────────────
    if c_org is not None:
        if c_org > 0.5:
            score += 4
            reasons.append(
                f"Carbone organique {c_org:.2f} % — matériau carboné adsorbe Au(CN)₂⁻ "
                f"(Stange §CIL) → **CIL impératif** (charbon en pulpe dès la lixiviation)"
            )
        elif c_org > 0.3:
            score += 3
            reasons.append(
                f"Carbone organique {c_org:.2f} % > 0,3 % — risque preg-robbing élevé → **CIL requis**"
            )
        elif c_org > 0.1:
            score += 2
            reasons.append(
                f"Carbone organique {c_org:.2f} % — preg-robbing modéré → **CIL recommandé**"
            )
        elif c_org <= 0.1:
            score -= 1
            reasons.append(
                f"Carbone organique {c_org:.2f} % faible — pas de preg-robbing significatif → **CIP envisageable**"
            )
    elif has_lims_a1:
        reasons.append("Carbone organique non renseigné (MIN-01a) — prudence : valider avant de choisir CIP")

    if preg_rob_index is not None and preg_rob_index > 10:
        score += 2 if preg_rob_index > 20 else 1
        reasons.append(
            f"Indice preg-robbing {preg_rob_index:.0f} — adsorption concurrente sur gangue → **CIL**"
        )

    # ── Stange: clean ore, decoupled leach + adsorption → CIP ─────────────────
    clean_ore = (
        c_org is not None
        and c_org < 0.05
        and s_sulf < 0.5
        and as_avg < 200
        and nacn < 0.8
    )
    if clean_ore:
        score -= 3
        reasons.append(
            "Minerai propre (C org., sulfures, As et NaCN faibles) — "
            "lixiviation 20–40 h puis cascade d'adsorption CIP (Stange §CIP) → **CIP optimal**"
        )

    if (
        c_org is not None
        and c_org < 0.1
        and leach_rec is not None
        and leach_rec >= 85
        and s_sulf < 3
    ):
        score -= 2
        reasons.append(
            f"Récupération lixiviation {leach_rec:.0f} % sur minerai non réfractaire — "
            "route Leach–CIP souvent plus efficace en récupération que CIL (Davidson, cité Stange)"
        )

    # Reactive sulfides, cyanide consumers → CIL (robustness)
    if s_sulf > 3.0:
        score += 2
        reasons.append(f"Sulfures réactifs {s_sulf:.1f} % — cinétique / consommation CN → CIL plus robuste")
    elif s_sulf > 1.5:
        score += 1

    if as_avg > 2000:
        score += 2
        reasons.append(f"Arsenic {as_avg:.0f} ppm — cyanicide / pénalisant → CIL")
    elif as_avg > 500:
        score += 1

    if sb_avg > 1000:
        score += 2
    elif sb_avg > 200:
        score += 1

    if cu_ppm > 2000:
        score += 2
    elif cu_ppm > 500:
        score += 1

    if nacn > 2.0:
        score += 2
        reasons.append(f"NaCN {nacn:.2f} kg/t — minéralogie complexe → CIL")
    elif nacn > 1.0:
        score += 1

    if grade < 1.0:
        score += 1
        reasons.append(f"Teneur {grade:.2f} g/t — maximiser récupération → CIL")

    if leach_recovery_cv is not None and leach_recovery_cv > 0.10:
        score += 1
        reasons.append("Variabilité récupération lixiviation — CIL plus tolérant aux variations d'aliment")

    if not has_lims_a1 and not has_lims_d1:
        reasons.append("Données LIMS insuffisantes — **CIL par défaut** (prudent, Stange)")
        score = max(score, 1)

    circuit = "CIP" if score <= -1 else "CIL"
    confidence = "high" if abs(score) >= 3 else "medium" if abs(score) >= 1 else "low"

    return {
        "circuit_type": circuit,
        "score": score,
        "confidence": confidence,
        "reasons": reasons,
        "reference": REFERENCE,
        "stange_summary": {
            "cip": (
                "Lixiviation (20–40 h) puis adsorption en cascade (6–8 cuves), "
                "transfert contre-courant du charbon — OPEX agitation optimisé."
            ),
            "cil": (
                "Charbon ajouté en lixiviation — lixiviation et adsorption simultanées, "
                "CAPEX réduit (un train d'agitateurs) — indiqué si gangue carbonée / preg-robbing."
            ),
        },
        "inputs": {
            "c_organic_pct": c_org,
            "s_total_pct": s_total_pct,
            "leach_recovery_pct": leach_rec,
            "nacn_kg_t": nacn if nacn else None,
            "gold_grade_g_t": grade,
        },
    }


def _safe_lims_rows(qall, table: str, pid: str) -> list[dict]:
    """SELECT * tolerates schema drift (missing optional columns)."""
    try:
        return list(qall(f"SELECT * FROM {table} WHERE project_id=%s", (pid,)) or [])
    except Exception:
        logger.warning("LIMS read failed for %s project=%s", table, pid, exc_info=True)
        return []


def recommend_cip_cil_from_lims(
    pid: str,
    qall,
    qone,
    project: Optional[dict] = None,
) -> dict[str, Any]:
    """Load LIMS + project fields and return CIP/CIL recommendation."""
    rows_a1 = _safe_lims_rows(qall, "lims_a1", pid)
    rows_d1 = _safe_lims_rows(qall, "lims_d1", pid)
    rows_a3 = _safe_lims_rows(qall, "lims_a3", pid)

    p = project if project is not None else qone(
        "SELECT gold_grade_g_t FROM projects WHERE id=%s", (pid,)
    )

    rec_vals = []
    for r in rows_d1:
        for key in ("au_recovery_pct", "leach_rec_48h_pct", "leach_rec_24h_pct"):
            v = r.get(key)
            if v not in (None, "", 0):
                try:
                    rec_vals.append(float(v))
                except (TypeError, ValueError):
                    pass
    leach_cv = None
    if len(rec_vals) >= 2:
        mean_r = sum(rec_vals) / len(rec_vals)
        std_r = (sum((x - mean_r) ** 2 for x in rec_vals) / len(rec_vals)) ** 0.5
        leach_cv = std_r / mean_r if mean_r > 0 else 0.0

    leach_rec = _avg(rows_d1, "au_recovery_pct")
    if leach_rec is None:
        leach_rec = _avg(rows_d1, "leach_rec_48h_pct") or _avg(rows_d1, "leach_rec_24h_pct")

    return recommend_cip_cil(
        c_organic_pct=_avg(rows_a1, "c_organic_pct"),
        s_total_pct=_avg(rows_a1, "s_total_pct"),
        s_sulfide_pct=_avg(rows_a1, "s_sulfide_pct"),
        as_ppm=_avg(rows_a1, "as_ppm"),
        sb_ppm=_avg(rows_a1, "sb_ppm"),
        cu_pct=_avg(rows_a1, "cu_pct"),
        nacn_kg_t=_avg(rows_d1, "nacn_consumption_kg_t"),
        leach_recovery_pct=leach_rec,
        preg_rob_index=_avg(rows_a3, "preg_rob_index") or _avg(rows_a3, "au_preg_rob_pct"),
        gold_grade_g_t=float((p or {}).get("gold_grade_g_t") or 1.5),
        leach_recovery_cv=leach_cv,
        has_lims_a1=bool(rows_a1),
        has_lims_d1=bool(rows_d1),
    )
