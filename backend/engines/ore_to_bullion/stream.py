"""Stream dataclass — process stream between circuits."""
from __future__ import annotations
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Stream:
    """Immutable process stream. Units: t/h, g/t, %, µm, m³/h."""
    solids_tph: float
    au_g_t: float
    pct_solids: float
    p80_um: float
    water_m3h: float = 0.0

    @property
    def au_mass_g_h(self) -> float:
        return self.solids_tph * self.au_g_t

    @property
    def pulp_tph(self) -> float:
        if self.pct_solids <= 0:
            return self.solids_tph
        return self.solids_tph / (self.pct_solids / 100.0)

    @property
    def water_tph(self) -> float:
        return self.pulp_tph - self.solids_tph

    @property
    def volumetric_flow_m3h(self) -> float:
        sg_s = 2.75
        w = self.pct_solids / 100.0 if self.pct_solids > 0 else 0.01
        sg_sl = 1.0 / (w / sg_s + (1 - w) / 1.0)
        return self.pulp_tph / sg_sl if sg_sl > 0 else self.pulp_tph

    def passthrough(self) -> "Stream":
        return replace(self)

    def with_updates(self, **kwargs) -> "Stream":
        return replace(self, **kwargs)

    def to_dict(self) -> dict:
        return {"solids_tph": round(self.solids_tph, 2), "au_g_t": round(self.au_g_t, 4),
                "pct_solids": round(self.pct_solids, 1), "p80_um": round(self.p80_um, 1),
                "water_m3h": round(self.water_m3h, 1), "au_mass_g_h": round(self.au_mass_g_h, 2),
                "pulp_tph": round(self.pulp_tph, 2)}

    @classmethod
    def from_feed(cls, tph: float, au: float, sg: float = 2.75, pct_sol: float = 96.0, p80: float = 600_000.0) -> "Stream":
        w_frac = 1.0 - (pct_sol / 100.0)
        water_tph = tph * w_frac / (pct_sol / 100.0) if pct_sol > 0 else 0
        return cls(solids_tph=tph, au_g_t=au, pct_solids=pct_sol, p80_um=p80, water_m3h=water_tph)
