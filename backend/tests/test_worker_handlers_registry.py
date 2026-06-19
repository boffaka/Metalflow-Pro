"""Layer 2 — registry wiring smoke test."""
from __future__ import annotations


def test_all_four_handlers_registered_after_import():
    # Trigger registration via package import side-effects. The handlers,
    # loop, and the rest of the worker subsystem use bare-first imports
    # ("from worker.registry import ..."), so query the same module.
    import worker.handlers  # noqa: F401
    from worker.registry import JOB_HANDLERS

    assert set(JOB_HANDLERS.keys()) >= {
        "sensitivity_spider",
        "sensitivity_tornado",
        "simulate_optimize",
        "ni43101_export",
    }
