"""Circuit Optimizer API — recommend optimal circuit from LIMS data."""
from __future__ import annotations

import logging
import psycopg2
from fastapi import APIRouter, HTTPException, Depends

logger = logging.getLogger("mpdpms.circuit_optimizer")

try:
    from ..auth import project_user
    from ..db import execute, qall, qone
    from ..engines.circuit_optimizer import recommend_circuit
    from ..engines.cip_cil_advisor import recommend_cip_cil_from_lims
    from ..engines.circuit_strategy import analyze_circuit_strategy
except ImportError:
    from auth import project_user
    from db import execute, qall, qone
    from engines.circuit_optimizer import recommend_circuit
    from engines.cip_cil_advisor import recommend_cip_cil_from_lims
    from engines.circuit_strategy import analyze_circuit_strategy

router = APIRouter(tags=["circuit-optimizer"])


@router.get("/{pid}/cip-cil/recommend")
def recommend_cip_cil(pid: str, user=Depends(project_user)):
    """CIP vs CIL recommendation from LIMS (Stange 1999 design basis)."""
    try:
        return recommend_cip_cil_from_lims(pid, qall, qone)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except Exception as e:
        logger.exception("cip-cil recommend failed for %s", pid)
        raise HTTPException(500, detail="Recommandation CIP/CIL indisponible") from e


@router.post("/{pid}/circuit-optimizer/recommend")
def recommend(pid: str, user=Depends(project_user)):
    """Analyze LIMS data and recommend the optimal process circuit."""
    try:
        # Legacy endpoint kept for compatibility with existing clients.
        data = analyze_circuit_strategy(pid, qall, qone)
        rec = data.get("recommendation") or recommend_circuit(pid, qall, qone)
        rec["strategy_source"] = data.get("scenario_source")
        return rec
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except Exception as e:
        logger.exception("circuit-optimizer recommend failed for %s", pid)
        raise HTTPException(500, detail="Optimisation circuit indisponible") from e


@router.post("/{pid}/circuit-tradeoff/compare")
def compare_circuit_tradeoff(pid: str, user=Depends(project_user)):
    """Compare baseline + HPGR + flowsheet actuel circuits (trade-off study)."""
    try:
        from ..engines.circuit_tradeoff import compare_tradeoff_circuits
    except ImportError:
        from engines.circuit_tradeoff import compare_tradeoff_circuits
    try:
        return compare_tradeoff_circuits(pid, qall, qone)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except Exception as e:
        logger.exception("circuit-tradeoff failed for %s", pid)
        raise HTTPException(500, detail="Trade-off circuits indisponible") from e


@router.post("/{pid}/circuit-strategy/analyze")
def analyze_strategy(pid: str, user=Depends(project_user)):
    """Unified endpoint for recommendation + advanced trade-off."""
    try:
        return analyze_circuit_strategy(pid, qall, qone)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except Exception as e:
        logger.exception("circuit-strategy failed for %s", pid)
        raise HTTPException(500, detail="Strategie circuit indisponible") from e


@router.put("/{pid}/circuit-strategy/snapshot")
def put_strategy_snapshot(pid: str, body: dict, user=Depends(project_user)):
    """Persist the current circuit strategy recommendation snapshot."""
    from datetime import datetime, timezone
    import json

    saved_at = datetime.now(timezone.utc).isoformat()
    execute(
        """
        INSERT INTO project_snapshots (project_id, snapshot_type, payload_json, created_by, created_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (pid, "circuit_strategy", json.dumps(body or {}), (user or {}).get("id"), saved_at),
    )
    return {"ok": True, "saved_at": saved_at}
