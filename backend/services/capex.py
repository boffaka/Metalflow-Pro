"""CAPEX computation service.

Reads `equipment_v2` and `capex_factors`; recomputes parametric prices
on un-overridden rows; aggregates with three Lang-style factors
(indirect / EPCM / contingency, cumulative).
"""
from __future__ import annotations

from typing import Any

try:
    from db import qone, qall, execute
    from services.circuit_templates import load_template
except ImportError:  # pragma: no cover
    from backend.db import qone, qall, execute
    from backend.services.circuit_templates import load_template


def parametric_price(alpha: float, beta: float, target_tph: float) -> float:
    """Class-5 parametric cost: alpha * tph^beta. Returns 0 if tph<=0."""
    if target_tph is None or target_tph <= 0:
        return 0.0
    return float(alpha) * (float(target_tph) ** float(beta))


def aggregate_totals(*, direct: float, indirect_pct: float,
                     epcm_pct: float, contingency_pct: float) -> dict[str, float]:
    """Apply cumulative factors. See spec §6.2. Percentages are in % (e.g. 15 = 15%)."""
    indirect = direct * indirect_pct / 100.0
    epcm = (direct + indirect) * epcm_pct / 100.0
    contingency = (direct + indirect + epcm) * contingency_pct / 100.0
    return {
        "direct_cad": direct,
        "indirect_cad": indirect,
        "epcm_cad": epcm,
        "contingency_cad": contingency,
        "total_cad": direct + indirect + epcm + contingency,
    }


def compute_total(pid: str) -> dict[str, Any]:
    """Read-only aggregate of CAPEX for a project. Returns the spec §6.2 dict."""
    direct_row = qone(
        "SELECT COALESCE(SUM(price_cad), 0) AS s "
        "FROM equipment_v2 WHERE project_id=%s AND enabled=true",
        (pid,),
    )
    direct = float(direct_row["s"]) if direct_row else 0.0

    factors_row = qone(
        "SELECT indirect_pct, epcm_pct, contingency_pct, "
        "       is_overridden_indirect, is_overridden_epcm, is_overridden_contingency "
        "FROM capex_factors WHERE project_id=%s",
        (pid,),
    )
    if not factors_row:
        execute(
            "INSERT INTO capex_factors (project_id) VALUES (%s) "
            "ON CONFLICT (project_id) DO NOTHING",
            (pid,),
        )
        factors_row = qone(
            "SELECT indirect_pct, epcm_pct, contingency_pct, "
            "       is_overridden_indirect, is_overridden_epcm, is_overridden_contingency "
            "FROM capex_factors WHERE project_id=%s",
            (pid,),
        )

    out = aggregate_totals(
        direct=direct,
        indirect_pct=float(factors_row["indirect_pct"]),
        epcm_pct=float(factors_row["epcm_pct"]),
        contingency_pct=float(factors_row["contingency_pct"]),
    )
    out["factor_pcts"] = {
        "indirect": float(factors_row["indirect_pct"]),
        "epcm": float(factors_row["epcm_pct"]),
        "contingency": float(factors_row["contingency_pct"]),
    }
    out["overridden"] = {
        "indirect": bool(factors_row["is_overridden_indirect"]),
        "epcm": bool(factors_row["is_overridden_epcm"]),
        "contingency": bool(factors_row["is_overridden_contingency"]),
    }
    return out


def recompute_for_project(pid: str) -> dict[str, Any]:
    """For each equipment_v2 row where is_overridden=False AND parametric coefs are set:
    recompute price_cad using projects.target_tph. Skip if target_tph is null/0
    (log warning). Returns the aggregate via compute_total."""
    import logging
    log = logging.getLogger("mpdpms")

    proj = qone("SELECT target_tph FROM projects WHERE id=%s", (pid,))
    if not proj or proj["target_tph"] is None or float(proj["target_tph"]) <= 0:
        log.warning("recompute_for_project: target_tph absent for project %s — "
                    "skipping parametric pass", pid)
        return compute_total(pid)

    tph = float(proj["target_tph"])
    rows = qall(
        "SELECT id, parametric_alpha, parametric_beta "
        "FROM equipment_v2 "
        "WHERE project_id=%s AND is_overridden=false "
        "  AND parametric_alpha IS NOT NULL AND parametric_beta IS NOT NULL",
        (pid,),
    )
    for r in rows:
        new_price = parametric_price(
            float(r["parametric_alpha"]), float(r["parametric_beta"]), tph,
        )
        execute(
            "UPDATE equipment_v2 SET price_cad=%s, updated_at=now() "
            "WHERE id=%s",
            (new_price, r["id"]),
        )
    return compute_total(pid)


def seed_from_template(pid: str, circuit_type: str, force: bool = False) -> int:
    """Insert/update equipment_v2 + capex_factors from a YAML template.
    - force=False: skip equipment_v2 rows with is_overridden=true.
    - force=True: delete WHERE seeded_from_template=true AND is_overridden=false,
      then insert fresh from template. Manual rows (seeded_from_template=false) survive.
    Updates projects.circuit_type. Returns count of upserted equipment rows."""
    template = load_template(circuit_type)
    proj = qone("SELECT target_tph FROM projects WHERE id=%s", (pid,))
    if not proj:
        raise ValueError(f"Project {pid} not found")
    tph = float(proj["target_tph"]) if proj["target_tph"] else 0.0

    execute("UPDATE projects SET circuit_type=%s WHERE id=%s", (circuit_type, pid))

    if force:
        execute(
            "DELETE FROM equipment_v2 "
            "WHERE project_id=%s AND seeded_from_template=true AND is_overridden=false",
            (pid,),
        )

    upserted = 0
    for idx, item in enumerate(template["equipment"], start=1):
        existing = qone(
            "SELECT id, is_overridden FROM equipment_v2 "
            "WHERE project_id=%s AND template_key=%s",
            (pid, item["template_key"]),
        )
        alpha = float(item["cost"]["alpha"])
        beta = float(item["cost"]["beta"])
        price = parametric_price(alpha, beta, tph) if tph > 0 else 0.0

        if existing and existing["is_overridden"] and not force:
            continue  # preserve user override
        if existing:
            execute(
                "UPDATE equipment_v2 SET equipment_name=%s, eq_type=%s, "
                "  wbs_description=%s, price_cad=%s, "
                "  parametric_alpha=%s, parametric_beta=%s, "
                "  seeded_from_template=true, updated_at=now() "
                "WHERE id=%s",
                (item["name"], item["category"], item["name"], price,
                 alpha, beta, existing["id"]),
            )
        else:
            import uuid as _u
            seq = str(idx).zfill(3)
            tk = str(item["template_key"])
            tag = f"{tk[:18].upper()}-{seq}"
            wbs_code = (item.get("category") or "GEN")[:6].upper()
            execute(
                "INSERT INTO equipment_v2 "
                "(id, project_id, wbs_code, wbs_description, eq_type, seq_no, "
                " equipment_tag, equipment_name, price_cad, enabled, "
                " template_key, parametric_alpha, parametric_beta, "
                " seeded_from_template, is_overridden) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s, %s, true, false)",
                (str(_u.uuid4()), pid, wbs_code, item["name"], item["category"],
                 seq, tag, item["name"], price,
                 item["template_key"], alpha, beta),
            )
        upserted += 1

    execute(
        "INSERT INTO capex_factors (project_id) VALUES (%s) "
        "ON CONFLICT (project_id) DO NOTHING",
        (pid,),
    )

    defaults = template.get("default_factors", {})
    sets = []
    vals: list[float | str] = []
    for col, key in [("indirect_pct", "indirect_pct"),
                     ("epcm_pct", "epcm_pct"),
                     ("contingency_pct", "contingency_pct")]:
        if key in defaults:
            sets.append(f"{col} = CASE WHEN is_overridden_{col.replace('_pct','')} "
                        f"THEN {col} ELSE %s END")
            vals.append(float(defaults[key]))
    if sets:
        vals.append(pid)
        execute(
            f"UPDATE capex_factors SET {', '.join(sets)}, updated_at=now() "
            f"WHERE project_id=%s",
            tuple(vals),
        )
    return upserted
