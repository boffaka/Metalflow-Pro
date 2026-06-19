"""
MPDPMS — Project CRUD routes.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends, Body
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging
import psycopg2

try:
    from ..auth import current_user, project_user, require_role
    from orm_models.database import get_db
    from orm_models.models import Project
    from ..models import ProjectIn, ProjectPatch
    from ..logging_config import log_user_action
    from ..db import qall, qone, execute
except ImportError:  # pragma: no cover
    from auth import current_user, project_user, require_role
    from orm_models.database import get_db
    from orm_models.models import Project
    from models import ProjectIn, ProjectPatch
    from logging_config import log_user_action
    from db import qall, qone, execute

logger = logging.getLogger("mpdpms.projects")

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.get("")
def list_projects(user=Depends(current_user), db: Session = Depends(get_db)):
    if user["role"] == "Project Manager":
        return db.query(Project).order_by(Project.created_at.desc()).all()

    # Lot C Phase 1: a non-PM sees projects they own OR are a member of.
    # Defensive: if project_members is not migrated yet, fall back to owner-only.
    try:
        rows = db.execute(
            text("SELECT project_id FROM project_members WHERE user_id = :uid"),
            {"uid": str(user["id"])},
        ).fetchall()
        member_pids = [str(r[0]) for r in rows]
    except Exception:
        db.rollback()
        member_pids = []

    return (
        db.query(Project)
        .filter((Project.user_id == user["id"]) | (Project.id.in_(member_pids)))
        .order_by(Project.created_at.desc())
        .all()
    )


@router.post("", status_code=201)
def create_project(body: ProjectIn, user=Depends(current_user), db: Session = Depends(get_db)):
    new_project = Project(
        project_name=body.project_name,
        project_code=body.project_code,
        target_tph=body.target_tph,
        gold_grade_g_t=body.gold_grade_g_t,
        status=body.status,
        capex_musd=body.capex_musd,
        project_owner=body.project_owner,
        commodity=body.commodity,
        location=body.location,
        capacity_mtpa=body.capacity_mtpa,
        process_options=body.process_options,
        gold_price_usd_oz=body.gold_price_usd_oz,
        discount_rate_pct=body.discount_rate_pct,
        mine_life_years=body.mine_life_years,
        operating_hours_day=body.operating_hours_day,
        availability_pct=body.availability_pct,
        electricity_rate=body.electricity_rate,
        user_id=user["id"],
    )

    try:
        db.add(new_project)
        db.commit()
        db.refresh(new_project)

        # Lot C Phase 1: register the creator as the project's owner member.
        # Best-effort: if project_members is not migrated yet, the legacy
        # owner column (user_id) still grants the creator access.
        try:
            execute(
                "INSERT INTO project_members (project_id, user_id, role) "
                "VALUES (%s, %s, 'owner') ON CONFLICT (project_id, user_id) DO NOTHING",
                (str(new_project.id), str(user["id"])),
            )
        except Exception:  # pragma: no cover - table not migrated yet
            logger.warning("project_members insert skipped (table not migrated?)")

        log_user_action(
            "project.create",
            user_id=str(user["id"]),
            entity_type="project",
            entity_id=str(new_project.id),
            details={"project_name": body.project_name, "project_code": body.project_code},
        )
        return new_project
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/{pid}")
def get_project(pid: str, user=Depends(project_user), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == pid).first()
    if not project:
        raise HTTPException(404, "Projet introuvable ou acces refuse")

    # Convert ORM object to dict for response if feature flags missing
    project_dict = {c.name: getattr(project, c.name) for c in project.__table__.columns}
    if "feature_flags" not in project_dict or project_dict.get("feature_flags") is None:
        project_dict["feature_flags"] = {}

    return project_dict


@router.patch("/{pid}")
def patch_project(pid: str, body: ProjectPatch, user=Depends(project_user), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == pid).first()
    if not project:
        raise HTTPException(404, "Projet introuvable")

    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(400, "Aucune donnee a mettre a jour")

    try:
        for key, value in update_data.items():
            setattr(project, key, value)

        db.commit()
        db.refresh(project)
        return project
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.delete("/{pid}")
def delete_project(pid: str, user=Depends(require_role("Project Manager")), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == pid).first()
    if not project:
        raise HTTPException(404, "Projet introuvable")

    try:
        db.delete(project)
        db.commit()

        log_user_action(
            "project.delete",
            user_id=str(user["id"]),
            entity_type="project",
            entity_id=str(pid),
        )
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ─── Lot C Phase 1: project membership management (F2) ───────────────────────
_MEMBER_ROLES = {"owner", "member"}


def _require_member_admin(pid: str, user: dict) -> None:
    """Only a Project Manager or an 'owner' member may manage membership."""
    if user["role"] == "Project Manager":
        return
    row = qone(
        "SELECT 1 FROM project_members WHERE project_id = %s AND user_id = %s AND role = 'owner'",
        (pid, str(user["id"])),
    )
    if not row:
        raise HTTPException(403, "Reserve aux owners du projet ou Project Manager")


@router.get("/{pid}/members")
def list_members(pid: str, user=Depends(project_user)):
    """List members of a project (any member can view)."""
    rows = qall(
        "SELECT pm.user_id, pm.role, pm.created_at, u.email, u.full_name "
        "FROM project_members pm JOIN users u ON u.id = pm.user_id "
        "WHERE pm.project_id = %s ORDER BY pm.created_at",
        (pid,),
    )
    return {"members": rows}


@router.post("/{pid}/members", status_code=201)
def add_member(pid: str, body: dict = Body(...), user=Depends(project_user)):
    """Add (or re-role) a member. Owner/PM only."""
    _require_member_admin(pid, user)
    uid = body.get("user_id")
    role = (body.get("role") or "member").strip()
    if not uid:
        raise HTTPException(422, "user_id requis")
    if role not in _MEMBER_ROLES:
        raise HTTPException(422, f"role invalide (attendu: {', '.join(sorted(_MEMBER_ROLES))})")
    if not qone("SELECT 1 FROM users WHERE id = %s", (str(uid),)):
        raise HTTPException(404, "Utilisateur introuvable")
    execute(
        "INSERT INTO project_members (project_id, user_id, role) VALUES (%s, %s, %s) "
        "ON CONFLICT (project_id, user_id) DO UPDATE SET role = EXCLUDED.role",
        (pid, str(uid), role),
    )
    log_user_action(
        "project.member.add",
        user_id=str(user["id"]),
        entity_type="project",
        entity_id=str(pid),
        details={"member": str(uid), "role": role},
    )
    return {"ok": True, "user_id": str(uid), "role": role}


@router.delete("/{pid}/members/{uid}")
def remove_member(pid: str, uid: str, user=Depends(project_user)):
    """Remove a member. Owner/PM only. Refuses to remove the last owner."""
    _require_member_admin(pid, user)
    owners = qall(
        "SELECT user_id FROM project_members WHERE project_id = %s AND role = 'owner'",
        (pid,),
    )
    if any(str(o["user_id"]) == str(uid) for o in owners) and len(owners) <= 1:
        raise HTTPException(409, "Impossible de retirer le dernier owner du projet")
    execute(
        "DELETE FROM project_members WHERE project_id = %s AND user_id = %s",
        (pid, str(uid)),
    )
    log_user_action(
        "project.member.remove",
        user_id=str(user["id"]),
        entity_type="project",
        entity_id=str(pid),
        details={"member": str(uid)},
    )
    return {"ok": True}
