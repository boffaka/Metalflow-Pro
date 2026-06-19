from pathlib import Path

import pytest


pytestmark = pytest.mark.no_db


def test_monolithic_frontend_exposes_block_model_and_simulation_optimisation():
    html_path = Path(__file__).resolve().parents[1] / "MetalFlowPro_v3_1.html"
    html = html_path.read_text(encoding="utf-8")

    assert "label: 'Block Model'" in html
    assert "id: 'blockmodel'" in html
    assert "label: 'Simulation Optimisation'" in html
    assert "id: 'simulation'" in html


def test_monolithic_frontend_exposes_granulometry_psd_module_in_navigation():
    html_path = Path(__file__).resolve().parents[1] / "MetalFlowPro_v3_1.html"
    html = html_path.read_text(encoding="utf-8")

    assert "id: 'granulometry'" in html
    assert "label: 'Granulométrie / Particle Size Distribution'" in html
    assert "granulometry: globalThis.renderGranulometry" in html
    assert "granulometry: loadGranulometryData" in html


def test_monolithic_frontend_exposes_unified_circuit_strategy_in_navigation():
    html_path = Path(__file__).resolve().parents[1] / "MetalFlowPro_v3_1.html"
    html = html_path.read_text(encoding="utf-8")

    assert "id: 'circuit_strategy'" in html
    assert "label: 'Stratégie Circuit IA'" in html
    assert "circuit_strategy: globalThis.renderCircuitStrategy" in html
    assert "circuit_strategy: runCircuitStrategyAnalysis" in html
    # Placed after design process modules and before simulation/geomet modules
    dc_idx = html.index("id: 'dc'")
    strategy_idx = html.index("id: 'circuit_strategy'")
    sim_idx = html.index("id: 'simulation'")
    assert dc_idx < strategy_idx < sim_idx
