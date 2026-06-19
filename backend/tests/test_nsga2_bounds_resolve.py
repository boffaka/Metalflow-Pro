"""Tests for NSGA-II decision bounds merging from optimization job variables."""
import pytest

pytestmark = pytest.mark.no_db

import numpy as np

try:
    from engines.nsga2_optimizer import VARIABLE_BOUNDS, resolve_nsga2_bounds
except ImportError:
    from backend.engines.nsga2_optimizer import VARIABLE_BOUNDS, resolve_nsga2_bounds


def test_resolve_bounds_matches_defaults_when_empty():
    b = resolve_nsga2_bounds([])
    assert b.shape == VARIABLE_BOUNDS.shape
    assert np.allclose(b, VARIABLE_BOUNDS.astype(float))


def test_resolve_bounds_matches_defaults_when_none():
    b = resolve_nsga2_bounds(None)
    assert np.allclose(b, VARIABLE_BOUNDS.astype(float))


def test_resolve_bounds_narrows_p80():
    b = resolve_nsga2_bounds([{"param": "p80_um", "min": 70.0, "max": 90.0}])
    assert b[0, 0] == 70.0 and b[0, 1] == 90.0
    assert b[1, 0] == VARIABLE_BOUNDS[1, 0]


def test_resolve_bounds_clamps_to_global_envelope():
    b = resolve_nsga2_bounds([{"param": "p80_um", "min": 1.0, "max": 9999.0}])
    assert b[0, 0] == float(VARIABLE_BOUNDS[0, 0])
    assert b[0, 1] == float(VARIABLE_BOUNDS[0, 1])


def test_resolve_bounds_ignores_unknown_param():
    b = resolve_nsga2_bounds([{"param": "not_a_variable", "min": 1.0, "max": 2.0}])
    assert np.allclose(b, VARIABLE_BOUNDS.astype(float))
