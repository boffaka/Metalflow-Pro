"""Tests for dynamic metallurgical levers discovery."""
import pytest


@pytest.mark.no_db
def test_normalize_legacy_l1_to_grind_p80():
    from engines.metallurgical_levers import normalize_lever_dict

    out = normalize_lever_dict({"L1": 90, "L2": 8}, {"grind_p80", "flot_mass_pull"})
    assert out["grind_p80"] == 90
    assert out["flot_mass_pull"] == 8


@pytest.mark.no_db
def test_detect_heap_leach_family():
    from engines.metallurgical_levers import _detect_flowsheet_family

    assert _detect_flowsheet_family({"HEAP_LEACH", "ADR"}) == "heap_leach"


@pytest.mark.no_db
def test_detect_flotation_only_family():
    from engines.metallurgical_levers import _detect_flowsheet_family

    assert _detect_flowsheet_family({"FLOT_ROUGHER", "THICKENER"}) == "flotation_concentrate"


@pytest.mark.no_db
def test_nsga_variables_from_discovered_meta():
    from engines.metallurgical_levers import nsga_job_variables

    meta = [
        {"id": "grind_p80", "min": 50, "max": 120},
        {"id": "flot_mass_pull", "min": 3, "max": 12},
        {"id": "feed_tph", "min": 500, "max": 2000},
    ]
    vars_ = nsga_job_variables(meta, "cil_cip")
    params = {v["param"] for v in vars_}
    assert "p80_um" in params
    assert "mass_pull_pct" in params
    assert "feed_tph" not in params


@pytest.mark.no_db
def test_discover_fallback_without_template(monkeypatch):
    from engines import metallurgical_levers as lev

    def fake_qone(sql, params=None):
        if "circuit_templates" in sql:
            return None
        if "projects" in sql:
            return {
                "project_name": "Test",
                "project_code": "T",
                "commodity": "Au",
                "target_tph": 800,
                "gold_grade_g_t": 1.2,
                "status": "active",
            }
        return {"n": 0}

    monkeypatch.setattr(lev, "qone", fake_qone)
    monkeypatch.setattr(lev, "qall", lambda sql, params=None: [])
    import engines.gold_process_simulator as gps
    monkeypatch.setattr(
        gps,
        "resolve_simulation_ops",
        lambda pid, compile_if_needed=True: {"op_codes": [], "template_id": None},
    )
    monkeypatch.setattr(
        lev,
        "build_project_simulation_defaults",
        lambda pid: {},
    )
    monkeypatch.setattr(lev, "flat_simulation_defaults", lambda pid: {})

    pack = lev.discover_project_levers("fake-id")
    assert pack["levers"]
    assert pack["circuit_profile"]["flowsheet_family"] == "generic"
    assert len(pack["levers_meta"]) >= 2


@pytest.mark.no_db
def test_discover_cil_template_activates_leach(monkeypatch):
    from engines import metallurgical_levers as lev

    calls = {"n": 0}

    def fake_qone(sql, params=None):
        calls["n"] += 1
        if "circuit_templates" in sql:
            return {"id": "tpl-1", "name": "GVM CIL"}
        if "projects" in sql:
            return {
                "project_name": "GVM",
                "commodity": "Au",
                "target_tph": 1500,
                "gold_grade_g_t": 1.5,
            }
        return {"n": 0}

    monkeypatch.setattr(lev, "qone", fake_qone)
    monkeypatch.setattr(
        lev,
        "qall",
        lambda sql, params=None: [
            {"op_code": "SAG_MILL"},
            {"op_code": "BALL_MILL"},
            {"op_code": "FLOT_ROUGHER"},
            {"op_code": "CIL_TANK"},
        ],
    )
    import engines.gold_process_simulator as gps
    monkeypatch.setattr(
        gps,
        "resolve_simulation_ops",
        lambda pid, compile_if_needed=True: {"op_codes": [], "template_id": None},
    )
    monkeypatch.setattr(lev, "build_project_simulation_defaults", lambda pid: {})
    monkeypatch.setattr(
        lev,
        "flat_simulation_defaults",
        lambda pid: {"grind_p80": 113, "flot_mass_pull": 7, "cil_rec_au": 88},
    )

    pack = lev.discover_project_levers("pid")
    ids = {m["id"] for m in pack["levers_meta"]}
    assert "grind_p80" in ids
    assert "flot_mass_pull" in ids
    assert "leach_recovery" in ids
    assert pack["circuit_profile"]["flowsheet_family"] == "cil_cip"
