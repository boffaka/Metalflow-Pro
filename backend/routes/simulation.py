"""
MPDPMS — Simulation Params routes.
Handles simulation parameter CRUD, batch update, ML run, and optimization.
"""
from __future__ import annotations

import logging
import psycopg2
import math
import random
import uuid as _uuid
import json as _json

from fastapi import APIRouter, HTTPException, Depends, Body

try:
    from ..auth import project_user
    from ..db import qone, qall, execute, conn, release, build_update_sets
    from .. import config as _app_config
    from ..constants import TROY_OZ_PER_GRAM
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user
    from db import qone, qall, execute, conn, release, build_update_sets
    import config as _app_config
    from constants import TROY_OZ_PER_GRAM

try:
    from ..tasks.simulation_tasks import (
        run_rigorous_simulation as _task_rigorous,
        _run_rigorous_engine,
    )
except ImportError:
    from tasks.simulation_tasks import (
        run_rigorous_simulation as _task_rigorous,
        _run_rigorous_engine,
    )

router = APIRouter(prefix="/api/v1/projects", tags=["simulation"])
logger = logging.getLogger("mpdpms")


def _geomet_context(pid: str) -> dict:
    sample_total = int((qone("SELECT COUNT(*) AS n FROM lims_samples WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    domain_total = int((qone("SELECT COUNT(*) AS n FROM geomet_domains WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    composite_total = int((qone("SELECT COUNT(*) AS n FROM geomet_composites WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    assigned_total = int((qone(
        "SELECT COUNT(*) AS n FROM sample_geomet_domain sgd JOIN lims_samples ls ON ls.id = sgd.sample_id WHERE ls.project_id=%s",
        (pid,),
    ) or {}).get("n", 0))
    confidence_score = round(
        (min(100.0, (assigned_total / sample_total) * 100.0) * 0.4 if sample_total else 0.0)
        + min(100.0, domain_total * 20.0) * 0.3
        + min(100.0, composite_total * 25.0) * 0.3,
        1,
    )
    return {
        "samples": sample_total,
        "domains": domain_total,
        "composites": composite_total,
        "assigned_samples": assigned_total,
        "confidence_score": confidence_score,
    }

SIM_DEFAULTS = [
    # ═══════════════════════════════════════════════════
    # 1. COMMINUTION — Circuit de fragmentation
    # ═══════════════════════════════════════════════════
    # SAG / AG Mill
    ("comminution","sag_bwi",          "SAG — Bond Work Index (BWi)",         10.0,  None, "kWh/t",  0),
    ("comminution","sag_f80",           "SAG — F80 alimentation",              100.0, None, "mm",     1),
    ("comminution","sag_p80",           "SAG — P80 produit",                   3.0,   None, "mm",     2),
    ("comminution","sag_spi",           "SAG — SAG Power Index (SPI)",         80.0,  None, "min",    3),
    ("comminution","sag_density",       "SAG — Densité pulpe charge",          1.85,  None, "t/m³",   4),
    ("comminution","sag_ball_load",     "SAG — Charge billes",                 10.0,  None, "%v",     5),
    ("comminution","sag_speed",         "SAG — Vitesse (% critique)",          75.0,  None, "%Nc",    6),
    ("comminution","sag_cs_frac",        "SAG — Fraction charge solides",       30.0,  None, "%",      7),
    ("comminution","sag_specific_energy","SAG — Énergie spécifique",           8.0,   None, "kWh/t",  8),
    # Ball Mill
    ("comminution","bm_bwi",            "Ball Mill — Bond Work Index",         14.0,  None, "kWh/t",  10),
    ("comminution","bm_f80",            "Ball Mill — F80 alimentation",        float(_app_config.DEFAULT_BM_F80_UM),None, "µm",     11),
    ("comminution","bm_p80",            "Ball Mill — P80 produit (target)",    75.0,  None, "µm",     12),
    ("comminution","bm_ball_size",      "Ball Mill — Diamètre billes",         63.5,  None, "mm",     13),
    ("comminution","bm_filling",        "Ball Mill — Taux remplissage",        35.0,  None, "%v",     14),
    ("comminution","bm_speed",          "Ball Mill — Vitesse (% critique)",    72.0,  None, "%Nc",    15),
    ("comminution","bm_specific_energy","Ball Mill — Énergie spécifique",      7.0,   None, "kWh/t",  16),
    # HPGR
    ("comminution","hpgr_spf",          "HPGR — Force pression spécifique",   3.5,   None, "N/mm²",  20),
    ("comminution","hpgr_throughput",   "HPGR — Capacité nominale",           500.0, None, "t/h",    21),
    ("comminution","hpgr_p80",          "HPGR — P80 produit",                 6.0,   None, "mm",     22),
    ("comminution","hpgr_moisture",     "HPGR — Teneur eau alimentation",     2.0,   None, "%",      23),
    ("comminution","hpgr_specific_energy","HPGR — Énergie spécifique",        2.5,   None, "kWh/t",  24),
    # Rod Mill
    ("comminution","rod_bwi",           "Rod Mill — Bond Work Index",         12.0,  None, "kWh/t",  30),
    ("comminution","rod_p80",           "Rod Mill — P80 produit",              800.0, None, "µm",     31),
    ("comminution","rod_specific_energy","Rod Mill — Énergie spécifique",     6.0,   None, "kWh/t",  32),
    # IsaMill (rebroyage ultrafin)
    ("comminution","isa_p80",           "IsaMill — P80 cible (ultrafin)",      20.0,  None, "µm",     40),
    ("comminution","isa_specific_energy","IsaMill — Énergie spécifique",      40.0,  None, "kWh/t",  41),
    ("comminution","isa_media",         "IsaMill — Type de médias",            None,  "Céramique", "", 42),
    ("comminution","isa_media_size",    "IsaMill — Taille médias",            2.0,   None, "mm",     43),
    ("comminution","isa_media_filling", "IsaMill — Remplissage médias",       70.0,  None, "%",      44),
    # Vertimill (rebroyage moyen)
    ("comminution","vert_p80",          "Vertimill — P80 cible",              45.0,  None, "µm",     50),
    ("comminution","vert_specific_energy","Vertimill — Énergie spécifique",   20.0,  None, "kWh/t",  51),
    ("comminution","vert_ball_size",    "Vertimill — Diamètre billes",        19.0,  None, "mm",     52),
    # VRM / Roller Press
    ("comminution","vrm_p80",           "VRM — P80 produit",                  100.0, None, "µm",     60),
    ("comminution","vrm_specific_energy","VRM — Énergie spécifique",          8.0,   None, "kWh/t",  61),
    # Regrinding (général)
    ("comminution","rebroyage_active",  "Rebroyage — Circuit actif (O/N)",    None,  "Non","",       70),
    ("comminution","rebroyage_target",  "Rebroyage — P80 cible",              25.0,  None, "µm",     71),
    ("comminution","rec_baseline",      "Récupération baseline globale",       89.0,  None, "%",      80),

    # ═══════════════════════════════════════════════════
    # 2. CLASSIFICATION
    # ═══════════════════════════════════════════════════
    ("classification","cyclo_cut_size", "Hydrocyclone — Coupure (d50c)",       75.0,  None, "µm",     0),
    ("classification","cyclo_efficiency","Hydrocyclone — Efficacité E",       70.0,  None, "%",      1),
    ("classification","cyclo_density",  "Hydrocyclone — Densité overflow",    1.15,  None, "t/m³",   2),
    ("classification","cyclo_uf_density","Hydrocyclone — Densité underflow",  1.70,  None, "t/m³",   3),
    ("classification","screen_aperture","Crible — Ouverture de maille",       6.0,   None, "mm",     10),
    ("classification","screen_efficiency","Crible — Efficacité de criblage",  90.0,  None, "%",      11),
    ("classification","screen_type",    "Crible — Type",                      None,  "Vibrant","",   12),

    # ═══════════════════════════════════════════════════
    # 3. LIXIVIATION — Leach / CIP / Heap / Vat
    # ═══════════════════════════════════════════════════
    # Paramètres communs
    ("leaching","leach_type",           "Lixiviation — Type circuit (CIL/CIP)",None,  "", "",       0),
    ("leaching","rec_baseline",         "Lixiviation — Récup. Au baseline",   89.0,  None, "%",      1),
    ("leaching","ph_target",            "Lixiviation — pH cible",             10.5,  None, "",       2),
    ("leaching","cn_target",            "Lixiviation — [NaCN] cible",         350.0, None, "mg/L",   3),
    ("leaching","cn_min",               "Lixiviation — [NaCN] minimale",      200.0, None, "mg/L",   4),
    ("leaching","cn_max",               "Lixiviation — [NaCN] maximale",      500.0, None, "mg/L",   5),
    ("leaching","do_target",            "Lixiviation — DO cible",             7.0,   None, "mg/L",   6),
    # Leach (LIMS D1 / charbon en pulpe)
    ("leaching","cil_tanks",            "Leach — Nombre de cuves",            8.0,   None, "",       10),
    ("leaching","cil_srt",              "Leach — Temps résidence total",        24.0,  None, "h",      11),
    ("leaching","cil_carbon",           "Leach — Charge charbon actif",         10.0,  None, "g/L",    12),
    ("leaching","cil_pct_solids",       "Leach — Densité pulpe (% solides)",  45.0,  None, "%",      13),
    ("leaching","cil_carbon_advance",   "Leach — Avancement charbon (C→A)",     None,  "Contre-courant","", 14),
    # CIP
    ("leaching","cip_tanks",            "CIP — Nombre de cuves",              7.0,   None, "",       20),
    ("leaching","cip_srt",              "CIP — Temps résidence total",        24.0,  None, "h",      21),
    ("leaching","cip_carbon",           "CIP — Charge carbone actif",         15.0,  None, "g/L",    22),
    ("leaching","cip_wash_eff",         "CIP — Efficacité lavage carbon",     95.0,  None, "%",      23),
    # Heap Leach
    ("leaching","heap_height",          "Heap Leach — Hauteur tas",           6.0,   None, "m",      30),
    ("leaching","heap_irrigation",      "Heap Leach — Taux irrigation NaCN",  10.0,  None, "L/m²/h", 31),
    ("leaching","heap_cn_soln",         "Heap Leach — [NaCN] solution",       1.0,   None, "g/L",    32),
    ("leaching","heap_leach_time",      "Heap Leach — Durée lixiviation",     60.0,  None, "jours",  33),
    ("leaching","heap_rec",             "Heap Leach — Récupération Au",       65.0,  None, "%",      34),
    ("leaching","heap_crush_size",      "Heap Leach — P80 concassage",        19.0,  None, "mm",     35),
    # Vat Leach
    ("leaching","vat_srt",              "Vat Leach — Temps résidence",        48.0,  None, "h",      40),
    ("leaching","vat_cn",               "Vat Leach — [NaCN]",                 500.0, None, "mg/L",   41),
    ("leaching","vat_rec",              "Vat Leach — Récupération Au",        88.0,  None, "%",      42),

    # ═══════════════════════════════════════════════════
    # 4. CONCENTRATION — Flottation & Gravité
    # ═══════════════════════════════════════════════════
    ("concentration","flot_active",     "Flottation — Circuit actif (O/N)",   None,  "Non",  "",    0),
    ("concentration","flot_rec_au",     "Flottation — Récup. Au concentré",   92.0,  None, "%",      1),
    ("concentration","flot_rec_s",      "Flottation — Récup. soufre",         95.0,  None, "%",      2),
    ("concentration","flot_grade_conc", "Flottation — Teneur concentré Au",   50.0,  None, "g/t",    3),
    ("concentration","flot_mass_pull",  "Flottation — Mass pull",             10.0,  None, "%",      4),
    ("concentration","flot_xanthate",   "Flottation — Xanthate dosage",       50.0,  None, "g/t",    5),
    ("concentration","flot_frother",    "Flottation — Mousse dosage",         25.0,  None, "g/t",    6),
    ("concentration","flot_srt",        "Flottation — Temps résidence",       15.0,  None, "min",    7),
    ("concentration","flot_p80",        "Flottation — P80 alimentation",      75.0,  None, "µm",     8),
    ("concentration","gravity_active",  "Gravité — Circuit actif (O/N)",      None,  "Non",  "",    20),
    ("concentration","gravity_grg",     "Gravité — GRG dans le minerai",      35.0,  None, "%",      21),
    ("concentration","gravity_slip",    "Gravité — Détournement cyclone (slip)", 30.0, None, "%",      22),
    ("concentration","gravity_type",    "Gravité — Type équipement",          None,  "Knelson","",   23),
    ("concentration","gravity_rec",     "Gravité — Récup. Knelson sur GRG",   50.0,  None, "%",      24),
    ("concentration","gravity_ilr",     "Gravité — Récup. ILR sur concentré", 95.0,  None, "%",      25),
    ("concentration","gravity_mass_pull","Gravité — Mass pull (alim. gravité)", 0.2, None, "%",      26),
    ("concentration","gravity_plant_rec","Gravité — Récup. usine (GRG×Knelson×slip×ILR)", None, None, "%", 27),

    # ═══════════════════════════════════════════════════
    # 5. PRÉTRAITEMENT — POX / Grillage / BioOx
    # ═══════════════════════════════════════════════════
    ("pretraitement","pre_type",         "Prétraitement — Type",              None,  "Sans", "",     0),
    ("pretraitement","pox_temp",         "POX — Température réacteur",        220.0, None, "°C",     1),
    ("pretraitement","pox_pressure",     "POX — Pression partielle O₂",       700.0, None, "kPa",    2),
    ("pretraitement","pox_srt",         "POX — Temps résidence",              1.0,   None, "h",      3),
    ("pretraitement","pox_sulfide_pct", "POX — Teneur sulfures",              4.0,   None, "%S",     4),
    ("pretraitement","pox_ox_req",      "POX — Demande en oxygène",           120.0, None, "kg O₂/t",5),
    ("pretraitement","roast_temp",      "Grillage — Température",             640.0, None, "°C",     10),
    ("pretraitement","roast_time",      "Grillage — Temps résidence",         2.0,   None, "h",      11),
    ("pretraitement","roast_sulfox",    "Grillage — Taux oxydation sulfures", 98.0,  None, "%",      12),
    ("pretraitement","bioox_temp",      "BioOx — Température",               42.0,  None, "°C",     20),
    ("pretraitement","bioox_srt",       "BioOx — Temps résidence",            4.0,   None, "jours",  21),
    ("pretraitement","bioox_sulfox",    "BioOx — Taux oxydation sulfures",    95.0,  None, "%",      22),
    ("pretraitement","bioox_rec_boost", "BioOx — Gain récupération vs direct",15.0,  None, "%",      23),

    # ═══════════════════════════════════════════════════
    # 6. ADR — Élution / Électrolyse / Affinage
    # ═══════════════════════════════════════════════════
    ("adr","elution_method",            "Élution — Méthode",                 None,  "AARL", "",     0),
    ("adr","elution_temp",              "Élution — Température vapeur",       150.0, None, "°C",     1),
    ("adr","elution_cycle",             "Élution — Durée cycle",              9.5,   None, "h",      2),
    ("adr","elution_naoh",              "Élution — [NaOH]",                   1.0,   None, "%",      3),
    ("adr","elution_nacn",              "Élution — [NaCN]",                   0.5,   None, "%",      4),
    ("adr","elution_col_loading",       "Élution — Chargement colonne max",   15000.0,None,"g Au/t C",5),
    ("adr","elution_eff",               "Élution — Efficacité élution",       98.0,  None, "%",      6),
    ("adr","ew_voltage",                "EW — Tension appliquée",            2.5,   None, "V",      10),
    ("adr","ew_current_density",        "EW — Densité de courant",            150.0, None, "A/m²",   11),
    ("adr","ew_faradaic_eff",           "EW — Efficacité faradaïque",         90.0,  None, "%",      12),
    ("adr","ew_cathode_loading",        "EW — Chargement cathode",            500.0, None, "g Au/m²",13),
    ("adr","ew_cycle",                  "EW — Durée cycle harvest",           24.0,  None, "h",      14),
    ("adr","smelt_temp",                "Fusion — Température",               1200.0,None, "°C",     20),
    ("adr","smelt_flux",                "Fusion — Flux borax",                50.0,  None, "kg/t",   21),
    ("adr","doore_purity",              "Doré — Pureté cible",                90.0,  None, "%",      22),

    # ═══════════════════════════════════════════════════
    # 7. SÉPARATION SOLIDE-LIQUIDE
    # ═══════════════════════════════════════════════════
    ("separation","thickener_ua",       "Épaississeur — Surface unitaire",    1.5,   None, "m²/t/j", 0),
    ("separation","thickener_uf_pct",   "Épaississeur — Densité underflow",   55.0,  None, "% solides",1),
    ("separation","thickener_flocculant","Épaississeur — Dosage floculant",   15.0,  None, "g/t",    2),
    ("separation","thickener_overflow", "Épaississeur — Débordement eau",     98.0,  None, "% eau",  3),
    ("separation","filter_type",        "Filtre — Type",                      None,  "Disques","",   10),
    ("separation","filter_rate",        "Filtre — Taux filtration",           0.5,   None, "t/m²/h", 11),
    ("separation","filter_moisture",    "Filtre — Humidité gâteau",           12.0,  None, "%",      12),
    ("separation","ccd_stages",         "CCD — Nombre d'étages lavage",       4.0,   None, "",       20),
    ("separation","ccd_wash_eff",       "CCD — Efficacité lavage",            99.0,  None, "%",      21),

    # ═══════════════════════════════════════════════════
    # 8. RÉACTIFS (consommations spécifiques)
    # ═══════════════════════════════════════════════════
    ("reagents","nacn_kg_t",            "NaCN — Consommation spécifique",     0.5,   None, "kg/t",   0),
    ("reagents","cao_kg_t",             "CaO (chaux) — Consommation",         1.2,   None, "kg/t",   1),
    ("reagents","naoh_kg_t",            "NaOH — Consommation élution",        3.0,   None, "kg/cycle",2),
    ("reagents","h2o2_kg_t",            "H₂O₂ — Destruction CN (INCO)",      0.8,   None, "kg/t",   3),
    ("reagents","cuso4_kg_t",           "CuSO₄ — Activateur flottation",     50.0,  None, "g/t",    4),
    ("reagents","xanthate_kg_t",        "Xanthate — Consommation",            50.0,  None, "g/t",    5),
    ("reagents","frother_kg_t",         "Mousse (MIBC) — Consommation",      25.0,  None, "g/t",    6),
    ("reagents","flocculant_g_t",       "Floculant — Consommation",           15.0,  None, "g/t",    7),
    ("reagents","sulfuric_kg_t",        "H₂SO₄ — Consommation (POX/SX)",     20.0,  None, "kg/t",   8),
    ("reagents","oxygen_nm3_t",         "O₂ — Consommation (CIL/POX)",       3.0,   None, "Nm³/t",  9),
    ("reagents","carbon_regen_temp",    "Carbone — Temp. régénération",       650.0, None, "°C",     10),
    ("reagents","carbon_loss",          "Carbone — Pertes attrition",         0.1,   None, "kg/t C", 11),

    # ═══════════════════════════════════════════════════
    # 9. ENVIRONNEMENT & CONFORMITÉ
    # ═══════════════════════════════════════════════════
    ("environnement","wad_limit",       "WAD CN — Limite rejet",              50.0,  None, "mg/L",   0),
    ("environnement","total_cn_limit",  "CN Total — Limite rejet",            100.0, None, "mg/L",   1),
    ("environnement","as_limit",        "Arsenic — Limite eau rejet",         0.5,   None, "mg/L",   2),
    ("environnement","hg_limit",        "Mercure — Limite eau rejet",         0.01,  None, "mg/L",   3),
    ("environnement","ph_effluent",     "Effluent — pH cible",                8.0,   None, "",       4),
    ("environnement","tsf_capacity",    "TSF — Capacité stockage",            50.0,  None, "Mt",     5),
    ("environnement","tsf_pct_solids",  "TSF — Densité dépôt",               50.0,  None, "% solides",6),
    ("environnement","detox_method",    "Détox CN — Méthode",                None,  "INCO SO₂/Air","",7),

    # ═══════════════════════════════════════════════════
    # 10. FINANCIER
    # ═══════════════════════════════════════════════════
    ("financier","au_price",            "Prix or",                           float(_app_config.DEFAULT_GOLD_PRICE_USD_OZ), None, "USD/oz", 0),
    ("financier","ag_price",            "Prix argent",                        25.0,  None, "USD/oz", 1),
    ("financier","cu_price",            "Prix cuivre",                        4.0,   None, "USD/lb", 2),
    ("financier","energy_kwh_t",        "Énergie spécifique référence",       15.0,  None, "kWh/t",  3),
    ("financier","energy_rate",         "Coût électricité",                   0.08,  None, "$/kWh",  4),
    ("financier","nacn_price",          "Prix NaCN",                          3.50,  None, "$/kg",   5),
    ("financier","cao_price",           "Prix chaux",                         0.12,  None, "$/kg",   6),
    ("financier","aisc_baseline",       "AISC baseline",                      1150.0,None, "$/oz",   7),
    ("financier","avail_pct",           "Disponibilité opérationnelle",       92.0,  None, "%",      8),
    ("financier","hours_day",           "Heures opération/jour",              24.0, None, "h/j",    9),
    ("financier","royalty_pct",         "Redevances royalty",                 2.5,   None, "%",      10),
    ("financier","smelting_refining",   "Frais affinage/fonte",               15.0,  None, "$/oz",   11),
    ("financier","tc_rc",               "TC/RC concentrés (si applicable)",   50.0,  None, "$/t conc",12),
    # OPEX unit costs ($/t traité) — utilisés par auto-génération OPEX
    ("financier","opex_labor_usd_t",    "OPEX — Main d'œuvre ($/t)",          4.20,  None, "$/t",    13),
    ("financier","opex_maint_usd_t",    "OPEX — Maintenance pièces ($/t)",    2.00,  None, "$/t",    14),
    ("financier","opex_lab_usd_t",      "OPEX — Laboratoire ($/t)",           0.80,  None, "$/t",    15),
    ("financier","opex_ga_usd_t",       "OPEX — G&A frais généraux ($/t)",    1.50,  None, "$/t",    16),
    ("financier","opex_media_usd_t",    "OPEX — Consommables boulets ($/t)",  2.00,  None, "$/t",    17),
    ("financier","opex_liners_usd_t",   "OPEX — Consommables blindages ($/t)",1.20,  None, "$/t",    18),
    ("financier","opex_other_reag_usd_t","OPEX — Réactifs autres ($/t)",      0.80,  None, "$/t",    19),
    ("financier","opex_aux_energy_kwh_t","OPEX — Énergie auxiliaire (kWh/t)", 5.00,  None, "kWh/t",  20),

    # ═══════════════════════════════════════════════════
    # 11. PHASES D'OPTIMISATION
    # ═══════════════════════════════════════════════════
    ("optim","p1_invest_low",           "Phase 1 — Invest. min",              0.8,   None, "M$",     0),
    ("optim","p1_invest_high",          "Phase 1 — Invest. max",              1.5,   None, "M$",     1),
    ("optim","p1_gain_low",             "Phase 1 — Gain annuel min",          2.0,   None, "M$/an",  2),
    ("optim","p1_gain_high",            "Phase 1 — Gain annuel max",          4.0,   None, "M$/an",  3),
    ("optim","p2_invest_low",           "Phase 2 — Invest. min",              3.0,   None, "M$",     4),
    ("optim","p2_invest_high",          "Phase 2 — Invest. max",              5.0,   None, "M$",     5),
    ("optim","p2_gain_low",             "Phase 2 — Gain annuel min",          5.0,   None, "M$/an",  6),
    ("optim","p2_gain_high",            "Phase 2 — Gain annuel max",          8.0,   None, "M$/an",  7),
    ("optim","p3_invest_low",           "Phase 3 — Invest. min",              15.0,  None, "M$",     8),
    ("optim","p3_invest_high",          "Phase 3 — Invest. max",              30.0,  None, "M$",     9),

    # ═══════════════════════════════════════════════════
    # 12. KPIs CIBLES
    # ═══════════════════════════════════════════════════
    ("kpi_cible","rec_p1",              "Récupération cible Phase 1",         90.0,  None, "%",      0),
    ("kpi_cible","rec_p3",              "Récupération cible Phase 3",         93.0,  None, "%",      1),
    ("kpi_cible","tailings_p1",         "Teneur tailings cible P1",           0.12,  None, "g/t",    2),
    ("kpi_cible","tailings_p3",         "Teneur tailings cible P3",           0.10,  None, "g/t",    3),
    ("kpi_cible","aisc_p1",             "AISC cible Phase 1",                 1080.0,None, "$/oz",   4),
    ("kpi_cible","aisc_p3",             "AISC cible Phase 3",                 1000.0,None, "$/oz",   5),
    ("kpi_cible","avail_p1",            "Disponibilité cible Phase 1",        90.0,  None, "%",      6),
    ("kpi_cible","avail_p3",            "Disponibilité cible Phase 3",        92.0,  None, "%",      7),
    ("kpi_cible","wad_limit",           "WAD CN limite rejet",                50.0,  None, "mg/L",   8),
    ("kpi_cible","p80_p1",              "P80 broyage cible Phase 1",          70.0,  None, "µm",     9),
    ("kpi_cible","energy_reduction_p3", "Réduction énergie cible Phase 3",    18.0,  None, "%",      10),
]

# Keys added after initial deploy — seeded on read so existing projects pick them up.
_GRAVITY_SIM_MIGRATION_KEYS = frozenset({
    "gravity_slip", "gravity_ilr", "gravity_plant_rec",
})


def _seed_missing_sim_defaults(pid: str, keys: frozenset[str]) -> None:
    """Insert missing SIM_DEFAULTS rows (ON CONFLICT DO NOTHING)."""
    by_key = {row[1]: row for row in SIM_DEFAULTS if row[1] in keys}
    c = conn()
    try:
        cur = c.cursor()
        for key in keys:
            row = by_key.get(key)
            if not row:
                continue
            cat, _, label, val, txt, unit, order = row
            cur.execute(
                """INSERT INTO simulation_params
                   (project_id, category, param_key, param_label, param_value, param_value_text, unit, sort_order)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (project_id, category, param_key) DO NOTHING""",
                (pid, cat, key, label, val, txt, unit, order),
            )
        c.commit()
    finally:
        cur.close()
        release(c)


def _enrich_gravity_sim_rows(rows: list[dict]) -> list[dict]:
    """Attach computed plant recovery to gravity_plant_rec (read-only)."""
    try:
        from ..engines.gravity_model import plant_gravity_recovery_pct, resolve_gravity_params
    except ImportError:
        from engines.gravity_model import plant_gravity_recovery_pct, resolve_gravity_params

    sim = {
        r["param_key"]: r["param_value"]
        for r in rows
        if r.get("param_value") is not None and r.get("param_key")
    }
    plant_pct = round(plant_gravity_recovery_pct(resolve_gravity_params(sim)), 2)
    out: list[dict] = []
    for row in rows:
        r = dict(row)
        if r.get("param_key") == "gravity_plant_rec":
            r["param_value"] = plant_pct
            r["param_value_text"] = "Calculé"
            r["notes"] = "GRG × Knelson × slip × ILR (voir gravity_model)"
        elif r.get("param_key") == "gravity_rec" and r.get("param_label", "").startswith("Gravité — Récupération Au"):
            r["param_label"] = "Gravité — Récup. Knelson sur GRG"
        elif r.get("param_key") == "gravity_grg" and "Gravity Rec. Gold" in (r.get("param_label") or ""):
            r["param_label"] = "Gravité — GRG dans le minerai"
        out.append(r)
    return out


@router.get("/{pid}/simulation/params")
def get_sim_params(pid: str, user=Depends(project_user)):
    try:
        _seed_missing_sim_defaults(pid, _GRAVITY_SIM_MIGRATION_KEYS)
        rows = qall("""SELECT id, category, param_key, param_label, param_value,
                              param_value_text, unit, source, notes, sort_order
                       FROM simulation_params WHERE project_id=%s ORDER BY category, sort_order""",
                    (pid,))
        return _enrich_gravity_sim_rows(rows or [])
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/{pid}/simulation/params/init")
def init_sim_params(pid: str, user=Depends(project_user)):
    """Seed default parameters for a project if not already present."""
    count = 0
    c = conn()
    try:
        cur = c.cursor()
        for cat, key, label, val, txt, unit, order in SIM_DEFAULTS:
            try:
                cur.execute("""INSERT INTO simulation_params
                               (project_id, category, param_key, param_label, param_value, param_value_text, unit, sort_order)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (project_id, category, param_key) DO NOTHING""",
                            (pid, cat, key, label, val, txt, unit, order))
                if cur.rowcount == 1:
                    count += 1
            except Exception:  # intentional: ignore optional lookup failure
                pass
        c.commit()
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    finally:
        cur.close()
        release(c)
    return {"ok": True, "seeded": count}


@router.post("/{pid}/simulation/params")
def add_sim_param(pid: str, body: dict, user=Depends(project_user)):
    try:
        if not body.get("param_label"):
            raise HTTPException(400, "param_label requis")
        param_key = body.get("param_key") or body["param_label"].lower().replace(" ","_")[:50]
        row = execute("""INSERT INTO simulation_params
                         (project_id, category, param_key, param_label, param_value, param_value_text, unit, source, notes, sort_order)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                       (pid, body.get("category","process"), param_key, body["param_label"],
                        body.get("param_value"), body.get("param_value_text"),
                        body.get("unit",""), body.get("source","Utilisateur"),
                        body.get("notes",""), body.get("sort_order",99)))
        return {"ok": True, "id": str(row["id"])}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.patch("/{pid}/simulation/params/{param_id}")
# SQL SAFETY: field names checked against explicit allowlist ["param_label","param_value","param_value_text","unit","source","notes","category"].
def update_sim_param(pid: str, param_id: str, body: dict, user=Depends(project_user)):
    try:
        _ALLOWED = frozenset(["param_label","param_value","param_value_text","unit","source","notes","category"])
        fields, vals = build_update_sets({k: v for k, v in body.items() if k in _ALLOWED}, allowed=_ALLOWED)
        if not fields:
            raise HTTPException(400, "Aucune valeur à mettre à jour")
        fields.append("updated_at=NOW()")
        vals += [param_id, pid]
        execute(f"UPDATE simulation_params SET {', '.join(fields)} WHERE id=%s AND project_id=%s", vals)
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.delete("/{pid}/simulation/params/{param_id}")
def delete_sim_param(pid: str, param_id: str, user=Depends(project_user)):
    try:
        execute("DELETE FROM simulation_params WHERE id=%s AND project_id=%s", (param_id, pid))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/{pid}/simulation/params-batch")
def batch_update_sim_params(pid: str, body: list = Body(...), user=Depends(project_user)):
    """Bulk upsert simulation params: [{group_category, param_key, param_value}]."""
    if not body:
        return {"ok": True, "updated": 0}
    updated = 0
    c = conn()
    try:
        cur = c.cursor()
        for item in body:
            cat = item.get("group_category") or item.get("category", "process")
            key = item.get("param_key")
            val = item.get("param_value")
            if not key:
                continue
            if key == "gravity_plant_rec":
                continue  # read-only — computed on GET
            cur.execute("""UPDATE simulation_params
                           SET param_value=%s, updated_at=NOW()
                           WHERE project_id=%s AND category=%s AND param_key=%s""",
                        (val, pid, cat, key))
            if cur.rowcount == 0:
                cur.execute("""INSERT INTO simulation_params
                               (project_id, category, param_key, param_label, param_value)
                               VALUES (%s,%s,%s,%s,%s)
                               ON CONFLICT (project_id, category, param_key)
                               DO UPDATE SET param_value=EXCLUDED.param_value, updated_at=NOW()""",
                            (pid, cat, key, key, val))
            updated += 1
        c.commit()
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    finally:
        cur.close()
        release(c)
    return {"ok": True, "updated": updated}

# ─── AI & Simulation Models ───────────────────────────────────────────────────


@router.post("/{pid}/simulation/run-global")
def run_global_simulation(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Simule globalement tout le flowsheet du projet."""
    try:
        # 1. Obtenir les données du projet
        p = qone("SELECT target_tph, gold_grade_g_t FROM projects WHERE id=%s", (pid,))
        if not p: raise HTTPException(404, "Projet introuvable")
        tph = float(p.get("target_tph") or 100)
        grade = float(p.get("gold_grade_g_t") or 1.0)

        # 2. Obtenir le flowsheet (blocks & connections)
        blocks = qall("SELECT * FROM flowsheet_blocks WHERE project_id=%s", (pid,))

        if not blocks:
            return {"ok": False, "error": "Aucun flowsheet défini. Allez dans le module Flowsheet pour le générer."}

        # Simulation simple: on compte les types de blocs
        comminution_count = sum(1 for b in blocks if "mill" in b["type"].lower() or "crush" in b["type"].lower() or "hpgr" in b["type"].lower())
        leach_count = sum(1 for b in blocks if "leach" in b["type"].lower() or "cil" in b["type"].lower() or "cip" in b["type"].lower())
        flot_count = sum(1 for b in blocks if "flot" in b["type"].lower())

        # Calculs simulés basés sur la topologie globale
        base_recovery = 0.85
        if leach_count > 0: base_recovery += 0.05
        if flot_count > 0: base_recovery += 0.03
        if comminution_count > 2: base_recovery += 0.02 # Fine grinding

        recovery_pct = min(98.5, base_recovery * 100 + random.uniform(-1.5, 1.5))
        energy_kwh_t = 12.0 + (comminution_count * 3.5) + (flot_count * 2.0)
        water_m3_t = 0.8 + (leach_count * 0.3) + (flot_count * 0.5)

        return {
            "ok": True,
            "type": "global",
            "results": {
                "feed_tph": tph,
                "feed_grade_gt": grade,
                "overall_recovery_pct": round(recovery_pct, 2),
                "gold_production_oz_yr": round((tph * 24 * 365 * 0.92) * grade * (recovery_pct/100) * TROY_OZ_PER_GRAM, 0),
                "total_energy_kwh_t": round(energy_kwh_t, 2),
                "total_water_m3_t": round(water_m3_t, 2)
            }
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))

@router.post("/{pid}/simulation/run-circuit")
def run_circuit_simulation(pid: str, body: dict = Body(...), user=Depends(project_user)):
    """Simule un circuit spécifique (Comminution, Lixiviation, Flottation, etc.)."""
    try:
        circuit_type = body.get("circuit_type", "comminution").lower()
        tph = float(body.get("tph", 100))

        results = {"circuit": circuit_type, "throughput_tph": tph}

        if circuit_type == "comminution":
            p80 = float(body.get("p80", 75))
            bwi = float(body.get("bwi", 14.0))
            energy = 10 * bwi * (1/math.sqrt(p80) - 1/math.sqrt(100000)) # Simple Bond equation
            results["specific_energy_kwh_t"] = round(energy, 2)
            results["total_power_kw"] = round(energy * tph, 0)

        elif circuit_type in ["leaching", "cil", "cip"]:
            srt = float(body.get("srt", 24))
            cn = float(body.get("cn", 300))
            kinetics = 1.0 - math.exp(-0.15 * srt * (cn/300))
            results["leach_recovery_pct"] = round(kinetics * 95.0, 2)
            results["cn_consumption_kg_t"] = round(0.5 + (srt * 0.01) + (cn * 0.001), 2)

        elif circuit_type == "flotation":
            mass_pull = float(body.get("mass_pull", 8.0))
            results["concentrate_tph"] = round(tph * (mass_pull/100), 2)
            results["flot_recovery_pct"] = round(min(96.0, 70.0 + (mass_pull * 2.5)), 2)

        else:
            results["message"] = "Type de circuit non reconnu pour la simulation spécifique."

        return {
            "ok": True,
            "type": "circuit",
            "results": results
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))

@router.get("/{pid}/simulation/scenarios")
def get_simulation_scenarios(pid: str, user=Depends(project_user)):
    """Génère des scénarios basés sur les modules précédents (LIMS, Design)."""
    try:
        # Obtenir les données LIMS et Design
        a1 = qall("SELECT * FROM lims_a1 WHERE project_id=%s", (pid,))
        b1 = qall("SELECT * FROM lims_b1 WHERE project_id=%s", (pid,))

        scenarios = []

        # Scénario 1: Base Case (Flowsheet standard)
        scenarios.append({
            "id": "base_case",
            "name": "Cas de Base (Flowsheet Actuel)",
            "description": "Simulation utilisant les paramètres définis dans le schéma de procédé actuel.",
            "changes": []
        })

        # Scénario 2: Optimisation Énergie (Si BWi est élevé)
        avg_bwi = sum(float(r.get("bwi_kwh_t") or 14) for r in b1) / max(len(b1), 1) if b1 else 14
        if avg_bwi > 15:
            scenarios.append({
                "id": "opt_energy_hpgr",
                "name": "Alternative HPGR",
                "description": "Remplacement du SAG mill par un HPGR pour réduire la consommation énergétique due à la dureté du minerai.",
                "changes": ["Remplacer SAG par HPGR", "Ajouter criblage sec", "Augmenter taille du Ball Mill"]
            })

        # Scénario 3: Récupération vs Flottation (Si sulfures présents)
        avg_s = sum(float(r.get("s_sulfide_pct") or 0) for r in a1) / max(len(a1), 1) if a1 else 0
        if avg_s > 1.5:
            scenarios.append({
                "id": "opt_flot_pox",
                "name": "Circuit Réfractaire (Flottation + POX)",
                "description": f"Ajout d'un circuit de flottation et oxydation sous pression suite à la présence de sulfures ({round(avg_s,1)}%).",
                "changes": ["Ajouter circuit Flottation Rougher", "Ajouter Autoclave (POX)", "Modifier circuit CIL"]
            })

        # Scénario 4: Capacité augmentée
        scenarios.append({
            "id": "high_throughput",
            "name": "Augmentation Capacité (+20%)",
            "description": "Simulation de la réponse du circuit actuel avec une augmentation de 20% du tonnage.",
            "changes": ["Tonnage cible +20%", "Réduction temps de séjour (SRT)"]
        })

        return {
            "ok": True,
            "scenarios": scenarios
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))

@router.post("/{pid}/simulation/run-custom")
def run_custom_simulation(pid: str, body: dict = Body(...), user=Depends(project_user)):
    """Exécute une simulation sur un circuit entièrement personnalisé construit par l'utilisateur."""
    try:
        nodes = body.get("nodes", [])
        edges = body.get("edges", [])
        tph = float(body.get("feed_tph", 100))
        grade = float(body.get("feed_grade", 1.0))

        if not nodes:
            return {"ok": False, "error": "Le circuit personnalisé est vide."}

        # Simulation simplifiée de la topologie personnalisée
        recovery = 0.0
        energy = 0.0

        for node in nodes:
            ntype = node.get("type", "").lower()
            params = node.get("params", {})

            if "sag" in ntype or "mill" in ntype:
                energy += float(params.get("specific_energy", 8.0))
            elif "crush" in ntype or "hpgr" in ntype:
                energy += float(params.get("specific_energy", 2.5))
            elif "cil" in ntype or "cip" in ntype or "leach" in ntype:
                recovery += float(params.get("expected_recovery", 85.0))
            elif "flot" in ntype:
                recovery += float(params.get("expected_recovery", 70.0)) * 0.95 # Concentré vers leach
            elif "gravity" in ntype:
                recovery += float(params.get("expected_recovery", 30.0))

        # Normalisation
        recovery_pct = min(98.0, max(10.0, recovery))

        return {
            "ok": True,
            "type": "custom",
            "results": {
                "nodes_simulated": len(nodes),
                "edges_simulated": len(edges),
                "estimated_recovery_pct": round(recovery_pct, 2),
                "estimated_energy_kwh_t": round(energy, 2),
                "gold_production_oz_yr": round((tph * 24 * 365 * 0.92) * grade * (recovery_pct/100) * TROY_OZ_PER_GRAM, 0)
            }
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))

@router.post("/{pid}/simulation/run")
def run_simulation_ml(pid: str, body: dict, user=Depends(project_user)):
    """
    Modèle prédictif hybride (Cinétique de lixiviation + Thermodynamique de Broyage)
    Remplace les anciens mocks 'random.uniform' par des équations empiriques
    (Cinétique de premier ordre, Loi de Bond).
    """
    try:
        p80 = float(body.get("p80", 75))
        cn = float(body.get("cn", 350))
        doV = float(body.get("do", 7))
        ph = float(body.get("ph", 10.5))
        srt = float(body.get("srt", 24))
        float(body.get("tph", 100))
        float(body.get("gr", 1.0))
        bwi = float(body.get("bwi", 14.0))
        f80 = float(body.get("f80", 6000))
        geomet = _geomet_context(pid)

        # =========================================================================
        # 1. MODÈLE DE COMMINUTION (Loi de Bond + Efficacité de fragmentation)
        # =========================================================================
        # Modèle empirique de Bond: W = 10 * BWi * (1/sqrt(P80) - 1/sqrt(F80))
        # Avec correction d'efficacité (Rowland) selon le P80 cible
        efficiency_factor = 1.0
        if p80 < 70:
            efficiency_factor += 0.05 * ((70 - p80) / 10)  # Perte d'efficacité pour mouture fine

        # Confiance géométallurgique (impact sur la variabilité)
        confidence_factor = 1.0 - max(0.0, (80.0 - geomet["confidence_score"]) / 1000.0)

        base_energy = 10 * bwi * (1/math.sqrt(p80) - 1/math.sqrt(f80))
        energy = base_energy * efficiency_factor * confidence_factor

        # =========================================================================
        # 2. MODÈLE CINÉTIQUE DE LIXIVIATION (Équation de 1er ordre modifiée)
        # =========================================================================
        # Rec = R_max * (1 - exp(-k * t))

        rec_base = float(body.get("rec_baseline", 89.0)) / 100.0
        cn_min = float(body.get("cn_min", 200))

        # A. Libération (Effet du P80 sur R_max)
        # Si le P80 est plus grand, on perd de l'or non libéré. S'il est plus petit, R_max augmente.
        liberation_factor = 1.0
        if p80 > 75:
            liberation_factor = 1.0 - 0.005 * (p80 - 75)
        elif p80 < 75:
            liberation_factor = 1.0 + 0.002 * (75 - p80)

        r_max = min(0.98, rec_base * liberation_factor)

        # B. Constante de vitesse k (Fonction de [NaCN] et DO)
        # Équation de Kudryk et Kellogg (Limitation de transport de masse)
        k_base = 0.15  # h^-1

        # Limitation par le cyanure
        cn_effect = min(1.0, cn / (cn_min * 1.5))

        # Limitation par l'oxygène dissous (DO)
        # Ratio optimal [NaCN]/[O2] molaire approx 6:1 (en masse: [NaCN]/[O2] = 4.6)
        # Si DO < (CN / 4.6), alors O2 est limitant.
        ideal_do = cn / 4.6 / 10  # Facteur empirique d'échelle
        do_effect = min(1.0, doV / ideal_do) if ideal_do > 0 else 1.0

        # Effet du pH (inhibition si pH bas = HCN volatil)
        ph_effect = 1.0
        if ph < 10.0:
            ph_effect = 0.5 + 0.5 * (ph - 9.0)  # Baisse drastique de k

        k_kinetic = k_base * cn_effect * do_effect * ph_effect

        # Calcul de la récupération avec l'équation de cinétique chimique
        recovery = r_max * (1.0 - math.exp(-k_kinetic * srt))

        # =========================================================================
        # 3. RECOMMANDATIONS (Système Expert basé sur le modèle)
        # =========================================================================
        recommendations = []
        if p80 > 85:
            recommendations.append(f"⚠ Métallurgie: P80 élevé ({p80} µm). Perte de libération modélisée de {round((1-liberation_factor)*100, 1)}%.")

        if do_effect < 0.9:
            recommendations.append(f"🔴 Cinétique: Limitation par l'oxygène détectée (DO={doV} mg/L vs Idéal={round(ideal_do, 1)}). Injecter de l'oxygène.")

        if cn_effect < 0.9:
            recommendations.append(f"🔴 Cinétique: Cyanure limitant (NaCN={cn} ppm). Augmenter le dosage pour accélérer la lixiviation.")

        if ph < 10.0:
            recommendations.append("🔴 Chimie: pH dangereux (<10.0). Perte de cyanure par volatilisation (HCN).")

        if recovery < (rec_base - 0.02):
            recommendations.append(f"⚠ Rendement: Récupération estimée ({round(recovery*100, 1)}%) sous le baseline de {round(rec_base*100, 1)}%. Optimisez DO ou SRT.")

        if geomet["confidence_score"] < 60:
            recommendations.append("🧪 Géométallurgie: Score de confiance faible. Les prédictions cinétiques ont une forte marge d'erreur.")

        # Calcul de la confiance de prédiction du modèle
        model_confidence = min(98.0, 100.0 - (100.0 - geomet["confidence_score"]) * 0.2 - (1.0 - do_effect) * 10 - (1.0 - cn_effect) * 10)

        return {
            "ok": True,
            "prediction": {
                "recovery_pct": round(recovery * 100, 2),
                "energy_kwh_t": round(energy, 2),
                "ml_confidence": round(max(50.0, model_confidence), 1)
            },
            "geomet_context": geomet,
            "recommendations": recommendations or ["✔ Moteur Thermodynamique: Les paramètres sont à l'optimum cinétique."]
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ─────────────────────────────────────────────────────────────────────────────
# REMOVED 2026-05-06 — POST /{pid}/simulation/optimize (was deprecated=True)
#
# This endpoint was a mock NSGA-II solver returning hardcoded pseudo-Pareto
# data. The Sunset header was 2026-09-30 but no production caller exists
# (HTML monolith, React frontend, and seed scripts do not call it; only
# `tests/test_jobs_api.py::test_legacy_sync_endpoint_emits_deprecation_header`
# referenced it, and that test has also been removed).
#
# Async replacement: POST /{pid}/simulation/optimize/async (real Celery
# pipeline, line ~979 below) for synchronous-call needs.
# Real Pareto/NSGA-II compute: POST /simulation-v2/optimize (queued, with
# poll URL) — see backend/routes/simulation_v2.py.
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/{pid}/simulation/run-rigorous", status_code=202)
async def run_rigorous(pid: str, payload: dict = Body(...), sync: bool = False,
                       _auth=Depends(project_user)):
    run_id = str(_uuid.uuid4())
    c = conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO simulation_runs (id, project_id, type, status, params) VALUES (%s,%s,'rigorous','queued',%s)",
                (run_id, pid, _json.dumps(payload))
            )
        c.commit()

        if sync:
            results = _run_rigorous_engine(payload)
            with c.cursor() as cur:
                cur.execute(
                    "UPDATE simulation_runs SET status='done', results=%s WHERE id=%s",
                    (_json.dumps(results), run_id)
                )
            c.commit()
            return {"run_id": run_id, "status": "done", "results": results}

        _task_rigorous.delay(pid, run_id, payload)
        return {"run_id": run_id, "status": "queued"}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    finally:
        release(c)


@router.get("/{pid}/simulation/runs")
async def list_runs(pid: str, _auth=Depends(project_user)):
    c = conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT id, type, status, duration_s, created_at FROM simulation_runs WHERE project_id=%s ORDER BY created_at DESC LIMIT 50",
                (pid,)
            )
            rows = cur.fetchall()
        return [{"id": str(r[0]), "type": r[1], "status": r[2],
                 "duration_s": r[3], "created_at": str(r[4])} for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    finally:
        release(c)


@router.get("/{pid}/simulation/runs/{run_id}")
async def get_run(pid: str, run_id: str, _auth=Depends(project_user)):
    c = conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT id, type, status, params, results, duration_s FROM simulation_runs WHERE id=%s AND project_id=%s",
                (run_id, pid)
            )
            row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Run not found")
        return {"id": str(row[0]), "type": row[1], "status": row[2],
                "params": row[3], "results": row[4], "duration_s": row[5]}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    finally:
        release(c)


# ─────────────────────────────────────────────────────────────────────────────
# REMOVED 2026-05-06 — POST /{pid}/simulation/sensitivity (was deprecated=True)
#
# This endpoint queued (or ran inline with sync=true) sensitivity analysis
# via the legacy `_run_sensitivity_inline` engine, persisting to the legacy
# `simulation_runs` table. Sunset header was 2026-09-30 but no production
# caller exists; only `tests/test_simulation_route.py::test_sensitivity_endpoint_returns_ranked_params`
# referenced it, and that test has also been removed.
#
# Async replacements (still v1, kept active):
#   POST /{pid}/simulation/sensitivity/spider/async
#   POST /{pid}/simulation/sensitivity/tornado/async
# Synchronous v2 replacement (different body/response shape):
#   POST /simulation-v2/sensitivity — see backend/routes/simulation_v2.py
# ─────────────────────────────────────────────────────────────────────────────


# ─── Async submit endpoints (Chunk 4 — async heavy compute) ─────────────────
try:
    from .jobs import submit_job
except ImportError:  # pragma: no cover
    from routes.jobs import submit_job


def _validate_sensitivity_payload(body: dict) -> dict:
    base = body.get("base_params") or {}
    vary = body.get("params_to_vary") or []
    deltas = body.get("delta_pcts") or []
    if not isinstance(vary, list) or not vary:
        raise HTTPException(400, "params_to_vary must be a non-empty list")
    if not isinstance(deltas, list) or not deltas:
        raise HTTPException(400, "delta_pcts must be a non-empty list")
    if not isinstance(base, dict):
        raise HTTPException(400, "base_params must be an object")
    return {"base_params": base, "params_to_vary": vary, "delta_pcts": deltas}


@router.post("/{pid}/simulation/sensitivity/spider/async", status_code=202)
def submit_spider(pid: str, body: dict = Body(...), user=Depends(project_user)):
    payload = _validate_sensitivity_payload(body)
    return submit_job(
        project_id=pid, user_id=user["id"],
        job_type="sensitivity_spider", payload=payload,
    )


@router.post("/{pid}/simulation/sensitivity/tornado/async", status_code=202)
def submit_tornado(pid: str, body: dict = Body(...), user=Depends(project_user)):
    payload = _validate_sensitivity_payload(body)
    return submit_job(
        project_id=pid, user_id=user["id"],
        job_type="sensitivity_tornado", payload=payload,
    )


@router.post("/{pid}/simulation/optimize/async", status_code=202)
def submit_optimize(pid: str, body: dict = Body(...), user=Depends(project_user)):
    base = body.get("base_params")
    if not isinstance(base, dict) or not base:
        raise HTTPException(400, "base_params required (object, non-empty)")
    payload: dict = {"base_params": base, "grid": body.get("grid")}
    for key in (
        "study_context",
        "scenario_id",
        "uncertainty",
        "circuit_evaluation",
    ):
        if key in body and body[key] is not None:
            payload[key] = body[key]
    sc = payload.get("study_context")
    if sc is not None and not isinstance(sc, dict):
        raise HTTPException(400, "study_context must be an object when provided")
    unc = payload.get("uncertainty")
    if unc is not None and not isinstance(unc, dict):
        raise HTTPException(400, "uncertainty must be an object when provided")
    ce = payload.get("circuit_evaluation")
    if ce is not None and not isinstance(ce, dict):
        raise HTTPException(400, "circuit_evaluation must be an object when provided")
    return submit_job(
        project_id=pid, user_id=user["id"],
        job_type="simulate_optimize", payload=payload,
    )
