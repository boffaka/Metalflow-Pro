# backend/tests/test_unit_op_dispatcher.py
import pytest
from engines.stream_state import StreamState
from engines.unit_op_dispatcher import UnitOpDispatcher, ProjectContext

pytestmark = pytest.mark.no_db


FEED = StreamState(solids_tph=1517, water_tph=2275, au_g_t=1.5,
                   au_recovery_pct=100, p80_um=150_000, energy_kwh_t=0)
CTX = ProjectContext(target_tph=1517, gold_price_usd=2000, availability=0.92)


def test_feed_op_returns_stream():
    d = UnitOpDispatcher()
    out = d.dispatch("FEED", {"in": FEED}, {"feed_tph": 1517, "au_g_t": 1.5}, CTX)
    assert "out" in out
    assert out["out"].solids_tph == pytest.approx(1517, rel=0.01)


def test_pump_passthrough():
    d = UnitOpDispatcher()
    out = d.dispatch("POMPE", {"in": FEED}, {}, CTX)
    assert out["out"].solids_tph == FEED.solids_tph
    assert out["out"].au_g_t == FEED.au_g_t


def test_tsf_sink():
    d = UnitOpDispatcher()
    out = d.dispatch("TSF", {"in": FEED}, {}, CTX)
    assert out == {}


def test_sag_mill_reduces_p80():
    d = UnitOpDispatcher()
    params = {"wi": 14, "f80_mm": 150, "p80_um": 2000}
    out = d.dispatch("SAG_MILL", {"in": FEED}, params, CTX)
    assert out["out"].p80_um < FEED.p80_um
    assert out["out"].energy_kwh_t > 0


def test_cil_reduces_au_grade():
    d = UnitOpDispatcher()
    feed_cil = FEED.copy(p80_um=75)
    params = {"srt_h": 24, "r_inf": 0.95, "k_h": 0.15}
    out = d.dispatch("CIL_TANK", {"in": feed_cil}, params, CTX)
    assert "out" in out
    assert out["out"].au_recovery_pct <= FEED.au_recovery_pct


def test_flotation_rougher_two_ports():
    d = UnitOpDispatcher()
    params = {"r_max": 0.85, "k": 0.4, "tau_min": 8}
    out = d.dispatch("FLOTATION_ROUGHER", {"in": FEED}, params, CTX)
    assert "conc" in out
    assert "tails" in out


def test_cyclone_two_ports():
    d = UnitOpDispatcher()
    params = {"efficiency": 0.75, "p80_overflow_um": 75}
    out = d.dispatch("CYCLONE", {"in": FEED}, params, CTX)
    assert "overflow" in out
    assert "underflow" in out


def test_unknown_opcode_raises():
    d = UnitOpDispatcher()
    with pytest.raises(ValueError, match="op_code inconnu"):
        d.dispatch("UNKNOWN_OP", {"in": FEED}, {}, CTX)
