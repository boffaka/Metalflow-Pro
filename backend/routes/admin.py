"""
MPDPMS — Admin routes: user CRUD.
Restricted to Project Manager role.
"""
from __future__ import annotations
import logging
import psycopg2

from fastapi import APIRouter, HTTPException, Depends, Query

logger = logging.getLogger("mpdpms.admin")

try:
    from ..auth import require_role, VALID_ROLES, bump_token_version
    from ..db import qone, execute, build_update_sets, paginated_qall
    from ..models import UserCreate, UserPatch, PasswordChange
    from ..security import audit_log
    from ..logging_config import log_user_action
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import require_role, VALID_ROLES, bump_token_version
    from db import qone, execute, build_update_sets, paginated_qall
    from models import UserCreate, UserPatch, PasswordChange
    from security import audit_log
    from logging_config import log_user_action


router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

ALLOWED_FIELDS_USER = {"email", "full_name", "role"}

# All endpoints require Project Manager role
_pm_only = require_role("Project Manager")


@router.get("/users")
def list_users(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(_pm_only)):
    """List all users (id, email, role, full_name, created_at). No password hashes."""
    try:
        return paginated_qall(
            "SELECT id, email, role, full_name, created_at FROM users ORDER BY created_at",
            limit=limit, offset=offset)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/users", status_code=201)
def create_user(body: UserCreate, user=Depends(_pm_only)):
    """Create a new user. Password is hashed with pgcrypto bcrypt."""
    try:
        if body.role not in VALID_ROLES:
            raise HTTPException(400, f"Role invalide. Valeurs possibles: {', '.join(VALID_ROLES)}")

        existing = qone("SELECT id FROM users WHERE email = %s", (body.email,))
        if existing:
            raise HTTPException(409, "Un utilisateur avec cet email existe deja")

        row = execute(
            "INSERT INTO users (email, password_hash, full_name, role) "
            "VALUES (%s, crypt(%s, gen_salt('bf')), %s, %s) RETURNING id, email, role, full_name, created_at",
            (body.email, body.password, body.full_name, body.role),
        )
        audit_log(
            action="admin.create_user",
            entity_type="user",
            entity_id=str(row["id"]),
            user_id=str(user["id"]),
            new_value={"email": row["email"], "role": row["role"], "full_name": row.get("full_name")},
        )
        log_user_action(
            "admin.create_user",
            user_id=str(user["id"]),
            entity_type="user",
            entity_id=str(row["id"]),
            details={"email": row["email"], "role": row["role"]},
        )
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.get("/users/{uid}")
def get_user(uid: str, user=Depends(_pm_only)):
    """Get a single user by id."""
    try:
        row = qone(
            "SELECT id, email, role, full_name, created_at FROM users WHERE id = %s",
            (uid,),
        )
        if not row:
            raise HTTPException(404, "Utilisateur introuvable")
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.patch("/users/{uid}")
def patch_user(uid: str, body: UserPatch, user=Depends(_pm_only)):
    """Update user fields (email, full_name, role). Does not change password."""
    try:
        if body.role is not None and body.role not in VALID_ROLES:
            raise HTTPException(400, f"Role invalide. Valeurs possibles: {', '.join(VALID_ROLES)}")

        before = qone(
            "SELECT id, email, role, full_name FROM users WHERE id = %s",
            (uid,),
        )
        if not before:
            raise HTTPException(404, "Utilisateur introuvable")

        fields, vals = build_update_sets(body.model_dump(exclude_none=True), allowed=frozenset(type(body).model_fields.keys()))
        if not fields:
            raise HTTPException(400, "Aucune donnee a mettre a jour")

        vals.append(uid)
        row = execute(
            f"UPDATE users SET {', '.join(fields)} WHERE id = %s "
            "RETURNING id, email, role, full_name, created_at",
            vals,
        )
        if not row:
            raise HTTPException(404, "Utilisateur introuvable")
        audit_log(
            action="admin.patch_user",
            entity_type="user",
            entity_id=str(uid),
            user_id=str(user["id"]),
            old_value=before,
            new_value={"email": row["email"], "role": row["role"], "full_name": row.get("full_name")},
        )
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.patch("/users/{uid}/password")
def change_user_password(uid: str, body: PasswordChange, user=Depends(_pm_only)):
    """Reset a user's password (PM action)."""
    try:
        existing = qone("SELECT id, email FROM users WHERE id = %s", (uid,))
        if not existing:
            raise HTTPException(404, "Utilisateur introuvable")
        row = execute(
            "UPDATE users SET password_hash = crypt(%s, gen_salt('bf')) WHERE id = %s "
            "RETURNING id, email",
            (body.new_password, uid),
        )
        if not row:
            raise HTTPException(404, "Utilisateur introuvable")
        bump_token_version(uid)
        audit_log(
            action="admin.reset_password",
            entity_type="user",
            entity_id=str(uid),
            user_id=str(user["id"]),
            new_value={"email": row["email"]},
        )
        return {"ok": True, "message": "Mot de passe mis a jour"}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.delete("/users/{uid}")
def delete_user(uid: str, user=Depends(_pm_only)):
    """Delete a user. Cannot delete yourself."""
    try:
        if str(user["id"]) == str(uid):
            raise HTTPException(400, "Impossible de supprimer votre propre compte")

        existing = qone("SELECT id, email, role, full_name FROM users WHERE id = %s", (uid,))
        if not existing:
            raise HTTPException(404, "Utilisateur introuvable")

        execute("DELETE FROM users WHERE id = %s", (uid,))
        audit_log(
            action="admin.delete_user",
            entity_type="user",
            entity_id=str(uid),
            user_id=str(user["id"]),
            old_value=existing,
        )
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/apply-lims-m1-fix")
def apply_lims_m1_fix(user=Depends(_pm_only)):
    """
    One-shot migration: add missing columns to lims_m1.
    Safe to call multiple times (ADD COLUMN IF NOT EXISTS).
    Remove this endpoint after the fix is confirmed.
    """
    try:
        from ..db import conn, release
    except ImportError:
        from db import conn, release

    _COLS = [
        "k80_um", "other_sulphides_pct", "k_feldspar_pct",
        "other_silicates_pct", "k_other_pct", "muscovite_illite_pct",
        "ca_minerals_pct", "fe_oxides_pct", "ilmenite_pct",
        "ti_oxides_pct", "other_oxides_pct", "carbonates_pct",
        "apatite_pct", "other_pct", "au_free_pct",
    ]
    c = conn()
    cur = None
    try:
        cur = c.cursor()
        added = []
        for col in _COLS:
            cur.execute(
                f"ALTER TABLE lims_m1 ADD COLUMN IF NOT EXISTS {col} NUMERIC"
            )
            added.append(col)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_lims_m1_k80 "
            "ON lims_m1(project_id, k80_um) WHERE k80_um IS NOT NULL"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_lims_m1_au_free "
            "ON lims_m1(project_id, au_free_pct) WHERE au_free_pct IS NOT NULL"
        )
        c.commit()
        logger.info("lims_m1 fix applied: %d columns added", len(added))
        return {"ok": True, "columns_added": added, "indexes": 2}
    except Exception as e:
        c.rollback()
        logger.exception("lims_m1 fix failed")
        raise HTTPException(500, detail=str(e))
    finally:
        if cur:
            cur.close()
        release(c)
