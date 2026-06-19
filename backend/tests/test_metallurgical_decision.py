"""Tests for Décideur Métallurgique API."""
import pytest


def test_context_returns_levers(client, admin_headers, test_project_id):
    r = client.get(
        f"/api/v1/projects/{test_project_id}/metallurgical-decision/context",
        headers=admin_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert "levers" in data
    assert "levers_meta" in data
    assert "circuit_profile" in data
    assert len(data["levers"]) == len(data["levers_meta"])
    assert len(data["levers"]) >= 2
    assert "lever_ranking" in data


def test_impact_surrogate_changes_cone(client, admin_headers, test_project_id):
    ctx = client.get(
        f"/api/v1/projects/{test_project_id}/metallurgical-decision/context",
        headers=admin_headers,
    ).json()
    base = ctx["levers"]
    meta = ctx["levers_meta"]
    bump_id = next(
        (m["id"] for m in meta if m.get("unit") != "bool" and "mass" in m["id"]),
        next((m["id"] for m in meta if m.get("unit") != "bool"), None),
    )
    assert bump_id
    levers_high = {**base, bump_id: float(base.get(bump_id, 7)) + 2}
    r = client.post(
        f"/api/v1/projects/{test_project_id}/metallurgical-decision/impact",
        headers=admin_headers,
        json={"levers": levers_high},
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("is_surrogate") is True
    assert data.get("surrogate_version", 1) >= 1
    assert "cone" in data
    assert "recovery_at_risk" in data


def test_voi_endpoint(client, admin_headers, test_project_id):
    r = client.get(
        f"/api/v1/projects/{test_project_id}/metallurgical-decision/voi",
        headers=admin_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert "candidates" in data
    assert "message" in data
    assert "circuit_family" in data


def test_baseline_get_empty(client, admin_headers, test_project_id):
    r = client.get(
        f"/api/v1/projects/{test_project_id}/metallurgical-decision/baseline",
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json().get("locked") in (True, False)


@pytest.mark.no_db
def test_compute_voi_prioritizes_missing_d1(monkeypatch):
    from engines import metallurgical_levers as lev

    counts = {"a1": 5, "b1": 3, "d1": 0, "g1": 0}
    profile = {
        "op_codes": ["CIL_TANK", "FLOT_ROUGHER"],
        "flowsheet_family": "cil_cip",
    }
    monkeypatch.setattr(lev, "qone", lambda sql, params=None: {"n": 12})
    out = lev.voi_for_circuit("fake-pid", counts, profile)
    assert out["top"]["code"] == "d1"
    assert out["top"]["expected_npv_band_m_usd"] == 14.0


@pytest.mark.no_db
def test_filter_pareto_respects_capex_cap():
    import importlib
    mod = importlib.import_module("routes.metallurgical_decision")
    constraints = {
        "max_capex_musd": 100,
        "min_recovery_pct": 85,
        "mass_pull_min_pct": 5,
        "mass_pull_max_pct": 10,
    }
    front = [
        {
            "objectives": {"capex_musd": 90, "npv_musd": 200},
            "metrics": {"recovery_pct": 88, "annual_gold_oz": 500000},
            "variables": {"mass_pull_pct": 7},
        },
        {
            "objectives": {"capex_musd": 150, "npv_musd": 250},
            "metrics": {"recovery_pct": 90, "annual_gold_oz": 520000},
            "variables": {"mass_pull_pct": 7},
        },
    ]
    out = mod._filter_pareto_front(front, constraints)
    assert len(out) == 1
    assert out[0]["objectives"]["capex_musd"] == 90


@pytest.mark.no_db
def test_vars_to_levers_maps_p80_and_mass_pull():
    import importlib
    mod = importlib.import_module("routes.metallurgical_decision")
    base = {"grind_p80": 113, "flot_mass_pull": 7, "leach_recovery": 88}
    meta = [{"id": k} for k in base]
    out = mod._vars_to_levers({"p80_um": 75, "mass_pull_pct": 8}, base, meta)
    assert out["grind_p80"] == 75
    assert out["flot_mass_pull"] == 8


@pytest.mark.no_db
def test_recovery_at_risk_from_cone():
    import importlib
    mod = importlib.import_module("routes.metallurgical_decision")
    cone = {"recovery_pct": {"p10": 87.0, "p50": 89.0, "p90": 91.0}}
    rar = mod._recovery_at_risk(cone)
    assert rar["recovery_p10_pct"] == 87.0
    assert rar["level"] == "warn"


@pytest.mark.no_db
def test_surrogate_v2_coefficients_non_empty():
    import importlib
    mod = importlib.import_module("routes.metallurgical_decision")
    overall = {
        "feed_tph": 1517,
        "feed_grade_au": 1.5,
        "total_recovery_pct": 89.0,
        "total_energy_kwh_t": 15.0,
        "opex_per_t": 11.8,
        "annual_gold_oz": 589000,
    }
    levers = {"grind_p80": 113, "flot_mass_pull": 7, "leach_recovery": 88, "flot_intensity": 75}
    meta = [
        {"id": "grind_p80", "min": 50, "max": 200, "unit": "µm", "sensitivities": {"recovery_pct": -0.04}},
        {"id": "flot_mass_pull", "min": 3, "max": 15, "unit": "%", "sensitivities": {"recovery_pct": 0.35}},
        {"id": "leach_recovery", "min": 70, "max": 98, "unit": "%", "sensitivities": {"recovery_pct": 0.85}},
        {"id": "flot_intensity", "min": 50, "max": 100, "unit": "%", "sensitivities": {"recovery_pct": 0.12}},
    ]
    orig_pack = mod._project_lever_pack
    mod._project_lever_pack = lambda pid: {
        "levers_meta": meta,
        "active_lever_ids": ["grind_p80", "flot_mass_pull", "leach_recovery", "flot_intensity"],
        "uncertainty_by_lever": {m["id"]: 1.0 for m in meta},
        "levers": levers,
        "circuit_profile": {"flowsheet_family": "cil_cip"},
    }
    try:
        c = mod._surrogate_v2_coefficients("fake", overall, levers)
        assert c and "flot_mass_pull" in c
        assert "total_recovery_pct" in c["flot_mass_pull"]
    finally:
        mod._project_lever_pack = orig_pack


@pytest.mark.no_db
def test_apply_surrogate_dynamic_sensitivities():
    import importlib
    mod = importlib.import_module("routes.metallurgical_decision")
    overall = {
        "feed_tph": 1500,
        "feed_grade_au": 1.5,
        "total_recovery_pct": 89.0,
        "total_energy_kwh_t": 15.0,
        "opex_per_t": 11.8,
        "annual_gold_oz": 500000,
    }
    base = {"flot_mass_pull": 7}
    trial = {"flot_mass_pull": 9}
    meta = [{
        "id": "flot_mass_pull",
        "min": 3,
        "max": 15,
        "unit": "%",
        "sensitivities": {"recovery_pct": 0.35, "opex_per_t": 0.08},
    }]
    out = mod._apply_surrogate(dict(overall), base, trial, meta)
    assert out["total_recovery_pct"] > 89.0


@pytest.mark.no_db
def test_compute_variance_flags_red_recovery():
    import importlib
    mod = importlib.import_module("routes.metallurgical_decision")
    baseline = {"recovery_pct": 90.0, "gold_koz_y": 590.0, "opex_per_t": 11.8, "energy_kwh_t": 15.0}
    actuals = {"feed_tph": 1500, "head_grade_g_t": 1.5, "recovery_pct": 82.0, "opex_per_t": 12.0, "energy_kwh_t": 16.0}
    v = mod._compute_variance(actuals, baseline)
    assert v["statuses"]["recovery_pct"] == "red"
    assert v["below_p10_recovery"] is True
    assert v["deltas"]["recovery_pct"] < 0


@pytest.mark.no_db
def test_drift_alert_three_months_below_p10():
    import importlib
    mod = importlib.import_module("routes.metallurgical_decision")
    months = [
        {"period_yyyy_mm": "2026-01", "variance": {"below_p10_recovery": True}},
        {"period_yyyy_mm": "2026-02", "variance": {"below_p10_recovery": True}},
        {"period_yyyy_mm": "2026-03", "variance": {"below_p10_recovery": True}},
    ]
    alert = mod._drift_alert(months)
    assert alert["active"] is True
    assert "2026-03" in alert["message"]


@pytest.mark.no_db
def test_lever_economics_rank_orders_by_score():
    import importlib
    mod = importlib.import_module("routes.metallurgical_decision")
    levers = {"grind_p80": 113, "flot_mass_pull": 7, "leach_recovery": 88, "flot_intensity": 75}
    overall = {
        "feed_tph": 1517,
        "feed_grade_au": 1.5,
        "total_recovery_pct": 89,
        "total_energy_kwh_t": 15,
        "opex_per_t": 11.8,
        "annual_gold_oz": 589000,
    }
    meta = [
        {"id": "grind_p80", "label": "P80", "min": 50, "max": 200, "unit": "µm",
         "sensitivities": {"recovery_pct": -0.04}},
        {"id": "flot_mass_pull", "label": "MP", "min": 3, "max": 15, "unit": "%",
         "sensitivities": {"recovery_pct": 0.35}},
        {"id": "leach_recovery", "label": "Lix", "min": 70, "max": 98, "unit": "%",
         "sensitivities": {"recovery_pct": 0.85}},
        {"id": "flot_intensity", "label": "Int", "min": 50, "max": 100, "unit": "%",
         "sensitivities": {"recovery_pct": 0.12}},
    ]
    orig_meta = mod._levers_meta
    mod._levers_meta = lambda pid: meta
    try:
        rank = mod._lever_economics_rank("fake", levers, overall)
        assert len(rank) >= 3
        assert rank[0]["score"] >= rank[-1]["score"]
    finally:
        mod._levers_meta = orig_meta
