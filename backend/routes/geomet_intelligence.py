"""Geometallurgical Intelligence API — auto-domaining, LOM forecast, blend optimization."""
import logging
import psycopg2
from fastapi import APIRouter, HTTPException, Depends, Body
from pydantic import BaseModel, Field
from typing import Optional

logger = logging.getLogger("mpdpms.geomet_intelligence")

try:
    from ..auth import project_user
    from ..engines.geomet_storage import persist_gade_run
    from ..engines.geomet_predictor import auto_cluster_domains, predict_recovery
    from ..engines.recovery_forecast import forecast_lom, identify_critical_periods
    from ..engines.blend_optimizer import optimize_blend, generate_blend_schedule, evaluate_blend_impact
except ImportError:
    from auth import project_user
    from engines.geomet_storage import persist_gade_run
    from engines.geomet_predictor import auto_cluster_domains, predict_recovery
    from engines.recovery_forecast import forecast_lom, identify_critical_periods
    from engines.blend_optimizer import optimize_blend, generate_blend_schedule, evaluate_blend_impact

router = APIRouter(tags=["geomet-intelligence"])

_domain_cache: dict[str, dict] = {}


def invalidate_domain_cache(pid: str) -> None:
    """Drop in-memory geomet domains when LIMS/block data changes for a project."""
    _domain_cache.pop(str(pid), None)


def _classify_ore_simple(ore: dict) -> str:
    as_ppm = float(ore.get("as_ppm") or 0)
    s_total = float(ore.get("s_total_pct") or 0)
    c_org = float(ore.get("c_organic_pct") or 0)
    if as_ppm > 3000 or s_total > 5:
        return "refractory"
    if 1000 < as_ppm <= 3000 or 2 < s_total <= 5:
        return "semi_refractory"
    if c_org > 0.2:
        return "preg_robbing"
    return "free_milling"


def _predict_recovery_fallback(ore: dict) -> dict:
    """Heuristic fallback when geomet domaining cannot be trained yet."""
    au = float(ore.get("au_g_t") or 0)
    as_ppm = float(ore.get("as_ppm") or 0)
    s_total = float(ore.get("s_total_pct") or 0)
    c_org = float(ore.get("c_organic_pct") or 0)
    bwi = float(ore.get("bwi_kwh_t") or 0)

    pred = 88.0
    pred += max(-6.0, min(6.0, (au - 2.0) * 1.5))
    pred -= max(0.0, (as_ppm - 500.0) / 1200.0 * 4.0)
    pred -= max(0.0, (s_total - 1.5) * 2.5)
    pred -= max(0.0, (c_org - 0.1) * 25.0)
    pred -= max(0.0, (bwi - 14.0) * 0.8)
    pred = max(55.0, min(97.0, pred))

    return {
        "predicted_recovery_pct": round(pred, 2),
        "domain": "Fallback (sans domaines entraînés)",
        "ore_class": _classify_ore_simple(ore),
        "model_r_squared": None,
        "method": "heuristic_fallback",
        "warning": "Prediction uses fallback because A1+B1+D1 joined samples are insufficient.",
    }


class PredictRecoveryRequest(BaseModel):
    au_g_t: float = Field(..., ge=0, description="Gold grade g/t")
    fe_pct: float = Field(3.0, ge=0)
    s_total_pct: float = Field(1.5, ge=0)
    as_ppm: float = Field(500, ge=0)
    c_organic_pct: float = Field(0.1, ge=0)
    bwi_kwh_t: float = Field(14.0, ge=0)
    au_recovery_pct: Optional[float] = Field(None, ge=0, le=100)
    nacn_consumption_kg_t: Optional[float] = Field(None, ge=0)


class BlendConstraintsRequest(BaseModel):
    bwi_max_kwh_t: float = Field(18.0, ge=5, le=30)
    cu_max_pct: float = Field(0.05, ge=0, le=1)
    s_max_pct: float = Field(3.0, ge=0, le=10)
    min_recovery_pct: float = Field(80.0, ge=50, le=100)


def _resolve_domain_result(pid: str) -> dict:
    domain_result = _domain_cache.get(pid)
    if not domain_result or domain_result.get("status") != "ok":
        domain_result = auto_cluster_domains(pid)
        if domain_result.get("status") == "ok":
            _domain_cache[pid] = domain_result
    return domain_result


def _user_id(user) -> str | None:
    if isinstance(user, dict):
        return user.get("id")
    return getattr(user, "id", None)


@router.post("/{pid}/geomet-intelligence/auto-domain")
def run_auto_domain(pid: str, body: dict | None = Body(default=None), user=Depends(project_user)):
    """Run geometallurgical auto-domaining from LIMS data (A1+B1+D1)."""
    try:
        result = auto_cluster_domains(pid)
        if result["status"] == "ok":
            _domain_cache[pid] = result
            try:
                row = persist_gade_run(pid, body or {}, result, user_id=_user_id(user))
                result["persisted_run_id"] = row.get("id")
                result["persisted_at"] = row.get("computed_at")
            except Exception:
                logger.debug("GADE persistence skipped for %s", pid, exc_info=True)
        return result
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except Exception as e:
        logger.exception("auto-domain failed for %s", pid)
        raise HTTPException(500, detail=f"Auto-domaining failed: {str(e)}") from e


@router.put("/{pid}/geomet-intelligence/snapshot")
def save_geomet_snapshot(pid: str, user=Depends(project_user)):
    domain_result = _resolve_domain_result(pid)
    row = persist_gade_run(pid, {}, domain_result, user_id=_user_id(user))
    return {"ok": True, "persisted_run_id": row.get("id"), "saved_at": row.get("computed_at")}


@router.get("/{pid}/geomet-intelligence/domains")
def get_domains(pid: str, user=Depends(project_user)):
    """Return cached domain results for the project."""
    cached = _domain_cache.get(pid)
    if not cached:
        try:
            result = auto_cluster_domains(pid)
            if result["status"] == "ok":
                _domain_cache[pid] = result
            return result
        except psycopg2.OperationalError:
            raise HTTPException(503, detail="Database temporarily unavailable")
        except Exception as e:
            logger.exception("domains retrieval failed for %s", pid)
            raise HTTPException(500, detail="Domain retrieval failed") from e
    return cached


@router.post("/{pid}/geomet-intelligence/predict-recovery")
def run_predict_recovery(pid: str, body: PredictRecoveryRequest, user=Depends(project_user)):
    """Predict recovery for given ore features using domain models."""
    domain_result = _domain_cache.get(pid)
    if not domain_result or domain_result.get("status") != "ok":
        try:
            domain_result = auto_cluster_domains(pid)
            if domain_result["status"] == "ok":
                _domain_cache[pid] = domain_result
        except Exception as e:
            raise HTTPException(400, detail="Cannot compute domains; run auto-domain first") from e

    ore_features = body.model_dump()
    if ore_features.get("au_recovery_pct") is None:
        ore_features["au_recovery_pct"] = 90.0
    if ore_features.get("nacn_consumption_kg_t") is None:
        ore_features["nacn_consumption_kg_t"] = 0.3

    if not domain_result or domain_result.get("status") != "ok":
        return _predict_recovery_fallback(ore_features)

    result = predict_recovery(domain_result, ore_features)
    if result.get("predicted_recovery_pct") is None:
        return _predict_recovery_fallback(ore_features)
    return result


@router.post("/{pid}/geomet-intelligence/lom-forecast")
def run_lom_forecast(pid: str, user=Depends(project_user)):
    """Generate life-of-mine recovery forecast using domain models + block model."""
    domain_result = _domain_cache.get(pid)
    if not domain_result or domain_result.get("status") != "ok":
        try:
            domain_result = auto_cluster_domains(pid)
            if domain_result["status"] == "ok":
                _domain_cache[pid] = domain_result
        except Exception as e:
            raise HTTPException(400, detail="Run auto-domain first") from e

    if not domain_result or domain_result.get("status") != "ok":
        raise HTTPException(400, detail="Insufficient domain data")

    try:
        result = forecast_lom(pid, domain_result)
        return result
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except Exception as e:
        logger.exception("lom-forecast failed for %s", pid)
        raise HTTPException(500, detail=f"LOM forecast failed: {str(e)}") from e


@router.get("/{pid}/geomet-intelligence/critical-periods")
def get_critical_periods(pid: str, threshold: float = 80.0, user=Depends(project_user)):
    """Identify periods where recovery falls below economic threshold."""
    domain_result = _domain_cache.get(pid)
    if not domain_result or domain_result.get("status") != "ok":
        try:
            domain_result = auto_cluster_domains(pid)
            if domain_result["status"] == "ok":
                _domain_cache[pid] = domain_result
        except Exception as e:
            raise HTTPException(400, detail="Run auto-domain first") from e

    if not domain_result or domain_result.get("status") != "ok":
        raise HTTPException(400, detail="Insufficient domain data")

    try:
        result = identify_critical_periods(pid, domain_result, threshold_pct=threshold)
        return {"critical_periods": result, "threshold_pct": threshold, "n_critical": len(result)}
    except Exception as e:
        logger.exception("critical-periods failed for %s", pid)
        raise HTTPException(500, detail=f"Critical periods analysis failed: {str(e)}") from e


@router.post("/{pid}/geomet-intelligence/blend-optimize")
def run_blend_optimize(pid: str, body: BlendConstraintsRequest = None, user=Depends(project_user)):
    """Optimize ore blend under metallurgical constraints."""
    domain_result = _domain_cache.get(pid)
    if not domain_result or domain_result.get("status") != "ok":
        try:
            domain_result = auto_cluster_domains(pid)
            if domain_result["status"] == "ok":
                _domain_cache[pid] = domain_result
        except Exception as e:
            raise HTTPException(400, detail="Run auto-domain first") from e

    if not domain_result or domain_result.get("status") != "ok":
        raise HTTPException(400, detail="Insufficient domain data")

    constraints = body.model_dump() if body else None

    try:
        result = optimize_blend(pid, domain_result, constraints)
        return result
    except Exception as e:
        logger.exception("blend-optimize failed for %s", pid)
        raise HTTPException(500, detail=f"Blend optimization failed: {str(e)}") from e


@router.post("/{pid}/geomet-intelligence/blend-schedule")
def run_blend_schedule(pid: str, body: BlendConstraintsRequest = None, user=Depends(project_user)):
    """Generate LOM blend optimization schedule with yearly recommendations."""
    domain_result = _domain_cache.get(pid)
    if not domain_result or domain_result.get("status") != "ok":
        try:
            domain_result = auto_cluster_domains(pid)
            if domain_result["status"] == "ok":
                _domain_cache[pid] = domain_result
        except Exception as e:
            raise HTTPException(400, detail="Run auto-domain first") from e

    if not domain_result or domain_result.get("status") != "ok":
        raise HTTPException(400, detail="Insufficient domain data")

    constraints = body.model_dump() if body else None

    try:
        schedule = generate_blend_schedule(pid, domain_result, constraints)
        impact = evaluate_blend_impact(pid, domain_result, constraints)
        return {
            "schedule": schedule,
            "impact_summary": impact,
            "n_periods": len(schedule),
        }
    except Exception as e:
        logger.exception("blend-schedule failed for %s", pid)
        raise HTTPException(500, detail=f"Blend schedule generation failed: {str(e)}") from e
