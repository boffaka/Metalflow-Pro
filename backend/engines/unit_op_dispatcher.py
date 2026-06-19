# backend/engines/unit_op_dispatcher.py
"""
UnitOpDispatcher — routes chaque op_code vers le moteur existant.
Contrat : dispatch(op_code, inlet_streams, params, ctx) → dict[str, StreamState]
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .stream_state import StreamState

if TYPE_CHECKING:
    pass

logger = logging.getLogger("mpdpms.unit_op_dispatcher")


@dataclass
class ProjectContext:
    target_tph: float
    gold_price_usd: float
    availability: float = 0.92


class UnitOpDispatcher:

    def dispatch(
        self,
        op_code: str,
        inlet_streams: dict[str, StreamState],
        params: dict,
        ctx: ProjectContext,
    ) -> dict[str, StreamState]:
        feed = inlet_streams.get("in") or next(iter(inlet_streams.values()), None)
        if feed is None:
            raise ValueError(f"Aucun stream 'in' pour op_code={op_code}")

        handler = self._HANDLERS.get(op_code)
        if handler is None:
            raise ValueError(f"op_code inconnu: {op_code}")
        return handler(self, feed, params, ctx)

    # ── Utilities ────────────────────────────────────────────────────────────

    def _handle_feed(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        tph = float(params.get("feed_tph") or ctx.target_tph)
        au = float(params.get("au_g_t") or feed.au_g_t)
        s = StreamState(solids_tph=tph, water_tph=tph * 1.5,
                        au_g_t=au, au_recovery_pct=100,
                        p80_um=float(params.get("p80_um") or 150_000),
                        energy_kwh_t=0)
        return {"out": s}

    def _handle_pump(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        return {"out": feed.copy()}

    def _handle_tsf(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        return {}

    # ── Comminution ──────────────────────────────────────────────────────────

    def _handle_sag_mill(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        try:
            from .comminution import bond_ball_mill_energy
        except ImportError:
            from comminution import bond_ball_mill_energy
        wi = float(params.get("wi") or 14)
        p80 = float(params.get("p80_um") or 2000)
        f80 = feed.p80_um or 150_000
        energy = bond_ball_mill_energy(wi, p80, f80)
        return {"out": feed.copy(p80_um=p80, energy_kwh_t=energy)}

    def _handle_ball_mill(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        try:
            from .comminution import bond_ball_mill_energy
        except ImportError:
            from comminution import bond_ball_mill_energy
        wi = float(params.get("wi") or 12)
        p80 = float(params.get("p80_um") or 75)
        f80 = feed.p80_um or 2000
        energy = bond_ball_mill_energy(wi, p80, f80)
        return {"out": feed.copy(p80_um=p80, energy_kwh_t=energy)}

    def _handle_cyclone(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        eff = float(params.get("efficiency") or 0.75)
        p80_ov = float(params.get("p80_overflow_um") or 75)
        ov_solids = feed.solids_tph * (1 - eff)
        uf_solids = feed.solids_tph * eff
        overflow = feed.copy(solids_tph=ov_solids, water_tph=feed.water_tph * 0.8, p80_um=p80_ov)
        underflow = feed.copy(solids_tph=uf_solids, water_tph=feed.water_tph * 0.2)
        return {"overflow": overflow, "underflow": underflow}

    def _handle_generic_mill(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        p80 = float(params.get("p80_um") or feed.p80_um * 0.5)
        energy = float(params.get("energy_kwh_t") or 8)
        return {"out": feed.copy(p80_um=p80, energy_kwh_t=energy)}

    # ── Flotation ────────────────────────────────────────────────────────────

    def _handle_flotation_rougher(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        try:
            from .flotation import flotation_recovery, mass_pull
        except ImportError:
            from flotation import flotation_recovery, mass_pull
        r_max = float(params.get("r_max") or 0.85)
        k = float(params.get("k") or 0.4)
        tau = float(params.get("tau_min") or 8)
        rec = flotation_recovery(r_max, k, tau)
        mp = mass_pull(feed.solids_tph, float(params.get("mass_pull_pct") or 5) / 100)
        conc_solids = mp
        tail_solids = feed.solids_tph - conc_solids
        conc = feed.copy(solids_tph=conc_solids, water_tph=conc_solids * 3,
                         au_recovery_pct=rec * 100)
        tails = feed.copy(solids_tph=tail_solids, water_tph=feed.water_tph - conc_solids * 3,
                          au_recovery_pct=(1 - rec) * 100)
        return {"conc": conc, "tails": tails}

    def _handle_flotation_generic(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        return self._handle_flotation_rougher(feed, params, ctx)

    # ── Leaching / CIL ───────────────────────────────────────────────────────

    def _handle_cil(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        try:
            from .leaching import cil_recovery
        except ImportError:
            from leaching import cil_recovery
        r_inf = float(params.get("r_inf") or 0.92)
        k = float(params.get("k_h") or 0.15)
        srt = float(params.get("srt_h") or 24)
        rec = cil_recovery(r_inf, k, srt)
        return {"out": feed.copy(au_recovery_pct=rec * 100,
                                 cn_kg_t=float(params.get("cn_kg_t") or 0.5))}

    def _handle_leach_generic(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        rec = float(params.get("recovery_pct") or 92) / 100
        return {"out": feed.copy(au_recovery_pct=rec * 100)}

    # ── Gravity ──────────────────────────────────────────────────────────────

    def _handle_gravity(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        rec_pct = float(params.get("recovery_pct") or 35)
        mp = float(params.get("mass_pull_pct") or 2.0) / 100
        conc_solids = feed.solids_tph * mp
        tail_solids = feed.solids_tph - conc_solids
        conc = feed.copy(solids_tph=conc_solids, water_tph=conc_solids * 2,
                         au_recovery_pct=rec_pct)
        tails = feed.copy(solids_tph=tail_solids, water_tph=feed.water_tph - conc_solids * 2,
                          au_recovery_pct=100 - rec_pct)
        return {"conc": conc, "tails": tails}

    def _handle_ilr(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        rec = float(params.get("recovery_pct") or 98) / 100
        tails_solids = feed.solids_tph * (1 - rec * 0.02)
        bullion = feed.copy(solids_tph=feed.solids_tph * 0.001,
                            water_tph=0, au_recovery_pct=rec * 100)
        tails = feed.copy(solids_tph=tails_solids,
                          water_tph=feed.water_tph, au_recovery_pct=(1 - rec) * 100)
        return {"bullion": bullion, "tails": tails}

    # ── Thickener / Filtre ───────────────────────────────────────────────────

    def _handle_thickener(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        ov_water = feed.water_tph * 0.8
        uf_water = feed.water_tph * 0.2
        overflow = feed.copy(solids_tph=0, water_tph=ov_water)
        underflow = feed.copy(water_tph=uf_water)
        return {"overflow": overflow, "underflow": underflow}

    def _handle_filter(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        cake = feed.copy(water_tph=feed.solids_tph * 0.12)
        filtrate = feed.copy(solids_tph=0, water_tph=feed.water_tph * 0.88)
        return {"cake": cake, "filtrate": filtrate}

    # ── ADR ──────────────────────────────────────────────────────────────────

    def _handle_elution(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        rec = float(params.get("recovery_pct") or 99) / 100
        eluate = feed.copy(solids_tph=0, water_tph=100, au_recovery_pct=rec * 100)
        barren = feed.copy(au_recovery_pct=(1 - rec) * 100)
        return {"eluate": eluate, "barren": barren}

    def _handle_electrolyse(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        sludge = feed.copy(solids_tph=feed.solids_tph * 0.001, water_tph=0)
        spent = feed.copy(solids_tph=0, water_tph=feed.water_tph)
        return {"sludge": sludge, "spent": spent}

    def _handle_fusion(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        bullion = feed.copy(solids_tph=feed.solids_tph * 0.998, water_tph=0)
        slag = feed.copy(solids_tph=feed.solids_tph * 0.002, water_tph=0)
        return {"bullion": bullion, "slag": slag}

    # ── Réfractarité ─────────────────────────────────────────────────────────

    def _handle_biox(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        rec = float(params.get("oxidation_pct") or 95) / 100
        return {"out": feed.copy(au_recovery_pct=rec * 100)}

    def _handle_pox(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        return self._handle_biox(feed, params, ctx)

    def _handle_roasting(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        return self._handle_biox(feed, params, ctx)

    def _handle_detox(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        return {"out": feed.copy(cn_kg_t=0)}

    def _handle_neutralisation(self, feed: StreamState, params: dict, ctx: ProjectContext) -> dict:
        return {"out": feed.copy()}

    # ── Dispatch table ────────────────────────────────────────────────────────

    _HANDLERS = {
        "FEED": _handle_feed,
        "POMPE": _handle_pump,
        "TSF": _handle_tsf,
        # Comminution
        "CRUSH_GYRATORY": _handle_generic_mill,
        "CRUSH_CONE": _handle_generic_mill,
        "JAW_CRUSHER": _handle_generic_mill,
        "SAG_MILL": _handle_sag_mill,
        "BALL_MILL": _handle_ball_mill,
        "HPGR": _handle_generic_mill,
        "VERTIMILL": _handle_generic_mill,
        "REGRIND": _handle_generic_mill,
        "CYCLONE": _handle_cyclone,
        "TROMMEL": _handle_cyclone,
        # Flotation
        "FLOTATION_ROUGHER": _handle_flotation_rougher,
        "FLOTATION_SCAVENGER": _handle_flotation_generic,
        "FLOTATION_CLEANER_1": _handle_flotation_generic,
        "FLOTATION_CLEANER_2": _handle_flotation_generic,
        "FLOTATION_CLEANER_3": _handle_flotation_generic,
        "FLOTATION_COLONNE": _handle_flotation_generic,
        # Leaching
        "CIL_TANK": _handle_cil,
        "CIP": _handle_cil,
        "LEACH_CUVES": _handle_leach_generic,
        # Gravity
        "GRAVITE_KNELSON": _handle_gravity,
        "GRAVITE_FALCON": _handle_gravity,
        "GRAVITE_GEMENI": _handle_gravity,
        "SPIRALES": _handle_gravity,
        "JIG": _handle_gravity,
        "ILR": _handle_ilr,
        # Thickener / Filtre
        "THICKENER_PRE_LEACH": _handle_thickener,
        "THICKENER_POST_LEACH": _handle_thickener,
        "FILTRE": _handle_filter,
        # ADR
        "ELUTION_AARL": _handle_elution,
        "ELUTION_ZADRA": _handle_elution,
        "ELECTROLYSE": _handle_electrolyse,
        "FUSION_DORE": _handle_fusion,
        # Réfractarité
        "BIOX": _handle_biox,
        "POX": _handle_pox,
        "ROASTING": _handle_roasting,
        "UFG": _handle_generic_mill,
        # Utilities
        "DETOX_INCO": _handle_detox,
        "DETOX_CARO": _handle_detox,
        "NEUTRALISATION": _handle_neutralisation,
    }
