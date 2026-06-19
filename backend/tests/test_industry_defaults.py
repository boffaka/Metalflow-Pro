"""Tests for industry defaults YAML file."""
import pathlib
import yaml

YAML_PATH = pathlib.Path(__file__).parent.parent / "industry_defaults.yaml"


def test_yaml_file_exists():
    assert YAML_PATH.exists(), "industry_defaults.yaml not found"


def test_yaml_parses_without_error():
    data = yaml.safe_load(YAML_PATH.read_text())
    assert isinstance(data, dict)


def test_all_categories_present():
    data = yaml.safe_load(YAML_PATH.read_text())
    for cat in ("metallurgical", "economic", "equipment", "environmental", "geotechnical"):
        assert cat in data, f"Missing category: {cat}"


def test_each_param_has_required_fields():
    data = yaml.safe_load(YAML_PATH.read_text())
    required = {"value", "unit", "range", "reference"}
    for category, params in data.items():
        for key, spec in params.items():
            missing = required - set(spec.keys())
            assert missing == set(), f"{category}.{key} missing fields: {missing}"


def test_range_is_valid():
    data = yaml.safe_load(YAML_PATH.read_text())
    for category, params in data.items():
        for key, spec in params.items():
            r = spec["range"]
            assert len(r) == 2, f"{category}.{key} range must have 2 elements"
            if spec["value"] is not None and isinstance(spec["value"], (int, float)):
                assert r[0] <= spec["value"] <= r[1], (
                    f"{category}.{key}: default {spec['value']} outside range {r}"
                )


def test_minimum_param_count():
    data = yaml.safe_load(YAML_PATH.read_text())
    total = sum(len(params) for params in data.values())
    assert total >= 40, f"Expected at least 40 parameters, got {total}"
