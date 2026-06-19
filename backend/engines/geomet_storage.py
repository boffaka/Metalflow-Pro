"""
Persist GMIE GADE runs to PostgreSQL and sync normalized geomet_domains rows.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

try:
    from ..db import execute, qone, conn as _db_conn, release as _db_release
except ImportError:  # pragma: no cover
    from db import execute, qone, conn as _db_conn, release as _db_release

logger = logging.getLogger("mpdpms.geomet_storage")

GADE_DOMAIN_PREFIX = "GADE-"


def _json_loads(val: Any) -> dict:
    if val is None:
        return {}
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        return json.loads(val)
    return dict(val)


def load_active_gade_run(pid: str) -> Optional[dict]:
    """Load the active persisted GADE result for a project, if any."""
    try:
        row = qone(
            """
            SELECT id, config_json, result_json, computed_at
            FROM project_geomet_runs
            WHERE project_id = %s AND is_active = TRUE
            ORDER BY computed_at DESC
            LIMIT 1
            """,
            (pid,),
        )
    except Exception as exc:
        logger.debug("load_active_gade_run skipped (table may not exist): %s", exc)
        return None

    if not row:
        return None

    result = _json_loads(row.get("result_json"))
    if result:
        result["persisted_run_id"] = str(row["id"])
        result["persisted_at"] = str(row.get("computed_at", ""))
        result["persisted_config"] = _json_loads(row.get("config_json"))
    return result or None


def persist_gade_run(
    pid: str,
    config: dict,
    result: dict,
    user_id: Optional[str] = None,
) -> Optional[dict]:
    """Save GADE snapshot and sync geomet_domains + sample assignments.

    The deactivate + insert pair runs inside a single transaction so that
    concurrent calls cannot both insert an active row and violate the partial
    unique index uq_geomet_run_active_project.
    """
    if result.get("status") != "ok":
        return None

    db = None
    try:
        db = _db_conn()
        import psycopg2.extras as _pge
        with db.cursor() as cur:
            cur.execute(
                "UPDATE project_geomet_runs SET is_active = FALSE "
                "WHERE project_id = %s AND is_active = TRUE",
                (pid,),
            )
        with db.cursor(cursor_factory=_pge.RealDictCursor) as cur2:
            cur2.execute(
                """
                INSERT INTO project_geomet_runs
                    (project_id, config_json, result_json, computed_by, is_active)
                VALUES (%s, %s::jsonb, %s::jsonb, %s, TRUE)
                RETURNING id, computed_at
                """,
                (pid, json.dumps(config), json.dumps(result), user_id),
            )
            row = cur2.fetchone()
        db.commit()
        saved = dict(row) if row else {}
        sync_geomet_domains_table(pid, result, user_id)
        return saved
    except Exception as exc:
        if db is not None:
            db.rollback()
        logger.warning("persist_gade_run failed for %s: %s", pid, exc)
        return None
    finally:
        if db is not None:
            _db_release(db)


def sync_geomet_domains_table(
    pid: str,
    result: dict,
    user_id: Optional[str] = None,
) -> None:
    """Upsert GADE-* geomet_domains and sample_geomet_domain assignments."""
    domain_id_map: dict[int, str] = {}

    for dom in result.get("domains", []):
        code = f"{GADE_DOMAIN_PREFIX}{dom['domain_id']}"
        notes = (
            f"GMIE-GADE v1 | n={dom.get('n_samples', 0)} | "
            f"recovery={dom.get('avg_recovery_pct')}% | risk={dom.get('risk_level', '—')}"
        )
        row = execute(
            """
            INSERT INTO geomet_domains (
                project_id, domain_code, domain_name, lithology,
                oxidation_state, hardness_class, representative, notes
            )
            VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s)
            ON CONFLICT (project_id, domain_code) DO UPDATE SET
                domain_name = EXCLUDED.domain_name,
                lithology = EXCLUDED.lithology,
                oxidation_state = EXCLUDED.oxidation_state,
                hardness_class = EXCLUDED.hardness_class,
                notes = EXCLUDED.notes
            RETURNING id
            """,
            (
                pid,
                code,
                dom.get("domain_name", code),
                dom.get("mineral_type") or dom.get("ore_class"),
                dom.get("recovery_class"),
                dom.get("bwi_class"),
                notes,
            ),
        )
        if row:
            domain_id_map[int(dom["domain_id"])] = str(row["id"])

    confidence = 85.0
    for assign in result.get("sample_assignments", []):
        sid = assign.get("sample_id")
        db_domain_id = domain_id_map.get(int(assign.get("domain_id", -1)))
        if not sid or not db_domain_id:
            continue
        execute(
            """
            INSERT INTO sample_geomet_domain (sample_id, domain_id, confidence_pct, assigned_by, notes)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (sample_id) DO UPDATE SET
                domain_id = EXCLUDED.domain_id,
                confidence_pct = EXCLUDED.confidence_pct,
                assigned_by = EXCLUDED.assigned_by,
                assigned_at = NOW(),
                notes = EXCLUDED.notes
            """,
            (sid, db_domain_id, confidence, user_id, "GMIE-GADE auto-assignment"),
        )
