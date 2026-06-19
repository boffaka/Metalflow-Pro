# backend/tests/test_stream_state.py
import pytest
from engines.stream_state import StreamState

pytestmark = pytest.mark.no_db


def test_stream_state_defaults():
    s = StreamState(solids_tph=100, water_tph=150, au_g_t=1.5,
                    au_recovery_pct=100, p80_um=150_000, energy_kwh_t=0)
    assert s.cn_kg_t == 0.0
    assert s.extra == {}


def test_stream_state_copy():
    s = StreamState(solids_tph=100, water_tph=150, au_g_t=1.5,
                    au_recovery_pct=100, p80_um=150_000, energy_kwh_t=0)
    s2 = s.copy(au_g_t=1.0)
    assert s2.au_g_t == 1.0
    assert s2.solids_tph == 100
    assert s is not s2


def test_stream_state_slurry_tph():
    s = StreamState(solids_tph=100, water_tph=150, au_g_t=1.5,
                    au_recovery_pct=100, p80_um=75, energy_kwh_t=0)
    assert s.slurry_tph == 250.0


def test_stream_state_zero_feed():
    s = StreamState(solids_tph=0, water_tph=0, au_g_t=0,
                    au_recovery_pct=0, p80_um=0, energy_kwh_t=0)
    assert s.slurry_tph == 0
