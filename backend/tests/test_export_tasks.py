# backend/tests/test_export_tasks.py
"""Tests for export task functions (unit, no DB required)."""
import pytest

def test_generate_dxf_returns_bytes():
    try:
        from backend.tasks.export_tasks import generate_pid_dxf
    except ImportError:
        from tasks.export_tasks import generate_pid_dxf

    elements = [
        {"type": "equipment", "tag": "SAG-001", "x": 100, "y": 100, "symbol": "mill"},
        {"type": "instrument", "tag": "FIC-400-001", "x": 200, "y": 150},
    ]
    dxf_bytes = generate_pid_dxf(elements=elements, title="Test P&ID", sheet="1")
    assert isinstance(dxf_bytes, bytes)
    assert len(dxf_bytes) > 0
    content = dxf_bytes.decode("utf-8", errors="replace")
    assert "SECTION" in content

def test_generate_dxf_empty_elements():
    try:
        from backend.tasks.export_tasks import generate_pid_dxf
    except ImportError:
        from tasks.export_tasks import generate_pid_dxf
    dxf_bytes = generate_pid_dxf(elements=[], title="Empty Sheet", sheet="1")
    assert isinstance(dxf_bytes, bytes)
