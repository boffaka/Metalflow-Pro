"""Tests for hierarchical parameter resolution."""
import pytest

try:
    from param_resolver import load_industry_defaults, resolve_param, resolve_params_batch
except ImportError:
    from backend.param_resolver import load_industry_defaults, resolve_param, resolve_params_batch


def test_load_industry_defaults():
    defaults = load_industry_defaults()
    assert "ore_sg" in defaults
    assert defaults["ore_sg"]["value"] == 2.75


def test_resolve_param_falls_back_to_industry():
    defaults = load_industry_defaults()
    val, meta = resolve_param(
        project_id="fake-project-id",
        key="ore_sg",
        _industry_cache=defaults,
        _skip_db=True,
    )
    assert val == 2.75
    assert meta["source"] == "industry_default"


def test_resolve_param_unknown_key_raises():
    defaults = load_industry_defaults()
    with pytest.raises(KeyError, match="nonexistent_param"):
        resolve_param(
            project_id="fake",
            key="nonexistent_param",
            _industry_cache=defaults,
            _skip_db=True,
        )


def test_resolve_params_batch():
    defaults = load_industry_defaults()
    results = resolve_params_batch(
        project_id="fake",
        keys=["ore_sg", "default_recovery_pct"],
        _industry_cache=defaults,
        _skip_db=True,
    )
    assert "ore_sg" in results
    assert "default_recovery_pct" in results
    assert results["ore_sg"][0] == 2.75
    assert results["default_recovery_pct"][0] == 89.0


def test_resolve_params_batch_unknown_key_raises():
    defaults = load_industry_defaults()
    with pytest.raises(KeyError):
        resolve_params_batch(
            project_id="fake",
            keys=["ore_sg", "totally_fake_key"],
            _industry_cache=defaults,
            _skip_db=True,
        )


def test_industry_defaults_has_all_categories():
    defaults = load_industry_defaults()
    # Check at least one param from each category exists
    assert "ore_sg" in defaults  # metallurgical
    assert "gold_price_usd_oz" in defaults  # economic
    assert "lang_installation_pct" in defaults  # equipment
    assert "grid_co2_kg_kwh" in defaults  # environmental
    assert "tsf_fs_downstream_static" in defaults  # geotechnical
