"""Tests for scenario_advisor extensions — niveaux 4 (blockmodel), 5 (design),
6 (equipment/costs), 7 (géomet domains).

Plan 2 — following TDD: tests defined here must fail before implementation.
Uses mocked inputs (no DB fixtures) since data-source structure varies widely
across projects (see spec §6 graceful-degradation clause).
"""
from __future__ import annotations

try:
    from engines.scenario_advisor import (
        _blockmodel_rules,
        _design_consistency_rules,
        _equipment_economic_rules,
        _geomet_domain_rules,
        _deduplicate,
        _prioritize,
        _merge_all_levels,
    )
except ImportError:
    from backend.engines.scenario_advisor import (
        _blockmodel_rules,
        _design_consistency_rules,
        _equipment_economic_rules,
        _geomet_domain_rules,
        _deduplicate,
        _prioritize,
        _merge_all_levels,
    )


# ---------------------------------------------------------------------------
# Niveau 4 — Blockmodel rules
# ---------------------------------------------------------------------------

class TestBlockmodelRules:
    def test_high_bwi_cv_suggests_hpgr(self):
        stats = {"bwi_cv": 0.45, "avg_grade_g_t": 1.2, "tonnage_ktpd": 20.0,
                 "ox_share_pct": 5.0}
        result = _blockmodel_rules(stats, {"SAG_MILL"})
        ids = [s["id"] for s in result]
        assert "auto_blockmodel_hpgr_variability" in ids
        hit = next(s for s in result if s["id"] == "auto_blockmodel_hpgr_variability")
        assert hit["category"] == "comminution"
        assert "blockmodel_basis" in hit
        assert hit["blockmodel_basis"]["bwi_cv"] == 0.45

    def test_low_bwi_cv_no_suggestion(self):
        stats = {"bwi_cv": 0.15, "avg_grade_g_t": 1.2, "tonnage_ktpd": 20.0,
                 "ox_share_pct": 5.0}
        result = _blockmodel_rules(stats, {"SAG_MILL"})
        ids = [s["id"] for s in result]
        assert "auto_blockmodel_hpgr_variability" not in ids

    def test_high_oxide_share_suggests_dual_circuit(self):
        stats = {"bwi_cv": 0.15, "avg_grade_g_t": 1.5, "tonnage_ktpd": 20.0,
                 "ox_share_pct": 35.0}
        result = _blockmodel_rules(stats, {"CIL"})
        ids = [s["id"] for s in result]
        assert "auto_blockmodel_dual_circuit" in ids

    def test_low_grade_high_tonnage_suggests_heap(self):
        stats = {"bwi_cv": 0.2, "avg_grade_g_t": 0.4, "tonnage_ktpd": 50.0,
                 "ox_share_pct": 10.0}
        result = _blockmodel_rules(stats, {"CIL"})
        ids = [s["id"] for s in result]
        assert "auto_blockmodel_heap_leach" in ids

    def test_missing_blockmodel_returns_empty(self):
        """Graceful degradation: empty dict => 0 suggestions."""
        assert _blockmodel_rules({}, {"SAG_MILL"}) == []

    def test_none_blockmodel_returns_empty(self):
        assert _blockmodel_rules(None, {"SAG_MILL"}) == []


# ---------------------------------------------------------------------------
# Niveau 5 — Design / mass balance consistency
# ---------------------------------------------------------------------------

class TestDesignConsistencyRules:
    def test_dc_conflict_with_active_op(self):
        """DC references an op that is not in active_ops => suggest conflict."""
        dc = [{"op_code": "HPGR", "ref_number": "DC-100", "item": "Specific energy"}]
        mb = {"converged": True}
        # active_ops does NOT contain HPGR
        result = _design_consistency_rules(dc, mb, {"SAG_MILL", "BALL_MILL"})
        ids = [s["id"] for s in result]
        assert "auto_design_dc_op_conflict" in ids
        hit = next(s for s in result if s["id"] == "auto_design_dc_op_conflict")
        assert "design_basis" in hit

    def test_unconverged_mass_balance(self):
        dc = []
        mb = {"converged": False, "residual": 0.08}
        result = _design_consistency_rules(dc, mb, {"BALL_MILL", "CIL"})
        ids = [s["id"] for s in result]
        assert "auto_design_mb_unconverged" in ids

    def test_unmet_p80_target(self):
        dc = []
        mb = {"converged": True, "p80_target_um": 75, "p80_actual_um": 120}
        result = _design_consistency_rules(dc, mb, {"BALL_MILL"})
        ids = [s["id"] for s in result]
        assert "auto_design_p80_unmet" in ids

    def test_p80_within_tolerance_no_suggestion(self):
        dc = []
        mb = {"converged": True, "p80_target_um": 75, "p80_actual_um": 78}
        result = _design_consistency_rules(dc, mb, {"BALL_MILL"})
        ids = [s["id"] for s in result]
        assert "auto_design_p80_unmet" not in ids

    def test_empty_inputs_graceful(self):
        assert _design_consistency_rules([], {}, set()) == []
        assert _design_consistency_rules(None, None, {"BALL_MILL"}) == []


# ---------------------------------------------------------------------------
# Niveau 6 — Equipment / costs
# ---------------------------------------------------------------------------

class TestEquipmentEconomicRules:
    def test_underutilized_equipment_suggests_debottleneck(self):
        equipment = [
            {"op_code": "BALL_MILL", "utilization_pct": 55.0},
        ]
        costs = {"capex_usd_m": 100, "revenue_usd_m_y": 300, "reagent_opex_usd_t": 1.5}
        result = _equipment_economic_rules(equipment, costs, {"BALL_MILL"})
        ids = [s["id"] for s in result]
        assert "auto_equipment_debottleneck_BALL_MILL" in ids

    def test_well_utilized_no_debottleneck(self):
        equipment = [{"op_code": "BALL_MILL", "utilization_pct": 85.0}]
        costs = {"capex_usd_m": 100, "revenue_usd_m_y": 300, "reagent_opex_usd_t": 1.5}
        result = _equipment_economic_rules(equipment, costs, {"BALL_MILL"})
        ids = [s["id"] for s in result]
        assert "auto_equipment_debottleneck_BALL_MILL" not in ids

    def test_high_capex_ratio_suggests_reduction(self):
        equipment = []
        costs = {"capex_usd_m": 800, "revenue_usd_m_y": 500, "reagent_opex_usd_t": 1.5}
        result = _equipment_economic_rules(equipment, costs, {"BALL_MILL"})
        ids = [s["id"] for s in result]
        assert "auto_equipment_reduce_capex" in ids

    def test_high_reagent_opex_suggests_optimization(self):
        equipment = []
        costs = {"capex_usd_m": 100, "revenue_usd_m_y": 300, "reagent_opex_usd_t": 5.0}
        result = _equipment_economic_rules(equipment, costs, {"CIL"})
        ids = [s["id"] for s in result]
        assert "auto_equipment_optimize_reagents" in ids

    def test_empty_inputs_graceful(self):
        assert _equipment_economic_rules(None, None, set()) == []
        assert _equipment_economic_rules([], {}, {"BALL_MILL"}) == []


# ---------------------------------------------------------------------------
# Niveau 7 — Géomet domains
# ---------------------------------------------------------------------------

class TestGeometDomainRules:
    def test_generates_per_domain_scenario(self):
        domains = [
            {"name": "A", "bwi_kwh_t": 12.0, "grg_rec_pct": 45.0, "s_sulfide_pct": 0.3},
            {"name": "B", "bwi_kwh_t": 19.0, "grg_rec_pct": 8.0, "s_sulfide_pct": 3.0},
        ]
        campaigns = []
        result = _geomet_domain_rules(campaigns, domains, {"SAG_MILL", "CIL"})
        ids = [s["id"] for s in result]
        # One suggestion per domain
        assert any(sid.endswith("_A") for sid in ids)
        assert any(sid.endswith("_B") for sid in ids)
        for s in result:
            assert "geomet_basis" in s
            assert s["geomet_basis"].get("domain") in {"A", "B"}

    def test_no_domains_graceful_degradation(self):
        """§6 explicit requirement: no domains => 0 suggestions, no error."""
        assert _geomet_domain_rules([], [], {"SAG_MILL"}) == []
        assert _geomet_domain_rules(None, None, {"SAG_MILL"}) == []

    def test_domain_low_bwi_high_grg_suggests_gravity_ball(self):
        domains = [{"name": "soft", "bwi_kwh_t": 11.0, "grg_rec_pct": 50.0,
                    "s_sulfide_pct": 0.2}]
        result = _geomet_domain_rules([], domains, set())
        assert len(result) == 1
        s = result[0]
        assert "GRAVITY_CONCENTRATOR" in s["ops_to_add"]

    def test_domain_hard_sulfide_suggests_hpgr_flotation(self):
        domains = [{"name": "hard_sulf", "bwi_kwh_t": 20.0, "grg_rec_pct": 5.0,
                    "s_sulfide_pct": 4.0}]
        result = _geomet_domain_rules([], domains, set())
        assert len(result) == 1
        s = result[0]
        assert "HPGR" in s["ops_to_add"] or "FLOTATION_ROUGHER" in s["ops_to_add"]


# ---------------------------------------------------------------------------
# Pipeline integration — merge + dedup + prioritize
# ---------------------------------------------------------------------------

class TestAdvisorPipeline:
    def test_merge_all_levels_deduplicates(self):
        """_merge_all_levels should combine lists and deduplicate by id."""
        lists = [
            [{"id": "a", "confidence_score": 0.9, "estimated_impact": 8}],
            [{"id": "a", "confidence_score": 0.9, "estimated_impact": 8}],  # dup
            [{"id": "b", "confidence_score": 0.7, "estimated_impact": 5}],
        ]
        merged = _merge_all_levels(lists, tested_ids=set())
        ids = [s["id"] for s in merged]
        assert ids.count("a") == 1
        assert "b" in ids

    def test_merge_preserves_richest_basis(self):
        """When duplicate ids have different basis richness, keep the richest."""
        lists = [
            [{"id": "x", "confidence_score": 0.8, "estimated_impact": 7,
              "lims_basis": {"bwi": 18}}],
            [{"id": "x", "confidence_score": 0.8, "estimated_impact": 7,
              "lims_basis": {"bwi": 18}, "blockmodel_basis": {"bwi_cv": 0.35}}],
        ]
        merged = _merge_all_levels(lists, tested_ids=set())
        by_id = {s["id"]: s for s in merged}
        assert "blockmodel_basis" in by_id["x"]

    def test_pipeline_sorts_by_confidence_impact(self):
        items = [
            {"id": "low", "confidence_score": 0.5, "estimated_impact": 3},
            {"id": "high", "confidence_score": 0.9, "estimated_impact": 9},
            {"id": "mid", "confidence_score": 0.7, "estimated_impact": 6},
        ]
        # inject already_tested=False for prioritize
        merged = _merge_all_levels([items], tested_ids=set())
        sorted_ = _prioritize(merged)
        ids = [s["id"] for s in sorted_]
        assert ids[0] == "high"
        assert ids[-1] == "low"
