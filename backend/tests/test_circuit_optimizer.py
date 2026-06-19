"""Tests for the circuit optimizer engine."""
import pytest

pytestmark = pytest.mark.no_db

from engines.circuit_optimizer import (
    CIRCUITS,
    filter_circuits,
    generate_justification,
    recommend_circuit,
    score_circuits,
    select_four_circuits,
)


def _ore(**overrides):
    """Create a default ore profile with optional overrides."""
    base = {
        "grade_au": 1.5, "c_organic_pct": 0.1, "s_total_pct": 1.0,
        "as_ppm": 50, "bwi": 14.0, "grg_pct": 20.0,
        "leach_recovery_pct": 90.0, "nacn_kg_t": 0.5,
        "flot_recovery_pct": 0.0, "throughput_tph": 913,
        "gold_price": 2340, "availability_pct": 92,
        "op_hours_day": 22, "mine_life_years": 14, "discount_rate_pct": 5,
    }
    base.update(overrides)
    return base


def test_filter_removes_gravity_when_grg_low():
    """Circuits with gravity should be eliminated when GRG < 5%."""
    ore = _ore(grg_pct=2.0)
    valid, filtered = filter_circuits(ore)
    gravity_ids = {c["id"] for c in CIRCUITS if c["has_gravity"]}
    valid_ids = {c["id"] for c in valid}
    assert gravity_ids.isdisjoint(valid_ids), "Gravity circuits should be filtered when GRG < 5%"


def test_filter_requires_gravity_when_grg_high():
    """Circuits without gravity should be eliminated when GRG > 30%."""
    ore = _ore(grg_pct=45.0)
    valid, filtered = filter_circuits(ore)
    for c in valid:
        assert c["has_gravity"], f"{c['name']} should have gravity when GRG=45%"


def test_filter_eliminates_direct_cyanide_when_low_recovery():
    """Non-pretreat circuits eliminated when leach recovery < 70%."""
    ore = _ore(leach_recovery_pct=55.0, s_total_pct=8.0)
    valid, filtered = filter_circuits(ore)
    for c in valid:
        assert c["has_pretreat"] or c["is_heap"], f"{c['name']} should have pretreatment"


def test_filter_keeps_heap_for_low_grade():
    """Heap leach should be available for low-grade ores."""
    ore = _ore(grade_au=0.3, grg_pct=2.0)
    valid, _ = filter_circuits(ore)
    heap_ids = {c["id"] for c in valid if c["is_heap"]}
    assert len(heap_ids) > 0, "Heap leach should be available for grade < 0.5 g/t"


def test_scoring_produces_npv():
    """All scored circuits should have positive NPV for viable ore."""
    ore = _ore(grade_au=1.5, grg_pct=20.0, leach_recovery_pct=90.0)
    valid, _ = filter_circuits(ore)
    scored = score_circuits(valid, ore)
    assert len(scored) > 0
    assert scored[0]["npv_musd"] > 0, "Top circuit should have positive NPV"


def test_scoring_sorted_by_npv():
    """Results should be sorted by NPV descending."""
    ore = _ore()
    valid, _ = filter_circuits(ore)
    scored = score_circuits(valid, ore)
    npvs = [s["npv_musd"] for s in scored]
    assert npvs == sorted(npvs, reverse=True), "Should be sorted by NPV desc"


def test_pareto_identification():
    """At least one circuit should be Pareto-optimal."""
    ore = _ore()
    valid, _ = filter_circuits(ore)
    scored = score_circuits(valid, ore)
    pareto = [s for s in scored if s["is_pareto"]]
    assert len(pareto) >= 1, "At least one Pareto-optimal circuit expected"


def test_justification_contains_key_info():
    """Justification should reference ore characteristics."""
    ore = _ore(bwi=18.0, grg_pct=35.0, s_total_pct=1.5)
    valid, _ = filter_circuits(ore)
    scored = score_circuits(valid, ore)
    text = generate_justification(scored[0], ore)
    assert "BWi" in text
    assert "GRG" in text
    assert "Sulfures" in text
    assert "recuperation" in text.lower() or "récupération" in text.lower()


def test_select_four_circuits_returns_exactly_four():
    ore = _ore()
    circuits, label = select_four_circuits(ore)
    assert len(circuits) == 4
    assert len({c["id"] for c in circuits}) == 4
    assert len(label) > 3


def test_select_four_refractory_set():
    ore = _ore(s_total_pct=8.0)
    circuits, _ = select_four_circuits(ore)
    ids = {c["id"] for c in circuits}
    assert ids == {"C08", "C09", "C06", "C05"}


def test_select_four_swaps_cil_to_cip_when_advised():
    """Standard set C02/C03/... become C13/C14/... when leach_type is CIP."""
    ore = _ore()
    circuits, _ = select_four_circuits(ore, leach_type="CIP")
    ids = {c["id"] for c in circuits}
    assert "C13" in ids
    assert "C02" not in ids
    assert len(circuits) == 4
    assert ids.issubset({"C13", "C14", "C15", "C16"})


def test_select_four_cip_high_grg_returns_four():
    ore = _ore(grg_pct=35.0)
    circuits, _ = select_four_circuits(ore, leach_type="CIP")
    assert len(circuits) == 4


def test_recommend_circuit_evaluates_four(monkeypatch):
    """recommend_circuit must score exactly four candidates."""
    ore = _ore()

    def fake_extract(pid, db_qall, db_qone):
        return ore

    monkeypatch.setattr(
        "engines.circuit_optimizer.extract_ore_profile", fake_extract,
    )
    result = recommend_circuit("proj-1", lambda *a, **k: [], lambda *a, **k: None)
    assert result["evaluated_count"] == 4
    assert len(result["candidates"]) == 4
    assert result["recommended"] is not None
    assert result["comparison_summary"]
    assert result["data_sources"]["lims"] is True


def test_all_circuits_have_required_fields():
    """Every circuit in the library should have all required fields."""
    required = {"id", "name", "ops", "has_gravity", "has_flotation",
                "has_hpgr", "has_pretreat", "is_heap",
                "base_recovery", "energy_factor", "opex_base", "capex_factor"}
    for c in CIRCUITS:
        missing = required - set(c.keys())
        assert not missing, f"Circuit {c['id']} missing: {missing}"
