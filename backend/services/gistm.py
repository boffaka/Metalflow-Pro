"""GISTM design basis service — orchestration of versioned design basis,
violation persistence, and owner-signed overrides.

Pure compute lives in `engines/gistm.py`; this module wraps it with DB I/O
and atomic state transitions (draft → active → superseded).
"""
from __future__ import annotations

import logging
from typing import Any

try:
    from db import qone, qall, execute
    from engines.gistm import (
        ClassificationMatrix,
        ConsequenceInputs,
        DesignBasisSnapshot,
        TSFDesignSnapshot,
        Violation,
        classify_consequence,
        derive_design_criteria,
        load_default_matrix,
        validate_tsf_design,
    )
    from services.transaction import transaction
except ImportError:  # pragma: no cover
    from backend.db import qone, qall, execute
    from backend.engines.gistm import (
        ClassificationMatrix,
        ConsequenceInputs,
        DesignBasisSnapshot,
        TSFDesignSnapshot,
        Violation,
        classify_consequence,
        derive_design_criteria,
        load_default_matrix,
        validate_tsf_design,
    )
    from backend.services.transaction import transaction

logger = logging.getLogger("mpdpms.services.gistm")

_DEFAULT_MATRIX: ClassificationMatrix | None = None


def get_matrix() -> ClassificationMatrix:
    """Lazy singleton for the default classification matrix."""
    global _DEFAULT_MATRIX
    if _DEFAULT_MATRIX is None:
        _DEFAULT_MATRIX = load_default_matrix()
    return _DEFAULT_MATRIX


# --- Preview (no persist) ----------------------------------------------------


def preview_criteria(inputs: ConsequenceInputs) -> dict[str, Any]:
    """Run classification + criteria derivation without touching DB.

    Used by the frontend live-preview during basis form filling.
    """
    matrix = get_matrix()
    cls = classify_consequence(inputs, matrix)
    crit = derive_design_criteria(cls, matrix)
    return {
        "consequence_class": crit.consequence_class,
        "idf_return_period_yr": crit.idf_return_period_yr,
        "mde_return_period_yr": crit.mde_return_period_yr,
        "fs_static_min": crit.fs_static_min,
        "fs_seismic_min": crit.fs_seismic_min,
        "fs_post_liquefaction_min": crit.fs_post_liquefaction_min,
        "allowed_construction_methods": crit.allowed_construction_methods,
        "pga_threshold_g": crit.pga_threshold_g,
    }


# --- Design basis CRUD --------------------------------------------------------


def create_design_basis(
    project_id: str,
    inputs: ConsequenceInputs,
    notes: str | None,
    created_by: str,
) -> dict[str, Any]:
    """Persist a new draft basis (status='draft') with derived criteria snapshotted."""
    matrix = get_matrix()
    cls = classify_consequence(inputs, matrix)
    crit = derive_design_criteria(cls, matrix)

    next_version_row = qone(
        "SELECT COALESCE(MAX(version), 0) + 1 AS v "
        "FROM gistm_design_basis WHERE project_id = %s",
        (project_id,),
    )
    next_version = int(next_version_row["v"]) if next_version_row else 1

    row = execute(
        """
        INSERT INTO gistm_design_basis (
            project_id, version, status,
            par_count, env_damage_class, economic_damage_usd_m, critical_infra_downstream,
            consequence_class,
            idf_return_period_yr, mde_return_period_yr,
            fs_static_min, fs_seismic_min, fs_post_liquefaction_min,
            allowed_construction_methods, pga_threshold_g,
            created_by, notes
        ) VALUES (
            %s, %s, 'draft',
            %s, %s, %s, %s,
            %s,
            %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, %s
        )
        RETURNING *
        """,
        (
            project_id, next_version,
            inputs.par_count, inputs.env_damage_class,
            inputs.economic_damage_usd_m, inputs.critical_infra_downstream,
            crit.consequence_class,
            crit.idf_return_period_yr, crit.mde_return_period_yr,
            crit.fs_static_min, crit.fs_seismic_min, crit.fs_post_liquefaction_min,
            crit.allowed_construction_methods, crit.pga_threshold_g,
            created_by, notes,
        ),
    )
    return dict(row) if row else {}


def get_active_basis(project_id: str) -> dict[str, Any] | None:
    return qone(
        "SELECT * FROM gistm_design_basis "
        "WHERE project_id = %s AND status = 'active' "
        "LIMIT 1",
        (project_id,),
    )


def get_basis(basis_id: str) -> dict[str, Any] | None:
    return qone("SELECT * FROM gistm_design_basis WHERE id = %s", (basis_id,))


def list_history(project_id: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    items = qall(
        "SELECT * FROM gistm_design_basis "
        "WHERE project_id = %s "
        "ORDER BY version DESC LIMIT %s OFFSET %s",
        (project_id, limit, offset),
    )
    total_row = qone(
        "SELECT COUNT(*) AS c FROM gistm_design_basis WHERE project_id = %s",
        (project_id,),
    )
    return {"items": items, "total": int(total_row["c"]) if total_row else 0}


def activate_basis(basis_id: str, project_id: str, activated_by: str) -> dict[str, Any]:
    """Atomic: supersede the current active basis and activate the new one.

    Raises ValueError on:
      - basis not found in this project
      - basis already 'superseded' (cannot reactivate a superseded version)
    """
    with transaction(dict_cursor=True) as cur:
        cur.execute(
            "SELECT id, project_id, status FROM gistm_design_basis "
            "WHERE id = %s AND project_id = %s "
            "FOR UPDATE",
            (basis_id, project_id),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("basis not found in this project")
        if row["status"] == "superseded":
            raise ValueError("cannot reactivate a superseded basis")
        if row["status"] == "active":
            return _basis_dict_from_id(cur, basis_id)

        # Supersede currently-active basis (if any)
        cur.execute(
            "UPDATE gistm_design_basis SET status = 'superseded' "
            "WHERE project_id = %s AND status = 'active'",
            (project_id,),
        )
        # Activate this one
        cur.execute(
            "UPDATE gistm_design_basis "
            "SET status = 'active', activated_by = %s, activated_at = NOW() "
            "WHERE id = %s",
            (activated_by, basis_id),
        )
        return _basis_dict_from_id(cur, basis_id)


def _basis_dict_from_id(cur: Any, basis_id: str) -> dict[str, Any]:
    cur.execute("SELECT * FROM gistm_design_basis WHERE id = %s", (basis_id,))
    return dict(cur.fetchone())


# --- Validation + persistence of violations ----------------------------------


def evaluate_tsf_design(
    project_id: str, tsf_design_id: str, tsf: TSFDesignSnapshot
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Evaluate a TSF design against the active basis, persist violations.

    Returns (active_basis_row | None, violations_with_overrides).
    """
    active = get_active_basis(project_id)
    basis_snapshot: DesignBasisSnapshot | None = None
    if active is not None:
        basis_snapshot = DesignBasisSnapshot(
            consequence_class=active["consequence_class"],
            idf_return_period_yr=int(active["idf_return_period_yr"]),
            mde_return_period_yr=int(active["mde_return_period_yr"]),
            fs_static_min=float(active["fs_static_min"]),
            fs_seismic_min=float(active["fs_seismic_min"]),
            fs_post_liquefaction_min=float(active["fs_post_liquefaction_min"]),
            allowed_construction_methods=list(active["allowed_construction_methods"]),
            pga_threshold_g=(
                float(active["pga_threshold_g"])
                if active["pga_threshold_g"] is not None
                else None
            ),
        )
    violations = validate_tsf_design(tsf, basis_snapshot)

    persisted: list[dict[str, Any]] = []
    if active is None:
        # NO_ACTIVE_BASIS warning is informational only — not persisted (no FK target)
        for v in violations:
            persisted.append(_violation_to_dict(None, v))
    else:
        with transaction(dict_cursor=True) as cur:
            for v in violations:
                cur.execute(
                    """
                    INSERT INTO gistm_violations
                        (project_id, basis_id, tsf_design_id,
                         rule_code, severity, observed_value, required_value, message)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                    RETURNING id
                    """,
                    (
                        project_id, active["id"], tsf_design_id,
                        v.rule_code, v.severity,
                        _json(v.observed_value), _json(v.required_value),
                        v.message,
                    ),
                )
                vid = cur.fetchone()["id"]
                persisted.append(_violation_to_dict(str(vid), v))
    return (active, persisted)


def _violation_to_dict(vid: str | None, v: Violation) -> dict[str, Any]:
    return {
        "id": vid,
        "rule_code": v.rule_code,
        "severity": v.severity,
        "message": v.message,
        "observed_value": v.observed_value,
        "required_value": v.required_value,
        "override": None,
    }


def _json(d: dict) -> str:
    import json
    return json.dumps(d)


# --- Override (owner-signed) -------------------------------------------------


def record_override(
    violation_id: str, justification: str, signed_by: str
) -> dict[str, Any]:
    """Persist an override on a violation. Owner-only — RBAC enforced at route."""
    if len(justification) < 50:
        raise ValueError("justification must be at least 50 characters")
    row = execute(
        """
        INSERT INTO gistm_overrides (violation_id, justification, signed_by)
        VALUES (%s, %s, %s)
        RETURNING *
        """,
        (violation_id, justification, signed_by),
    )
    return dict(row) if row else {}


def list_violations_for_tsf(tsf_design_id: str) -> list[dict[str, Any]]:
    """Return violations + their attached override (if any), for display."""
    return qall(
        """
        SELECT v.*,
               o.id AS override_id,
               o.justification AS override_justification,
               o.signed_by AS override_signed_by,
               o.signed_at AS override_signed_at
        FROM gistm_violations v
        LEFT JOIN gistm_overrides o ON o.violation_id = v.id
        WHERE v.tsf_design_id = %s
        ORDER BY v.detected_at ASC
        """,
        (tsf_design_id,),
    )
