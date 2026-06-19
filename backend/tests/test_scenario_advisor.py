"""Tests for scenario_advisor engine rule functions."""

try:
    from engines.scenario_advisor import (
        _lims_rules,
        _economic_rules,
        _history_rules,
        _deduplicate,
        _prioritize,
    )
except ImportError:
    from backend.engines.scenario_advisor import (
        _lims_rules,
        _economic_rules,
        _history_rules,
        _deduplicate,
        _prioritize,
    )


# ---------------------------------------------------------------------------
# _lims_rules
# ---------------------------------------------------------------------------

class TestLimsRules:
    def test_high_bwi_suggests_hpgr(self):
        lims = {"b1.bwi_kwh_t": 19.0}
        ops = {"SAG_MILL_01"}
        result = _lims_rules(lims, ops)
        ids = [s["id"] for s in result]
        assert "auto_hpgr_swap" in ids
        match = [s for s in result if s["id"] == "auto_hpgr_swap"][0]
        assert match["confidence"] == "high"

    def test_medium_bwi_suggests_hpgr_medium(self):
        lims = {"b1.bwi_kwh_t": 17.0}
        ops = {"SAG_MILL_01"}
        result = _lims_rules(lims, ops)
        match = [s for s in result if s["id"] == "auto_hpgr_swap"][0]
        assert match["confidence"] == "medium"

    def test_low_bwi_no_hpgr_suggestion(self):
        lims = {"b1.bwi_kwh_t": 12.0}
        ops = {"SAG_MILL_01"}
        result = _lims_rules(lims, ops)
        ids = [s["id"] for s in result]
        assert "auto_hpgr_swap" not in ids

    def test_hpgr_already_present_no_suggestion(self):
        lims = {"b1.bwi_kwh_t": 20.0}
        ops = {"SAG_MILL_01", "HPGR_01"}
        result = _lims_rules(lims, ops)
        ids = [s["id"] for s in result]
        assert "auto_hpgr_swap" not in ids

    def test_high_grg_suggests_gravity(self):
        lims = {"c2.grg_rec_pct": 50.0}
        ops = {"SAG_MILL_01"}
        result = _lims_rules(lims, ops)
        ids = [s["id"] for s in result]
        assert "auto_add_gravity" in ids
        match = [s for s in result if s["id"] == "auto_add_gravity"][0]
        assert match["confidence"] == "high"

    def test_gravity_already_present_no_suggestion(self):
        lims = {"c2.grg_rec_pct": 50.0}
        ops = {"GRAVITY_CONCENTRATOR"}
        result = _lims_rules(lims, ops)
        ids = [s["id"] for s in result]
        assert "auto_add_gravity" not in ids

    def test_clean_ore_suggests_cip(self):
        lims = {
            "a1.c_organic_pct": 0.01,
            "a1.s_sulfide_pct": 0.1,
            "d1.nacn_consumption_kg_t": 0.5,
        }
        ops = {"CIL"}
        result = _lims_rules(lims, ops)
        ids = [s["id"] for s in result]
        assert "auto_cip_vs_cil" in ids

    def test_dirty_ore_no_cip_suggestion(self):
        lims = {
            "a1.c_organic_pct": 0.1,
            "a1.s_sulfide_pct": 0.1,
            "d1.nacn_consumption_kg_t": 0.5,
        }
        ops = {"CIL"}
        result = _lims_rules(lims, ops)
        ids = [s["id"] for s in result]
        assert "auto_cip_vs_cil" not in ids

    def test_high_sulfide_suggests_flotation(self):
        lims = {"a1.s_sulfide_pct": 3.5}
        ops = {"SAG_MILL_01"}
        result = _lims_rules(lims, ops)
        ids = [s["id"] for s in result]
        assert "auto_add_flotation" in ids

    def test_high_arsenic_suggests_detox(self):
        lims = {"a1.as_ppm": 1500}
        ops = {"CIP"}
        result = _lims_rules(lims, ops)
        ids = [s["id"] for s in result]
        assert "auto_add_detox" in ids

    def test_high_nacn_suggests_pretreat(self):
        lims = {"d1.nacn_consumption_kg_t": 3.0}
        ops = {"CIP"}
        result = _lims_rules(lims, ops)
        ids = [s["id"] for s in result]
        assert "auto_pretreat_high_nacn" in ids


# ---------------------------------------------------------------------------
# _economic_rules
# ---------------------------------------------------------------------------

class TestEconomicRules:
    def test_high_energy_low_gold_price(self):
        project = {"economics": {"au_price_usd_oz": 1800}}
        last_run = {"results": {"energy_kwh_t": 30}, "simulation_params": {}}
        result = _economic_rules(project, last_run)
        ids = [s["id"] for s in result]
        assert "auto_reduce_energy" in ids

    def test_high_aisc(self):
        project = {}
        last_run = {"results": {"aisc_usd_oz": 1400}, "simulation_params": {}}
        result = _economic_rules(project, last_run)
        ids = [s["id"] for s in result]
        assert "auto_optimize_opex" in ids

    def test_low_recovery(self):
        project = {}
        last_run = {"results": {"recovery_pct": 80}, "simulation_params": {}}
        result = _economic_rules(project, last_run)
        ids = [s["id"] for s in result]
        assert "auto_improve_recovery" in ids


# ---------------------------------------------------------------------------
# _history_rules
# ---------------------------------------------------------------------------

class TestHistoryRules:
    def test_suggests_monte_carlo_after_5_rigorous(self):
        runs = [{"run_type": "rigorous_steady_state"} for _ in range(6)]
        result = _history_rules(runs)
        ids = [s["id"] for s in result]
        assert "auto_run_monte_carlo" in ids

    def test_no_monte_carlo_if_already_done(self):
        runs = [{"run_type": "rigorous_steady_state"} for _ in range(6)]
        runs.append({"run_type": "monte_carlo_lom"})
        result = _history_rules(runs)
        ids = [s["id"] for s in result]
        assert "auto_run_monte_carlo" not in ids

    def test_no_monte_carlo_if_few_runs(self):
        runs = [{"run_type": "rigorous_steady_state"} for _ in range(3)]
        result = _history_rules(runs)
        ids = [s["id"] for s in result]
        assert "auto_run_monte_carlo" not in ids

    def test_sensitivity_driven_suggestion(self):
        runs = []
        sensitivity = {"grind_p80": 3.5, "recovery": 1.0}
        result = _history_rules(runs, sensitivity)
        ids = [s["id"] for s in result]
        assert "auto_optimize_grind_p80" in ids
        assert "auto_optimize_recovery" not in ids  # impact <= 2


# ---------------------------------------------------------------------------
# _deduplicate
# ---------------------------------------------------------------------------

class TestDeduplicate:
    def test_marks_tested(self):
        suggestions = [
            {"id": "auto_hpgr_swap", "confidence": "high"},
            {"id": "auto_add_gravity", "confidence": "medium"},
        ]
        tested = {"auto_hpgr_swap"}
        result = _deduplicate(suggestions, tested)
        by_id = {s["id"]: s for s in result}
        assert by_id["auto_hpgr_swap"]["already_tested"] is True
        assert by_id["auto_add_gravity"]["already_tested"] is False

    def test_removes_duplicates(self):
        suggestions = [
            {"id": "auto_hpgr_swap", "confidence": "high"},
            {"id": "auto_hpgr_swap", "confidence": "high"},
        ]
        result = _deduplicate(suggestions, set())
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _prioritize
# ---------------------------------------------------------------------------

class TestPrioritize:
    def test_higher_confidence_gets_lower_priority_number(self):
        suggestions = [
            {"id": "low", "confidence_score": 0.5, "estimated_impact": 5, "already_tested": False},
            {"id": "high", "confidence_score": 0.9, "estimated_impact": 9, "already_tested": False},
        ]
        result = _prioritize(suggestions)
        by_id = {s["id"]: s for s in result}
        assert by_id["high"]["priority"] < by_id["low"]["priority"]

    def test_tested_penalty_reduces_priority(self):
        suggestions = [
            {"id": "tested", "confidence_score": 0.9, "estimated_impact": 9, "already_tested": True},
            {"id": "fresh", "confidence_score": 0.9, "estimated_impact": 9, "already_tested": False},
        ]
        result = _prioritize(suggestions)
        by_id = {s["id"]: s for s in result}
        assert by_id["fresh"]["priority"] < by_id["tested"]["priority"]
