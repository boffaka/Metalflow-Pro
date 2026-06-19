from pathlib import Path

import pytest


pytestmark = pytest.mark.no_db


BACKEND = Path(__file__).resolve().parents[1]


def test_circuit_criteria_route_orders_groups_by_metallurgical_process():
    source = (BACKEND / "routes" / "circuit.py").read_text(encoding="utf-8")

    assert "PROCESS_OP_ORDER" in source
    assert '"GIRATOIRE": 110' in source
    assert '"CONE": 120' in source
    assert '"CRIBLE": 130' in source
    assert '"STOCKPILE": 140' in source
    assert '"HPGR": 150' in source
    assert '"BALL_MILL": 210' in source
    assert "base_sort = PROCESS_OP_ORDER.get(op_code" in source
    assert "sorted(grouped.values(), key=_criteria_group_sort_key)" in source


def test_design_criteria_regeneration_reapplies_lims_psd_enrichment():
    source = (BACKEND / "routes" / "circuit.py").read_text(encoding="utf-8")

    assert "enrich_criteria_with_lims(pid, tid, cur)" in source
    assert '"lims_enriched": enriched' in source


def test_dc_generator_includes_granulometry_and_psd_sources():
    source = (BACKEND / "engines" / "dc_generator.py").read_text(encoding="utf-8")

    assert '"a2.p80_um":' in source
    assert '"a3.p80_broyage_um":' in source
    assert '"m1.k80_um":' in source
    assert '"psd.grind_p80_um"' in source
    assert '"psd.regrind_p80_um"' in source
    assert '("BALL_MILL", "P80 cible", "psd.grind_p80_um")' in source
    assert '("VERTIMILL_REGRIND", "P80 produit", "psd.regrind_p80_um")' in source
