"""Tests for DAG registry loading and validation."""
import pathlib
import yaml

DAG_PATH = pathlib.Path(__file__).parent.parent / "engines" / "dc_dag_registry.yaml"


def test_dag_file_exists():
    assert DAG_PATH.exists()


def test_dag_parses():
    data = yaml.safe_load(DAG_PATH.read_text())
    assert isinstance(data, dict)
    assert "nodes" in data


def test_each_node_has_required_fields():
    data = yaml.safe_load(DAG_PATH.read_text())
    required = {"depends_on", "formula_ref", "section"}
    for key, node in data["nodes"].items():
        missing = required - set(node.keys())
        assert missing == set(), f"Node {key} missing: {missing}"


def test_no_self_dependency():
    data = yaml.safe_load(DAG_PATH.read_text())
    for key, node in data["nodes"].items():
        assert key not in node["depends_on"], f"Node {key} depends on itself"


def test_all_dependencies_exist_or_are_inputs():
    data = yaml.safe_load(DAG_PATH.read_text())
    all_keys = set(data["nodes"].keys())
    inputs = set(data.get("inputs", []))
    valid = all_keys | inputs
    for key, node in data["nodes"].items():
        for dep in node["depends_on"]:
            assert dep in valid, f"Node {key} depends on unknown {dep}"


def test_minimum_node_count():
    data = yaml.safe_load(DAG_PATH.read_text())
    assert len(data["nodes"]) >= 20, f"Expected >=20 nodes, got {len(data['nodes'])}"
