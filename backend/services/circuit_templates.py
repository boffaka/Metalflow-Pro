"""Circuit template loader. Templates are versioned YAML files in
`backend/data/circuit_templates/`. Reviewed via PR; no UI to author."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "data" / "circuit_templates"


def load_template(key: str) -> dict[str, Any]:
    """Return the parsed YAML for a circuit. Raises ValueError if missing."""
    path = TEMPLATES_DIR / f"{key}.yaml"
    if not path.exists():
        raise ValueError(f"Unknown circuit template: {key}")
    return yaml.safe_load(path.read_text())


def list_templates() -> list[dict[str, Any]]:
    """Return [{key, label, equipment_count}, ...] for every YAML in the dir."""
    out = []
    for path in sorted(TEMPLATES_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        out.append({
            "key": data["key"],
            "label": data["label"],
            "equipment_count": len(data.get("equipment", [])),
        })
    return out
