"""
Lightweight database management entrypoint for MPDPMS.

Examples:
    python manage_db.py upgrade
    python manage_db.py current
    python manage_db.py history
"""
from __future__ import annotations

import sys
from pathlib import Path
import importlib

from alembic.config import Config
try:
    from .settings import get_settings
except ImportError:  # pragma: no cover - supports direct script imports
    from settings import get_settings


def _config() -> Config:
    backend_dir = Path(__file__).resolve().parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    return cfg


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python manage_db.py [upgrade|downgrade|current|history] [revision]")
        return 1

    cmd = argv[1]
    cfg = _config()
    backend_path = str(Path(__file__).resolve().parent)
    removed = False
    if backend_path in sys.path:
        sys.path.remove(backend_path)
        removed = True
    try:
        command = importlib.import_module("alembic.command")
    finally:
        if removed:
            sys.path.insert(0, backend_path)

    if cmd == "upgrade":
        command.upgrade(cfg, argv[2] if len(argv) > 2 else "head")
    elif cmd == "downgrade":
        command.downgrade(cfg, argv[2] if len(argv) > 2 else "-1")
    elif cmd == "current":
        command.current(cfg)
    elif cmd == "history":
        command.history(cfg)
    else:
        print(f"Unknown command: {cmd}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
