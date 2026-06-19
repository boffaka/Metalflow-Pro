"""
Dynamic metallurgical levers — derived from active circuit template + project + LIMS.

Enables Décideur Métallurgique to adapt to any flowsheet topology (Au, Cu, heap leach,
flotation-only, gravity, CIL/CIP, etc.) without hardcoded L1–L7 gold-CIL assumptions.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("mpdpms.metallurgical_levers")

try:
    from ..db import qall, qone
    from ..routes.simulation_defaults import build_project_simulation_defaults, flat_simulation_defaults
except ImportError:
    from db import qall, qone
    from routes.simulation_defaults import build_project_simulation_defaults, flat_simulation_defaults

# Legacy UI/API ids (GVM v5) → stable param ids
LEGACY_LEVER_MAP: dict[str, str] = {
    "L1": "grind_p80",
    "L2": "flot_mass_pull",
    "L3": "leach_recovery",
    "L4": "hpgr_enabled",
    "L5": "isamill_enabled",
    "L6": "flot_intensity",
    "L7": "cil_mode",
}

# Catalog: levers activated when template contains matching op patterns
_LEVER_CATALOG: list[dict[str, Any]] = [
    {
        "id": "grind_p80",
        "param_keys": ("grind_p80", "regrind_p80"),
        "label_fr": "P80 broyage",
        "label_en": "Grind P80",
        "unit": "µm",
        "min": 40.0,
        "max": 300.0,
        "priority": 100,
        "group": "comminution",
        "op_patterns": (
            "BALL", "SAG", "ROD", "HPGR", "HYDROCYCLONE", "ISAMILL", "VERTIMILL", "REGRIND", "GIRATOIRE",
        ),
        "sensitivities": {"recovery_pct": -0.04, "energy_kwh_t": 0.12},
        "uncertainty": 8.0,
    },
    {
        "id": "feed_tph",
        "param_keys": ("feed_tph",),
        "label_fr": "Débit alimentation",
        "label_en": "Feed rate",
        "unit": "t/h",
        "min": 100.0,
        "max": 5000.0,
        "priority": 95,
        "group": "throughput",
        "op_patterns": tuple(),  # always when project has target tph
        "always": True,
        "sensitivities": {"metal_koz_y": 0.85, "energy_kwh_t": 0.02},
        "uncertainty": 80.0,
    },
    {
        "id": "flot_mass_pull",
        "param_keys": ("flot_mass_pull",),
        "label_fr": "Mass pull flottation",
        "label_en": "Flotation mass pull",
        "unit": "%",
        "min": 1.0,
        "max": 20.0,
        "priority": 90,
        "group": "concentration",
        "op_patterns": ("FLOT",),
        "sensitivities": {"recovery_pct": 0.35, "opex_per_t": 0.08},
        "uncertainty": 1.0,
    },
    {
        "id": "flot_intensity",
        "param_keys": ("flot_rough_time", "pax_dosage"),
        "label_fr": "Intensité flottation",
        "label_en": "Flotation intensity",
        "unit": "%",
        "min": 50.0,
        "max": 100.0,
        "priority": 85,
        "group": "concentration",
        "op_patterns": ("FLOT",),
        "sensitivities": {"recovery_pct": 0.12},
        "uncertainty": 4.0,
    },
    {
        "id": "grav_mass_pull",
        "param_keys": ("grav_mass_pull",),
        "label_fr": "Mass pull gravité",
        "label_en": "Gravity mass pull",
        "unit": "%",
        "min": 0.2,
        "max": 8.0,
        "priority": 88,
        "group": "concentration",
        "op_patterns": ("GRAV", "KNELSON", "FALCON", "GEMENI"),
        "sensitivities": {"recovery_pct": 0.25},
        "uncertainty": 0.3,
    },
    {
        "id": "leach_recovery",
        "param_keys": ("cil_rec_au", "cil_rec_ag", "flot_rec_au"),
        "label_fr": "Récup. lixiviation / CIL",
        "label_en": "Leach / CIL recovery",
        "unit": "%",
        "min": 50.0,
        "max": 99.0,
        "priority": 92,
        "group": "leach",
        "op_patterns": ("CIL", "CIP", "LEACH", "HEAP", "VAT", "PREAERATION"),
        "sensitivities": {"recovery_pct": 0.85},
        "uncertainty": 2.0,
    },
    {
        "id": "cil_residence",
        "param_keys": ("cil_time", "srt_h"),
        "label_fr": "Temps résidence CIL/CIP",
        "label_en": "CIL/CIP residence",
        "unit": "h",
        "min": 12.0,
        "max": 72.0,
        "priority": 80,
        "group": "leach",
        "op_patterns": ("CIL", "CIP", "LEACH_CUVES"),
        "sensitivities": {"recovery_pct": 0.15, "opex_per_t": 0.03},
        "uncertainty": 3.0,
    },
    {
        "id": "regrind_p80",
        "param_keys": ("regrind_p80",),
        "label_fr": "P80 rebroyage",
        "label_en": "Regrind P80",
        "unit": "µm",
        "min": 10.0,
        "max": 50.0,
        "priority": 75,
        "group": "comminution",
        "op_patterns": ("ISAMILL", "VERTIMILL", "SMD", "REGRIND"),
        "sensitivities": {"recovery_pct": 0.08, "energy_kwh_t": 0.06},
        "uncertainty": 2.0,
    },
    {
        "id": "pretreat_intensity",
        "param_keys": ("pretreat_severity",),
        "label_fr": "Prétraitement réfractaire",
        "label_en": "Refractory pretreatment",
        "unit": "%",
        "min": 0.0,
        "max": 100.0,
        "priority": 70,
        "group": "pretreatment",
        "op_patterns": ("BIOX", "POX", "ROAST", "UFG"),
        "sensitivities": {"recovery_pct": 0.2, "opex_per_t": 0.15},
        "uncertainty": 5.0,
    },
]

# LIMS VOI rows — suggested when tests missing AND circuit matches
_VOI_CATALOG: list[dict[str, Any]] = [
    {
        "code": "d1",
        "label": "D1 Lixiviation / cyanuration",
        "op_patterns": ("CIL", "CIP", "LEACH", "HEAP", "VAT"),
        "npv_band_m_usd": 14.0,
        "rationale": "Réduit l'incertitude sur la récupération en circuit hydrométallurgique.",
    },
    {
        "code": "g1",
        "label": "G1 Flottation",
        "op_patterns": ("FLOT",),
        "npv_band_m_usd": 11.0,
        "rationale": "Contraint mass pull et cinétique de flottation.",
    },
    {
        "code": "c2",
        "label": "C2 Gravité",
        "op_patterns": ("GRAV", "KNELSON", "FALCON"),
        "npv_band_m_usd": 6.5,
        "rationale": "Valide la récupération gravimétrique amont concentration.",
    },
    {
        "code": "b1",
        "label": "B1 Comminution (BWi)",
        "op_patterns": ("BALL", "SAG", "HPGR", "ROD"),
        "npv_band_m_usd": 9.0,
        "rationale": "Affine P80 et consommation énergétique de broyage.",
    },
    {
        "code": "m1",
        "label": "M1 Minéralogie",
        "op_patterns": tuple(),
        "npv_band_m_usd": 5.0,
        "rationale": "Caractérise libération et réfractarité — tous circuits.",
        "always": True,
    },
    {
        "code": "a1",
        "label": "A1 Géochimie tête",
        "op_patterns": tuple(),
        "npv_band_m_usd": 4.0,
        "rationale": "Base teneurs et blend géomét — tous circuits.",
        "always": True,
    },
]


def _template_context(pid: str) -> dict[str, Any]:
    """Prefer flowsheet-compiled route (gold_process_simulator), else active template."""
    try:
        from .gold_process_simulator import resolve_simulation_ops
    except ImportError:
        from engines.gold_process_simulator import resolve_simulation_ops

    try:
        resolved = resolve_simulation_ops(pid, compile_if_needed=True)
        codes = [str(c).upper() for c in (resolved.get("op_codes") or []) if c]
        if codes and resolved.get("template_id"):
            src = resolved.get("source") or {}
            name = src.get("template_name") or src.get("source_type") or "Flowsheet compilé"
            return {
                "template_id": resolved["template_id"],
                "template_name": name,
                "op_codes": codes,
                "op_set": set(codes),
                "source_kind": src.get("kind"),
            }
    except Exception as exc:
        logger.debug("gold_process resolve skipped for %s: %s", pid, exc)

    tpl = qone(
        "SELECT id, name FROM circuit_templates WHERE project_id=%s "
        "ORDER BY is_active DESC NULLS LAST, updated_at DESC LIMIT 1",
        (pid,),
    )
    if not tpl:
        return {"template_id": None, "template_name": None, "op_codes": [], "op_set": set()}
    rows = qall(
        "SELECT op_code FROM circuit_template_operations WHERE template_id=%s ORDER BY sort_order",
        (tpl["id"],),
    )
    codes = [str(r["op_code"]).upper() for r in (rows or []) if r.get("op_code")]
    return {
        "template_id": str(tpl["id"]),
        "template_name": tpl.get("name"),
        "op_codes": codes,
        "op_set": set(codes),
    }


def _op_matches(code: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return False
    return any(p in code for p in patterns)


def _detect_flowsheet_family(op_set: set[str]) -> str:
    has_heap = any("HEAP" in c for c in op_set)
    has_cil = any("CIL" in c for c in op_set)
    has_cip = any("CIP" in c for c in op_set)
    has_flot = any("FLOT" in c for c in op_set)
    has_grav = any("GRAV" in c or "KNELSON" in c or "FALCON" in c for c in op_set)
    has_pretreat = any(c in op_set for c in ("BIOX", "POX", "ROASTING", "UFG"))
    has_sx = any("SX" in c or "EW" in c for c in op_set)

    if has_heap:
        return "heap_leach"
    if has_sx:
        return "sx_ew"
    if has_pretreat and (has_cil or has_cip):
        return "refractory_cil"
    if has_cil or has_cip:
        return "cil_cip"
    if has_flot and not (has_cil or has_cip):
        return "flotation_concentrate"
    if has_grav and not has_flot:
        return "gravity_only"
    if any("BALL" in c or "SAG" in c for c in op_set):
        return "comminution_heavy"
    return "generic"


def _resolve_param(flat: dict, defaults: dict, keys: tuple[str, ...], fallback: float) -> float:
    for k in keys:
        if flat.get(k) is not None:
            return float(flat[k])
        d = defaults.get(k)
        if isinstance(d, dict) and d.get("value") is not None:
            return float(d["value"])
        if d is not None and not isinstance(d, dict):
            return float(d)
    return fallback


def normalize_lever_dict(raw: dict[str, Any], valid_ids: set[str]) -> dict[str, Any]:
    """Map legacy L1–L7 keys to stable lever ids."""
    out: dict[str, Any] = {}
    for key, val in (raw or {}).items():
        lid = LEGACY_LEVER_MAP.get(str(key), str(key))
        if lid in valid_ids or not valid_ids:
            out[lid] = val
    return out


def discover_project_levers(pid: str, max_levers: int = 10) -> dict[str, Any]:
    """
    Build levers_meta + values for this project's active circuit.

    Returns levers only for unit operations present (or global throughput).
    """
    proj = qone(
        "SELECT project_name, project_code, commodity, target_tph, gold_grade_g_t, status "
        "FROM projects WHERE id=%s",
        (pid,),
    ) or {}
    ctx = _template_context(pid)
    op_set = ctx["op_set"]
    defaults = build_project_simulation_defaults(pid)
    flat = flat_simulation_defaults(pid)

    def _v(keys: tuple[str, ...], fb: float) -> float:
        return _resolve_param(flat, defaults, keys, fb)

    family = _detect_flowsheet_family(op_set)
    selected: list[dict[str, Any]] = []

    for spec in sorted(_LEVER_CATALOG, key=lambda s: -s["priority"]):
        if spec.get("always"):
            active = True
        elif not op_set:
            active = spec["id"] in ("feed_tph", "grind_p80", "leach_recovery")
        else:
            active = any(_op_matches(code, spec["op_patterns"]) for code in op_set)
        if not active:
            continue

        val = _v(tuple(spec["param_keys"]), _default_for_lever(spec, flat, proj))
        if spec["id"] == "flot_intensity" and val > 100:
            val = 75.0
        if spec["unit"] == "%" and spec["id"] not in ("feed_tph",):
            val = max(spec["min"], min(spec["max"], val))

        meta = {
            "id": spec["id"],
            "key": spec["param_keys"][0],
            "label": spec["label_fr"],
            "label_en": spec["label_en"],
            "unit": spec["unit"],
            "min": spec["min"],
            "max": spec["max"],
            "group": spec["group"],
            "sensitivities": dict(spec.get("sensitivities") or {}),
            "uncertainty": spec.get("uncertainty", 1.0),
        }
        selected.append({**meta, "value": val})

    if not selected:
        selected = [
            {
                "id": "feed_tph",
                "key": "feed_tph",
                "label": "Débit alimentation",
                "label_en": "Feed rate",
                "unit": "t/h",
                "min": 100.0,
                "max": 5000.0,
                "group": "throughput",
                "sensitivities": {"metal_koz_y": 0.85},
                "uncertainty": 80.0,
                "value": float(proj.get("target_tph") or 500),
            },
            {
                "id": "leach_recovery",
                "key": "cil_rec_au",
                "label": "Récupération usine",
                "label_en": "Plant recovery",
                "unit": "%",
                "min": 50.0,
                "max": 99.0,
                "group": "leach",
                "sensitivities": {"recovery_pct": 0.85},
                "uncertainty": 2.0,
                "value": 85.0,
            },
        ]

    selected = selected[:max_levers]
    levers_meta = [{k: m[k] for k in m if k != "value"} for m in selected]
    levers = {m["id"]: m["value"] for m in selected}
    active_ids = [m["id"] for m in selected if m.get("unit") != "bool"]

    commodity = (proj.get("commodity") or "Au").strip()
    primary_metal = _primary_metal_label(commodity)

    return {
        "levers_meta": levers_meta,
        "levers": levers,
        "active_lever_ids": active_ids,
        "circuit_profile": {
            "template_id": ctx["template_id"],
            "template_name": ctx["template_name"],
            "op_codes": sorted(op_set),
            "n_operations": len(op_set),
            "flowsheet_family": family,
            "commodity": commodity,
            "primary_metal": primary_metal,
            "is_dynamic": True,
        },
        "uncertainty_by_lever": {m["id"]: m.get("uncertainty", 1.0) for m in selected},
    }


def _default_for_lever(spec: dict, flat: dict, proj: dict) -> float:
    if spec["id"] == "feed_tph":
        return float(proj.get("target_tph") or flat.get("feed_tph") or 500)
    if spec["id"] == "grind_p80":
        return float(flat.get("grind_p80") or 113.0)
    if spec["id"] == "flot_mass_pull":
        return float(flat.get("flot_mass_pull") or 7.0)
    if spec["id"] == "leach_recovery":
        return float(flat.get("cil_rec_au") or flat.get("flot_rec_au") or 88.0)
    if spec["id"] == "flot_intensity":
        return 75.0
    if spec["id"] == "grav_mass_pull":
        return float(flat.get("grav_mass_pull") or 1.2)
    if spec["id"] == "cil_residence":
        return float(flat.get("cil_time") or 24.0)
    if spec["id"] == "regrind_p80":
        return float(flat.get("regrind_p80") or 22.0)
    return float(spec["min"])


def _primary_metal_label(commodity: str) -> str:
    c = commodity.lower()
    if "cu" in c and "au" not in c:
        return "Cu"
    if "ni" in c:
        return "Ni"
    if "zn" in c or "pb" in c:
        return "base metals"
    if "fe" in c or "iron" in c:
        return "Fe"
    return "Au"


def voi_for_circuit(pid: str, lims_counts: dict[str, int], circuit_profile: dict) -> dict[str, Any]:
    """VOI ranked for missing tests relevant to this circuit."""
    op_set = set(circuit_profile.get("op_codes") or [])
    sample_n = int((qone("SELECT COUNT(*) AS n FROM lims_samples WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    candidates: list[dict[str, Any]] = []

    for item in _VOI_CATALOG:
        if lims_counts.get(item["code"], 0) > 0:
            continue
        if not item.get("always"):
            if not op_set:
                continue
            if not any(_op_matches(code, item["op_patterns"]) for code in op_set):
                continue
        band = float(item["npv_band_m_usd"])
        candidates.append({
            **item,
            "priority_score": round(band * (1.2 if item["code"] == "d1" else 1.0), 2),
            "lims_rows": 0,
            "expected_npv_band_m_usd": band,
            "domain_hint": circuit_profile.get("flowsheet_family", "all"),
        })

    candidates.sort(key=lambda x: x["priority_score"], reverse=True)
    top = candidates[0] if candidates else None
    family = circuit_profile.get("flowsheet_family", "generic")
    return {
        "sample_count": sample_n,
        "lims_counts": lims_counts,
        "candidates": candidates,
        "top": top,
        "circuit_family": family,
        "message": (
            f"Prochain essai : {top['label']} ({family}) — bande NPV ~${top['expected_npv_band_m_usd']:.1f} M"
            if top
            else "Programme LIMS aligné au circuit — pas d'essai prioritaire."
        ),
    }


def nsga_job_variables(levers_meta: list[dict], family: str) -> list[dict]:
    """Map discovered levers to NSGA-II bound overrides where applicable."""
    nsga_map = {
        "grind_p80": ("p80_um", 53, 150),
        "flot_mass_pull": ("mass_pull_pct", 3, 12),
        "leach_recovery": ("srt_h", 18, 42),
        "cil_residence": ("srt_h", 16, 48),
    }
    out: list[dict] = []
    for m in levers_meta:
        lid = m["id"]
        if lid not in nsga_map:
            continue
        param, lo, hi = nsga_map[lid]
        out.append({"param": param, "min": max(lo, m["min"]), "max": min(hi, m["max"])})
    if not out and family in ("cil_cip", "heap_leach", "generic"):
        out = [
            {"param": "p80_um", "min": 53, "max": 150},
            {"param": "mass_pull_pct", "min": 3, "max": 12},
            {"param": "srt_h", "min": 18, "max": 42},
        ]
    return out
