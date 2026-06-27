"""Working capital module — BFR calculation."""
from __future__ import annotations
import logging
import psycopg2
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

try:
    from ..auth import project_user
    from ..db import qone, execute
    from ..helpers import compute_annual_t, compute_daily_opex
    from .. import config as cfg
except ImportError:
    from auth import project_user
    from db import qone, execute
    from helpers import compute_annual_t, compute_daily_opex
    import config as cfg

logger = logging.getLogger("mpdpms.working_capital")

router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["working_capital"])

_WRITE_ROLES = ("Project Manager", "Cost Engineer")


class WorkingCapitalIn(BaseModel):
    receivable_days: int = Field(30, ge=0)
    inventory_days: int = Field(45, ge=0)
    payable_days: int = Field(30, ge=0)
    other_current_assets: float = 0.0
    other_current_liabilities: float = 0.0
    currency: str = "USD"


def _serialize(row: dict) -> dict:
    out = dict(row)
    if out.get("id"):
        out["id"] = str(out["id"])
    if out.get("project_id"):
        out["project_id"] = str(out["project_id"])
    if out.get("updated_at"):
        out["updated_at"] = str(out["updated_at"])
    return out


def _compute_bfr_inline(pid: str, row: dict) -> dict:
    """Compute BFR fields from OPEX and project data, return extra fields."""
    extra: dict = {}
    try:
        opex_model = qone(
            "SELECT id FROM cost_models WHERE project_id=%s AND model_type='OPEX' ORDER BY version DESC LIMIT 1",
            (pid,)
        )
        if not opex_model:
            return extra
        agg = qone(
            "SELECT COALESCE(SUM(total_cost_usd),0) as total FROM cost_line_items WHERE model_id=%s",
            (opex_model["id"],)
        )
        project = qone("SELECT * FROM projects WHERE id=%s", (pid,))
        if not (project and agg):
            return extra

        target_tph       = float(project.get("target_tph") or 0)
        op_hours_day     = float(project.get("operating_hours_day") or 24.0)
        availability_pct = float(project.get("availability_pct") or 92.0)
        float(project.get("gold_grade_g_t") or 0)
        gold_price       = float(project.get("gold_price_usd_oz") or cfg.DEFAULT_GOLD_PRICE_USD_OZ)

        annual_t = compute_annual_t(target_tph, op_hours_day, availability_pct)

        total_opex_annual = float(agg.get("total") or 0)
        # If total_cost_usd looks like $/t (small vs throughput), multiply by annual_t
        if annual_t > 0 and 0 < total_opex_annual < annual_t:
            agg2 = qone(
                "SELECT COALESCE(SUM(unit_cost_usd),0) as total_per_t FROM cost_line_items WHERE model_id=%s",
                (opex_model["id"],)
            )
            if agg2:
                total_opex_annual = float(agg2.get("total_per_t") or 0) * annual_t

        daily_opex = compute_daily_opex(total_opex_annual)

        try:
            from ..helpers import resolve_process_production
        except ImportError:
            from helpers import resolve_process_production
        prod = resolve_process_production(pid, project)
        daily_au_oz = prod["annual_gold_oz"] / 365.0 if prod["annual_gold_oz"] > 0 else 0.0
        daily_revenue = daily_au_oz * gold_price

        recv_d = float(row.get("receivable_days") or 0)
        inv_d  = float(row.get("inventory_days") or 45)
        pay_d  = float(row.get("payable_days") or 30)
        bfr = ((recv_d * daily_revenue)
               + (inv_d * daily_opex)
               - (pay_d * daily_opex)
               + float(row.get("other_current_assets") or 0)
               - float(row.get("other_current_liabilities") or 0))

        extra["bfr_computed_usd"]  = round(bfr, 0)
        extra["daily_opex_usd"]    = round(daily_opex, 0)
        extra["daily_revenue_usd"] = round(daily_revenue, 0)
    except Exception as e:  # intentional: graceful fallback on optional BFR compute
        logger.warning("BFR inline compute failed for project %s: %s", pid, e)
    return extra


@router.get("/working-capital")
def get_working_capital(pid: str, user=Depends(project_user)):
    try:
        row = qone("SELECT * FROM working_capital WHERE project_id=%s", (pid,))
        if not row:
            wc_out = {
                "project_id": pid,
                "receivable_days": 30,
                "inventory_days": 45,
                "payable_days": 30,
                "other_current_assets": 0.0,
                "other_current_liabilities": 0.0,
                "currency": "USD",
            }
        else:
            wc_out = _serialize(row)
        # Compute BFR on the fly
        wc_out.update(_compute_bfr_inline(pid, row if row else wc_out))
        return wc_out
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.put("/working-capital")
def upsert_working_capital(pid: str, body: WorkingCapitalIn, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant pour modifier le fonds de roulement")
        row = execute(
            """INSERT INTO working_capital
               (project_id, receivable_days, inventory_days, payable_days,
                other_current_assets, other_current_liabilities, currency, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())
               ON CONFLICT (project_id) DO UPDATE SET
                 receivable_days=EXCLUDED.receivable_days,
                 inventory_days=EXCLUDED.inventory_days,
                 payable_days=EXCLUDED.payable_days,
                 other_current_assets=EXCLUDED.other_current_assets,
                 other_current_liabilities=EXCLUDED.other_current_liabilities,
                 currency=EXCLUDED.currency,
                 updated_at=NOW()
               RETURNING *""",
            (pid, body.receivable_days, body.inventory_days, body.payable_days,
             body.other_current_assets, body.other_current_liabilities, body.currency)
        )
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.get("/working-capital/computed")
def get_working_capital_computed(pid: str, user=Depends(project_user)):
    """Compute net working capital using OPEX daily rate."""
    try:
        wc = qone("SELECT * FROM working_capital WHERE project_id=%s", (pid,))
        if not wc:
            wc = {"receivable_days": 30, "inventory_days": 45, "payable_days": 30,
                  "other_current_assets": 0.0, "other_current_liabilities": 0.0}

        opex_row = qone(
            "SELECT COALESCE(SUM(cli.total_cost_usd), 0) AS total "
            "FROM cost_line_items cli "
            "JOIN cost_models cm ON cm.id = cli.model_id "
            "WHERE cm.project_id=%s AND cm.model_type='OPEX'",
            (pid,)
        ) or {}
        opex_total = float(opex_row.get("total") or 0)

        daily_opex = compute_daily_opex(opex_total)

        receivables = int(wc["receivable_days"]) * daily_opex
        inventory = int(wc["inventory_days"]) * daily_opex
        payables = int(wc["payable_days"]) * daily_opex
        other_assets = float(wc.get("other_current_assets") or 0)
        other_liab = float(wc.get("other_current_liabilities") or 0)
        net_wc = receivables + inventory - payables + other_assets - other_liab

        return {
            "receivables": round(receivables, 2),
            "inventory": round(inventory, 2),
            "payables": round(payables, 2),
            "other_current_assets": round(other_assets, 2),
            "other_current_liabilities": round(other_liab, 2),
            "net_working_capital": round(net_wc, 2),
            "daily_opex_rate": round(daily_opex, 2),
            "currency": wc.get("currency", "USD"),
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
