"""Recovery breakdown for PLM dashboard (gravity + leach + plant)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db

try:
    from backend.helpers import (
        combined_plant_recovery_pct,
        _parse_simulation_operations,
    )
except ImportError:
    from helpers import (
        combined_plant_recovery_pct,
        _parse_simulation_operations,
    )


def test_combined_plant_recovery_gravity_then_leach():
    # 20% gravity on feed, 90% leach on residue -> 20 + 0.8*90 = 92%
    assert combined_plant_recovery_pct(20.0, 90.0) == pytest.approx(92.0)


def test_parse_simulation_operations_gravity_and_cil():
    ops = [
        {
            "op_code": "GRAVITE_KNELSON",
            "model_used": "gravity_plant_recovery",
            "performance": {"recovery_pct": 18.5},
        },
        {
            "op_code": "CIL",
            "model_used": "cil",
            "performance": {"recovery_pct": 94.0},
        },
    ]
    parsed = _parse_simulation_operations(ops)
    assert parsed["gravity_recovery_pct"] == 18.5
    assert parsed["leach_recovery_pct"] == 94.0
