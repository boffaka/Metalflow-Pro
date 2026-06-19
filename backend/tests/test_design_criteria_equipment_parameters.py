from pathlib import Path

import pytest


pytestmark = pytest.mark.no_db


def _catalog_entry(op_code: str) -> dict:
    from backend.engines.circuit_catalog import CATALOG

    for entry in CATALOG:
        if entry["op_code"] == op_code:
            return entry
    raise AssertionError(f"Missing catalog entry for {op_code}")


def _items(op_code: str) -> str:
    return "\n".join(
        f"{c.get('section', '')} {c.get('item', '')}"
        for c in _catalog_entry(op_code)["default_criteria"]
    )


def test_catalog_covers_reference_workbook_parameters_by_equipment():
    """Critical workbook parameter families must exist in the equipment catalog."""
    expectations = {
        "HPGR": [
            "Débit fresh feed",
            "Recycle ratio",
            "Diamètre rouleau",
            "Force spécifique",
            "Puissance totale HPGR",
        ],
        "BALL_MILL": [
            "Débit alimentation",
            "Bond BWi",
            "Corrections Rowland",
            "Diamètre intérieur",
            "Consommation boulets",
        ],
        "HYDROCYCLONE": [
            "Charge circulante CL",
            "Débit feed cyclone",
            "% solides feed cyclone",
            "Pression opérationnelle",
        ],
        "GRAVITE_KNELSON": [
            "% UF cyclone détourné",
            "GRG dans minerai",
            "Nombre concentrateurs",
            "Temps cycle leach intensif",
        ],
        "FLOTATION_ROUGHER": [
            "Circuit flottation activé",
            "% solides feed",
            "Temps résidence rougher",
            "PAX addition",
        ],
        "CIL": [
            "% solides leach",
            "Temps résidence total",
            "Nombre réservoirs",
            "Concentration charbon",
            "NaCN dosage",
        ],
        "ELUTION_AARL": [
            "Méthode élution",
            "Charge charbon transférée",
            "Cycles par jour",
            "Température élution",
        ],
        "EPAISSISSEUR": [
            "Activé",
            "Débit solides",
            "Surface unitaire requise",
            "Diamètre épaississeur",
        ],
    }

    for op_code, expected_fragments in expectations.items():
        text = _items(op_code).lower()
        for fragment in expected_fragments:
            assert fragment.lower() in text, f"{op_code} missing {fragment!r}"


def test_monolithic_design_criteria_has_bulk_equipment_parameter_refresh():
    html = (Path(__file__).resolve().parents[1] / "MetalFlowPro_v3_1.html").read_text(
        encoding="utf-8"
    )

    assert "dcRegenerateSelectedCriteria" in html
    assert "Mettre à jour paramètres équipements" in html
    assert "dcRecalculateCriteriaSilently" in html
    assert "/criteria/recalculate" in html


def test_backend_exposes_bulk_selected_criteria_regeneration_endpoint():
    source = (Path(__file__).resolve().parents[1] / "routes" / "circuit.py").read_text(
        encoding="utf-8"
    )

    assert "/criteria/regenerate-selected" in source
    assert "def regenerate_selected_operation_criteria" in source
    assert 'op_row["recalculated"] = recalculated' in source
    assert "ON CONFLICT (template_id, ref_number) DO UPDATE SET" in source
    assert "enabled = TRUE" in source


def test_startup_reseeds_unit_operation_catalog_from_code():
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")

    assert "seed_unit_operations_catalog" in source
    assert "seed_catalog" in source
