#!/usr/bin/env python3
"""Verify that every ORM-declared table exists in the Alembic schema.

Strategy
--------
Alembic is the canonical source of truth for the DB schema. The ORM in
`backend/orm_models/` is a *subset* covering the modules that use SQLAlchemy
sessions (the rest of the app uses raw SQL). This script asserts that no ORM
table has drifted from Alembic — every `__tablename__` declared in the ORM
must be defined either in `schema.sql` (the baseline) or in a subsequent
Alembic migration.

If this check fails, it means someone added a model class to the ORM without
shipping a corresponding Alembic migration. Fix: write a migration.

Exit code: 0 on success, 1 on missing table(s).
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
SCHEMA_SQL = BACKEND / "schema.sql"
REBUILD_SCRIPT = BACKEND / "scripts" / "rebuild_db_from_migrations.py"
ORM_MODELS = BACKEND / "orm_models" / "models.py"


CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)

TABLENAME_RE = re.compile(r'__tablename__\s*=\s*[\'"]([a-zA-Z_][a-zA-Z0-9_]*)[\'"]')


def tables_in_baseline() -> set[str]:
    if not SCHEMA_SQL.exists():
        raise SystemExit(f"FATAL: {SCHEMA_SQL} not found")
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    return set(CREATE_TABLE_RE.findall(sql))


def tables_in_migrations() -> set[str]:
    """Run rebuild_db_from_migrations.py --dry-run and parse table names."""
    if not REBUILD_SCRIPT.exists():
        raise SystemExit(f"FATAL: {REBUILD_SCRIPT} not found")
    proc = subprocess.run(
        [sys.executable, str(REBUILD_SCRIPT), "--dry-run"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"FATAL: rebuild script exited {proc.returncode}")
    return set(CREATE_TABLE_RE.findall(proc.stdout))


def tables_in_orm() -> set[str]:
    """Parse __tablename__ assignments from ORM model files (no DB needed)."""
    if not ORM_MODELS.exists():
        raise SystemExit(f"FATAL: {ORM_MODELS} not found")
    text = ORM_MODELS.read_text(encoding="utf-8")
    return set(TABLENAME_RE.findall(text))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="only print errors")
    args = parser.parse_args()

    baseline = tables_in_baseline()
    migrations = tables_in_migrations()
    orm = tables_in_orm()
    schema = baseline | migrations

    missing = orm - schema

    if not args.quiet:
        print(f"  baseline (schema.sql):    {len(baseline)} tables")
        print(f"  migrations (000002..N):   {len(migrations)} tables")
        print(f"  ORM (__tablename__):      {len(orm)} tables")
        print(f"  combined Alembic schema:  {len(schema)} tables")

    if missing:
        print("\nFAIL: ORM tables not present in Alembic schema:", file=sys.stderr)
        for t in sorted(missing):
            print(f"  - {t}", file=sys.stderr)
        print(
            "\nFix: write an Alembic migration that creates these tables, "
            "or rename the ORM __tablename__ to match an existing one.",
            file=sys.stderr,
        )
        return 1

    if not args.quiet:
        print("\nOK: all ORM tables are covered by Alembic.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
