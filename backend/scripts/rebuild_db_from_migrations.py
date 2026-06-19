#!/usr/bin/env python3
"""Rebuild the database schema idempotently from all Alembic migrations.

Why this exists
---------------
`backend/schema.sql` is only the baseline snapshot (migration 000001). The
live schema is the baseline PLUS every subsequent migration. When a DB is
reconstructed from `schema.sql` alone (e.g. after a wipe/restore), all
tables and columns added by migrations 000002..N are missing, even though
`alembic_version` might be stamped to the latest revision.

This script extracts every DDL statement from every migration's
`upgrade()` function and emits idempotent SQL (CREATE TABLE IF NOT EXISTS,
ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT EXISTS) that can be applied
against an existing DB safely — re-running is a no-op.

It skips:
- The baseline migration (that's what schema.sql is for).
- Any statement mentioning `timescaledb`, `create_hypertable`, or
  `add_dimension` (the TimescaleDB extension is not installed in local dev).
- Data-seed migrations that use `op.get_bind().execute(...)` — those are
  not schema DDL. Run `alembic upgrade head` (on a TimescaleDB-enabled
  stack) or re-run individual seed modules if you need that data.

Strategy
--------
We load each migration module with a monkey-patched `alembic.op` that
does not connect to a database. Instead of actually running the Alembic
operation, each `op.*` call emits the equivalent idempotent raw SQL into
an in-memory list. `op.create_table()` and `op.add_column()` calls are
compiled via SQLAlchemy's PostgreSQL dialect. The collected SQL is then
either printed (--dry-run) or applied to the target DB.

Usage
-----
    python scripts/rebuild_db_from_migrations.py \\
        --db-url "postgresql://postgres:postgres@localhost:5432/mpdpms"

    # Preview only (don't touch the DB):
    python scripts/rebuild_db_from_migrations.py --dry-run

The script does NOT touch `alembic_version`. If you're stamping a fresh
DB, run `alembic stamp head` separately after this (or use `alembic
upgrade head` instead, which is the preferred path when TimescaleDB is
available).
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import pathlib
import subprocess
import sys
import types

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent
MIG_DIR = BACKEND_DIR / "alembic_migrations" / "versions"
DEFAULT_DB_URL = "postgresql://postgres:postgres@localhost:5432/mpdpms"


# ── State ────────────────────────────────────────────────────────────────
collected: list[tuple[str, str]] = []   # (source_migration, sql_or_comment)
current_migration: str = ""


import re as _re


def _make_idempotent(sql: str) -> str:
    """Rewrite non-idempotent CREATE statements to IF NOT EXISTS form.

    Handles:
    - CREATE [UNIQUE] INDEX name  →  CREATE [UNIQUE] INDEX IF NOT EXISTS name
    - CREATE TRIGGER name         →  wrap in DO $$ ... IF NOT EXISTS ... $$
      (Postgres doesn't support `CREATE TRIGGER IF NOT EXISTS`, so we wrap
      in a PL/pgSQL DO block that checks pg_trigger first.)
    """
    # CREATE [UNIQUE] INDEX <name> ON ...  — insert IF NOT EXISTS if missing
    sql = _re.sub(
        r"\bCREATE\s+(UNIQUE\s+)?INDEX\s+(?!IF\s+NOT\s+EXISTS\b)(\S+)",
        lambda m: f"CREATE {m.group(1) or ''}INDEX IF NOT EXISTS {m.group(2)}",
        sql,
        flags=_re.IGNORECASE,
    )

    # CREATE TRIGGER <name> ...  →  wrap whole statement in DO block
    # Only when trigger is emitted as a stand-alone statement.
    trig_match = _re.match(
        r"\s*CREATE\s+TRIGGER\s+(\w+)\s+(.*)",
        sql,
        flags=_re.IGNORECASE | _re.DOTALL,
    )
    if trig_match and "IF NOT EXISTS" not in sql.upper():
        trig_name = trig_match.group(1)
        sql = (
            f"DO $$ BEGIN\n"
            f"  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = '{trig_name}') THEN\n"
            f"    EXECUTE $trig${sql.rstrip(';')}$trig$;\n"
            f"  END IF;\n"
            f"END $$"
        )

    return sql


def _emit(sql: str) -> None:
    sql = sql.strip().rstrip(";")
    if not sql:
        return
    lowered = sql.lower()
    if (
        "timescaledb" in lowered
        or "create_hypertable" in lowered
        or "add_dimension" in lowered
    ):
        collected.append(
            (current_migration, f"-- [skipped timescaledb] {sql[:80]}...")
        )
        return
    # If a single op.execute contains multiple statements separated by ;,
    # split them so we can individually make indexes/triggers idempotent.
    # We need to be careful not to split inside strings or $$ bodies.
    pieces = _split_sql_statements(sql)
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        piece = _make_idempotent(piece)
        collected.append((current_migration, piece))


def _split_sql_statements(sql: str) -> list[str]:
    """Split on top-level semicolons, ignoring ';' inside single-quoted
    strings and $$...$$ dollar-quoted bodies."""
    out: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    in_sq = False  # single-quoted string
    dollar_tag: str | None = None  # inside $tag$ ... $tag$
    while i < n:
        ch = sql[i]
        if dollar_tag is not None:
            # Look for closing tag
            end_tag = f"${dollar_tag}$"
            if sql.startswith(end_tag, i):
                buf.append(end_tag)
                i += len(end_tag)
                dollar_tag = None
                continue
            buf.append(ch)
            i += 1
            continue
        if in_sq:
            buf.append(ch)
            if ch == "'":
                # Handle escaped ''
                if i + 1 < n and sql[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_sq = False
            i += 1
            continue
        # Not in string — check for dollar-quote start
        m = _re.match(r"\$(\w*)\$", sql[i:])
        if m:
            tag = m.group(1)
            buf.append(m.group(0))
            i += len(m.group(0))
            dollar_tag = tag
            continue
        if ch == "'":
            in_sq = True
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


# ── Fake alembic.op implementation ───────────────────────────────────────
def _fake_execute(sql):
    if hasattr(sql, "text"):
        sql = str(sql.text)
    elif not isinstance(sql, str):
        sql = str(sql)
    _emit(sql)


def _column_sql(col: sa.Column) -> str:
    col_type = col.type.compile(dialect=postgresql.dialect())
    parts = [f"{col.name} {col_type}"]
    if col.primary_key:
        parts.append("PRIMARY KEY")
    if col.nullable is False and not col.primary_key:
        parts.append("NOT NULL")
    if col.unique:
        parts.append("UNIQUE")
    if col.server_default is not None:
        default_arg = col.server_default.arg
        if isinstance(default_arg, str):
            parts.append(f"DEFAULT '{default_arg}'")
        else:
            parts.append(f"DEFAULT {default_arg.text}")
    for fk in col.foreign_keys:
        colspec = fk._colspec if isinstance(fk._colspec, str) else fk.target_fullname
        ondelete = f" ON DELETE {fk.ondelete}" if fk.ondelete else ""
        onupdate = f" ON UPDATE {fk.onupdate}" if fk.onupdate else ""
        if "." in colspec:
            tgt_tbl, tgt_col = colspec.rsplit(".", 1)
            parts.append(
                f"REFERENCES {tgt_tbl}({tgt_col}){ondelete}{onupdate}"
            )
    return " ".join(parts)


def _fake_create_table(name, *columns, **kwargs):
    cols = [c for c in columns if isinstance(c, sa.Column)]
    constraints = [c for c in columns if not isinstance(c, sa.Column)]
    schema = kwargs.pop("schema", None)
    prefix = f"{schema}." if schema else ""
    col_defs = [_column_sql(c) for c in cols]
    for c in constraints:
        try:
            col_defs.append(str(c).strip())
        except Exception:
            pass
    sql = (
        f"CREATE TABLE IF NOT EXISTS {prefix}{name} (\n    "
        + ",\n    ".join(col_defs)
        + "\n)"
    )
    _emit(sql)
    return None


def _fake_add_column(table_name, column, schema=None):
    col_type = column.type.compile(dialect=postgresql.dialect())
    parts = [f"{column.name} {col_type}"]
    if column.nullable is False and not column.primary_key:
        parts.append("NOT NULL")
    if column.server_default is not None:
        default_arg = column.server_default.arg
        if isinstance(default_arg, str):
            parts.append(f"DEFAULT '{default_arg}'")
        else:
            parts.append(f"DEFAULT {default_arg.text}")
    for fk in column.foreign_keys:
        colspec = fk._colspec if isinstance(fk._colspec, str) else fk.target_fullname
        ondelete = f" ON DELETE {fk.ondelete}" if fk.ondelete else ""
        if "." in colspec:
            tgt_tbl, tgt_col = colspec.rsplit(".", 1)
            parts.append(f"REFERENCES {tgt_tbl}({tgt_col}){ondelete}")
    col_sql = " ".join(parts)
    schema_prefix = f"{schema}." if schema else ""
    _emit(
        f"ALTER TABLE {schema_prefix}{table_name} "
        f"ADD COLUMN IF NOT EXISTS {col_sql}"
    )


def _fake_create_index(name, table_name, columns, unique=False, schema=None, **kwargs):
    cols = ", ".join(columns)
    uniq = "UNIQUE " if unique else ""
    schema_prefix = f"{schema}." if schema else ""
    _emit(
        f"CREATE {uniq}INDEX IF NOT EXISTS {name} "
        f"ON {schema_prefix}{table_name} ({cols})"
    )


def _noop(*args, **kwargs):
    """For downgrade-style ops we don't want to apply."""
    pass


class _NoopConnection:
    """Stub for op.get_bind() — used by data-seed migrations, which we skip."""

    def execute(self, *args, **kwargs):
        collected.append(
            (current_migration, "-- [skipped data-seed via op.get_bind()]")
        )
        return None


def _fake_get_bind():
    return _NoopConnection()


def install_fake_op():
    fake = types.ModuleType("alembic.op")
    fake.execute = _fake_execute
    fake.create_table = _fake_create_table
    fake.add_column = _fake_add_column
    fake.create_index = _fake_create_index
    fake.drop_column = _noop
    fake.drop_table = _noop
    fake.drop_index = _noop
    fake.alter_column = _noop
    fake.get_bind = _fake_get_bind
    alembic_pkg = types.ModuleType("alembic")
    alembic_pkg.op = fake
    sys.modules["alembic"] = alembic_pkg
    sys.modules["alembic.op"] = fake


def load_and_run(mig_path: pathlib.Path, skip_baseline: bool = True) -> None:
    global current_migration
    current_migration = mig_path.name
    if skip_baseline and "000001_baseline" in mig_path.name:
        collected.append(
            (current_migration, "-- baseline skipped (use schema.sql)")
        )
        return
    spec = importlib.util.spec_from_file_location(f"mig_{mig_path.stem}", mig_path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        collected.append((current_migration, f"-- ERROR loading module: {e!r}"))
        return
    if hasattr(mod, "upgrade"):
        try:
            mod.upgrade()
        except Exception as e:
            collected.append(
                (current_migration, f"-- ERROR running upgrade(): {e!r}")
            )


def render_sql() -> str:
    lines: list[str] = []
    last_mig = None
    for mig, sql in collected:
        if mig != last_mig:
            lines.append(f"\n-- ========== {mig} ==========")
            last_mig = mig
        lines.append(sql + ";")
    return "\n".join(lines)


def real_statement_count() -> int:
    return sum(1 for _, s in collected if not s.lstrip().startswith("--"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL", DEFAULT_DB_URL),
        help="PostgreSQL connection URL",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the SQL that would be executed instead of applying it",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Also write the generated SQL to this file",
    )
    args = parser.parse_args()

    install_fake_op()
    migs = sorted(MIG_DIR.glob("*.py"))
    print(f"Found {len(migs)} migration files in {MIG_DIR}", file=sys.stderr)
    for m in migs:
        load_and_run(m)

    sql = render_sql()
    print(
        f"Extracted {real_statement_count()} real DDL statements "
        f"({len(collected)} total items with comments)",
        file=sys.stderr,
    )

    if args.output:
        pathlib.Path(args.output).write_text(sql)
        print(f"Wrote SQL to {args.output}", file=sys.stderr)

    if args.dry_run:
        print(sql)
        return 0

    # Apply via psql (simpler + gets CREATE TABLE notices right).
    print(f"Applying SQL to {args.db_url} ...", file=sys.stderr)
    proc = subprocess.run(
        ["psql", args.db_url, "-v", "ON_ERROR_STOP=0"],
        input=sql,
        text=True,
        capture_output=True,
    )
    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    errors = [
        line for line in proc.stderr.splitlines() if line.startswith("ERROR:")
    ]
    if errors:
        print(f"\n{len(errors)} error(s) encountered:", file=sys.stderr)
        for e in errors[:20]:
            print(f"  {e}", file=sys.stderr)
        return 1
    print("All DDL applied cleanly (any NOTICE lines above are expected idempotent no-ops).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
