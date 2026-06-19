from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db

try:
    from backend.engines import mass_balance_engine as mb
except ImportError:  # pragma: no cover
    from engines import mass_balance_engine as mb  # type: ignore[no-redef]


def test_section_registry_covers_selected_crushing_and_classification_ops():
    registry = {name: set(required) for name, _, required in mb.SECTION_REGISTRY}
    assert {"GIRATOIRE", "CONE", "CRIBLE", "STOCKPILE"} <= registry["CRUSHING"]
    assert "HYDROCYCLONE" in registry["COMMINUTION_BALL"]


def test_ball_mill_sets_canonical_grinding_product_for_downstream_sections():
    pp = {"target_tph": 1596.0, "ore_sg": 2.75, "gold_grade": 1.5}
    dc = {
        "plant_h_per_d": 22.1,
        "bm_feed_pct_solids": 72.0,
        "bm_recirc_cl_pct": 250.0,
        "cyc_of_pct_solids": 35.0,
        "cyc_uf_pct_solids": 70.0,
        "bm_gland_m3h": 1.2,
    }
    carry = {"hpgr_product_tph": 1596.0, "au_gt": 1.5}

    streams = mb._gen_comminution_ball(pp, dc, carry)

    assert carry["bm_product_tph"] == pytest.approx(1596.0)
    assert carry["grind_product_tph"] == pytest.approx(1596.0)
    assert carry["grind_pct_sol"] == pytest.approx(35.0)
    assert any(s["stream_name"] == "Cyclone O/F (Grinding Product)" for s in streams)


def _balance_row(streams):
    return next(s for s in streams if s["is_balance_check"])


def test_ball_mill_uses_nominal_throughput_from_design_criteria_not_design_value():
    pp = {"target_tph": 1596.0, "ore_sg": 2.75, "gold_grade": 1.5}
    dc = {
        "plant_h_per_d": 22.1,
        "mill_nominal_tph": 1596.0,
        "mill_design_tph": 1835.4,
        "bm_feed_pct_solids": 70.0,
        "bm_recirc_cl_pct": 300.0,
        "cyc_of_pct_solids": 50.0,
        "cyc_uf_pct_solids": 75.0,
        "bm_gland_m3h": 1.2,
    }
    carry = {"hpgr_product_tph": 1835.4, "au_gt": 1.5}

    streams = mb._gen_comminution_ball(pp, dc, carry)
    bal = _balance_row(streams)

    assert carry["bm_product_tph"] == pytest.approx(1596.0)
    assert carry["grind_pct_sol"] == pytest.approx(50.0)
    assert bal["solids_tph"] == pytest.approx(0.0, abs=0.01)
    assert bal["water_tph"] == pytest.approx(0.0, abs=0.01)


def test_crushing_uses_nominal_throughput_from_design_criteria():
    pp = {"target_tph": 1596.0, "ore_sg": 2.75, "gold_grade": 1.5}
    dc = {
        "crush_hours_per_day": 18.0,
        "crush_nominal_tph": 1708.8,
        "crush_design_tph": 1965.0,
        "crush_pct_solids": 97.0,
        "cone_recirc_pct": 120.0,
    }
    carry = {"enabled_ops": {"GIRATOIRE", "CRIBLE", "CONE"}}

    streams = mb._gen_crushing(pp, dc, carry)

    rom = next(s for s in streams if "ROM Feed" in s["stream_name"])
    assert rom["solids_tph"] == pytest.approx(1708.8)
    assert carry["crush_product_tph"] == pytest.approx(1708.8)


def test_hpgr_uses_nominal_throughput_not_ball_mill_design():
    pp = {"target_tph": 1596.0, "ore_sg": 2.75, "gold_grade": 1.5}
    dc = {
        "hpgr_h_per_d": 19.0,
        "hpgr_nominal_tph": 1596.0,
        "mill_design_tph": 1835.4,
        "hpgr_feed_pct_solids": 97.0,
        "hpgr_recirc_pct": 100.0,
    }
    carry = {"crush_product_tph": 1708.8, "au_gt": 1.5}

    streams = mb._gen_comminution_hpgr(pp, dc, carry)
    fresh = next(s for s in streams if s["stream_name"] == "HPGR Fresh Feed")

    assert fresh["solids_tph"] == pytest.approx(1596.0)
    assert carry["hpgr_product_tph"] == pytest.approx(1596.0)


def test_flotation_uses_grinding_product_not_project_default_when_available():
    pp = {"target_tph": 1596.0, "ore_sg": 2.75, "gold_grade": 1.5}
    dc = {
        "plant_h_per_d": 22.1,
        "flot_mass_pull_pct": 8.0,
        "flot_au_recovery_pct": 92.0,
        "scav_mass_pull_pct": 3.0,
        "scav_au_recovery_pct": 30.0,
    }
    carry = {"grind_product_tph": 420.0, "grind_pct_sol": 35.0, "au_gt": 2.4}

    mb._gen_flotation(pp, dc, carry)

    assert carry["flot_feed_tph"] == pytest.approx(420.0)
    assert carry["flot_conc_tph"] == pytest.approx(46.2)
    assert carry["flot_tails_tph"] == pytest.approx(373.8)
    bal = _balance_row(mb._gen_flotation(pp, dc, {"grind_product_tph": 420.0, "grind_pct_sol": 35.0, "au_gt": 2.4}))
    assert bal["solids_tph"] == pytest.approx(0.0, abs=0.01)
    assert bal["water_tph"] == pytest.approx(0.0, abs=0.01)


def test_cil_feed_follows_available_process_stream_priority():
    pp = {"target_tph": 1596.0, "ore_sg": 2.75, "gold_grade": 1.5}
    dc = {"plant_h_per_d": 22.1, "nacn_consumption_kg_t": 2.5, "cao_consumption_kg_t": 1.5}

    carry = {"regrind_product_tph": 95.0, "regrind_product_pct_sol": 25.0, "regrind_product_au": 18.0}
    mb._gen_cil(pp, dc, carry)
    assert carry["cip_discharge_tph"] == pytest.approx(95.0)

    carry = {"bm_product_tph": 1596.0, "bm_product_pct_sol": 35.0, "au_gt": 1.5}
    mb._gen_cil(pp, dc, carry)
    assert carry["cip_discharge_tph"] == pytest.approx(1596.0)


def test_flotation_regrind_cil_chain_propagates_leach_feed():
    pp = {"target_tph": 1596.0, "ore_sg": 2.75, "gold_grade": 1.5}
    dc = {
        "plant_h_per_d": 22.1,
        "flot_mass_pull_pct": 8.0,
        "flot_au_recovery_pct": 92.0,
        "scav_mass_pull_pct": 3.0,
        "scav_au_recovery_pct": 30.0,
        "regrind_recirc_pct": 200.0,
        "regrind_cyc_of_pct_sol": 45.0,
        "regrind_cyc_uf_pct_sol": 60.0,
        "regrind_water_tph": 0.0,
        "regrind_gland_m3h": 0.66,
        "nacn_consumption_kg_t": 0.5,
        "cao_consumption_kg_t": 1.5,
        "cil_recovery_pct": 94.0,
        "cil_gland_m3h": 1.2,
    }
    carry = {
        "grind_product_tph": 420.0,
        "grind_pct_sol": 35.0,
        "grind_product_au": 2.4,
        "au_gt": 2.4,
    }

    mb._gen_flotation(pp, dc, carry)
    assert carry["leach_feed_source"] == "flotation"
    assert carry["leach_feed_tph"] == pytest.approx(carry["flot_conc_tph"])
    flot_au = carry["flot_conc_au"]

    mb._gen_concentrate_regrind(pp, dc, carry)
    assert carry["leach_feed_source"] == "concentrate_regrind"
    assert carry["regrind_product_tph"] == pytest.approx(carry["flot_conc_tph"])
    assert carry["regrind_product_au"] == pytest.approx(flot_au)

    mb._gen_cil(pp, dc, carry)
    assert carry["cip_discharge_tph"] == pytest.approx(carry["regrind_product_tph"])


def test_thickener_overrides_regrind_for_leach_feed():
    pp = {"target_tph": 1596.0, "ore_sg": 2.75, "gold_grade": 1.5}
    dc = {
        "plant_h_per_d": 22.1,
        "conc_thick_uf_pct_sol": 58.0,
        "floc_dosage_tph": 0.01,
        "conc_thick_gland_m3h": 0.66,
    }
    carry = {
        "regrind_product_tph": 95.0,
        "regrind_product_pct_sol": 45.0,
        "regrind_product_au": 18.0,
        "flot_conc_tph": 95.0,
    }

    mb._gen_concentrate_thickener(pp, dc, carry)
    assert carry["leach_feed_source"] == "concentrate_thickener"
    feed = mb._resolve_concentrate_feed(carry, pp)
    assert feed["tph"] == pytest.approx(95.0)
    assert feed["au"] == pytest.approx(18.0)


def test_regrind_and_downstream_water_balances_close():
    pp = {"target_tph": 1596.0, "ore_sg": 2.75, "gold_grade": 1.5}
    dc = {
        "plant_h_per_d": 22.1,
        "mill_nominal_tph": 1835.4,
        "regrind_recirc_pct": 200.0,
        "regrind_cyc_of_pct_sol": 45.0,
        "regrind_cyc_uf_pct_sol": 60.0,
        "regrind_water_tph": 0.0,
        "regrind_gland_m3h": 0.66,
        "nacn_consumption_kg_t": 0.5,
        "cao_consumption_kg_t": 3.0,
        "cil_recovery_pct": 94.0,
        "cil_gland_m3h": 1.2,
        "cuso4_dosage_kg_t": 0.5,
        "detox_cao_kg_t": 1.0,
        "detox_gland_m3h": 0.66,
        "tails_thick_uf_pct_sol": 60.0,
        "tails_floc_tph": 0.02,
        "tails_thick_gland_m3h": 0.66,
    }
    carry = {"bm_product_tph": 1835.4, "bm_product_pct_sol": 50.0, "bm_product_au": 1.5, "au_gt": 1.5}

    for gen in (mb._gen_concentrate_regrind, mb._gen_cil, mb._gen_detox, mb._gen_tailings_thickener):
        streams = gen(pp, dc, carry)
        bal = _balance_row(streams)
        assert bal["solids_tph"] == pytest.approx(0.0, abs=0.01)
        assert bal["water_tph"] == pytest.approx(0.0, abs=0.01)
