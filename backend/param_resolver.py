"""
Hierarchical parameter resolution engine.

Resolution order (highest priority first):
  4. Scenario  -> scenario_simulation_params table
  3. Project   -> project_params table (latest version)
  2. Industry  -> industry_defaults.yaml (loaded at startup)
  1. Code      -> settings.py defaults (fallback, logged as warning)
"""
from __future__ import annotations

import logging
import pathlib

import yaml

logger = logging.getLogger("mpdpms.param_resolver")

_YAML_PATH = pathlib.Path(__file__).parent / "industry_defaults.yaml"
_INDUSTRY_CACHE: dict[str, dict] | None = None


def load_industry_defaults(path: pathlib.Path | None = None) -> dict[str, dict]:
    """Load and flatten industry_defaults.yaml into {key: {value, unit, range, reference}}."""
    global _INDUSTRY_CACHE
    if _INDUSTRY_CACHE is not None and path is None:
        return _INDUSTRY_CACHE

    p = path or _YAML_PATH
    try:
        raw = yaml.safe_load(p.read_text())
        flat: dict[str, dict] = {}
        for _category, params in raw.items():
            for key, spec in params.items():
                flat[key] = spec
        if path is None:
            _INDUSTRY_CACHE = flat
        return flat
    except FileNotFoundError:
        logger.error("Industry defaults file not found at %s — returning empty defaults", p)
        return {}
    except (yaml.YAMLError, AttributeError) as e:
        logger.error("Failed to parse industry defaults YAML at %s: %s — returning empty defaults", p, e)
        return {}


def resolve_param(
    project_id: str,
    key: str,
    scenario_id: str | None = None,
    *,
    _industry_cache: dict | None = None,
    _skip_db: bool = False,
) -> tuple[float | str, dict]:
    """
    Resolve a single parameter through the 4-level hierarchy.
    Returns (value, metadata_dict).
    Raises KeyError if key is not found at any level.
    """
    # Level 4: Scenario
    if scenario_id and not _skip_db:
        val = _lookup_scenario(scenario_id, key)
        if val is not None:
            return val, {"source": "scenario", "scenario_id": scenario_id}

    # Level 3: Project
    if not _skip_db:
        val = _lookup_project(project_id, key)
        if val is not None:
            return val["value"], {
                "source": "project",
                "set_by": val.get("set_by"),
                "set_at": str(val.get("set_at", "")),
            }

    # Level 2: Industry defaults
    cache = _industry_cache or load_industry_defaults()
    if key in cache:
        spec = cache[key]
        return spec["value"], {
            "source": "industry_default",
            "reference": spec.get("reference", ""),
        }

    # Level 1: Code fallback (settings.py)
    if not _skip_db:
        val = _lookup_settings(key)
        if val is not None:
            logger.warning("Using code fallback for %s in project %s", key, project_id)
            return val, {"source": "code_fallback"}

    raise KeyError(f"Parameter '{key}' not found at any level for project {project_id}")


def resolve_params_batch(
    project_id: str,
    keys: list[str],
    scenario_id: str | None = None,
    *,
    _industry_cache: dict | None = None,
    _skip_db: bool = False,
) -> dict[str, tuple[float | str, dict]]:
    """
    Resolve multiple parameters. Single DB query per level for efficiency.
    Returns {key: (value, metadata)}.
    """
    results = {}
    remaining = set(keys)

    # Level 4: Batch scenario lookup
    if scenario_id and not _skip_db and remaining:
        found = _batch_lookup_scenario(scenario_id, remaining)
        for k, v in found.items():
            results[k] = (v, {"source": "scenario", "scenario_id": scenario_id})
        remaining -= found.keys()

    # Level 3: Batch project lookup
    if not _skip_db and remaining:
        found = _batch_lookup_project(project_id, remaining)
        for k, row in found.items():
            results[k] = (row["value"], {
                "source": "project",
                "set_by": row.get("set_by"),
                "set_at": str(row.get("set_at", "")),
            })
        remaining -= found.keys()

    # Level 2: Industry defaults (in-memory, no DB)
    cache = _industry_cache or load_industry_defaults()
    for k in list(remaining):
        if k in cache:
            results[k] = (cache[k]["value"], {
                "source": "industry_default",
                "reference": cache[k].get("reference", ""),
            })
            remaining.discard(k)

    # Level 1: Code fallback
    for k in list(remaining):
        if not _skip_db:
            val = _lookup_settings(k)
            if val is not None:
                logger.warning("Using code fallback for %s in project %s", k, project_id)
                results[k] = (val, {"source": "code_fallback"})
                remaining.discard(k)

    if remaining:
        raise KeyError(
            f"Parameters not found at any level for project {project_id}: {remaining}"
        )
    return results


# -- DB lookup helpers --------------------------------------------------------

def _lookup_scenario(scenario_id: str, key: str) -> float | None:
    try:
        from .db import qone
    except ImportError:
        from db import qone
    row = qone(
        "SELECT param_value, param_value_text FROM scenario_simulation_params "
        "WHERE scenario_id = %s AND param_key = %s",
        (scenario_id, key),
    )
    if row:
        return row["param_value"] if row["param_value"] is not None else row["param_value_text"]
    return None


def _batch_lookup_scenario(scenario_id: str, keys: set[str]) -> dict[str, float]:
    try:
        from .db import qall
    except ImportError:
        from db import qall
    rows = qall(
        "SELECT param_key, param_value, param_value_text FROM scenario_simulation_params "
        "WHERE scenario_id = %s AND param_key = ANY(%s)",
        (scenario_id, list(keys)),
    )
    return {
        r["param_key"]: (r["param_value"] if r["param_value"] is not None else r["param_value_text"])
        for r in rows
    }


def _lookup_project(project_id: str, key: str) -> dict | None:
    try:
        from .db import qone
    except ImportError:
        from db import qone
    row = qone(
        "SELECT value, value_text, set_by, set_at FROM project_params "
        "WHERE project_id = %s AND param_key = %s "
        "ORDER BY version DESC LIMIT 1",
        (project_id, key),
    )
    if row:
        return {
            "value": row["value"] if row["value"] is not None else row["value_text"],
            "set_by": row.get("set_by"),
            "set_at": row.get("set_at"),
        }
    return None


def _batch_lookup_project(project_id: str, keys: set[str]) -> dict[str, dict]:
    try:
        from .db import qall
    except ImportError:
        from db import qall
    rows = qall(
        "SELECT DISTINCT ON (param_key) param_key, value, value_text, set_by, set_at "
        "FROM project_params WHERE project_id = %s AND param_key = ANY(%s) "
        "ORDER BY param_key, version DESC",
        (project_id, list(keys)),
    )
    return {
        r["param_key"]: {
            "value": r["value"] if r["value"] is not None else r["value_text"],
            "set_by": r.get("set_by"),
            "set_at": r.get("set_at"),
        }
        for r in rows
    }


def _lookup_settings(key: str) -> float | None:
    try:
        from .settings import get_settings
    except ImportError:
        from settings import get_settings
    s = get_settings()
    attr = f"default_{key}" if not key.startswith("default_") else key
    return getattr(s, attr, None)
