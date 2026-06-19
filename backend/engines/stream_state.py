# backend/engines/stream_state.py
from __future__ import annotations
from dataclasses import dataclass, field, replace


@dataclass
class StreamState:
    solids_tph: float
    water_tph: float
    au_g_t: float
    au_recovery_pct: float
    p80_um: float
    energy_kwh_t: float
    cn_kg_t: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def slurry_tph(self) -> float:
        return self.solids_tph + self.water_tph

    def copy(self, **overrides) -> "StreamState":
        return replace(self, **overrides)
