# backend/routes/economics.py
"""
Economics API — DCF, Monte Carlo, AISC, economic indicators.

Routes (all under /api/v1/projects/{pid}/economics/):
  POST /dcf                  — Compute DCF model
  POST /monte-carlo          — Queue Monte Carlo (Celery, status 202)
  GET  /monte-carlo/{id}     — Get MC results
  GET  /indicators           — NPV/IRR/AISC summary list
"""
from __future__ import annotations
import psycopg2
import os
import uuid, json, logging
from fastapi import APIRouter, Depends, HTTPException, Body

logger = logging.getLogger(__name__)

try:
    from ..db import conn, release, qone, qall
    from ..auth import project_user
    from .. import config as _app_config
    from ..engines.dcf import compute_npv, compute_irr, compute_aisc, build_cashflows
    from ..tasks.economic_tasks import run_monte_carlo_task
except ImportError:
    from db import conn, release, qone, qall
    from auth import project_user
    import config as _app_config
    from engines.dcf import compute_npv, compute_irr, compute_aisc, build_cashflows
    from tasks.economic_tasks import run_monte_carlo_task

router = APIRouter()


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _dcf_core(pid: str, payload: dict | None = None) -> dict:
    """Synchronous DCF computation.

    Sourced for both the async route and `routes/capex._recompute_dcf_or_none`
    (sync mutation handlers). MUST stay sync (no `await`, no `Depends`) so the
    CAPEX router can call it inline. Auth is enforced by the wrapping route.
    """
    payload = payload or {}

    # Read project parameters as defaults
    project = qone("SELECT * FROM projects WHERE id=%s", (pid,))
    if not project:
        raise HTTPException(404, "Projet introuvable")

    # Sourced from services.capex (equipment SUM + Lang factors). See spec §7.1.
    try:
        from ..services.capex import compute_total as _capex_total
    except ImportError:  # pragma: no cover
        from services.capex import compute_total as _capex_total
    _capex = _capex_total(pid)
    db_capex = float(_capex["total_cad"])
    if db_capex <= 0:
        raise HTTPException(400, "CAPEX projet à zéro — seedez un template ou ajoutez des équipements")

    # Read OPEX from cost_models
    opex_row = qone(
        "SELECT COALESCE(SUM(cli.total_cost_usd), 0) AS total "
        "FROM cost_line_items cli JOIN cost_models cm ON cm.id = cli.model_id "
        "WHERE cm.project_id=%s AND cm.model_type='OPEX'",
        (pid,),
    )
    db_opex = float(opex_row["total"]) if opex_row else 0

    default_mine_life = int(_env_float("ECON_DEFAULT_MINE_LIFE_YEARS", 10))
    default_gold_price = float(_app_config.DEFAULT_GOLD_PRICE_USD_OZ)
    default_discount_rate = _env_float("ECON_DEFAULT_DISCOUNT_RATE_PCT", 5.0)
    default_opex = _env_float("ECON_DEFAULT_OPEX_ANNUAL_USD", 25_000_000)

    mine_life = int(payload.get("mine_life_years", project.get("mine_life_years") or default_mine_life))
    au_price = float(payload.get("au_price", project.get("gold_price_usd_oz") or default_gold_price))
    discount_rate = float(payload.get("discount_rate", project.get("discount_rate_pct") or default_discount_rate))
    initial_capex = float(payload.get("initial_capex", db_capex))
    opex_annual = float(payload.get("opex_annual", db_opex or default_opex))

    # Annual gold: same recovery path as simulation / mass balance (helpers)
    production_meta: dict = {}
    if "annual_oz" not in payload:
        try:
            from ..helpers import resolve_process_production
        except ImportError:
            from helpers import resolve_process_production
        prod = resolve_process_production(pid, project)
        annual_oz = float(prod["annual_gold_oz"])
        production_meta = prod
        if annual_oz <= 0:
            annual_oz = _env_float("ECON_DEFAULT_ANNUAL_OZ", 100_000)
    else:
        annual_oz = float(payload["annual_oz"])

    if mine_life < 1:
        raise HTTPException(400, "mine_life_years must be >= 1")
    if annual_oz <= 0:
        raise HTTPException(400, "annual_oz must be > 0")
    royalty_pct = float(payload.get("royalty_pct", _env_float("ECON_DEFAULT_ROYALTY_PCT", 3.0)))
    sustaining = float(payload.get("sustaining_capex_annual", _env_float("ECON_DEFAULT_SUSTAINING_CAPEX_ANNUAL_USD", 5_000_000)))
    tax_rate = float(payload.get("tax_rate", _env_float("ECON_DEFAULT_TAX_RATE_PCT", 30.0)))

    cfs = build_cashflows(
        mine_life_years=mine_life, annual_oz=annual_oz, au_price=au_price,
        royalty_pct=royalty_pct, opex_annual=opex_annual,
        sustaining_capex_annual=sustaining, tax_rate=tax_rate, discount_rate=discount_rate,
        # Without initial_capex the straight-line depreciation is 0, dropping the
        # depreciation tax shield → tax overstated, FCF/NPV understated. Pass it so
        # the FCF stream matches the capex used in compute_npv/compute_irr below.
        initial_capex=initial_capex,
    )
    fcf_values = [cf["fcf"] for cf in cfs]
    npv = compute_npv(fcf_values, discount_rate=discount_rate / 100.0, initial_capex=initial_capex)
    irr = compute_irr(fcf_values, initial_capex=initial_capex)

    # Compute AISC with real OPEX breakdown from cost_line_items if available
    # Otherwise fall back to industry-standard split (mining 40%, processing 45%, G&A 15%)
    opex_mining = 0.0
    opex_processing = 0.0
    opex_ga = 0.0
    try:
        opex_breakdown = qall(
            "SELECT cli.cost_category, COALESCE(SUM(cli.total_cost_usd), 0) AS total "
            "FROM cost_line_items cli JOIN cost_models cm ON cm.id = cli.model_id "
            "WHERE cm.project_id=%s AND cm.model_type='OPEX' "
            "GROUP BY cli.cost_category",
            (pid,),
        )
        if opex_breakdown:
            for row in opex_breakdown:
                cat = (row.get("cost_category") or "").lower()
                val = float(row.get("total") or 0)
                if "mining" in cat or "mine" in cat:
                    opex_mining += val
                elif "g&a" in cat or "general" in cat or "admin" in cat:
                    opex_ga += val
                else:
                    opex_processing += val
    except Exception:
        pass

    # Fall back to proportional split if breakdown not available
    if opex_mining + opex_processing + opex_ga == 0:
        opex_mining = opex_annual * 0.40
        opex_processing = opex_annual * 0.45
        opex_ga = opex_annual * 0.15

    aisc = compute_aisc(
        opex_mining=opex_mining,
        opex_processing=opex_processing,
        ga=opex_ga,
        byproduct_credits=0,
        sustaining_capex=sustaining,
        royalties=au_price * annual_oz * royalty_pct / 100.0,
        exploration=0,
        corporate_ga=0,
        oz_produced=annual_oz,
    )

    model_id = str(uuid.uuid4())
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO dcf_models
                   (id, project_id, version, discount_rate, tax_rate, mine_life_years,
                    cashflows, npv, irr, payback_years, aisc)
                   VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s, NULL, %s)""",
                (model_id, pid, discount_rate, tax_rate, mine_life,
                 json.dumps(cfs), npv, irr, aisc)
            )
            cur.execute(
                """INSERT INTO economic_indicators
                   (id, project_id, dcf_model_id, npv_usd, irr_pct, aisc_usd_oz)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (str(uuid.uuid4()), pid, model_id, npv,
                 irr * 100.0 if irr is not None else None, aisc)
            )
        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            try:
                db.rollback()
            except Exception:  # intentional: ignore optional lookup failure
                pass
        raise
    finally:
        if db is not None:
            release(db)

    result = {
        "model_id": model_id,
        "npv": round(npv, 2),
        "irr": round(irr * 100.0, 2) if irr is not None else None,
        "aisc": round(aisc, 2),
        "initial_capex": round(initial_capex, 2),
        "annual_oz": round(annual_oz, 0),
        "cashflows": cfs,
    }
    if production_meta:
        result["production"] = production_meta
    return result


@router.post("/{pid}/economics/dcf")
async def compute_dcf(pid: str, payload: dict = Body(...), _auth=Depends(project_user)):
    """Compute DCF model synchronously and store in dcf_models table."""
    result = _dcf_core(pid, payload=payload)

    try:
        from ..audit import record_event
    except ImportError:
        from audit import record_event
    record_event(
        user_id=_auth["id"], project_id=pid,
        entity_type="dcf_model", entity_id=result["model_id"],
        action="create",
        new_value={
            "npv": result["npv"], "irr": result["irr"], "aisc": result["aisc"],
            "initial_capex": result["initial_capex"],
        },
        source="web",
    )
    return result


@router.post("/{pid}/economics/monte-carlo", status_code=202)
async def queue_monte_carlo(pid: str, payload: dict = Body(...), _auth=Depends(project_user)):
    """Queue Monte Carlo simulation as Celery task."""
    mc_run_id = str(uuid.uuid4())
    n_iter = int(payload.get("n_iterations", 10_000))
    base_params = payload.get("base_params", {})

    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO monte_carlo_runs
                   (id, project_id, n_iterations, variables, status)
                   VALUES (%s, %s, %s, %s, 'queued')""",
                (mc_run_id, pid, n_iter, json.dumps(base_params))
            )
        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            try:
                db.rollback()
            except Exception:  # intentional: ignore optional lookup failure
                pass
        raise
    finally:
        if db is not None:
            release(db)

    try:
        run_monte_carlo_task.delay(pid, mc_run_id, n_iter, base_params)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    return {"mc_run_id": mc_run_id, "status": "queued", "n_iterations": n_iter}


@router.get("/{pid}/economics/monte-carlo/{mc_run_id}")
async def get_monte_carlo_result(pid: str, mc_run_id: str, _auth=Depends(project_user)):
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                "SELECT id, status, n_iterations, results FROM monte_carlo_runs WHERE id=%s AND project_id=%s",
                (mc_run_id, pid)
            )
            row = cur.fetchone()
    finally:
        if db is not None:
            release(db)
    if not row:
        raise HTTPException(404, "Monte Carlo run not found")
    return {"mc_run_id": str(row[0]), "status": row[1], "n_iterations": row[2], "results": row[3]}


@router.get("/{pid}/economics/indicators")
async def get_economic_indicators(pid: str, _auth=Depends(project_user)):
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """SELECT id, npv_usd, irr_pct, payback_years, aisc_usd_oz,
                          cash_cost_usd_oz, margin_pct, created_at
                   FROM economic_indicators WHERE project_id=%s ORDER BY created_at DESC""",
                (pid,)
            )
            rows = cur.fetchall()
    finally:
        if db is not None:
            release(db)
    return [
        {
            "id": str(r[0]),
            "npv_usd": r[1],
            "irr_pct": r[2],
            "payback_years": r[3],
            "aisc_usd_oz": r[4],
            "cash_cost_usd_oz": r[5],
            "margin_pct": r[6],
            "computed_at": str(r[7]),
        }
        for r in rows
    ]


@router.post("/{pid}/economics/sensitivity")
async def compute_sensitivity(pid: str, payload: dict = Body(...), _auth=Depends(project_user)):
    """
    Tornado chart sensitivity analysis.

    Varies each specified parameter ±variation_pct% and computes NPV impact.
    Returns sorted results (largest swing first) for tornado chart visualization.
    """
    try:
        from ..engines.dcf import sensitivity_analysis
    except ImportError:
        from engines.dcf import sensitivity_analysis

    project = qone("SELECT * FROM projects WHERE id=%s", (pid,))
    if not project:
        raise HTTPException(404, "Projet introuvable")

    # Build base params from project + payload overrides
    base_params = {
        "mine_life_years": int(project.get("mine_life_years") or 10),
        "au_price": float(project.get("gold_price_usd_oz") or _app_config.DEFAULT_GOLD_PRICE_USD_OZ),
        "discount_rate": float(project.get("discount_rate_pct") or 5.0),
        "royalty_pct": float(payload.get("royalty_pct", 3.0)),
        "tax_rate": float(payload.get("tax_rate", 30.0)),
        "opex_annual": float(payload.get("opex_annual", 25_000_000)),
        "sustaining_capex": float(payload.get("sustaining_capex", 5_000_000)),
        "initial_capex": float(payload.get("initial_capex", 150_000_000)),
        "annual_oz": float(payload.get("annual_oz", 100_000)),
    }
    base_params.update({k: v for k, v in payload.items() if k in base_params})

    variables = payload.get("variables", [
        "au_price", "annual_oz", "opex_annual", "initial_capex",
        "discount_rate", "royalty_pct", "tax_rate",
    ])
    variation_pct = float(payload.get("variation_pct", 20.0))

    try:
        result = sensitivity_analysis(base_params, variables, variation_pct)
        return result
    except Exception as e:
        raise HTTPException(500, detail=f"Erreur analyse de sensibilité: {e}")


@router.post("/{pid}/economics/payback")
async def compute_payback(pid: str, payload: dict = Body(...), _auth=Depends(project_user)):
    """
    Compute simple payback period from DCF cashflows.
    """
    try:
        from ..engines.dcf import compute_payback_period, build_cashflows
    except ImportError:
        from engines.dcf import compute_payback_period, build_cashflows

    project = qone("SELECT * FROM projects WHERE id=%s", (pid,))
    if not project:
        raise HTTPException(404, "Projet introuvable")

    mine_life = int(payload.get("mine_life_years", project.get("mine_life_years") or 10))
    annual_oz = float(payload.get("annual_oz", 100_000))
    au_price = float(payload.get("au_price", project.get("gold_price_usd_oz") or _app_config.DEFAULT_GOLD_PRICE_USD_OZ))
    royalty_pct = float(payload.get("royalty_pct", 3.0))
    opex_annual = float(payload.get("opex_annual", 25_000_000))
    sustaining = float(payload.get("sustaining_capex", 5_000_000))
    tax_rate = float(payload.get("tax_rate", 30.0))
    discount_rate = float(payload.get("discount_rate", project.get("discount_rate_pct") or 5.0))
    initial_capex = float(payload.get("initial_capex", 150_000_000))

    cfs = build_cashflows(
        mine_life_years=mine_life, annual_oz=annual_oz, au_price=au_price,
        royalty_pct=royalty_pct, opex_annual=opex_annual,
        sustaining_capex_annual=sustaining, tax_rate=tax_rate,
        discount_rate=discount_rate, initial_capex=initial_capex,
    )
    fcf_values = [cf["fcf"] for cf in cfs]
    payback = compute_payback_period(fcf_values, initial_capex)

    return {
        "payback_years": round(payback, 2) if payback is not None else None,
        "recovered": payback is not None,
        "mine_life_years": mine_life,
        "cashflows": cfs,
    }
