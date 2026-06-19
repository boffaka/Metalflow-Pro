"""
Seed parameter_registry table from industry_defaults.yaml.
Idempotent: INSERT ON CONFLICT DO UPDATE.
"""
from __future__ import annotations

import pathlib
import yaml
import logging

logger = logging.getLogger("mpdpms.seed_params")

YAML_PATH = pathlib.Path(__file__).parent / "industry_defaults.yaml"

_STAGE_MAP = {
    "metallurgical": "pfs",
    "economic": "scoping",
    "equipment": "fs",
    "environmental": "pfs",
    "geotechnical": "pfs",
}


def seed_registry(conn_execute):
    """Insert/update all parameters from YAML into parameter_registry."""
    data = yaml.safe_load(YAML_PATH.read_text())
    count = 0
    for category, params in data.items():
        for key, spec in params.items():
            r = spec.get("range", [None, None])
            conn_execute(
                """
                INSERT INTO parameter_registry
                    (key, category, display_name, unit, value_type,
                     min_value, max_value, default_value, default_value_text,
                     ni43101_stage, source_reference, description)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET
                    category = EXCLUDED.category,
                    display_name = EXCLUDED.display_name,
                    default_value = EXCLUDED.default_value,
                    min_value = EXCLUDED.min_value,
                    max_value = EXCLUDED.max_value,
                    source_reference = EXCLUDED.source_reference
                """,
                (
                    key,
                    category,
                    key.replace("_", " ").title(),
                    spec.get("unit", ""),
                    "text" if isinstance(spec.get("value"), str) else "numeric",
                    r[0], r[1],
                    spec["value"] if isinstance(spec.get("value"), (int, float)) else None,
                    spec["value"] if isinstance(spec.get("value"), str) else None,
                    _STAGE_MAP.get(category, "pfs"),
                    spec.get("reference", ""),
                    "",
                ),
            )
            count += 1
    logger.info("Seeded %d parameters into parameter_registry", count)
    return count
