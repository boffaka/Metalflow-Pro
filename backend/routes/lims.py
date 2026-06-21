"""
MPDPMS — LIMS (Laboratory Information Management System) routes.
Handles samples and all test type endpoints with SQL injection protection.
"""
from __future__ import annotations

import logging
import math
import psycopg2
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Depends

logger = logging.getLogger("mpdpms.lims")

try:
    from ..auth import project_user
    from ..db import qone, qall, execute, build_update_sets
    from ..models import SampleIn
    from orm_models.database import get_db
    from orm_models.models import LimsSample, LimsA1
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user
    from db import qone, qall, execute, build_update_sets
    from models import SampleIn
    from orm_models.database import get_db
    from orm_models.models import LimsSample, LimsA1
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/v1/projects", tags=["lims"])


def _signal_lims_change(pid: str, user_id: str = None) -> None:
    """Mark LIMS complete and cascade staleness to all downstream modules."""
    try:
        from .geomet_intelligence import invalidate_domain_cache
    except ImportError:
        try:
            from geomet_intelligence import invalidate_domain_cache
        except ImportError:
            invalidate_domain_cache = None  # type: ignore[assignment,misc]
    if invalidate_domain_cache:
        try:
            invalidate_domain_cache(pid)
        except Exception:
            pass
    try:
        from .pipeline import set_status, mark_stale_cascade
    except ImportError:
        from pipeline import set_status, mark_stale_cascade
    try:
        set_status(pid, "lims", "complete", user_id=user_id, triggered_by="lims_write")
        mark_stale_cascade(pid, "lims", user_id=user_id)
    except Exception:  # intentional: ignore optional lookup failure
        pass  # never block a LIMS write due to pipeline signalling

LIMS_TABLES = {
    "a1": "lims_a1", "a2": "lims_a2", "a3": "lims_a3", "m1": "lims_mineralogy",
    "b1": "lims_b1",
    "c2": "lims_c2", "c2b": "lims_c2b", "c2c": "lims_c2c",
    "c3": "lims_c3", "d1": "lims_d1",
    "e1": "lims_e1", "f2": "lims_e2", "g1": "lims_flotation",
    "h1": "lims_elution", "i1": "lims_environmental",
    "dtx": "lims_detox",
}

# Whitelist immutable — empêche toute injection SQL via noms de tables
_SAFE_TABLES: frozenset[str] = frozenset(LIMS_TABLES.values())


def safe_table_name(tbl: str) -> str:
    """Valide que le nom de table est dans la whitelist avant interpolation SQL."""
    if tbl not in _SAFE_TABLES:
        raise ValueError(f"Table interdite: {tbl}")
    return tbl


def _table_has_column(tbl: str, col: str) -> bool:
    """True when the whitelisted LIMS table has the given column."""
    safe_tbl = safe_table_name(tbl)
    row = qone(
        "SELECT 1 AS ok FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s AND column_name=%s "
        "LIMIT 1",
        (safe_tbl, col),
    )
    return bool(row)


# ── LIMS data validation rules ──────────────────────────────────────────────
_VALIDATION_RULES: dict[str, dict[str, tuple[float, float, str]]] = {
    "a1": {
        "au_g_t":      (0, 50_000, "Gold grade g/t"),
        "s_total_pct": (0, 100,    "Total sulfur %"),
        "s_sulfide_pct": (0, 100,  "Sulfide sulfur %"),
        "c_organic_pct": (0, 100,  "Organic carbon %"),
        "fe_pct":      (0, 100,    "Iron %"),
        "cu_pct":      (0, 100,    "Copper %"),
    },
    "b1": {
        "bwi_kwh_t":   (4, 30,     "Bond Work Index kWh/t"),
        "abrasion_index_ai": (0, 1.0, "Abrasion Index"),
    },
    "c2": {
        "au_recovery_pct": (0, 100, "Gravity Au recovery %"),
        "mass_pull_pct":   (0.1, 50, "Mass pull %"),
    },
    "d1": {
        "au_recovery_pct":       (0, 100, "Leach Au recovery %"),
        "nacn_consumption_kg_t": (0, 10,  "NaCN consumption kg/t"),
        "cao_consumption_kg_t":  (0, 20,  "CaO consumption kg/t"),
    },
    "e1": {
        "underflow_density_pct": (0, 100, "Underflow density %"),
    },
    "g1": {
        "recovery_pct":  (0, 100, "Flotation recovery %"),
        "mass_pull_pct": (0.1, 50, "Mass pull %"),
    },
}

_NON_NEGATIVE_SUFFIXES = ("_g_t", "_ppm", "_mg_l", "_pct")


def validate_lims_data(test_code: str, data: dict) -> list[str]:
    """
    Validate LIMS data against physical bounds.
    Returns list of error messages (empty if valid).
    """
    errors = []
    rules = _VALIDATION_RULES.get(test_code, {})

    for field, value in data.items():
        if value is None:
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue

        if field in rules:
            lo, hi, desc = rules[field]
            if v < lo or v > hi:
                errors.append(
                    f"{field}: {v} hors limites [{lo}, {hi}] ({desc})"
                )
            continue

        if field.endswith("_pct") and (v < 0 or v > 100):
            errors.append(f"{field}: {v} hors limites [0, 100]")
        elif any(field.endswith(s) for s in ("_g_t", "_ppm", "_mg_l")) and v < 0:
            errors.append(f"{field}: {v} ne peut pas etre negatif")

    return errors


def _build_import_log_entry(
    project_id: str,
    user_id: str,
    import_type: str,
    test_type: str,
    samples_count: int,
    accepted_count: int,
    rejected_count: int = 0,
    rejected_details: list | None = None,
    filename: str | None = None,
    checksum: str | None = None,
) -> dict:
    """Build a dict for lims_import_log insertion."""
    return {
        "project_id": project_id,
        "user_id": user_id,
        "import_type": import_type,
        "filename": filename,
        "test_type": test_type,
        "samples_count": samples_count,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "rejected_details": rejected_details or [],
        "checksum_sha256": checksum,
    }


def _record_import_log(entry: dict) -> None:
    """Insert a lims_import_log row (best-effort, never blocks LIMS write)."""
    try:
        import json as _json
        execute(
            "INSERT INTO lims_import_log "
            "(project_id, user_id, import_type, filename, test_type, "
            "samples_count, accepted_count, rejected_count, rejected_details, checksum_sha256) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
            (
                entry["project_id"], entry["user_id"], entry["import_type"],
                entry["filename"], entry["test_type"], entry["samples_count"],
                entry["accepted_count"], entry["rejected_count"],
                _json.dumps(entry["rejected_details"]),
                entry["checksum_sha256"],
            ),
        )
    except Exception:  # intentional: ignore optional lookup failure
        pass  # never block LIMS writes due to logging


LIMS_FIELDS = {
    "a1":  ["sample_id","au_g_t","ag_g_t","cu_pct","fe_pct","s_total_pct","s_sulfide_pct","as_ppm","c_organic_pct","sb_ppm",
             "hg_ppm","sio2_pct","al2o3_pct","cao_pct","mgo_pct","na2o_pct","k2o_pct","tio2_pct","mno_pct","loi_pct",
             "c_total_pct","s_sulfate_pct","se_ppm","te_ppm"],
    "a2":  ["sample_id","p80_um","d50_um",
             "ret_plus500_pct","ret_plus212_pct","ret_plus150_pct","ret_plus106_pct",
             "ret_plus75_pct","ret_plus53_pct","ret_plus38_pct","ret_minus38_pct",
             "au_head_g_t","au_plus212_g_t","au_plus75_g_t","au_minus38_g_t",
             "au_dist_plus212_pct","au_dist_plus75_pct","au_dist_minus38_pct"],
    "a3":  ["sample_id","p80_broyage_um","au_libre_pct","au_assoc_sulfures_pct",
             "au_assoc_silicates_pct","au_assoc_oxydes_pct","au_occlus_pct","au_preg_rob_pct"],
    "m1":  ["sample_id","k80_um","pyrite_pct","pyrrhotite_pct","other_sulphides_pct",
             "quartz_pct","plagioclase_pct","k_feldspar_pct","kaolinite_pct",
             "other_silicates_pct","k_other_pct","muscovite_illite_pct",
             "ca_minerals_pct","fe_oxides_pct","ilmenite_pct","ti_oxides_pct",
             "other_oxides_pct","carbonates_pct","apatite_pct","other_pct","au_free_pct"],
    "b1":  ["sample_id","bwi_kwh_t","brwi_kwh_t","crushing_wi_kwh_t",
             "f80_um","p80_target_um","a_x_b","ta","dwi_kwh_m3",
             "mia_kwh_t","mib_kwh_t","mic_kwh_t","mih_kwh_t",
             "smc_scse_kwh_t","abrasion_index_ai","ucs_mpa","sg",
             "bulk_density_t_m3","sag_classification"],
    "c2":  ["sample_id","p80_alim_um","solides_alim_pct","masse_alim_kg","au_alim_g_t",
             "vitesse_knelson_rpm","pression_fluidisation_psi","duree_concentration_min",
             "masse_concentre_g","au_concentre_g_t","au_tail_g_t","grg_rec_pct","mass_pull_pct"],
    "c2b": ["sample_id","p80_alim_um","au_conc_grade_g_t","au_recovery_pct",
             "cumul_recovery_pct","au_tail_g_t","mass_pull_pct"],
    "c2c": ["sample_id","inclinaison_table_deg","freq_vibration_hz","debit_eau_lavage_l_min",
             "densite_coupure_t_m3","temps_residence_min","vitesse_mgs_rpm",
             "au_alim_g_t","au_conc_g_t","au_tail_g_t","mass_pull_pct","recup_au_pct"],
    "c3":  ["sample_id","k80_um","au_conc_g_t","recovery_pct","cumul_recovery_pct",
             "au_recalc_g_t","au_measured_g_t","au_residue_g_t"],
    "d1":  ["sample_id","au_leach_feed_g_t","p80_alim_um","solides_pulpe_pct",
             "nacn_initiale_ppm","nacn_residuel_ppm","nacn_consumption_kg_t",
             "ph_initial","ph_final","cao_consumption_kg_t",
             "o2_dissous_mg_l","o2_consumption_kg_t",
             "temperature_c","duree_h","carbon_dose_g_l","densite_solide_sg",
             "leach_rec_2h_pct","leach_rec_4h_pct","leach_rec_8h_pct",
             "leach_rec_12h_pct","leach_rec_24h_pct","leach_rec_48h_pct",
             "au_tail_g_t","preg_rob_index"],
    "e1":  ["sample_id","unit_area_m2_t_d","flocculant_dosage_g_t","underflow_density_pct_solids",
             "isr_m_h","fsr_m_h","uf_density_pct","uf_density_t_m3","overflow_turbidity_ntu",
             "flux_t_m2_d","cn_overflow_ppm","au_overflow_ppb","viscosity_mpa_s"],
    "f2":  ["sample_id","filtration_rate_kg_m2_h","cake_moisture_pct",
             "solides_alim_pct","vide_kpa","temps_cycle_min","temps_formation_min",
             "temps_sechage_min","flux_filtrat_l_m2_h","resistance_alpha",
             "epaisseur_gateau_mm","cn_gateau_ppm","pression_diff_kpa"],
    "g1":    ["sample_id","au_alim_g_t","p80_alim_um","concentrate_wt_pct","au_concentrate_g_t",
              "au_recovery_pct","au_tail_g_t","temps_total_min","collecteur_g_t",
              "moussant_g_t","depressant_g_t","recup_s_pct"],
    "h1":      ["sample_id","type_test","charbon_type","charge_charbon_g_l",
                "au_solution_ini_mg_l","au_solution_fin_mg_l","kinetique_adsorption",
                "elution_t_c","eluant_cn_g_l","eluant_naoh_g_l","debit_bv_h",
                "temps_elution_h","au_elue_mg_l","recup_au_elution_pct",
                "fines_charbon_pct","observations"],
    "i1":  ["sample_id","wad_cn_mg_l","total_cn_mg_l","arsenic_mg_l","mercury_mg_l","sulphate_mg_l","acid_drainage_risk",
             "s_total_pct","s_sulfure_pct","ap_kg_caco3_t","np_kg_caco3_t","nnp_kg_caco3_t","npr_ratio",
             "ph_paste","conductivity_us_cm","ard_classification",
             "tclp_as_mg_l","tclp_ba_mg_l","tclp_cd_mg_l","tclp_cr_mg_l","tclp_hg_mg_l",
             "tclp_pb_mg_l","tclp_se_mg_l","tclp_ag_mg_l",
             "splp_as_mg_l","splp_pb_mg_l",
             "density_solids_sg","permeability_m_s","shear_strength_deg","liquid_limit_pct","plastic_limit_pct"],
    "dtx": ["sample_id","cn_wad_mg_l","cn_total_mg_l","cn_free_mg_l","scn_mg_l","ph_final",
             "cu_mg_l","fe_mg_l","ni_mg_l","zn_mg_l","as_mg_l","hg_ug_l","pb_mg_l",
             "consomm_so2_kg_t","consomm_h2o2_kg_t","consomm_cuso4_kg_t","consomm_cao_kg_t",
             "duree_traitement_min","cn_wad_rebound_24h","cn_wad_rebound_7d"],
}


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _append_issue(issues: list[dict], severity: str, code: str, message: str, context: dict | None = None) -> None:
    issues.append({
        "severity": severity,
        "code": code,
        "message": message,
        "context": context or {},
    })


def _audit_lims_project(pid: str) -> dict:
    issues: list[dict] = []
    sample_count = int((qone("SELECT COUNT(*) AS n FROM lims_samples WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    completeness = lims_completeness(pid, user=None)

    if sample_count == 0:
        _append_issue(issues, "high", "no_samples", "Aucun échantillon LIMS n'est enregistré pour ce projet.")

    if completeness.get("a1", 0) == 0:
        _append_issue(issues, "high", "missing_head_assays", "Aucun essai A1 (teneurs tête / géochimie) détecté.")
    if completeness.get("b1", 0) == 0:
        _append_issue(issues, "medium", "missing_comminution", "Aucun essai B1 (comminution) détecté.")
    if completeness.get("d1", 0) == 0:
        _append_issue(issues, "high", "missing_leach", "Aucun essai D1 Leach (cyanuration / lixiviation) détecté.")
    if completeness.get("i1", 0) == 0:
        _append_issue(issues, "medium", "missing_environmental", "Aucun essai environnemental I1 détecté.")

    b1_rows = qall("SELECT id, sample_id, bwi_kwh_t, p80_target_um, f80_um, abrasion_index_ai FROM lims_b1 WHERE project_id=%s", (pid,)) or []
    for row in b1_rows:
        bwi = _safe_float(row.get("bwi_kwh_t"))
        p80 = _safe_float(row.get("p80_target_um"))
        f80 = _safe_float(row.get("f80_um"))
        ai = _safe_float(row.get("abrasion_index_ai"))
        if bwi is not None and bwi <= 0:
            _append_issue(issues, "high", "invalid_bwi", "BWi non valide (<= 0).", {"row_id": str(row.get("id")), "sample_id": str(row.get("sample_id"))})
        if p80 is not None and f80 is not None and p80 >= f80:
            _append_issue(issues, "high", "invalid_size_relationship", "P80 cible doit rester inférieur au F80 d'alimentation.", {"row_id": str(row.get("id")), "p80_target_um": p80, "f80_um": f80})
        if ai is not None and ai < 0:
            _append_issue(issues, "medium", "invalid_abrasion_index", "Indice d'abrasion négatif détecté.", {"row_id": str(row.get("id"))})

    d1_rows = qall("SELECT id, sample_id, au_recovery_pct, nacn_consumption_kg_t, cao_consumption_kg_t FROM lims_d1 WHERE project_id=%s", (pid,)) or []
    for row in d1_rows:
        recovery = _safe_float(row.get("au_recovery_pct"))
        nacn = _safe_float(row.get("nacn_consumption_kg_t"))
        cao = _safe_float(row.get("cao_consumption_kg_t"))
        if recovery is not None and not (0 <= recovery <= 100):
            _append_issue(issues, "high", "invalid_leach_recovery", "Récupération Au hors plage 0-100%.", {"row_id": str(row.get("id")), "au_recovery_pct": recovery})
        if nacn is not None and nacn > 10:
            _append_issue(issues, "medium", "high_nacn_consumption", "Consommation NaCN exceptionnellement élevée.", {"row_id": str(row.get("id")), "nacn_consumption_kg_t": nacn})
        if cao is not None and cao > 20:
            _append_issue(issues, "medium", "high_lime_consumption", "Consommation CaO exceptionnellement élevée.", {"row_id": str(row.get("id")), "cao_consumption_kg_t": cao})

    env_rows = qall("SELECT id, sample_id, wad_cn_mg_l, total_cn_mg_l, arsenic_mg_l, mercury_mg_l FROM lims_environmental WHERE project_id=%s", (pid,)) or []
    for row in env_rows:
        wad = _safe_float(row.get("wad_cn_mg_l"))
        total_cn = _safe_float(row.get("total_cn_mg_l"))
        arsenic = _safe_float(row.get("arsenic_mg_l"))
        mercury = _safe_float(row.get("mercury_mg_l"))
        if wad is not None and wad > 50:
            _append_issue(issues, "high", "wad_cn_exceedance", "WAD CN supérieur au seuil de 50 mg/L.", {"row_id": str(row.get("id")), "wad_cn_mg_l": wad})
        if total_cn is not None and wad is not None and total_cn < wad:
            _append_issue(issues, "high", "cn_inconsistency", "Le CN total ne peut pas être inférieur au WAD CN.", {"row_id": str(row.get("id")), "wad_cn_mg_l": wad, "total_cn_mg_l": total_cn})
        if arsenic is not None and arsenic > 0.5:
            _append_issue(issues, "medium", "arsenic_exceedance", "Arsenic supérieur au seuil de 0.5 mg/L.", {"row_id": str(row.get("id")), "arsenic_mg_l": arsenic})
        if mercury is not None and mercury > 0.01:
            _append_issue(issues, "medium", "mercury_exceedance", "Mercure supérieur au seuil de 0.01 mg/L.", {"row_id": str(row.get("id")), "mercury_mg_l": mercury})

    counts = {
        "high": sum(1 for issue in issues if issue["severity"] == "high"),
        "medium": sum(1 for issue in issues if issue["severity"] == "medium"),
        "low": sum(1 for issue in issues if issue["severity"] == "low"),
    }
    score = max(0.0, round(100.0 - (counts["high"] * 12.5) - (counts["medium"] * 5.0) - (counts["low"] * 2.0), 1))
    return {
        "project_id": pid,
        "sample_count": sample_count,
        "completeness": completeness,
        "quality_score": score,
        "issue_counts": counts,
        "issues": issues,
    }


@router.get("/{pid}/lims/samples")
def list_samples(pid: str, user=Depends(project_user), db: Session = Depends(get_db)):
    try:
        # Perform a LEFT JOIN with LimsA1 to get the geochemical data for the dashboard.
        # Order by sort_order (Excel row index, set during bulk import) then created_at
        # so imports come back in their original sheet order. Manual one-off creates
        # default to sort_order=0 and fall back to created_at-based ordering among
        # themselves.
        results = db.query(LimsSample, LimsA1).outerjoin(
            LimsA1, LimsSample.id == LimsA1.sample_id
        ).filter(
            LimsSample.project_id == pid
        ).order_by(
            LimsSample.sort_order.asc(),
            LimsSample.created_at.asc(),
        ).all()

        output = []
        for sample, a1_test in results:
            s_dict = {c.name: getattr(sample, c.name) for c in sample.__table__.columns}
            # Flatten some important a1 test fields into the sample dictionary for the frontend
            s_dict['sample_id'] = str(s_dict.get('sample_id_display'))
            s_dict['hole_id'] = s_dict.get('provenance', 'N/A')
            s_dict['geomet_domain'] = s_dict.get('domain', 'N/A')
            s_dict['au_g_t'] = float(a1_test.au_g_t) if a1_test and a1_test.au_g_t is not None else 0.0
            s_dict['s_sulfide_pct'] = float(a1_test.s_sulfide_pct) if a1_test and a1_test.s_sulfide_pct is not None else 0.0
            s_dict['c_organic_pct'] = float(a1_test.c_organic_pct) if a1_test and a1_test.c_organic_pct is not None else 0.0
            output.append(s_dict)

        return output
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.post("/{pid}/lims/samples", status_code=201)
def create_sample(pid: str, body: SampleIn, user=Depends(project_user)):
    try:
        from services.lims_samples import create_sample as _create_sample
    except ImportError:
        from ..services.lims_samples import create_sample as _create_sample
    try:
        return _create_sample(pid, body, user_id=str(user["id"]))
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/{pid}/lims/samples/bulk", status_code=201)
def create_samples_bulk(pid: str, body: List[Dict[str, Any]], user=Depends(project_user)):
    """Bulk insert — single transaction + single pipeline cascade signal.
    Validates each row individually so a single bad row doesn't fail the
    whole batch with a 422; bad rows come back in `rejected` instead.
    ~30× faster than POSTing one at a time."""
    if not body:
        raise HTTPException(status_code=400, detail="Aucun échantillon à importer")
    if len(body) > 5000:
        raise HTTPException(status_code=400, detail=f"Trop d'échantillons: {len(body)} > 5000")

    validated: list[tuple[int, SampleIn]] = []
    rejected: list[dict[str, Any]] = []
    for idx, raw in enumerate(body):
        for key in ("collection_date", "reception_date"):
            v = raw.get(key)
            if isinstance(v, (int, float)):
                from datetime import datetime, timedelta
                raw[key] = (datetime(1899, 12, 30) + timedelta(days=int(v))).strftime("%Y-%m-%d")
            elif isinstance(v, str) and "T" in v and len(v) >= 10:
                raw[key] = v[:10]
        for key in ("total_mass_kg", "sent_mass_kg", "mass_kg"):
            v = raw.get(key)
            if isinstance(v, str):
                try:
                    raw[key] = float(v.replace(",", "."))
                except ValueError:
                    raw.pop(key, None)
        for key, val in list(raw.items()):
            if isinstance(val, (int, float)) and key not in ("total_mass_kg", "sent_mass_kg", "mass_kg", "waste_rock_dilution_pct"):
                raw[key] = str(val)
        try:
            validated.append((idx, SampleIn(**raw)))
        except Exception as e:
            lines = str(e).splitlines()
            msg = " | ".join(l.strip() for l in lines[:3])[:300]
            rejected.append({"index": idx, "error": msg})

    if not validated:
        return {"ok": True, "count": 0, "rejected_count": len(rejected), "rejected": rejected, "rows": []}

    try:
        from services.lims_samples import create_samples_bulk as _bulk
    except ImportError:
        from ..services.lims_samples import create_samples_bulk as _bulk
    try:
        # _bulk takes a flat list and returns (accepted, db_rejected) where
        # indices reference position in `validated`. Map them back to the
        # original body indices.
        accepted, db_rejected = _bulk(pid, [s for _, s in validated], user_id=str(user["id"]))
        for r in db_rejected:
            orig_idx = validated[r["index"]][0]
            rejected.append({"index": orig_idx, "error": r["error"]})
        return {
            "ok": True,
            "count": len(accepted),
            "rejected_count": len(rejected),
            "rejected": rejected,
            "rows": accepted,
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.patch("/{pid}/lims/samples/{sid}")
# SQL SAFETY: field names checked against explicit allowlist ["sample_id_display", "phase", "sample_type", "lithology", "mass_kg"].
def patch_sample(pid: str, sid: str, body: Dict[str, Any], user=Depends(project_user)):
    try:
        fields, vals = build_update_sets(
            body, allowed=frozenset(["sample_id_display", "phase", "sample_type", "lithology", "mass_kg"])
        )
        if not fields: raise HTTPException(400, "Aucun champ à mettre à jour")
        vals += [sid, pid]
        row = execute(f"UPDATE lims_samples SET {', '.join(fields)} WHERE id=%s AND project_id=%s RETURNING *", vals)
        _signal_lims_change(pid, user_id=str(user["id"]))
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.delete("/{pid}/lims/samples/all")
def purge_samples(pid: str, user=Depends(project_user)):
    """Delete ALL samples for a project. Cascade FKs in lims_a1/a2/a3/m1/b1/c2/.../detox
    will wipe their test data automatically."""
    try:
        count = (qone("SELECT COUNT(*) AS n FROM lims_samples WHERE project_id=%s", (pid,)) or {}).get("n", 0)
        execute("DELETE FROM lims_samples WHERE project_id=%s", (pid,))
        _signal_lims_change(pid, user_id=str(user["id"]))
        return {"ok": True, "deleted": count}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.delete("/{pid}/lims/samples/{sid}")
def delete_sample(pid: str, sid: str, user=Depends(project_user)):
    try:
        execute("DELETE FROM lims_samples WHERE id=%s AND project_id=%s", (sid, pid))
        _signal_lims_change(pid, user_id=str(user["id"]))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/{pid}/lims/completeness")
def lims_completeness(pid: str, user=Depends(project_user)):
    try:
        union = " UNION ALL ".join(
            f"SELECT '{code}' AS code, COUNT(*) AS n FROM {safe_table_name(tbl)} WHERE project_id=%s"
            for code, tbl in LIMS_TABLES.items()
        )
        params = tuple(pid for _ in LIMS_TABLES)
        rows = qall(f"SELECT code, n FROM ({union}) AS t", params)
        return {r["code"]: int(r["n"]) for r in rows}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.get("/{pid}/lims/qaqc")
def lims_qaqc(pid: str, user=Depends(project_user)):
    try:
        return _audit_lims_project(pid)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/{pid}/lims/tests/{code}")
def list_tests(pid: str, code: str, user=Depends(project_user)):
    try:
        tbl = LIMS_TABLES.get(code)
        if not tbl: raise HTTPException(404, f"Type d'essai inconnu: {code}")
        safe_tbl = safe_table_name(tbl)
        # Backward-compatible ordering: some legacy LIMS tables don't have
        # `sort_order` yet (older deployments), which used to break MIN-01a
        # listing and silently render 0 rows on the frontend.
        has_sort_order = _table_has_column(tbl, "sort_order")
        has_created_at = _table_has_column(tbl, "created_at")
        if has_sort_order and has_created_at:
            order_by = "t.sort_order ASC NULLS LAST, t.created_at ASC"
        elif has_sort_order:
            order_by = "t.sort_order ASC NULLS LAST"
        elif has_created_at:
            order_by = "t.created_at ASC"
        else:
            order_by = "t.id ASC"
        rows = qall(f"SELECT t.*, s.sample_id_display FROM {safe_tbl} t "
                    f"LEFT JOIN lims_samples s ON s.id=t.sample_id "
                    f"WHERE t.project_id=%s ORDER BY {order_by}", (pid,))
        return rows
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/{pid}/lims/tests/{code}", status_code=201)
def create_test(pid: str, code: str, body: Dict[str, Any], user=Depends(project_user)):
    try:
        tbl = LIMS_TABLES.get(code)
        if not tbl: raise HTTPException(404, f"Type d'essai inconnu: {code}")

        # Validation stricte des données LIMS
        validation_errors = validate_lims_data(code, body)
        if validation_errors:
            raise HTTPException(422, detail={
                "message": "Donnees LIMS hors limites physiques",
                "errors": validation_errors,
            })

        safe_tbl = safe_table_name(tbl)
        allowed = LIMS_FIELDS.get(code, [])
        cols = ["project_id"] + [k for k in allowed if k in body]
        vals = [pid] + [body[k] for k in allowed if k in body]
        placeholders = ", ".join(["%s"] * len(cols))
        row = execute(
            f"INSERT INTO {safe_tbl} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *",
            vals
        )
        _record_import_log(_build_import_log_entry(
            project_id=pid,
            user_id=user.get("id", ""),
            import_type="manual",
            test_type=code,
            samples_count=1,
            accepted_count=1,
        ))
        _signal_lims_change(pid, user_id=str(user["id"]))
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.post("/{pid}/lims/tests/{code}/bulk", status_code=201)
def create_tests_bulk(pid: str, code: str, body: List[Dict[str, Any]], user=Depends(project_user)):
    """Bulk insert LIMS test results — one transaction, single cascade signal.
    Skips per-row validation (callers should pre-validate); duplicates allowed.
    Resolves `sample_id_display` to UUID if `sample_id` is not a valid UUID."""
    if not body:
        raise HTTPException(status_code=400, detail="Aucun résultat à importer")
    if len(body) > 5000:
        raise HTTPException(status_code=400, detail=f"Trop de résultats: {len(body)} > 5000")
    tbl = LIMS_TABLES.get(code)
    if not tbl:
        raise HTTPException(404, f"Type d'essai inconnu: {code}")
    safe_tbl = safe_table_name(tbl)
    allowed = LIMS_FIELDS.get(code, [])
    has_sort_order = _table_has_column(tbl, "sort_order")

    # Build a sample_id_display -> UUID lookup once for FK resolution.
    display_to_uuid: dict[str, str] = {}
    for s in qall("SELECT id, sample_id_display FROM lims_samples WHERE project_id=%s", (pid,)):
        if s.get("sample_id_display"):
            display_to_uuid[str(s["sample_id_display"])] = str(s["id"])

    # Append after existing rows so re-imports don't restart at 0 and break ordering.
    base_offset = 0
    if has_sort_order:
        try:
            existing = qone(
                f"SELECT COALESCE(MAX(sort_order), -1) AS m FROM {safe_tbl} WHERE project_id=%s",
                (pid,),
            )
            if existing and existing.get("m") is not None:
                base_offset = int(existing["m"]) + 1
        except Exception:  # intentional: ignore optional lookup failure
            pass

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    try:
        from services.transaction import register_after_commit, transaction
    except ImportError:
        from ..services.transaction import register_after_commit, transaction
    with transaction() as cur:
        for idx, item in enumerate(body):
            # Resolve sample_id: accept either a UUID or a display name.
            raw_sid = str(item.get("sample_id") or "").strip()
            if raw_sid and raw_sid in display_to_uuid:
                item["sample_id"] = display_to_uuid[raw_sid]
            # Stamp the row with its sheet order when the target table supports it.
            if has_sort_order:
                item["sort_order"] = base_offset + idx
                cols = ["project_id", "sort_order"] + [k for k in allowed if k in item]
                vals = [pid, item["sort_order"]] + [item[k] for k in allowed if k in item]
            else:
                cols = ["project_id"] + [k for k in allowed if k in item]
                vals = [pid] + [item[k] for k in allowed if k in item]
            placeholders = ", ".join(["%s"] * len(cols))
            cur.execute("SAVEPOINT row")
            try:
                cur.execute(
                    f"INSERT INTO {safe_tbl} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id",
                    vals,
                )
                accepted.append({"index": idx, "id": str(cur.fetchone()[0])})
                cur.execute("RELEASE SAVEPOINT row")
            except Exception as e:  # intentional: collect error and continue processing
                cur.execute("ROLLBACK TO SAVEPOINT row")
                rejected.append({"index": idx, "error": str(e).splitlines()[0][:200]})
        if accepted:
            register_after_commit(lambda: _signal_lims_change(pid, user_id=str(user["id"])))

    return {"ok": True, "count": len(accepted), "rejected_count": len(rejected), "rejected": rejected}


@router.patch("/{pid}/lims/tests/{code}/{tid}")
# SQL SAFETY: field names checked against LIMS_FIELDS[code] allowlist — only whitelisted columns used.
def patch_test(pid: str, code: str, tid: str, body: Dict[str, Any], user=Depends(project_user)):
    try:
        tbl = LIMS_TABLES.get(code)
        if not tbl: raise HTTPException(404, "Type inconnu")
        safe_tbl = safe_table_name(tbl)
        allowed = LIMS_FIELDS.get(code, [])
        fields, vals = build_update_sets(
            {k: body[k] for k in allowed if k in body and k != "sample_id"},
            allowed=frozenset(allowed),
        )
        if not fields: raise HTTPException(400, "Aucun champ à mettre à jour")
        vals += [tid, pid]
        row = execute(f"UPDATE {safe_tbl} SET {', '.join(fields)} WHERE id=%s AND project_id=%s RETURNING *", vals)
        _signal_lims_change(pid, user_id=str(user["id"]))
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.delete("/{pid}/lims/tests/{code}/all")
def purge_tests(pid: str, code: str, user=Depends(project_user)):
    """Delete ALL tests of a given type for a project."""
    try:
        tbl = LIMS_TABLES.get(code)
        if not tbl: raise HTTPException(404, "Type inconnu")
        safe_tbl = safe_table_name(tbl)
        count = (qone(f"SELECT COUNT(*) AS n FROM {safe_tbl} WHERE project_id=%s", (pid,)) or {}).get("n", 0)
        execute(f"DELETE FROM {safe_tbl} WHERE project_id=%s", (pid,))
        _signal_lims_change(pid, user_id=str(user["id"]))
        return {"ok": True, "deleted": count}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.delete("/{pid}/lims/tests/{code}/{tid}")
def delete_test(pid: str, code: str, tid: str, user=Depends(project_user)):
    try:
        tbl = LIMS_TABLES.get(code)
        if not tbl: raise HTTPException(404, "Type inconnu")
        execute(f"DELETE FROM {safe_table_name(tbl)} WHERE id=%s AND project_id=%s", (tid, pid))
        _signal_lims_change(pid, user_id=str(user["id"]))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/{pid}/lims/statistics/{code}")
def lims_statistics(
    pid: str,
    code: str,
    method: str = "modified_zscore",
    threshold: float = 3.5,
    alpha: float | None = None,
    user=Depends(project_user),
):
    """
    Statistical analysis of LIMS test data.

    Returns descriptive statistics and outlier detection for each numeric field.

    Query params:
      - method: "zscore", "modified_zscore" (default), "iqr", or "grubbs"
      - threshold: Outlier detection threshold (default 3.5 for modified Z-score)
      - alpha: Significance level for Grubbs (default 0.05 when method=grubbs,
               ignored otherwise)
    """
    try:
        from ..engines.lims_statistics import analyze_lims_dataset
    except ImportError:
        from engines.lims_statistics import analyze_lims_dataset

    try:
        tbl = LIMS_TABLES.get(code)
        if not tbl:
            raise HTTPException(404, f"Type d'essai inconnu: {code}")
        safe_tbl = safe_table_name(tbl)

        rows = qall(
            f"SELECT * FROM {safe_tbl} WHERE project_id=%s ORDER BY created_at",
            (pid,),
        ) or []

        if not rows:
            return {"code": code, "count": 0, "fields": {}}

        # Identify numeric fields (exclude UUIDs and timestamps)
        numeric_fields = [
            f for f in LIMS_FIELDS.get(code, [])
            if f != "sample_id"
            and any(
                rows[0].get(f) is not None
                and isinstance(rows[0].get(f), (int, float))
                for _ in [None]
            )
        ]

        # Fallback: detect numeric fields from first row
        if not numeric_fields:
            first = rows[0] if rows else {}
            numeric_fields = [
                k for k, v in first.items()
                if isinstance(v, (int, float))
                and k not in ("sort_order",)
                and not k.endswith("_id")
            ]

        analysis = analyze_lims_dataset(
            rows=[dict(r) for r in rows],
            fields=numeric_fields,
            outlier_method=method,
            zscore_threshold=threshold,
            alpha=alpha,
        )

        return {
            "code": code,
            "count": len(rows),
            "method": method,
            "threshold": threshold,
            "alpha": alpha,
            "fields": analysis,
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except Exception as e:
        logger.error("lims_statistics failed for pid=%s, code=%s: %s", pid, code, e)
        raise HTTPException(500, detail=f"Erreur analyse statistique: {e}")


@router.get("/{pid}/lims/kinetics/{sample_id}")
def lims_kinetics_fit(pid: str, sample_id: str, user=Depends(project_user)):
    """
    Fit kinetic parameters (k, R∞) from D1 leach kinetics data.

    Returns fitted curve parameters for the CIL kinetic model:
      R(t) = R∞ × (1 − exp(−k × t))
    """
    try:
        from ..engines.leaching import fit_kinetic_params
    except ImportError:
        from engines.leaching import fit_kinetic_params

    try:
        row = qone(
            "SELECT leach_rec_2h_pct, leach_rec_4h_pct, leach_rec_8h_pct, "
            "       leach_rec_12h_pct, leach_rec_24h_pct, leach_rec_48h_pct "
            "FROM lims_d1 WHERE sample_id=%s AND project_id=%s LIMIT 1",
            (sample_id, pid),
        )
        if not row:
            raise HTTPException(404, "Données cinétiques D1 introuvables")

        time_points = [2.0, 4.0, 8.0, 12.0, 24.0, 48.0]
        rec_fields = [
            "leach_rec_2h_pct", "leach_rec_4h_pct", "leach_rec_8h_pct",
            "leach_rec_12h_pct", "leach_rec_24h_pct", "leach_rec_48h_pct",
        ]

        times = []
        recoveries = []
        for t, field in zip(time_points, rec_fields):
            v = row.get(field)
            if v is not None:
                times.append(t)
                recoveries.append(float(v))

        if len(times) < 2:
            raise HTTPException(422, "Minimum 2 points cinétiques requis pour l'ajustement")

        k, r_inf = fit_kinetic_params(times, recoveries)

        # Generate fitted curve points
        curve = [
            {"time_h": t, "recovery_pct": round(r_inf * (1 - math.exp(-k * t)) * 100, 2)}
            for t in [0, 2, 4, 6, 8, 12, 16, 20, 24, 32, 40, 48]
        ]

        return {
            "sample_id": sample_id,
            "k_per_h": round(k, 4),
            "r_inf_pct": round(r_inf * 100, 2),
            "model": "R(t) = R∞ × (1 − exp(−k × t))",
            "data_points": [
                {"time_h": t, "recovery_pct": r}
                for t, r in zip(times, recoveries)
            ],
            "fitted_curve": curve,
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except Exception as e:
        logger.error("lims_kinetics_fit failed for sample_id=%s: %s", sample_id, e)
        raise HTTPException(500, detail=f"Erreur ajustement cinétique: {e}")
