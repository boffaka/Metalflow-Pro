"""MPDPMS — LIMS alerts routes migrated to ORM."""
from __future__ import annotations

import logging
import psycopg2
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

logger = logging.getLogger("mpdpms.lims_alerts")

try:
    from ..auth import project_user
    from ..db import qall, execute
    from orm_models.database import get_db
    from orm_models.models import LimsA1
except ImportError:
    from auth import project_user
    from db import qall, execute
    from orm_models.database import get_db
    from orm_models.models import LimsA1

router = APIRouter(prefix="/api/v1/projects", tags=["lims-alerts"])

@router.get("/{pid}/lims/alerts")
def list_alerts(pid: str, acknowledged: bool = False, user=Depends(project_user), db: Session = Depends(get_db)):
    """List LIMS alerts. (Mixed ORM approach for quick migration)"""
    try:
        # Fallback to existing SQL since LimsAlerts model might not be in models.py yet
        rows = qall(
            "SELECT * FROM lims_alerts WHERE project_id = %s AND is_acknowledged = %s "
            "ORDER BY created_at DESC",
            (pid, acknowledged),
        )
        return rows or []
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")

@router.post("/{pid}/lims/alerts/{alert_id}/acknowledge")
def acknowledge_alert(pid: str, alert_id: str, user=Depends(project_user), db: Session = Depends(get_db)):
    try:
        row = execute(
            "UPDATE lims_alerts SET is_acknowledged = TRUE, acknowledged_by = %s "
            "WHERE id = %s AND project_id = %s RETURNING *",
            (user["id"], alert_id, pid),
        )
        if not row:
            raise HTTPException(404, "Alerte non trouvee")
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")

@router.post("/{pid}/lims/alerts/run-analysis")
def run_lims_analysis(pid: str, user=Depends(project_user), db: Session = Depends(get_db)):
    try:
        try:
            from ..engines.lims_intelligence import detect_outliers, detect_cross_test_issues
        except ImportError:
            from engines.lims_intelligence import detect_outliers, detect_cross_test_issues

        # Refactored queries to ORM where applicable
        a1_records = db.query(LimsA1).filter(LimsA1.project_id == pid).all()
        a1 = [r.__dict__ for r in a1_records]

        b1 = qall("SELECT * FROM lims_b1 WHERE project_id = %s", (pid,))
        c2 = qall("SELECT * FROM lims_c2 WHERE project_id = %s", (pid,))
        d1 = qall("SELECT * FROM lims_d1 WHERE project_id = %s", (pid,))
        e1 = qall("SELECT * FROM lims_e1 WHERE project_id = %s", (pid,))

        all_alerts = []

        for field in ("au_g_t", "s_total_pct", "c_organic_pct"):
            all_alerts.extend(detect_outliers("a1", a1 or [], field))
        for field in ("bwi_kwh_t",):
            all_alerts.extend(detect_outliers("b1", b1 or [], field))
        for field in ("au_recovery_pct",):
            all_alerts.extend(detect_outliers("d1", d1 or [], field))

        all_alerts.extend(detect_cross_test_issues(
            a1 or [], d1_data=d1, c2_data=c2, b1_data=b1, e1_data=e1,
        ))

        execute(
            "DELETE FROM lims_alerts WHERE project_id = %s AND is_acknowledged = FALSE",
            (pid,),
        )
        for alert in all_alerts:
            execute(
                "INSERT INTO lims_alerts (project_id, alert_type, severity, test_type, message) "
                "VALUES (%s, %s, %s, %s, %s)",
                (pid, alert.get("alert_type", "outlier"), alert["severity"], alert.get("test_type", ""), alert["message"]),
            )

        return {"alerts_generated": len(all_alerts), "alerts": all_alerts}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
