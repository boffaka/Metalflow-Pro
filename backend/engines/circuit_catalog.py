"""
Unit Operations Catalog for gold processing plants.
Contains 60 operations with criteria templates, LIMS trigger rules, and dependencies.

Reference design basis: 1517 t/h plant, BWi ~18.4 kWh/t, 1.5 g/t Au, refractory ore.
"""
import json
import logging

logger = logging.getLogger(__name__)

CATEGORIES = [
    {"code": "concassage", "label": "Concassage", "sort_base": 100},
    {"code": "broyage", "label": "Broyage", "sort_base": 200},
    {"code": "classification", "label": "Classification", "sort_base": 300},
    {"code": "rebroyage", "label": "Rebroyage", "sort_base": 400},
    {"code": "concentration", "label": "Concentration", "sort_base": 500},
    {"code": "pretraitement", "label": "Prétraitement", "sort_base": 600},
    {"code": "epaississement", "label": "Épaississement", "sort_base": 700},
    {"code": "lixiviation", "label": "Lixiviation", "sort_base": 800},
    {"code": "adr", "label": "ADR", "sort_base": 900},
    {"code": "detoxification", "label": "Détoxification", "sort_base": 1000},
    {"code": "residus", "label": "Résidus", "sort_base": 1100},
    {"code": "eau", "label": "Eau", "sort_base": 1200},
    {"code": "reactifs", "label": "Réactifs", "sort_base": 1300},
]

# ---------------------------------------------------------------------------
# Helper to build criteria dicts concisely
# ---------------------------------------------------------------------------

def _c(ref_suffix, section, item, unit, pea, pfs, fs, detail, typ, source="X", dag_key=None):
    """Shorthand constructor for a default_criteria entry.

    source codes (rendered as badges in the DC table):
      L = LIMS-derived (auto-filled from lab test data when project has LIMS)
      C = Calculated (recomputed by the cascade engine from formulas)
      M = Manual (user must enter; no automation)
      P = Project-level (pulled from `projects` table — throughput, grade, etc.)
      D = Design assumption (industry-standard design choice)
      X = Default (factory default value, overridable)

    dag_key (optional): when this criterion corresponds to a node or input in
    `dc_dag_registry.yaml`, set the canonical key (e.g. ``"sag_power_kw"``).
    The cascade engine reads this directly instead of deriving keys from
    `ref_number` (Chunk 1.5 — Option A). Most descriptive criteria
    (equipment count, motor type, dimensions) leave this as `None`.
    """
    return {
        "ref_suffix": ref_suffix,
        "section": section,
        "item": item,
        "unit": unit,
        "pea": pea,
        "pfs": pfs,
        "fs": fs,
        "detail": detail,
        "typ": typ,
        "source": source,
        "dag_key": dag_key,
    }

# ===================================================================
# CATALOG — 60 unit operations
# ===================================================================

CATALOG = [
    # ------------------------------------------------------------------
    # CONCASSAGE  (100-199)
    # ------------------------------------------------------------------
    {
        "op_code": "GIRATOIRE",
        "category": "concassage",
        "label": "Primary gyratory crusher",
        "sort_order": 110,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            # Ordre PDF: Type → Débit → F80 → F100 → OSS → P80 → Ratio → CWi
            # → W → P_shaft → η → marge → P_install → ouverture → modèle.
            _c("01", "Concasseur primaire", "Type concasseur",                    "-",     None, None, None, None, [],                   source="M"),
            _c("02", "Concasseur primaire", "Débit design alimentation",          "t/h",   1800, 1800, 1800, 1800, [1200, 3000],          source="P", dag_key="target_tph"),
            _c("03", "Concasseur primaire", "F80 alimentation (ROM)",             "µm",  600000, 528000, 528000, 528000, [400000, 700000],source="D", dag_key="rom_f80_mm"),
            _c("04", "Concasseur primaire", "F100 alimentation (top size)",       "µm", 1000000, 1000000, 1000000, 1000000, [600000, 1200000], source="C"),
            _c("05", "Concasseur primaire", "OSS (Open Side Setting)",            "mm",     175, 165, 165, 165, [125, 250],               source="D", dag_key="pc_css_mm"),
            _c("06", "Concasseur primaire", "P80 produit",                        "µm",  150000, 135000, 135000, 135000, [100000, 200000], source="C", dag_key="pc_p80_mm"),
            _c("07", "Concasseur primaire", "Ratio de réduction R80 = F80/P80",   "-",      4.0, 3.9, 3.9, 3.9, [3.0, 6.0],               source="C"),
            _c("08", "Concasseur primaire", "Bond CWi (Crushing Work Index)",     "kWh/t",   14, 14, 14, 14, [8, 22],                     source="L"),
            _c("09", "Concasseur primaire", "Énergie Bond W = 10·Wi·(1/√P80 - 1/√F80)", "kWh/t", 0.65, 0.60, 0.60, 0.60, [0.3, 1.2],     source="C"),
            _c("10", "Concasseur primaire", "Puissance arbre P_shaft = W × débit","kW",    1170, 1080, 1080, 1080, [400, 2500],           source="C"),
            _c("11", "Concasseur primaire", "Rendement mécanique η_mech",         "%",       90, 92, 92, 92, [85, 95],                    source="D"),
            _c("12", "Concasseur primaire", "Marge installation k_install",       "%",       15, 15, 15, 15, [10, 20],                    source="D"),
            _c("13", "Concasseur primaire", "PUISSANCE INSTALLÉE moteur",         "kW",    1500, 1350, 1350, 1350, [600, 3000],           source="C"),
            _c("14", "Concasseur primaire", "Ouverture alim. min (1.2 × top size)","mm",   1200, 1200, 1200, 1200, [800, 1500],           source="C"),
            _c("15", "Concasseur primaire", "Modèle suggéré",                     "-",     None, None, None, None, [],                   source="M"),
        ],
    },
    {
        "op_code": "CRIBLE",
        "category": "concassage",
        "label": "Vibrating screen",
        "sort_order": 120,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Crible scalping", "Type crible", "-", None, None, None, None, [], source="M"),
            _c("02", "Crible scalping", "Largeur", "m", 3.0, 3.0, 3.0, 3.0, [2.0, 4.0], source="D"),
            _c("03", "Crible scalping", "Longueur", "m", 8.0, 8.0, 8.0, 8.0, [5.0, 9.0], source="D"),
            _c("04", "Crible scalping", "Nombre de pontets", "-", 2, 2, 2, 2, [1, 3], source="D"),
            _c("05", "Crible scalping", "Débit alimentation crible", "t/h", 1800, 1800, 1800, 1800, [1000, 3500], source="D"),
            _c("06", "Crible scalping", "Coupure (mesh)", "mm", 50, 50, 50, 50, [25, 75], source="D"),
            _c("07", "Crible scalping", "% passant à la coupure", "%", 65, 65, 65, 65, [50, 80], source="D"),
            _c("08", "Crible scalping", "Débit undersize (Feed × passing)", "t/h", 1170, 1170, 1170, 1170, [600, 2500], source="C"),
            _c("09", "Crible scalping", "Débit oversize (vers MP)", "t/h", 630, 630, 630, 630, [300, 1200], source="D"),
            _c("10", "Crible scalping", "Capacité base C (VSMA, sec, 50 mm)", "t/h/m²", 22, 22, 22, 22, [15, 35], source="D"),
            _c("11", "Crible scalping", "Facteur efficacité M (90%)", "-", 0.90, 0.90, 0.90, 0.90, [0.80, 0.95], source="D"),
            _c("12", "Crible scalping", "Produit facteurs correctifs K (humid×forme×dens)", "-", 0.65, 0.65, 0.65, 0.65, [0.4, 0.85], source="D"),
            _c("13", "Crible scalping", "Facteur stratification S", "-", 0.85, 0.85, 0.85, 0.85, [0.7, 0.95], source="D"),
            _c("14", "Crible scalping", "Surface utile requise (VSMA)", "m²", 156, 156, 156, 156, [50, 300], source="C"),
            _c("15", "Crible scalping", "Nb cribles (3×8 m = 24 m²)", "unités", 7, 7, 7, 7, [2, 12], source="C"),
            _c("16", "Crible scalping", "Charge circulante", "%", 25, 25, 25, 25, [10, 50], source="D"),
        ],
    },
    {
        "op_code": "CONE",
        "category": "concassage",
        "label": "Secondary cone crusher",
        "sort_order": 130,
        "dependencies": ["GIRATOIRE"],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Concasseur secondaire", "Type concasseur (cône / VSI)", "-", None, None, None, None, [], source="M"),
            _c("02", "Concasseur secondaire", "Modèle suggéré (HP500/MP1000)", "-", None, None, None, None, [], source="M"),
            _c("03", "Concasseur secondaire", "Débit alim. (oversize crible)", "t/h", 630, 630, 630, 630, [300, 1500], source="D"),
            _c("04", "Concasseur secondaire", "F80 alimentation", "µm", 150000, 135000, 135000, 135000, [80000, 200000], source="D"),
            _c("05", "Concasseur secondaire", "CSS (Closed Side Setting)", "mm", 38, 35, 35, 35, [20, 60], source="D", dag_key="sc_css_mm"),
            _c("06", "Concasseur secondaire", "P80 produit secondaire", "µm", 38000, 35000, 35000, 35000, [20000, 60000], source="D", dag_key="sc_p80_mm"),
            _c("07", "Concasseur secondaire", "Ratio de réduction F80/P80", "-", 4.0, 3.9, 3.9, 3.9, [3.0, 6.0], source="D"),
            _c("08", "Concasseur secondaire", "Énergie Bond W secondaire", "kWh/t", 1.4, 1.3, 1.3, 1.3, [0.8, 2.5], source="D"),
            _c("09", "Concasseur secondaire", "Puissance arbre (W × débit oversize)", "kW", 880, 820, 820, 820, [350, 2500], source="C"),
            _c("10", "Concasseur secondaire", "Rendement mécanique", "%", 92, 92, 92, 92, [88, 95], source="D"),
            _c("11", "Concasseur secondaire", "Marge installation", "%", 15, 15, 15, 15, [10, 20], source="D"),
            _c("12", "Concasseur secondaire", "PUISSANCE INSTALLÉE", "kW", 1100, 1025, 1025, 1025, [400, 3000], source="D"),
        ],
    },
    {
        "op_code": "HPGR",
        "category": "concassage",
        "label": "High Pressure Grinding Roll",
        "sort_order": 140,
        "dependencies": ["CRIBLE"],
        "lims_triggers": {
            "condition": "b1.mb_kwh_t > 16",
            "field": "mb_kwh_t",
            "table": "lims_b1",
            "operator": ">",
            "threshold": 16,
        },
        "default_criteria": [
            # ── 1. Paramètres opératoires HPGR (PDF section 1) ────────────────
            _c("01", "HPGR — Paramètres opératoires", "Débit fresh feed (design)",          "t/h", 1500, 1500, 1500, 1500, [500, 3000],   source="P"),
            _c("02", "HPGR — Paramètres opératoires", "Recycle ratio (edge + crible)",      "%",     25, 22, 22, 20, [15, 30],            source="D"),
            _c("03", "HPGR — Paramètres opératoires", "Débit total roll (incl. recycle)",   "t/h", 1875, 1830, 1830, 1800, [600, 4000],   source="C"),
            _c("04", "HPGR — Paramètres opératoires", "F80 alimentation (= P80 secondaire)","µm",  35000, 35000, 35000, 35000, [20000, 60000], source="D"),
            _c("05", "HPGR — Paramètres opératoires", "P80 produit (post-crible, cible)",   "µm",   6500, 6000, 6000, 6000, [3000, 12000],source="D"),
            _c("06", "HPGR — Paramètres opératoires", "Ratio de réduction = F80/P80",       "-",      5.4, 5.8, 5.8, 5.8, [3, 10],         source="C"),
            # ── 2. Géométrie des rouleaux (PDF section 2) ─────────────────────
            _c("07", "HPGR — Géométrie des rouleaux", "Diamètre rouleau D",                 "m",     2.4, 2.4, 2.4, 2.4, [1.0, 2.8],       source="M"),
            _c("08", "HPGR — Géométrie des rouleaux", "Longueur rouleau L (L/D ≈ 0.6-0.8)", "m",    1.65, 1.65, 1.65, 1.65, [0.6, 2.2],     source="M"),
            _c("09", "HPGR — Géométrie des rouleaux", "Surface roll = D × L",               "m²",   3.96, 3.96, 3.96, 3.96, [0.6, 6.2],     source="C"),
            _c("10", "HPGR — Géométrie des rouleaux", "Vitesse rotation N",                 "tr/min", 18, 18, 18, 18, [14, 25],            source="D"),
            _c("11", "HPGR — Géométrie des rouleaux", "Vitesse périph. u = π·D·N/60",       "m/s",  2.26, 2.26, 2.26, 2.26, [1.0, 1.8],     source="C"),
            # ── 3. Calculs de capacité (PDF section 3) ────────────────────────
            _c("12", "HPGR — Calculs de capacité",    "Débit spécifique m-dot",             "ts/(h·m³)", 250, 250, 250, 250, [230, 280],    source="D"),
            _c("13", "HPGR — Calculs de capacité",    "Capacité par unité M = ṁ·D·L·u",     "t/h",  1900, 1900, 1900, 1900, [800, 3500],    source="C"),
            _c("14", "HPGR — Calculs de capacité",    "Nombre HPGR requis = Total/M",       "unités", 1, 1, 1, 1, [1, 4],                  source="C"),
            # ── 4. Force de pressage (PDF section 4) ──────────────────────────
            _c("15", "HPGR — Force de pressage",      "Force spécifique F_sp",              "N/mm²",  4.5, 4.5, 4.5, 4.5, [3.0, 5.5],       source="D"),
            _c("16", "HPGR — Force de pressage",      "Force totale par rouleau = F_sp·D·L","kN",  17820, 17820, 17820, 17820, [4000, 30000], source="C"),
            # ── 5. Énergie et puissance (PDF section 5) ───────────────────────
            _c("17", "HPGR — Énergie et puissance",   "Énergie spécifique E_sp",            "kWh/t",  2.5, 2.3, 2.3, 2.2, [1.5, 2.8],       source="D"),
            _c("18", "HPGR — Énergie et puissance",   "Puissance nette par HPGR = E_sp·M",  "kW",   4750, 4370, 4370, 4180, [1500, 8000],   source="C"),
            _c("19", "HPGR — Énergie et puissance",   "Rendement transmission (direct drive)","%",    95, 95, 95, 95, [92, 97],             source="D"),
            _c("20", "HPGR — Énergie et puissance",   "Marge moteur",                       "%",      15, 15, 15, 15, [10, 20],             source="D"),
            _c("21", "HPGR — Énergie et puissance",   "Puissance installée par rouleau",    "kW",   2875, 2645, 2645, 2530, [1000, 4500],   source="C"),
            _c("22", "HPGR — Énergie et puissance",   "Puissance totale HPGR (2×N×P_install)","kW", 5750, 5290, 5290, 5060, [2000, 9000],   source="C"),
            # ── 6. Crible humide post-HPGR (PDF section 6) ────────────────────
            _c("23", "HPGR — Crible humide post-HPGR","Coupure crible humide (P80 cible)",  "mm",     6, 6, 6, 6, [3, 12],                  source="D"),
            _c("24", "HPGR — Crible humide post-HPGR","Débit feed crible (= débit roll total)","t/h", 1875, 1830, 1830, 1800, [600, 4000],   source="C"),
            _c("25", "HPGR — Crible humide post-HPGR","% passant 6mm sortie HPGR (test pilote)","%",  65, 70, 70, 72, [50, 85],             source="L"),
            _c("26", "HPGR — Crible humide post-HPGR","Undersize → ball mill = débit·% passant","t/h", 1500, 1500, 1500, 1500, [500, 3000], source="C"),
            _c("27", "HPGR — Crible humide post-HPGR","Oversize (recycle) = débit - undersize","t/h", 375, 330, 330, 300, [100, 1000],      source="C"),
            _c("28", "HPGR — Crible humide post-HPGR","Capacité base C humide (wet screening)","t/h/m²", 18, 18, 18, 18, [12, 25],          source="D"),
            _c("29", "HPGR — Crible humide post-HPGR","Facteur efficacité M",               "-",   0.85, 0.85, 0.85, 0.85, [0.7, 0.95],     source="D"),
            _c("30", "HPGR — Crible humide post-HPGR","Produit facteurs correctifs K",      "-",   0.65, 0.65, 0.65, 0.65, [0.4, 0.9],      source="D"),
            _c("31", "HPGR — Crible humide post-HPGR","Surface utile requise (VSMA)",       "m²",   188, 184, 184, 180, [60, 400],          source="C"),
            _c("32", "HPGR — Crible humide post-HPGR","Nb cribles (banana 3.6×7.3=26m²)",   "unités",  8, 8, 8, 7, [2, 16],                 source="C"),
        ],
    },
    {
        "op_code": "STOCKPILE",
        "category": "concassage",
        "label": "Coarse ore stockpile",
        "sort_order": 150,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Stockpile", "Stockpile type", "-", None, None, None, None, [], source="D"),
            _c("02", "Stockpile", "Total capacity", "t", 60000, 60000, 60000, 60000, [20000, 120000], source="D"),
            _c("03", "Stockpile", "Live capacity", "t", 20000, 20000, 20000, 20000, [5000, 50000], source="D"),
            _c("04", "Stockpile", "Live residence time", "h", 12, 12, 12, 12, [4, 24], source="D"),
            _c("05", "Stockpile", "Diameter", "m", 50, 50, 50, 50, [30, 80], source="D"),
            _c("06", "Stockpile", "Reclaim method", "-", None, None, None, None, [], source="D"),
        ],
    },

    # ------------------------------------------------------------------
    # BROYAGE  (200-299)
    # ------------------------------------------------------------------
    {
        "op_code": "SAG_MILL",
        "category": "broyage",
        "label": "SAG Mill",
        "sort_order": 210,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            # ── Paramètres opératoires SAG (alternative au HPGR) ──────────────
            _c("01", "SAG Mill — Paramètres", "Si circuit SAG: activer ?", "-", None, None, None, None, [], source="M"),
            # SAG F80 alim is in µm in catalog; DAG key sag_f80_mm is in mm — unit conversion
            # is handled at the writer/cascade layer (cf. _LIMS_UNIT_CONVERSION in dc_generator).
            _c("02", "SAG Mill — Paramètres", "F80 alim SAG (post primaire)", "µm", 135000, 135000, 135000, 135000, [80000, 200000], source="D", dag_key="sag_f80_mm"),
            # T80 SAG (transfer size) → sag_p80_mm (input). The DAG node name is "sag_p80_mm"
            # but it represents the transfer size out of SAG into the BM circuit.
            _c("03", "SAG Mill — Paramètres", "T80 sortie SAG (transfer size)", "µm", 2500, 2200, 2200, 2200, [1000, 5000], source="D", dag_key="sag_p80_mm"),
            _c("04", "SAG Mill — Paramètres", "SMC SCSE (SAG specific energy)", "kWh/t", 9.0, 9.0, 9.0, 9.0, [4, 18], source="D"),
            _c("05", "SAG Mill — Paramètres", "Énergie SAG (Morrell approx.)", "kWh/t", 9.5, 9.5, 9.5, 9.5, [5, 20], source="D"),
            _c("06", "SAG Mill — Paramètres", "Puissance SAG arbre", "kW", 14250, 14250, 14250, 14250, [4000, 22000], source="D"),
            _c("07", "SAG Mill — Paramètres", "PUISSANCE INSTALLÉE SAG", "kW", 16400, 16400, 16400, 16400, [4500, 25000], source="D", dag_key="sag_power_kw"),
            # ── Géométrie SAG ─────────────────────────────────────────────────
            _c("08", "SAG Mill — Géométrie", "Diamètre SAG typique (ø intérieur)", "m", 10.4, 10.4, 10.4, 10.4, [6.0, 12.2], source="D"),
            _c("09", "SAG Mill — Géométrie", "Longueur SAG (L/D ≈ 0.5)", "m", 5.2, 5.2, 5.2, 5.2, [3.0, 7.0], source="D"),
            _c("10", "SAG Mill — Géométrie", "Charge boulets SAG (% vol)", "%", 12, 12, 12, 12, [8, 15], source="D"),
            _c("11", "SAG Mill — Géométrie", "Charge totale SAG (boulets+minerai)", "%", 28, 28, 28, 28, [22, 35], source="D"),
            _c("12", "SAG Mill — Géométrie", "Vitesse (% critique)", "%", 74, 74, 74, 74, [68, 78], source="D"),
        ],
    },
    {
        "op_code": "BALL_MILL",
        "category": "broyage",
        "label": "Ball Mill",
        "sort_order": 220,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            # ── 1. Paramètres opératoires Ball Mill ────────────────────────────
            _c("01", "Ball Mill — Paramètres", "Débit alimentation (Design throughput)", "t/h", 1500, 1500, 1500, 1500, [500, 3000], source="P"),
            _c("02", "Ball Mill — Paramètres", "F80 alimentation (≈0.75×P80 HPGR)", "µm", 4500, 4500, 4500, 4500, [2000, 8000], source="D", dag_key="bm_f80_um"),
            _c("03", "Ball Mill — Paramètres", "P80 cible (Cyclone OF)", "µm", 100, 90, 90, 90, [63, 150], source="D", dag_key="avg_p80_um"),
            _c("04", "Ball Mill — Paramètres", "Ratio de réduction", "-", 45, 50, 50, 50, [25, 80], source="D"),
            _c("05", "Ball Mill — Paramètres", "Bond BWi (Ball mill work index)", "kWh/t", 14, 14, 14, 14, [10, 22], source="L", dag_key="avg_bwi"),
            _c("06", "Ball Mill — Paramètres", "Énergie Bond non corrigée W (équation Bond)", "kWh/t", 13.5, 13.5, 13.5, 13.5, [8, 22], source="D"),
            # ── 1a. Corrections de Rowland (EF1-EF8) ──────────────────────────
            _c("07", "Ball Mill — Corrections Rowland", "EF1 (broyage à sec — 1.30 si sec)", "-", 1.0, 1.0, 1.0, 1.0, [1.0, 1.30], source="D"),
            _c("08", "Ball Mill — Corrections Rowland", "EF2 (circuit ouvert — 1.0 si fermé)", "-", 1.0, 1.0, 1.0, 1.0, [1.0, 1.20], source="D"),
            _c("09", "Ball Mill — Corrections Rowland", "EF3 (diamètre — (2.44/D)^0.2)", "-", 0.95, 0.95, 0.95, 0.95, [0.85, 1.05], source="D"),
            _c("10", "Ball Mill — Corrections Rowland", "F80,opt = 4000·√(13/Wi)", "µm", 3850, 3850, 3850, 3850, [2000, 5000], source="C"),
            _c("11", "Ball Mill — Corrections Rowland", "Rr = F80/P80", "-", 45, 50, 50, 50, [20, 80], source="C"),
            _c("12", "Ball Mill — Corrections Rowland", "EF4 (oversize feed, si F80>F80,opt)", "-", 1.05, 1.05, 1.05, 1.05, [1.0, 1.20], source="D"),
            _c("13", "Ball Mill — Corrections Rowland", "EF5 (finesse, P80<75µm)", "-", 1.0, 1.0, 1.0, 1.0, [1.0, 1.30], source="D"),
            _c("14", "Ball Mill — Corrections Rowland", "EF6 (rod mill L/D — rod only)", "-", 1.0, 1.0, 1.0, 1.0, [1.0, 1.10], source="D"),
            _c("15", "Ball Mill — Corrections Rowland", "EF7 (low reduction ratio si Rr<6)", "-", 1.0, 1.0, 1.0, 1.0, [1.0, 1.20], source="D"),
            _c("16", "Ball Mill — Corrections Rowland", "EF8 (rod mill feed — rod only)", "-", 1.0, 1.0, 1.0, 1.0, [1.0, 1.20], source="D"),
            _c("17", "Ball Mill — Corrections Rowland", "Énergie corrigée W_corrected = W × ∏ EFi", "kWh/t", 13.5, 13.5, 13.5, 13.5, [8, 25], source="C"),
            # ── 1b. Puissance & Dimensions Ball Mill ──────────────────────────
            _c("18", "Ball Mill — Puissance & Dimensions", "Puissance arbre requise (W_corr × débit)", "kW", 20250, 20250, 20250, 20250, [4000, 35000], source="C"),
            _c("19", "Ball Mill — Puissance & Dimensions", "Rendement moteur η_motor", "%", 95, 95, 95, 95, [92, 97], source="D", dag_key="mech_efficiency"),
            _c("20", "Ball Mill — Puissance & Dimensions", "Marge installation", "%", 10, 10, 10, 10, [5, 15], source="D", dag_key="bm_install_margin_pct"),
            _c("21", "Ball Mill — Puissance & Dimensions", "PUISSANCE INSTALLÉE total moteur", "kW", 23500, 23500, 23500, 23500, [5000, 40000], source="D", dag_key="bm_power_kw"),
            _c("22", "Ball Mill — Puissance & Dimensions", "Configuration moteur (Single / Twin / GMD)", "-", None, None, None, None, [], source="M"),
            _c("23", "Ball Mill — Puissance & Dimensions", "Remplissage boulets J_b", "%", 32, 32, 32, 32, [28, 35], source="D"),
            _c("24", "Ball Mill — Puissance & Dimensions", "Fraction vitesse critique φ_c", "%", 75, 75, 75, 75, [72, 78], source="D"),
            _c("25", "Ball Mill — Puissance & Dimensions", "Densité apparente charge ρ_b (acier forgé)", "t/m³", 4.65, 4.65, 4.65, 4.65, [4.5, 4.8], source="D"),
            _c("26", "Ball Mill — Puissance & Dimensions", "Aspect ratio L/D cible (overflow 1.3-1.7)", "-", 1.5, 1.5, 1.5, 1.5, [1.3, 1.7], source="D"),
            _c("27", "Ball Mill — Puissance & Dimensions", "Diamètre intérieur D (EGL)", "m", 6.7, 6.7, 6.7, 6.7, [4.0, 8.0], source="D"),
            _c("28", "Ball Mill — Puissance & Dimensions", "Longueur intérieure L (EGL = D × L/D)", "m", 10.0, 10.0, 10.0, 10.0, [6.0, 14.0], source="C"),
            _c("29", "Ball Mill — Puissance & Dimensions", "Vérif puissance Bond/Rowland (≈ P_install)", "kW", 23500, 23500, 23500, 23500, [5000, 40000], source="D"),
            _c("30", "Ball Mill — Puissance & Dimensions", "Top size boulet (Bond)", "mm", 60, 60, 60, 60, [25, 90], source="C"),
            _c("31", "Ball Mill — Puissance & Dimensions", "Type broyeur (Overflow/Grate/Center)", "-", None, None, None, None, [], source="M"),
            _c("32", "Ball Mill — Puissance & Dimensions", "Consommation boulets", "kg/t", 0.6, 0.6, 0.6, 0.6, [0.3, 1.2], source="D"),
        ],
    },
    {
        "op_code": "ROD_MILL",
        "category": "broyage",
        "label": "Rod Mill",
        "sort_order": 230,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Rod Mill", "RWi", "kWh/t", 18.4, 18.4, 18.4, 18.4, [10, 25], source="D"),
            _c("02", "Rod Mill", "Diameter (inside shell)", "m", 4.0, 4.0, 4.0, 4.0, [3.0, 5.0], source="D"),
            _c("03", "Rod Mill", "Length", "m", 6.1, 6.1, 6.1, 6.1, [4.0, 7.0], source="D"),
            _c("04", "Rod Mill", "Installed power", "kW", 2500, 2500, 2500, 2500, [1000, 4000], source="D"),
            _c("05", "Rod Mill", "Product P80", "um", 1000, 900, 900, 900, [500, 2000], source="D"),
            _c("06", "Rod Mill", "Rod consumption", "kg/t", 0.5, 0.5, 0.5, 0.5, [0.2, 1.0], source="D"),
        ],
    },
    {
        "op_code": "VERTIMILL",
        "category": "broyage",
        "label": "Vertical stirred mill",
        "sort_order": 240,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Vertimill — Paramètres", "Débit alimentation (Cyclone OF primaire)", "t/h", 1500, 1500, 1500, 1500, [500, 3000], source="D"),
            _c("02", "Vertimill — Paramètres", "F80 alimentation (P80 ball mill)", "µm", 100, 100, 100, 100, [63, 200], source="D"),
            _c("03", "Vertimill — Paramètres", "P80 cible (Cyclone OF secondaire)", "µm", 38, 38, 38, 38, [20, 75], source="D"),
            _c("04", "Vertimill — Paramètres", "Bond BWi effectif (signature plot)", "kWh/t", 16, 16, 16, 16, [12, 22], source="L"),
            _c("05", "Vertimill — Paramètres", "Facteur efficacité Vertimill f_v", "-", 0.70, 0.70, 0.70, 0.70, [0.65, 0.75], source="D"),
            _c("06", "Vertimill — Paramètres", "Énergie spécifique E_v = W_Bond × f_v", "kWh/t", 22, 22, 22, 22, [10, 40], source="C"),
            _c("07", "Vertimill — Paramètres", "Puissance arbre requise", "kW", 33000, 33000, 33000, 33000, [1500, 50000], source="D"),
            _c("08", "Vertimill — Paramètres", "Rendement moteur", "%", 95, 95, 95, 95, [92, 97], source="D"),
            _c("09", "Vertimill — Paramètres", "PUISSANCE INSTALLÉE (×1.10 marge)", "kW", 36300, 36300, 36300, 36300, [1700, 55000], source="D"),
            _c("10", "Vertimill — Paramètres", "Modèle suggéré (total ≈ 10 080 kW)", "-", None, None, None, None, [], source="M"),
            _c("11", "Vertimill — Paramètres", "Top size boulet Vertimill", "mm", 25, 25, 25, 25, [19, 32], source="D"),
            _c("12", "Vertimill — Paramètres", "Charge média (% volume)", "%", 80, 80, 80, 80, [75, 85], source="D"),
        ],
    },

    # ------------------------------------------------------------------
    # CLASSIFICATION  (300-399)
    # ------------------------------------------------------------------
    {
        "op_code": "HYDROCYCLONE",
        "category": "classification",
        "label": "Hydrocyclone cluster",
        "sort_order": 310,
        "dependencies": ["BALL_MILL"],
        "lims_triggers": {},
        "default_criteria": [
            # ── 1. Hydrocyclones primaires ─────────────────────────────────────
            _c("01", "Cyclone primaire — Alimentation", "Débit fresh feed broyage", "t/h", 1500, 1500, 1500, 1500, [500, 3000], source="P"),
            _c("02", "Cyclone primaire — Alimentation", "Charge circulante CL (std 250-350% ball mill)", "%", 280, 280, 280, 280, [200, 400], source="D", dag_key="bm_circ_load_pct"),
            _c("03", "Cyclone primaire — Alimentation", "Débit feed cyclone M_solides = Fresh×(1+CL)", "t/h", 5700, 5700, 5700, 5700, [1500, 12000], source="C", dag_key="cyc_feed_tph"),
            _c("04", "Cyclone primaire — Alimentation", "% solides feed cyclone (w/w)", "%", 65, 65, 65, 65, [55, 70], source="D"),
            _c("05", "Cyclone primaire — Alimentation", "SG minerai", "t/m³", 2.75, 2.75, 2.75, 2.75, [2.5, 3.0], source="D", dag_key="ore_sg"),
            _c("06", "Cyclone primaire — Alimentation", "Débit solides", "t/h", 5700, 5700, 5700, 5700, [1500, 12000], source="D"),
            _c("07", "Cyclone primaire — Alimentation", "Débit liquide = Solides × (1-Cs)/Cs", "t/h", 3070, 3070, 3070, 3070, [800, 6500], source="C"),
            _c("08", "Cyclone primaire — Alimentation", "Débit volumique solides = Masse/SG", "m³/h", 2070, 2070, 2070, 2070, [550, 4400], source="C"),
            _c("09", "Cyclone primaire — Alimentation", "Débit volumique liquide (ρ_eau=1.0)", "m³/h", 3070, 3070, 3070, 3070, [800, 6500], source="D"),
            _c("10", "Cyclone primaire — Alimentation", "Débit volumique pulpe total Q_v feed", "m³/h", 5140, 5140, 5140, 5140, [1350, 10900], source="D"),
            # ── Géométrie & sizing primaire ────────────────────────────────────
            _c("11", "Cyclone primaire — Sizing", "Cible d50c (cut size, ≈ P80/1.5 — Plitt 1976)", "µm", 65, 65, 65, 65, [40, 100], source="D"),
            _c("12", "Cyclone primaire — Sizing", "Diamètre cyclone D_c (Krebs gMAX26 / Cavex)", "mm", 660, 660, 660, 660, [250, 800], source="D"),
            _c("13", "Cyclone primaire — Sizing", "Capacité unitaire (à 80-100 kPa nominal)", "m³/h", 510, 510, 510, 510, [200, 800], source="D"),
            _c("14", "Cyclone primaire — Sizing", "Pression opérationnelle", "kPa", 90, 90, 90, 90, [70, 120], source="D"),
            _c("15", "Cyclone primaire — Sizing", "Nombre cyclones opérationnels = Q_v/cap unitaire", "-", 10, 10, 10, 10, [4, 24], source="C"),
            _c("16", "Cyclone primaire — Sizing", "Standby (N+1)", "-", 2, 2, 2, 2, [1, 4], source="D"),
            _c("17", "Cyclone primaire — Sizing", "Total cyclones cluster (op + standby)", "-", 12, 12, 12, 12, [5, 28], source="D"),
            _c("18", "Cyclone primaire — Sizing", "Configuration (1 ou 2 selon nombre)", "-", None, None, None, None, [], source="D"),
            # ── 2. Hydrocyclones secondaires (post-Vertimill) ──────────────────
            _c("19", "Cyclone secondaire — Alimentation", "Débit fresh feed Vertimill", "t/h", 1500, 1500, 1500, 1500, [500, 3000], source="P"),
            _c("20", "Cyclone secondaire — Alimentation", "Charge circulante CL (std 150-250% Vertimill)", "%", 200, 200, 200, 200, [120, 300], source="D"),
            _c("21", "Cyclone secondaire — Alimentation", "Débit feed cyclone secondaire", "t/h", 4500, 4500, 4500, 4500, [1100, 9000], source="D"),
            _c("22", "Cyclone secondaire — Alimentation", "% solides feed (w/w — plus fluide pour fines)", "%", 55, 55, 55, 55, [45, 65], source="D"),
            _c("23", "Cyclone secondaire — Alimentation", "Débit volumique total", "m³/h", 4900, 4900, 4900, 4900, [1200, 10000], source="D"),
            _c("24", "Cyclone secondaire — Sizing", "Cible d50c (pour P80 OF)", "µm", 25, 25, 25, 25, [15, 50], source="D"),
            _c("25", "Cyclone secondaire — Sizing", "Diamètre cyclone D_c (gMAX10 / petit HP)", "mm", 250, 250, 250, 250, [150, 400], source="D"),
            _c("26", "Cyclone secondaire — Sizing", "Capacité unitaire (pression élevée)", "m³/h", 80, 80, 80, 80, [40, 150], source="D"),
            _c("27", "Cyclone secondaire — Sizing", "Pression opérationnelle (std 150-180 kPa)", "kPa", 165, 165, 165, 165, [120, 200], source="D"),
            _c("28", "Cyclone secondaire — Sizing", "Nombre cyclones opérationnels", "-", 60, 60, 60, 60, [20, 120], source="D"),
            _c("29", "Cyclone secondaire — Sizing", "Configuration clusters (répartis maintenance)", "-", None, None, None, None, [], source="M"),
            # ── 3. Pompes d'alimentation cyclones ──────────────────────────────
            _c("30", "Cyclone — Pompes alimentation", "TDH cyclone primaire (≈ pression + pertes)", "m", 35, 35, 35, 35, [20, 60], source="D"),
            _c("31", "Cyclone — Pompes alimentation", "Densité pulpe (Cs en %)", "t/m³", 1.55, 1.55, 1.55, 1.55, [1.3, 1.8], source="D"),
            _c("32", "Cyclone — Pompes alimentation", "Puissance hydraulique = Q×ρ×g×H/3600", "kW", 750, 750, 750, 750, [200, 2500], source="C"),
            _c("33", "Cyclone — Pompes alimentation", "Rendement pompe η pulpe typique", "%", 65, 65, 65, 65, [55, 75], source="D"),
            _c("34", "Cyclone — Pompes alimentation", "Marge moteur", "%", 15, 15, 15, 15, [10, 25], source="D"),
            _c("35", "Cyclone — Pompes alimentation", "PUISSANCE pompe primaire", "kW", 1330, 1330, 1330, 1330, [350, 4000], source="D"),
            _c("36", "Cyclone — Pompes alimentation", "Product P80 (cyclone OF)", "µm", 100, 90, 90, 90, [63, 150], source="D"),
        ],
    },
    {
        "op_code": "CRIBLE_CLASS",
        "category": "classification",
        "label": "Classification screen",
        "sort_order": 320,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Classification Screen", "Screen type", "-", None, None, None, None, [], source="D"),
            _c("02", "Classification Screen", "Aperture", "mm", 0.5, 0.5, 0.5, 0.5, [0.1, 2.0], source="D"),
            _c("03", "Classification Screen", "Screen area", "m2", 15, 15, 15, 15, [5, 40], source="D"),
            _c("04", "Classification Screen", "Screening efficiency", "%", 85, 88, 88, 88, [70, 95], source="D"),
        ],
    },

    # ------------------------------------------------------------------
    # REBROYAGE  (400-499)
    # ------------------------------------------------------------------
    {
        "op_code": "ISAMILL",
        "category": "rebroyage",
        "label": "IsaMill ultra-fine grinding",
        "sort_order": 410,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "IsaMill", "Model", "-", None, None, None, None, [], source="D"),
            _c("02", "IsaMill", "Specific energy", "kWh/t", 30, 28, 28, 28, [10, 60], source="D"),
            _c("03", "IsaMill", "Feed P80", "um", 75, 75, 75, 75, [30, 150], source="D"),
            _c("04", "IsaMill", "Product P80", "um", 12, 10, 10, 10, [5, 25], source="D"),
            _c("05", "IsaMill", "Feed density", "% w/w", 35, 35, 35, 35, [25, 50], source="D"),
            _c("06", "IsaMill", "Installed power", "kW", 3000, 3000, 3000, 3000, [1100, 3000], source="D"),
            _c("07", "IsaMill", "Media size", "mm", 2.5, 2.5, 2.5, 2.5, [1.0, 4.0], source="D"),
            _c("08", "IsaMill", "Media consumption", "kg/t", 0.5, 0.5, 0.5, 0.5, [0.2, 1.2], source="D"),
        ],
    },
    {
        "op_code": "VERTIMILL_REGRIND",
        "category": "rebroyage",
        "label": "Vertimill for regrind",
        "sort_order": 420,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Vertimill Regrind — Feed", "Regrind circuit feed", "t/h", 95, 95, 95, 95, [10, 450], source="C", dag_key="regrind_feed_tph"),
            _c("02", "Vertimill Regrind — Feed", "Source stream (rougher/cleaner concentrate or full stream)", "-", None, None, None, None, [], source="M"),
            _c("03", "Vertimill Regrind — Feed", "Feed P80", "um", 106, 100, 100, 100, [50, 250], source="D", dag_key="regrind_feed_p80_um"),
            _c("04", "Vertimill Regrind — Feed", "Product P80", "um", 25, 25, 25, 25, [10, 50], source="D", dag_key="regrind_product_p80_um"),
            _c("05", "Vertimill Regrind — Feed", "Feed density (% solids)", "% w/w", 40, 40, 40, 40, [30, 50], source="D"),
            _c("06", "Vertimill Regrind — Energy", "Signature plot specific intensity", "kWh/t", 7.5, 7.5, 7.5, 7.5, [4, 14], source="L", dag_key="regrind_sig_kwh_t"),
            _c("07", "Vertimill Regrind — Energy", "Specific energy E = Sig × ln(F80/P80)", "kWh/t", 10.8, 10.8, 10.8, 10.8, [5, 30], source="C", dag_key="regrind_specific_energy_kwh_t"),
            _c("08", "Vertimill Regrind — Energy", "Shaft power required", "kW", 1025, 1025, 1025, 1025, [100, 12000], source="C", dag_key="regrind_shaft_power_kw"),
            _c("09", "Vertimill Regrind — Energy", "Mechanical efficiency", "%", 94, 94, 94, 94, [90, 97], source="D", dag_key="regrind_mech_efficiency"),
            _c("10", "Vertimill Regrind — Energy", "Installation margin", "%", 15, 15, 15, 15, [10, 25], source="D", dag_key="regrind_install_margin_pct"),
            _c("11", "Vertimill Regrind — Energy", "Installed power", "kW", 1255, 1255, 1255, 1255, [250, 15000], source="C", dag_key="regrind_installed_power_kw"),
            _c("12", "Vertimill Regrind — Configuration", "Recommended Vertimill model", "-", None, None, None, None, [], source="M"),
            _c("13", "Vertimill Regrind — Configuration", "Number of operating mills", "-", 1, 1, 1, 1, [1, 6], source="C"),
            _c("14", "Vertimill Regrind — Configuration", "Installed power per mill", "kW", 1255, 1255, 1255, 1255, [250, 5000], source="C"),
            _c("15", "Vertimill Regrind — Media", "Media size", "mm", 12, 12, 12, 12, [6, 25], source="D"),
            _c("16", "Vertimill Regrind — Media", "Media consumption", "kg/t", 0.6, 0.6, 0.6, 0.6, [0.2, 1.2], source="D"),
            _c("17", "Vertimill Regrind — Classification", "Circulating load", "%", 200, 200, 200, 200, [100, 350], source="D", dag_key="regrind_recirc_pct"),
            _c("18", "Vertimill Regrind — Classification", "Cyclone overflow % solids", "% w/w", 25, 25, 25, 25, [20, 35], source="D"),
            _c("19", "Vertimill Regrind — Classification", "Cyclone underflow % solids", "% w/w", 55, 55, 55, 55, [45, 65], source="D"),
            _c("20", "Vertimill Regrind — Water", "Water addition", "m3/h", 10, 10, 10, 10, [0, 150], source="D"),
            _c("21", "Vertimill Regrind — Water", "Gland seal water", "m3/h", 0.66, 0.66, 0.66, 0.66, [0.2, 2.0], source="D"),
        ],
    },
    {
        "op_code": "SMD",
        "category": "rebroyage",
        "label": "Stirred Media Detritor",
        "sort_order": 430,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "SMD", "Feed P80", "um", 75, 75, 75, 75, [30, 150], source="D"),
            _c("02", "SMD", "Product P80", "um", 15, 12, 12, 12, [5, 30], source="D"),
            _c("03", "SMD", "Specific energy", "kWh/t", 25, 25, 25, 25, [10, 50], source="D"),
            _c("04", "SMD", "Installed power", "kW", 1100, 1100, 1100, 1100, [355, 1100], source="D"),
            _c("05", "SMD", "Media type", "-", None, None, None, None, [], source="D"),
        ],
    },

    # ------------------------------------------------------------------
    # CONCENTRATION  (500-599)
    # ------------------------------------------------------------------
    {
        "op_code": "FLOTATION_ROUGHER",
        "category": "concentration",
        "label": "Rougher flotation",
        "sort_order": 510,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            # ── 1. Applicabilité et alimentation ──────────────────────────────
            _c("01", "Rougher — Alimentation", "Circuit flottation activé ? (sulfuré/réfractaire)", "-", None, None, None, None, [], source="M"),
            _c("02", "Rougher — Alimentation", "Débit alimentation flottation", "t/h", 1500, 1500, 1500, 1500, [500, 3000], source="D"),
            _c("03", "Rougher — Alimentation", "% solides feed (std rougher)", "%", 35, 35, 35, 35, [25, 45], source="D"),
            _c("04", "Rougher — Alimentation", "P80 alim flottation (cible)", "µm", 75, 75, 75, 75, [40, 150], source="D"),
            _c("05", "Rougher — Alimentation", "Densité pulpe", "t/m³", 1.30, 1.30, 1.30, 1.30, [1.2, 1.5], source="D"),
            _c("06", "Rougher — Alimentation", "Débit volumique pulpe Q_v", "m³/h", 3300, 3300, 3300, 3300, [800, 7000], source="D"),
            _c("07", "Rougher — Alimentation", "Teneur tête Au", "g/t", 1.5, 1.5, 1.5, 1.5, [0.5, 10], source="P", dag_key="gold_grade_g_t"),
            _c("08", "Rougher — Alimentation", "Teneur tête S", "%", 5.0, 5.0, 5.0, 5.0, [1, 20], source="D"),
            # ── 2. Rougher (dégrossisseuse) ───────────────────────────────────
            _c("09", "Rougher — Dégrossisseuse", "Temps résidence rougher (std 10-20 min)", "min", 15, 15, 15, 15, [10, 20], source="D"),
            _c("10", "Rougher — Dégrossisseuse", "Volume pulpe rougher (Q × t / 60)", "m³", 825, 825, 825, 825, [200, 2300], source="C"),
            _c("11", "Rougher — Dégrossisseuse", "Facteur foisonnement (pulpe + air)", "-", 1.20, 1.20, 1.20, 1.20, [1.10, 1.30], source="D"),
            _c("12", "Rougher — Dégrossisseuse", "Volume cellule unitaire (std 100-300 m³)", "m³", 200, 200, 200, 200, [100, 300], source="D"),
            _c("13", "Rougher — Dégrossisseuse", "Nombre cellules rougher (min 5-7 série)", "-", 6, 6, 6, 6, [5, 12], source="D"),
            _c("14", "Rougher — Dégrossisseuse", "Type cellule (mécanique/colonne)", "-", None, None, None, None, [], source="M"),
            _c("15", "Rougher — Dégrossisseuse", "Mass pull rougher", "%", 12, 12, 12, 12, [5, 25], source="D", dag_key="flot_mass_pull_pct"),
            _c("16", "Rougher — Dégrossisseuse", "Concentrate grade Au", "g/t", 12, 12, 12, 12, [5, 50], source="D"),
            _c("17", "Rougher — Dégrossisseuse", "Récupération Au rougher", "%", 92, 93, 93, 93, [85, 97], source="D", dag_key="avg_au_recovery_pct"),
            _c("18", "Rougher — Dégrossisseuse", "PAX addition", "g/t", 80, 80, 80, 80, [20, 200], source="D"),
            _c("19", "Rougher — Dégrossisseuse", "MIBC addition", "g/t", 25, 25, 25, 25, [10, 60], source="D"),
        ],
    },
    {
        "op_code": "FLOTATION_SCAVENGER",
        "category": "concentration",
        "label": "Scavenger flotation",
        "sort_order": 520,
        "dependencies": ["FLOTATION_ROUGHER"],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Scavenger — Récupératrice", "Temps résidence scavenger (std 8-15 min)", "min", 12, 12, 12, 12, [8, 15], source="D"),
            _c("02", "Scavenger — Récupératrice", "Volume pulpe scavenger (Q×t/60)", "m³", 660, 660, 660, 660, [200, 1800], source="C"),
            _c("03", "Scavenger — Récupératrice", "Facteur foisonnement (aération)", "-", 1.20, 1.20, 1.20, 1.20, [1.10, 1.30], source="D"),
            _c("04", "Scavenger — Récupératrice", "Volume cellule scavenger", "m³", 150, 150, 150, 150, [50, 300], source="D"),
            _c("05", "Scavenger — Récupératrice", "Nombre cellules scavenger", "-", 5, 5, 5, 5, [3, 10], source="D"),
            _c("06", "Scavenger — Récupératrice", "Mass pull scavenger", "%", 5, 5, 5, 5, [2, 15], source="D"),
            _c("07", "Scavenger — Récupératrice", "Récup Au rougher attendue (combinée)", "%", 95, 95, 95, 95, [88, 98], source="L"),
            _c("08", "Scavenger — Récupératrice", "PAX addition", "g/t", 30, 30, 30, 30, [10, 80], source="D"),
            _c("09", "Scavenger — Récupératrice", "MIBC addition", "g/t", 10, 10, 10, 10, [5, 25], source="D"),
        ],
    },
    {
        "op_code": "FLOTATION_CLEANER",
        "category": "concentration",
        "label": "Cleaner flotation",
        "sort_order": 530,
        "dependencies": ["FLOTATION_ROUGHER"],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Cleaner — Nettoyage", "Ratio masse concentré rougher (mass pull)", "%", 12, 12, 12, 12, [5, 25], source="D"),
            _c("02", "Cleaner — Nettoyage", "Débit concentré rougher", "t/h", 180, 180, 180, 180, [30, 500], source="D"),
            _c("03", "Cleaner — Nettoyage", "% solides cleaner feed", "%", 25, 25, 25, 25, [20, 35], source="D"),
            _c("04", "Cleaner — Nettoyage", "Temps résidence cleaner", "min", 15, 15, 15, 15, [8, 30], source="D"),
            _c("05", "Cleaner — Nettoyage", "Volume cellule cleaner", "m³", 30, 30, 30, 30, [10, 100], source="D"),
            _c("06", "Cleaner — Nettoyage", "Nombre étages cleaner (cleaner/recleaner)", "-", 2, 2, 2, 2, [1, 4], source="D"),
            _c("07", "Cleaner — Nettoyage", "Cellules par étage", "-", 4, 4, 4, 4, [2, 8], source="D"),
            _c("08", "Cleaner — Nettoyage", "Mass pull cleaner", "%", 8, 8, 8, 8, [3, 20], source="D"),
            _c("09", "Cleaner — Nettoyage", "Récupération Au cleaner", "%", 96, 96, 96, 96, [90, 99], source="D"),
            _c("10", "Cleaner — Nettoyage", "Concentrate grade Au final", "g/t", 25, 25, 25, 25, [10, 80], source="D"),
            _c("11", "Cleaner — Nettoyage", "Concentrate grade S final", "%", 30, 30, 30, 30, [15, 45], source="D"),
        ],
    },
    {
        "op_code": "FLOTATION_COLONNE",
        "category": "concentration",
        "label": "Column flotation",
        "sort_order": 540,
        "dependencies": ["FLOTATION_CLEANER"],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Column Flotation", "Column diameter", "m", 2.5, 2.5, 2.5, 2.5, [1.0, 4.0], source="D"),
            _c("02", "Column Flotation", "Column height", "m", 12, 12, 12, 12, [8, 16], source="D"),
            _c("03", "Column Flotation", "Number of columns", "-", 2, 2, 2, 2, [1, 6], source="D"),
            _c("04", "Column Flotation", "Wash water rate", "m3/m2/h", 0.4, 0.4, 0.4, 0.4, [0.2, 0.8], source="D"),
            _c("05", "Column Flotation", "Air rate", "cm/s", 1.5, 1.5, 1.5, 1.5, [0.5, 3.0], source="D"),
            _c("06", "Column Flotation", "Bias factor", "-", 0.2, 0.2, 0.2, 0.2, [0.05, 0.5], source="D"),
        ],
    },
    {
        "op_code": "GRAVITE_KNELSON",
        "category": "concentration",
        "label": "Knelson gravity concentrator",
        "sort_order": 550,
        "dependencies": [],
        "lims_triggers": {
            "condition": "c2.grg_rec_pct > 20",
            "field": "grg_rec_pct",
            "table": "lims_c2",
            "operator": ">",
            "threshold": 20,
        },
        "default_criteria": [
            # ── 1. Paramètres GRG ─────────────────────────────────────────────
            _c("01", "Gravité — Paramètres GRG", "Débit fresh feed broyage", "t/h", 1500, 1500, 1500, 1500, [500, 3000], source="P"),
            _c("02", "Gravité — Paramètres GRG", "% UF cyclone détourné gravimétrie (std 20-40%)", "%", 30, 30, 30, 30, [20, 40], source="D"),
            _c("03", "Gravité — Paramètres GRG", "Débit feed gravimétrie (UF cyclone × % détourné)", "t/h", 450, 450, 450, 450, [100, 1200], source="C"),
            _c("04", "Gravité — Paramètres GRG", "GRG dans minerai (test Knelson lab)", "%", 35, 35, 35, 35, [10, 80], source="L", dag_key="avg_grg_pct"),
            _c("05", "Gravité — Paramètres GRG", "Teneur Au alim. brute (head grade)", "g/t", 1.5, 1.5, 1.5, 1.5, [0.5, 10], source="P"),
            _c("06", "Gravité — Paramètres GRG", "Récupération unitaire Knelson (par passage)", "%", 50, 50, 50, 50, [30, 75], source="L"),
            # ── 2. Dimensionnement Knelson / Falcon ───────────────────────────
            _c("07", "Gravité — Dimensionnement Knelson", "Type concentrateur (Knelson CVD / Falcon SB)", "-", None, None, None, None, [], source="M"),
            _c("08", "Gravité — Dimensionnement Knelson", "Capacité unitaire (KC-XD48, à 30-40% solides)", "t/h", 250, 250, 250, 250, [50, 400], source="D"),
            _c("09", "Gravité — Dimensionnement Knelson", "Modèle", "-", None, None, None, None, [], source="M"),
            _c("10", "Gravité — Dimensionnement Knelson", "Nombre concentrateurs", "-", 2, 2, 2, 2, [1, 6], source="D"),
            _c("11", "Gravité — Dimensionnement Knelson", "Standby (N+1)", "-", 1, 1, 1, 1, [1, 2], source="D"),
            _c("12", "Gravité — Dimensionnement Knelson", "Total installé", "-", 3, 3, 3, 3, [2, 8], source="D"),
            # ── 3. Circuit ILR / Acacia (lixiviation intensive concentré grav.) ─
            _c("13", "Gravité — ILR / Acacia", "Masse conc. grav./jour (ratio enrich. ~1000:1)", "t/j", 12, 12, 12, 12, [2, 50], source="D"),
            _c("14", "Gravité — ILR / Acacia", "Volume cuve ILR / Acacia (selon production)", "m³", 8, 8, 8, 8, [2, 30], source="C"),
            _c("15", "Gravité — ILR / Acacia", "Temps cycle leach intensif", "h", 18, 18, 18, 18, [12, 36], source="D"),
            _c("16", "Gravité — ILR / Acacia", "[NaCN] solution leach (30 g/L — élevé)", "ppm", 30000, 30000, 30000, 30000, [20000, 50000], source="D"),
            _c("17", "Gravité — ILR / Acacia", "Récupération Au sur conc. (lixiv. forte)", "%", 95, 95, 95, 95, [88, 99], source="D"),
            _c("18", "Gravité — ILR / Acacia", "Récupération circuit grav. global = GRG×η_unit×η_ILR", "%", 17, 17, 17, 17, [5, 50], source="C"),
        ],
    },
    {
        "op_code": "GRAVITE_FALCON",
        "category": "concentration",
        "label": "Falcon gravity concentrator",
        "sort_order": 560,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Falcon Concentrator", "Model", "-", None, None, None, None, [], source="D"),
            _c("02", "Falcon Concentrator", "Bowl size", "inch", 24, 24, 24, 24, [12, 40], source="D"),
            _c("03", "Falcon Concentrator", "Feed rate", "t/h", 80, 80, 80, 80, [10, 200], source="D"),
            _c("04", "Falcon Concentrator", "G-force", "G", 200, 200, 200, 200, [60, 300], source="D"),
            _c("05", "Falcon Concentrator", "Recovery Au", "%", 40, 40, 40, 40, [15, 70], source="D"),
            _c("06", "Falcon Concentrator", "Bowl water", "L/min", 300, 300, 300, 300, [80, 500], source="D"),
        ],
    },

    # ------------------------------------------------------------------
    # PRETRAITEMENT  (600-699)
    # ------------------------------------------------------------------
    {
        "op_code": "BIOX",
        "category": "pretraitement",
        "label": "Bio-oxidation (BIOX)",
        "sort_order": 610,
        "dependencies": [],
        "lims_triggers": {
            "condition": "a1.s_sulfide_pct > 5 AND d1.leach_rec_48h_pct < 80",
            "field": "s_sulfide_pct",
            "table": "lims_a1",
            "operator": ">",
            "threshold": 5,
        },
        "default_criteria": [
            _c("01", "BIOX", "Feed rate", "t/h", 182, 182, 182, 182, [50, 500], source="D"),
            _c("02", "BIOX", "Residence time", "d", 5, 5, 5, 5, [3, 7], source="D"),
            _c("03", "BIOX", "Number of reactors", "-", 6, 6, 6, 6, [3, 9], source="D"),
            _c("04", "BIOX", "Reactor volume", "m3", 900, 900, 900, 900, [300, 1500], source="D"),
            _c("05", "BIOX", "Sulphide oxidation", "%", 95, 95, 95, 95, [85, 99], source="D"),
            _c("06", "BIOX", "Nutrient dosage N", "kg/t", 1.5, 1.5, 1.5, 1.5, [0.5, 3.0], source="D"),
            _c("07", "BIOX", "Operating temperature", "°C", 42, 42, 42, 42, [35, 50], source="D"),
            _c("08", "BIOX", "Cooling water", "m3/h", 500, 500, 500, 500, [100, 1200], source="D"),
        ],
    },
    {
        "op_code": "POX",
        "category": "pretraitement",
        "label": "Pressure oxidation",
        "sort_order": 620,
        "dependencies": [],
        "lims_triggers": {
            "condition": "a1.c_organic_pct > 0.3",
            "field": "c_organic_pct",
            "table": "lims_a1",
            "operator": ">",
            "threshold": 0.3,
        },
        "default_criteria": [
            _c("01", "POX", "Feed rate", "t/h", 182, 182, 182, 182, [50, 500], source="D"),
            _c("02", "POX", "Operating pressure", "kPa", 3200, 3200, 3200, 3200, [2000, 4500], source="D"),
            _c("03", "POX", "Operating temperature", "°C", 220, 220, 220, 220, [180, 240], source="D"),
            _c("04", "POX", "Residence time", "min", 90, 90, 90, 90, [45, 180], source="D"),
            _c("05", "POX", "Autoclave volume", "m3", 250, 250, 250, 250, [80, 500], source="D"),
            _c("06", "POX", "Sulphide oxidation", "%", 98, 98, 98, 98, [90, 99.5], source="D"),
        ],
    },
    {
        "op_code": "ROASTING",
        "category": "pretraitement",
        "label": "Roasting",
        "sort_order": 630,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Roasting", "Feed rate", "t/h", 150, 150, 150, 150, [50, 400], source="D"),
            _c("02", "Roasting", "Kiln temperature", "°C", 650, 650, 650, 650, [500, 800], source="D"),
            _c("03", "Roasting", "Residence time", "min", 60, 60, 60, 60, [30, 120], source="D"),
            _c("04", "Roasting", "Sulphide oxidation", "%", 95, 95, 95, 95, [85, 99], source="D"),
            _c("05", "Roasting", "Off-gas treatment", "-", None, None, None, None, [], source="D"),
        ],
    },
    {
        "op_code": "UFG",
        "category": "pretraitement",
        "label": "Ultra-fine grinding pretreatment",
        "sort_order": 640,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "UFG Pretreatment", "Feed P80", "um", 75, 75, 75, 75, [40, 150], source="D"),
            _c("02", "UFG Pretreatment", "Product P80", "um", 10, 10, 10, 10, [5, 20], source="D"),
            _c("03", "UFG Pretreatment", "Specific energy", "kWh/t", 40, 40, 40, 40, [15, 80], source="D"),
            _c("04", "UFG Pretreatment", "Installed power", "kW", 3000, 3000, 3000, 3000, [1100, 6000], source="D"),
        ],
    },

    # ------------------------------------------------------------------
    # EPAISSISSEMENT  (700-799)
    # ------------------------------------------------------------------
    {
        "op_code": "EPAISSISSEUR",
        "category": "epaississement",
        "label": "Conventional thickener",
        "sort_order": 710,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            # ── Épaississeur pré-leach (option, souvent inutile en CIL) ───────
            _c("01", "Épaississeur pré-leach", "Activé ? (Si CIL: souvent inutile)", "-", None, None, None, None, [], source="D"),
            _c("02", "Épaississeur pré-leach", "Type (HRT compact + floculant)", "-", None, None, None, None, [], source="D"),
            _c("03", "Épaississeur pré-leach", "Débit solides", "t/h", 1500, 1500, 1500, 1500, [500, 3000], source="D"),
            _c("04", "Épaississeur pré-leach", "% solides UF cible (densif. avant leach)", "%", 50, 50, 50, 50, [40, 60], source="D"),
            _c("05", "Épaississeur pré-leach", "Solids Loading Rate SLR (avec floculant)", "t/(m²·h)", 1.0, 1.0, 1.0, 1.0, [0.8, 1.2], source="D", dag_key="avg_unit_area"),
            _c("06", "Épaississeur pré-leach", "Surface unitaire requise = Débit / SLR", "m²", 1500, 1500, 1500, 1500, [400, 4000], source="C", dag_key="thickener_area_m2"),
            _c("07", "Épaississeur pré-leach", "Diamètre épaississeur", "m", 44, 44, 44, 44, [22, 75], source="D", dag_key="thickener_diameter_m"),
            # ── Floculant (aussi applicable ici) ──────────────────────────────
            _c("08", "Épaississeur pré-leach", "Dosage floculant (PAM anionique 20-40 g/t)", "g/t", 30, 30, 30, 30, [20, 40], source="D"),
            _c("09", "Épaississeur pré-leach", "Consommation par jour", "kg/j", 1080, 1080, 1080, 1080, [300, 3000], source="D"),
            _c("10", "Épaississeur pré-leach", "Consommation annuelle", "t/an", 380, 380, 380, 380, [100, 1100], source="D"),
        ],
    },
    {
        "op_code": "EPAISSISSEUR_HD",
        "category": "epaississement",
        "label": "High-density thickener",
        "sort_order": 720,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            # ── Épaississeur de résidus (tailings) ────────────────────────────
            _c("01", "Tailings — Épaississeur HD", "Débit solides résidus", "t/h", 1500, 1500, 1500, 1500, [500, 3000], source="D"),
            _c("02", "Tailings — Épaississeur HD", "% solides feed (post-CIL)", "%", 45, 45, 45, 45, [35, 55], source="D"),
            _c("03", "Tailings — Épaississeur HD", "% solides UF cible (std 55-65% pour pompage)", "%", 60, 60, 60, 60, [55, 65], source="D", dag_key="underflow_pct_solids"),
            _c("04", "Tailings — Épaississeur HD", "% solides OF clarté (≈ 50 ppm MES)", "%", 0.005, 0.005, 0.005, 0.005, [0.001, 0.05], source="D"),
            _c("05", "Tailings — Épaississeur HD", "SLR design avec floculant (prudent fines)", "t/(m²·h)", 0.8, 0.8, 0.8, 0.8, [0.5, 1.0], source="D"),
            _c("06", "Tailings — Épaississeur HD", "Surface requise", "m²", 1875, 1875, 1875, 1875, [500, 5000], source="D"),
            _c("07", "Tailings — Épaississeur HD", "Diamètre", "m", 49, 49, 49, 49, [25, 80], source="D"),
            _c("08", "Tailings — Épaississeur HD", "Hauteur paroi cylindrique (std 4-6 m)", "m", 5, 5, 5, 5, [4, 6], source="D"),
            _c("09", "Tailings — Épaississeur HD", "Pente cône (std 10-15°)", "°", 12, 12, 12, 12, [10, 15], source="D"),
            _c("10", "Tailings — Épaississeur HD", "Couple mécanisme (1.5 × design, bridge type)", "kN·m", 5250, 5250, 5250, 5250, [1500, 12000], source="C"),
            # ── Floculant tailings ────────────────────────────────────────────
            _c("11", "Tailings — Floculant", "Dosage floculant (PAM anionique 20-40 g/t)", "g/t", 30, 30, 30, 30, [20, 40], source="D"),
            _c("12", "Tailings — Floculant", "Consommation par jour", "kg/j", 1080, 1080, 1080, 1080, [300, 3000], source="D"),
            _c("13", "Tailings — Floculant", "Consommation annuelle", "t/an", 380, 380, 380, 380, [100, 1100], source="D"),
            # ── Filtration (dry stack tailings) ───────────────────────────────
            _c("14", "Tailings — Filtration dry stack", "Filtration activée ? (dry stack vs slurry TSF)", "-", None, None, None, None, [], source="D"),
            _c("15", "Tailings — Filtration dry stack", "% solides UF feed filtre (sortie épaississeur)", "%", 60, 60, 60, 60, [55, 65], source="D"),
            _c("16", "Tailings — Filtration dry stack", "% humidité gâteau cible (std 15-22%)", "%", 18, 18, 18, 18, [15, 22], source="D"),
            _c("17", "Tailings — Filtration dry stack", "Capacité spécifique filtre presse vertical", "kg/m²/h", 350, 350, 350, 350, [200, 600], source="D"),
            _c("18", "Tailings — Filtration dry stack", "Surface filtration totale", "m²", 4290, 4290, 4290, 4290, [1500, 10000], source="D"),
            _c("19", "Tailings — Filtration dry stack", "Type filtre suggéré (presse / belt)", "-", None, None, None, None, [], source="M"),
        ],
    },
    {
        "op_code": "EPAISSISSEUR_CONC",
        "category": "epaississement",
        "label": "Concentrate thickener",
        "sort_order": 730,
        "dependencies": ["FLOTATION_ROUGHER"],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Concentré — Épaississeur", "Type épaississeur (HRT compact + floculant)", "-", None, None, None, None, [], source="D"),
            _c("02", "Concentré — Épaississeur", "Débit solides (concentré flotation)", "t/h", 180, 180, 180, 180, [30, 500], source="D"),
            _c("03", "Concentré — Épaississeur", "% solides feed", "%", 20, 20, 20, 20, [10, 35], source="D"),
            _c("04", "Concentré — Épaississeur", "% solides UF cible (densif. avant leach)", "%", 55, 55, 55, 55, [45, 65], source="D"),
            _c("05", "Concentré — Épaississeur", "% solides OF clarté (≈ 50 ppm MES)", "%", 0.005, 0.005, 0.005, 0.005, [0.001, 0.05], source="D"),
            _c("06", "Concentré — Épaississeur", "Solids Loading Rate SLR", "t/(m²·h)", 0.6, 0.6, 0.6, 0.6, [0.3, 1.0], source="D"),
            _c("07", "Concentré — Épaississeur", "Surface unitaire requise", "m²", 300, 300, 300, 300, [80, 800], source="D"),
            _c("08", "Concentré — Épaississeur", "Diamètre", "m", 20, 20, 20, 20, [10, 35], source="D"),
            _c("09", "Concentré — Épaississeur", "Hauteur paroi cylindrique", "m", 5, 5, 5, 5, [4, 6], source="D"),
            _c("10", "Concentré — Épaississeur", "Couple mécanisme (1.5 × design)", "kN·m", 1800, 1800, 1800, 1800, [500, 4500], source="C"),
            _c("11", "Concentré — Épaississeur", "Dosage floculant", "g/t", 30, 30, 30, 30, [15, 60], source="D"),
            _c("12", "Concentré — Épaississeur", "Dosage coagulant", "g/t", 5, 5, 5, 5, [0, 15], source="D"),
        ],
    },

    # ------------------------------------------------------------------
    # LIXIVIATION  (800-899)
    # ------------------------------------------------------------------
    {
        "op_code": "PREAERATION",
        "category": "lixiviation",
        "label": "Pre-aeration tank",
        "sort_order": 805,
        "dependencies": ["EPAISSISSEUR"],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Pre-aeration", "Feed rate solids", "t/h", 1517, 1517, 1517, 1517, [500, 3000], source="D"),
            _c("02", "Pre-aeration", "Feed % solids", "% w/w", 55, 55, 55, 55, [45, 65], source="D"),
            _c("03", "Pre-aeration", "Slurry SG", "-", 1.55, 1.55, 1.55, 1.55, [1.3, 1.8], source="D"),
            _c("04", "Pre-aeration", "Number of tanks", "-", 2, 2, 2, 2, [1, 4], source="D"),
            _c("05", "Pre-aeration", "Residence time", "h", 4, 4, 4, 4, [2, 8], source="D"),
            _c("06", "Pre-aeration", "Total volume", "m3", 7200, 7200, 7200, 7200, [1000, 15000], source="D"),
            _c("07", "Pre-aeration", "Tank diameter", "m", 18, 18, 18, 18, [8, 25], source="D"),
            _c("08", "Pre-aeration", "Tank height", "m", 14, 14, 14, 14, [8, 20], source="D"),
            _c("09", "Pre-aeration", "Lime dosage", "kg/t", 3, 3, 3, 3, [1, 8], source="D"),
            _c("10", "Pre-aeration", "O2 dissolution", "mg/L", 8, 8, 8, 8, [4, 15], source="D"),
        ],
    },
    {
        "op_code": "LEACH_CUVES",
        "category": "lixiviation",
        "label": "Leach tanks (agitated)",
        "sort_order": 810,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            # ── 1. Alimentation et densité ────────────────────────────────────
            _c("01", "Leach — Alimentation et densité", "Débit alimentation solides (cyclone OF sec.)", "t/h", 1500, 1500, 1500, 1500, [500, 3000], source="D", dag_key="leach_feed_tph"),
            _c("02", "Leach — Alimentation et densité", "% solides leach (w/w, std 40-50% CIL)", "%", 45, 45, 45, 45, [40, 50], source="D", dag_key="cil_pct_solids"),
            _c("03", "Leach — Alimentation et densité", "SG minerai", "t/m³", 2.75, 2.75, 2.75, 2.75, [2.5, 3.0], source="D"),
            _c("04", "Leach — Alimentation et densité", "Densité pulpe", "t/m³", 1.45, 1.45, 1.45, 1.45, [1.3, 1.6], source="D", dag_key="slurry_sg"),
            _c("05", "Leach — Alimentation et densité", "Débit volumique pulpe Q_v", "m³/h", 2300, 2300, 2300, 2300, [800, 5000], source="D", dag_key="vol_flow_m3h"),
            _c("06", "Leach — Alimentation et densité", "P80 feed leach", "µm", 75, 75, 75, 75, [40, 150], source="D"),
            # ── 2. Cinétique et temps de résidence ────────────────────────────
            _c("07", "Leach — Cinétique", "Temps résidence total (cyanuration)", "h", 24, 24, 24, 24, [12, 48], source="D", dag_key="cil_srt_h"),
            _c("08", "Leach — Cinétique", "Constante cinétique k (1er ordre)", "1/h", 0.18, 0.18, 0.18, 0.18, [0.05, 0.40], source="D"),
            _c("09", "Leach — Cinétique", "Récupération attendue (théorique)", "%", 95, 95, 95, 95, [85, 99], source="D"),
            _c("10", "Leach — Cinétique", "Vérif: 1-exp(-k·t) (théorique mixed flow)", "%", 99, 99, 99, 99, [85, 99.5], source="D"),
            # ── 3. Dimensionnement réservoirs ─────────────────────────────────
            _c("11", "Leach — Dimensionnement", "Volume utile total requis (Q × t)", "m³", 55200, 55200, 55200, 55200, [10000, 100000], source="C", dag_key="cil_volume_m3"),
            _c("12", "Leach — Dimensionnement", "Marge volume (sécurité 20%)", "%", 20, 20, 20, 20, [15, 30], source="D"),
            _c("13", "Leach — Dimensionnement", "Volume design total", "m³", 66240, 66240, 66240, 66240, [12000, 120000], source="D"),
            _c("14", "Leach — Dimensionnement", "Nombre réservoirs (CIL std 6-10 série)", "-", 8, 8, 8, 8, [6, 12], source="D", dag_key="cil_n_tanks"),
            _c("15", "Leach — Dimensionnement", "Volume unitaire", "m³", 8280, 8280, 8280, 8280, [1000, 15000], source="D", dag_key="max_vol_per_tank"),
            _c("16", "Leach — Dimensionnement", "Hauteur tank H (std 12-16 m)", "m", 14, 14, 14, 14, [12, 16], source="D"),
            _c("17", "Leach — Dimensionnement", "Diamètre tank D = √(4V/πH)", "m", 27, 27, 27, 27, [10, 40], source="C", dag_key="cil_tank_diameter_m"),
            _c("18", "Leach — Dimensionnement", "Aspect ratio H/D (std 1.0-1.5)", "-", 1.2, 1.2, 1.2, 1.2, [1.0, 1.5], source="D", dag_key="cil_hd_ratio"),
            # ── 4. Agitation ──────────────────────────────────────────────────
            _c("19", "Leach — Agitation", "Puissance spécifique agitation (std 0.08-0.15 kW/m³)", "kW/m³", 0.10, 0.10, 0.10, 0.10, [0.08, 0.15], source="D"),
            _c("20", "Leach — Agitation", "Puissance par tank", "kW", 830, 830, 830, 830, [100, 2000], source="D"),
            _c("21", "Leach — Agitation", "Puissance totale agitation", "kW", 6630, 6630, 6630, 6630, [800, 16000], source="D"),
            _c("22", "Leach — Agitation", "Type agitateur (mixing + dispersion air)", "-", None, None, None, None, [], source="M"),
            # ── Réactifs ───────────────────────────────────────────────────────
            _c("23", "Leach — Réactifs", "NaCN dosage", "kg/t", 0.5, 0.5, 0.5, 0.5, [0.2, 2.0], source="D", dag_key="avg_nacn_kg_t"),
            _c("24", "Leach — Réactifs", "O₂ dissous", "mg/L", 8, 8, 8, 8, [4, 15], source="D"),
            _c("25", "Leach — Réactifs", "Lime dosage", "kg/t", 3, 3, 3, 3, [1, 8], source="D", dag_key="avg_cao_kg_t"),
        ],
    },
    {
        "op_code": "CIP",
        "category": "lixiviation",
        "label": "Carbon-in-Pulp",
        "sort_order": 820,
        "dependencies": ["LEACH_CUVES"],
        "lims_triggers": {},
        "default_criteria": [
            # ── Alimentation et densité (post-leach) ──────────────────────────
            _c("01", "CIP — Alimentation", "Débit alimentation solides (post-leach)", "t/h", 1500, 1500, 1500, 1500, [500, 3000], source="D"),
            _c("02", "CIP — Alimentation", "% solides feed (w/w)", "%", 45, 45, 45, 45, [40, 50], source="D"),
            _c("03", "CIP — Alimentation", "SG minerai", "t/m³", 2.75, 2.75, 2.75, 2.75, [2.5, 3.0], source="D"),
            _c("04", "CIP — Alimentation", "Densité pulpe", "t/m³", 1.45, 1.45, 1.45, 1.45, [1.3, 1.6], source="D"),
            _c("05", "CIP — Alimentation", "Débit volumique pulpe Q_v", "m³/h", 2300, 2300, 2300, 2300, [800, 5000], source="D"),
            # ── Cinétique CIP (séparée du leach) ───────────────────────────────
            _c("06", "CIP — Cinétique", "Temps résidence CIP", "h", 8, 8, 8, 8, [4, 16], source="D"),
            _c("07", "CIP — Cinétique", "Récupération attendue (charbon)", "%", 99, 99, 99, 99, [95, 99.9], source="D"),
            # ── Dimensionnement réservoirs CIP ─────────────────────────────────
            _c("08", "CIP — Dimensionnement", "Volume utile total requis", "m³", 18400, 18400, 18400, 18400, [3000, 40000], source="D"),
            _c("09", "CIP — Dimensionnement", "Marge volume (sécurité 20%)", "%", 20, 20, 20, 20, [15, 30], source="D"),
            _c("10", "CIP — Dimensionnement", "Volume design total", "m³", 22080, 22080, 22080, 22080, [3500, 48000], source="D"),
            _c("11", "CIP — Dimensionnement", "Nombre réservoirs CIP (4-8 série)", "-", 6, 6, 6, 6, [4, 10], source="D"),
            _c("12", "CIP — Dimensionnement", "Volume unitaire", "m³", 3680, 3680, 3680, 3680, [500, 7000], source="D"),
            _c("13", "CIP — Dimensionnement", "Hauteur tank H", "m", 12, 12, 12, 12, [10, 16], source="D"),
            _c("14", "CIP — Dimensionnement", "Diamètre tank D", "m", 20, 20, 20, 20, [10, 30], source="D"),
            _c("15", "CIP — Dimensionnement", "Aspect ratio H/D", "-", 1.0, 1.0, 1.0, 1.0, [0.8, 1.3], source="D"),
            # ── Agitation CIP ──────────────────────────────────────────────────
            _c("16", "CIP — Agitation", "Puissance spécifique (std 0.08-0.15 kW/m³)", "kW/m³", 0.10, 0.10, 0.10, 0.10, [0.08, 0.15], source="D"),
            _c("17", "CIP — Agitation", "Puissance par tank", "kW", 370, 370, 370, 370, [50, 800], source="D"),
            _c("18", "CIP — Agitation", "Puissance totale", "kW", 2200, 2200, 2200, 2200, [300, 5000], source="D"),
            _c("19", "CIP — Agitation", "Type agitateur", "-", None, None, None, None, [], source="M"),
            # ── Circuit charbon (CIP) ──────────────────────────────────────────
            _c("20", "CIP — Circuit charbon", "Concentration charbon par tank (std 10-25 g/L)", "g/L", 20, 20, 20, 20, [10, 25], source="D"),
            _c("21", "CIP — Circuit charbon", "Inventaire charbon par tank (V × g/L / 1000)", "t", 74, 74, 74, 74, [10, 200], source="C"),
            _c("22", "CIP — Circuit charbon", "Nombre tanks avec charbon (CIP : pas pre-leach)", "-", 6, 6, 6, 6, [4, 10], source="D"),
            _c("23", "CIP — Circuit charbon", "Inventaire charbon total circuit", "t", 442, 442, 442, 442, [60, 1200], source="D"),
            _c("24", "CIP — Circuit charbon", "Charge Au sur charbon (loaded, std 2000-5000 g/t)", "g/t", 4000, 4000, 4000, 4000, [2000, 5000], source="D"),
            _c("25", "CIP — Circuit charbon", "Transfert charbon contre-courant", "t/j", 12, 12, 12, 12, [3, 30], source="D"),
            _c("26", "CIP — Circuit charbon", "Cribles inter-tanks (Kemix/Derrick/MPS)", "-", None, None, None, None, [], source="D"),
            _c("27", "CIP — Circuit charbon", "Récupération Au overall (CIP)", "%", 96, 97, 97, 97, [90, 99], source="D"),
            _c("28", "CIP — Circuit charbon", "Tail solution Au", "mg/L", 0.005, 0.003, 0.003, 0.003, [0.001, 0.02], source="D"),
        ],
    },
    {
        "op_code": "CIL",
        "category": "lixiviation",
        "label": "Carbon-in-Leach",
        "sort_order": 830,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            # ── 1. Alimentation et densité ────────────────────────────────────
            _c("01", "CIL — Alimentation", "Débit alimentation solides (cyclone OF sec.)", "t/h", 1500, 1500, 1500, 1500, [500, 3000], source="D"),
            _c("02", "CIL — Alimentation", "% solides leach (w/w, std 40-50% CIL)", "%", 45, 45, 45, 45, [40, 50], source="D"),
            _c("03", "CIL — Alimentation", "SG minerai", "t/m³", 2.75, 2.75, 2.75, 2.75, [2.5, 3.0], source="D"),
            _c("04", "CIL — Alimentation", "Densité pulpe", "t/m³", 1.45, 1.45, 1.45, 1.45, [1.3, 1.6], source="D"),
            _c("05", "CIL — Alimentation", "Débit volumique pulpe Q_v", "m³/h", 2300, 2300, 2300, 2300, [800, 5000], source="D"),
            _c("06", "CIL — Alimentation", "P80 feed leach", "µm", 75, 75, 75, 75, [40, 150], source="D"),
            # ── 2. Cinétique et temps de résidence ────────────────────────────
            _c("07", "CIL — Cinétique", "Temps résidence total (cyanuration)", "h", 24, 24, 24, 24, [16, 48], source="D"),
            _c("08", "CIL — Cinétique", "Constante cinétique k (1er ordre)", "1/h", 0.18, 0.18, 0.18, 0.18, [0.05, 0.40], source="D"),
            _c("09", "CIL — Cinétique", "Récupération attendue (théorique)", "%", 95, 96, 96, 96, [88, 99], source="D"),
            _c("10", "CIL — Cinétique", "Vérif: 1-exp(-k·t)", "%", 99, 99, 99, 99, [85, 99.5], source="D"),
            # ── 3. Dimensionnement réservoirs ─────────────────────────────────
            _c("11", "CIL — Dimensionnement", "Volume utile total requis (Q × t)", "m³", 55200, 55200, 55200, 55200, [10000, 100000], source="C"),
            _c("12", "CIL — Dimensionnement", "Marge volume (sécurité 20%)", "%", 20, 20, 20, 20, [15, 30], source="D"),
            _c("13", "CIL — Dimensionnement", "Volume design total", "m³", 66240, 66240, 66240, 66240, [12000, 120000], source="D"),
            _c("14", "CIL — Dimensionnement", "Nombre réservoirs (CIL std 6-10 série)", "-", 8, 8, 8, 8, [6, 12], source="D"),
            _c("15", "CIL — Dimensionnement", "Volume unitaire", "m³", 8280, 8280, 8280, 8280, [1000, 15000], source="D"),
            _c("16", "CIL — Dimensionnement", "Hauteur tank H (std 12-16 m)", "m", 14, 14, 14, 14, [12, 16], source="D"),
            _c("17", "CIL — Dimensionnement", "Diamètre tank D = √(4V/πH)", "m", 27, 27, 27, 27, [10, 40], source="C"),
            _c("18", "CIL — Dimensionnement", "Aspect ratio H/D (std 1.0-1.5)", "-", 1.2, 1.2, 1.2, 1.2, [1.0, 1.5], source="D"),
            # ── 4. Agitation ──────────────────────────────────────────────────
            _c("19", "CIL — Agitation", "Puissance spécifique (std 0.08-0.15 kW/m³)", "kW/m³", 0.10, 0.10, 0.10, 0.10, [0.08, 0.15], source="D"),
            _c("20", "CIL — Agitation", "Puissance par tank", "kW", 830, 830, 830, 830, [100, 2000], source="D"),
            _c("21", "CIL — Agitation", "Puissance totale agitation", "kW", 6630, 6630, 6630, 6630, [800, 16000], source="D"),
            _c("22", "CIL — Agitation", "Type agitateur (mixing + dispersion air)", "-", None, None, None, None, [], source="M"),
            # ── 5. Circuit charbon ────────────────────────────────────────────
            _c("23", "CIL — Circuit charbon", "Concentration charbon par tank (std 10-25 g/L)", "g/L", 20, 20, 20, 20, [10, 25], source="D"),
            _c("24", "CIL — Circuit charbon", "Inventaire charbon par tank (V × g/L / 1000)", "t", 166, 166, 166, 166, [20, 400], source="C"),
            _c("25", "CIL — Circuit charbon", "Nombre tanks avec charbon (Tank #1 = pre-leach)", "-", 7, 7, 7, 7, [5, 11], source="C"),
            _c("26", "CIL — Circuit charbon", "Inventaire charbon total circuit", "t", 1160, 1160, 1160, 1160, [150, 3000], source="D"),
            _c("27", "CIL — Circuit charbon", "Charge Au sur charbon (loaded, std 2000-5000 g/t)", "g/t", 4000, 4000, 4000, 4000, [2000, 5000], source="D"),
            _c("28", "CIL — Circuit charbon", "Transfert charbon contre-courant", "t/j", 12, 12, 12, 12, [3, 30], source="D"),
            _c("29", "CIL — Circuit charbon", "Cribles inter-tanks (Kemix/Derrick/MPS)", "-", None, None, None, None, [], source="D"),
            # ── Réactifs ──────────────────────────────────────────────────────
            _c("30", "CIL — Réactifs", "NaCN dosage", "kg/t", 0.5, 0.5, 0.5, 0.5, [0.2, 2.0], source="D"),
            _c("31", "CIL — Réactifs", "O₂ dissous", "mg/L", 8, 8, 8, 8, [4, 15], source="D"),
            _c("32", "CIL — Réactifs", "Lime dosage", "kg/t", 3, 3, 3, 3, [1, 8], source="D"),
        ],
    },
    {
        "op_code": "HEAP_LEACH",
        "category": "lixiviation",
        "label": "Heap leach",
        "sort_order": 840,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Heap Leach", "Stacking rate", "t/d", 36400, 36400, 36400, 36400, [5000, 80000], source="D"),
            _c("02", "Heap Leach", "Lift height", "m", 10, 10, 10, 10, [5, 15], source="D"),
            _c("03", "Heap Leach", "Irrigation rate", "L/m2/h", 10, 10, 10, 10, [5, 20], source="D"),
            _c("04", "Heap Leach", "NaCN concentration", "mg/L", 500, 500, 500, 500, [200, 1000], source="D"),
            _c("05", "Heap Leach", "Leach cycle", "d", 90, 90, 90, 90, [45, 180], source="D"),
            _c("06", "Heap Leach", "Recovery Au", "%", 70, 72, 72, 72, [50, 85], source="D"),
            _c("07", "Heap Leach", "Pad area", "m2", 500000, 500000, 500000, 500000, [50000, 2000000], source="D"),
            _c("08", "Heap Leach", "Agglomeration", "-", None, None, None, None, [], source="D"),
        ],
    },
    {
        "op_code": "VAT_LEACH",
        "category": "lixiviation",
        "label": "Vat leach",
        "sort_order": 850,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Vat Leach", "Vat volume", "m3", 2000, 2000, 2000, 2000, [200, 5000], source="D"),
            _c("02", "Vat Leach", "Number of vats", "-", 6, 6, 6, 6, [3, 12], source="D"),
            _c("03", "Vat Leach", "Leach cycle", "d", 7, 7, 7, 7, [3, 14], source="D"),
            _c("04", "Vat Leach", "NaCN concentration", "mg/L", 1000, 1000, 1000, 1000, [300, 2000], source="D"),
            _c("05", "Vat Leach", "Recovery Au", "%", 80, 82, 82, 82, [60, 95], source="D"),
            _c("06", "Vat Leach", "Drainage time", "h", 12, 12, 12, 12, [6, 24], source="D"),
        ],
    },

    # ------------------------------------------------------------------
    # ADR  (900-999)
    # ------------------------------------------------------------------
    {
        "op_code": "ELUTION_AARL",
        "category": "adr",
        "label": "AARL elution",
        "sort_order": 910,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            # ── 1. Élution AARL — Caractéristiques ────────────────────────────
            _c("01", "AARL — Caractéristiques", "Méthode élution (AARL)", "-", None, None, None, None, [], source="M"),
            _c("02", "AARL — Caractéristiques", "Charge charbon transférée par cycle (batch column)", "t", 6, 6, 6, 6, [2, 12], source="D"),
            _c("03", "AARL — Caractéristiques", "Cycles par jour (std 1 batch/jour)", "-", 1, 1, 1, 1, [1, 4], source="D"),
            _c("04", "AARL — Caractéristiques", "Charge Au sur charbon (loaded carbon)", "g/t", 4000, 4000, 4000, 4000, [2000, 6000], source="D"),
            _c("05", "AARL — Caractéristiques", "Production Au par cycle (élu = masse × g/t / 1000)", "kg", 24, 24, 24, 24, [4, 60], source="C"),
            _c("06", "AARL — Caractéristiques", "Volume colonne élution (ρ_charbon ≈ 0.5 t/m³)", "m³", 12, 12, 12, 12, [4, 25], source="D"),
            _c("07", "AARL — Caractéristiques", "Hauteur colonne (std 4-6 m)", "m", 5, 5, 5, 5, [4, 6], source="D"),
            _c("08", "AARL — Caractéristiques", "Diamètre colonne", "m", 1.75, 1.75, 1.75, 1.75, [1.0, 2.5], source="D"),
            # ── 2. Paramètres opératoires AARL ────────────────────────────────
            _c("09", "AARL — Opératoires", "Pré-traitement acide (wash HCl 3%)", "-", None, None, None, None, [], source="D"),
            _c("10", "AARL — Opératoires", "Concentration NaCN solution élu (très dilué AARL)", "%", 0.1, 0.1, 0.1, 0.1, [0.05, 0.5], source="D"),
            _c("11", "AARL — Opératoires", "Concentration NaOH solution élu", "%", 1.0, 1.0, 1.0, 1.0, [0.5, 2.0], source="D"),
            _c("12", "AARL — Opératoires", "Température élution (100-130°C)", "°C", 110, 110, 110, 110, [100, 130], source="D"),
            _c("13", "AARL — Opératoires", "Pression colonne (pour T>100°C)", "kPa", 350, 350, 350, 350, [200, 500], source="D"),
            _c("14", "AARL — Opératoires", "Débit eau élution (≈ 8 BV)", "m³/cycle", 96, 96, 96, 96, [40, 200], source="D"),
            _c("15", "AARL — Opératoires", "Durée cycle élution AARL (soak+élu+cool)", "h", 8, 8, 8, 8, [6, 12], source="D"),
            _c("16", "AARL — Opératoires", "Stripping efficiency", "%", 97, 97, 97, 97, [92, 99], source="D"),
        ],
    },
    {
        "op_code": "ELUTION_ZADRA",
        "category": "adr",
        "label": "Zadra elution",
        "sort_order": 920,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            # ── 1. Élution Zadra — Caractéristiques ───────────────────────────
            _c("01", "Zadra — Caractéristiques", "Méthode élution (Zadra / Pressure Zadra)", "-", None, None, None, None, [], source="M"),
            _c("02", "Zadra — Caractéristiques", "Charge charbon par cycle", "t", 6, 6, 6, 6, [2, 12], source="D"),
            _c("03", "Zadra — Caractéristiques", "Cycles par jour", "-", 1, 1, 1, 1, [1, 2], source="D"),
            _c("04", "Zadra — Caractéristiques", "Charge Au sur charbon (loaded)", "g/t", 4000, 4000, 4000, 4000, [2000, 6000], source="D"),
            _c("05", "Zadra — Caractéristiques", "Production Au par cycle", "kg", 24, 24, 24, 24, [4, 60], source="D"),
            _c("06", "Zadra — Caractéristiques", "Volume colonne élution", "m³", 12, 12, 12, 12, [4, 25], source="D"),
            _c("07", "Zadra — Caractéristiques", "Hauteur colonne", "m", 5, 5, 5, 5, [4, 6], source="D"),
            _c("08", "Zadra — Caractéristiques", "Diamètre colonne", "m", 1.75, 1.75, 1.75, 1.75, [1.0, 2.5], source="D"),
            # ── 2. Paramètres opératoires Zadra ───────────────────────────────
            _c("09", "Zadra — Opératoires", "Concentration NaCN solution élu", "%", 1.0, 1.0, 1.0, 1.0, [0.5, 2.0], source="D"),
            _c("10", "Zadra — Opératoires", "Concentration NaOH solution élu", "%", 1.0, 1.0, 1.0, 1.0, [0.5, 2.0], source="D"),
            _c("11", "Zadra — Opératoires", "Température élution (Zadra: 95-130°C)", "°C", 110, 110, 110, 110, [95, 130], source="D"),
            _c("12", "Zadra — Opératoires", "Pression colonne (Pressure Zadra)", "kPa", 350, 350, 350, 350, [200, 500], source="D"),
            _c("13", "Zadra — Opératoires", "Débit eluate (BV/h)", "BV/h", 2.0, 2.0, 2.0, 2.0, [1.0, 3.0], source="D"),
            _c("14", "Zadra — Opératoires", "Durée cycle Zadra (plus lent que AARL)", "h", 48, 48, 48, 48, [24, 72], source="D"),
            _c("15", "Zadra — Opératoires", "Volume eluate (BV)", "BV", 10, 10, 10, 10, [5, 20], source="D"),
            _c("16", "Zadra — Opératoires", "Stripping efficiency", "%", 95, 95, 95, 95, [90, 99], source="D"),
        ],
    },
    {
        "op_code": "ELECTROWINNING",
        "category": "adr",
        "label": "Electrowinning cells",
        "sort_order": 930,
        "dependencies": ["ELUTION_AARL"],
        "lims_triggers": {},
        "default_criteria": [
            # ── Électrowinning (EW) ──────────────────────────────────────────
            _c("01", "Électrowinning", "Volume cellule EW (std 2-6 m³)", "m³", 4, 4, 4, 4, [2, 6], source="D"),
            _c("02", "Électrowinning", "Nombre cathodes laine acier inox (steel wool)", "-", 8, 8, 8, 8, [4, 16], source="D"),
            _c("03", "Électrowinning", "Densité courant (std 150-300 A/m²)", "A/m²", 220, 220, 220, 220, [150, 300], source="D"),
            _c("04", "Électrowinning", "Tension cellule (std 3.5-5 V)", "V", 4.5, 4.5, 4.5, 4.5, [3.5, 5.0], source="D"),
            _c("05", "Électrowinning", "Surface cathode totale (≈ 2 m²/cathode)", "m²", 16, 16, 16, 16, [8, 32], source="D"),
            _c("06", "Électrowinning", "Courant total = densité × surface", "A", 3520, 3520, 3520, 3520, [1500, 8000], source="C"),
            _c("07", "Électrowinning", "Puissance EW = V × I / 1000", "kW", 16, 16, 16, 16, [6, 35], source="C"),
            _c("08", "Électrowinning", "Rendement Faraday (standard)", "%", 60, 60, 60, 60, [40, 80], source="D"),
            _c("09", "Électrowinning", "Production Au théorique (Faraday: 0.7350 g/A·h pour Au)", "kg/j", 38, 38, 38, 38, [10, 100], source="D"),
            _c("10", "Électrowinning", "Nombre de cellules", "-", 4, 4, 4, 4, [2, 8], source="D"),
            _c("11", "Électrowinning", "Débit solution", "m³/h", 15, 15, 15, 15, [5, 40], source="D"),
            _c("12", "Électrowinning", "Fréquence stripping cathode", "j", 7, 7, 7, 7, [3, 14], source="D"),
        ],
    },
    {
        "op_code": "FONDERIE",
        "category": "adr",
        "label": "Gold room / smelting",
        "sort_order": 940,
        "dependencies": ["ELECTROWINNING"],
        "lims_triggers": {},
        "default_criteria": [
            # ── Régénération charbon (four) ──────────────────────────────────
            _c("01", "Régénération charbon", "Charbon à régénérer (= élution)", "t/cycle", 6, 6, 6, 6, [2, 12], source="C"),
            _c("02", "Régénération charbon", "Type four (rotary / vertical)", "-", None, None, None, None, [], source="M"),
            _c("03", "Régénération charbon", "Température régénération (std 650-800°C)", "°C", 725, 725, 725, 725, [650, 800], source="D"),
            _c("04", "Régénération charbon", "Temps résidence dans four (std 15-30 min)", "min", 22, 22, 22, 22, [15, 30], source="D"),
            _c("05", "Régénération charbon", "Capacité four alim. (std 300-1000 kg/h)", "kg/h", 600, 600, 600, 600, [300, 1000], source="D"),
            _c("06", "Régénération charbon", "Combustible (gaz/électrique)", "-", None, None, None, None, [], source="M"),
            # ── Affinage (smelting) ───────────────────────────────────────────
            _c("07", "Affinage (smelting)", "Type four affinage (pour doré bullion)", "-", None, None, None, None, [], source="M"),
            _c("08", "Affinage (smelting)", "Capacité par batch (Au+Ag+impuretés)", "kg", 100, 100, 100, 100, [30, 300], source="D"),
            _c("09", "Affinage (smelting)", "Flux (borax/silice/soude, % du poids charge)", "%", 20, 20, 20, 20, [10, 35], source="D"),
            _c("10", "Affinage (smelting)", "Pureté doré obtenue (Au+Ag combinés)", "%", 92, 92, 92, 92, [85, 99], source="D"),
            _c("11", "Affinage (smelting)", "Smelts par semaine", "-", 3, 3, 3, 3, [1, 7], source="D"),
            _c("12", "Affinage (smelting)", "Puissance four", "kW", 250, 250, 250, 250, [100, 500], source="D"),
        ],
    },

    # ------------------------------------------------------------------
    # DETOXIFICATION  (1000-1099)
    # ------------------------------------------------------------------
    {
        "op_code": "DETOX_INCO",
        "category": "detoxification",
        "label": "INCO/SO2 detoxification",
        "sort_order": 1010,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "INCO Detox", "Feed rate solids", "t/h", 1517, 1517, 1517, 1517, [500, 3000], source="D"),
            _c("02", "INCO Detox", "Feed % solids", "% w/w", 50, 50, 50, 50, [35, 60], source="D"),
            _c("03", "INCO Detox", "WAD CN feed", "mg/L", 50, 50, 50, 50, [10, 200], source="D"),
            _c("04", "INCO Detox", "Number of tanks", "-", 2, 2, 2, 2, [1, 4], source="D"),
            _c("05", "INCO Detox", "Residence time", "h", 1.5, 1.5, 1.5, 1.5, [0.5, 3], source="D"),
            _c("06", "INCO Detox", "Total volume", "m3", 2700, 2700, 2700, 2700, [500, 6000], source="D"),
            _c("07", "INCO Detox", "Tank diameter", "m", 14, 14, 14, 14, [6, 20], source="D"),
            _c("08", "INCO Detox", "Tank height", "m", 14, 14, 14, 14, [6, 18], source="D"),
            _c("09", "INCO Detox", "SO2 dosage", "g/g CNWAD", 4.5, 4.5, 4.5, 4.5, [3, 8], source="D"),
            _c("10", "INCO Detox", "CuSO4 dosage", "mg Cu2+/L", 30, 30, 30, 30, [10, 80], source="D"),
            _c("11", "INCO Detox", "Lime dosage", "kg/t", 1.5, 1.5, 1.5, 1.5, [0.5, 4], source="D"),
            _c("12", "INCO Detox", "O2 dissolution", "mg/L", 8, 8, 8, 8, [4, 12], source="D"),
        ],
    },
    {
        "op_code": "DETOX_CARO",
        "category": "detoxification",
        "label": "Caro's acid detoxification",
        "sort_order": 1020,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Caro's Acid Detox", "Feed WAD CN", "mg/L", 50, 50, 50, 50, [10, 200], source="D"),
            _c("02", "Caro's Acid Detox", "Dosage ratio H2SO5/CN", "g/g", 5, 5, 5, 5, [3, 10], source="D"),
            _c("03", "Caro's Acid Detox", "Residence time", "min", 30, 30, 30, 30, [10, 60], source="D"),
            _c("04", "Caro's Acid Detox", "Number of reactors", "-", 2, 2, 2, 2, [1, 4], source="D"),
            _c("05", "Caro's Acid Detox", "H2O2 concentration", "%", 70, 70, 70, 70, [50, 70], source="D"),
            _c("06", "Caro's Acid Detox", "Target WAD CN discharge", "mg/L", 5, 5, 5, 5, [0.5, 10], source="D"),
        ],
    },
    {
        "op_code": "DETOX_PEROXIDE",
        "category": "detoxification",
        "label": "Hydrogen peroxide detoxification",
        "sort_order": 1030,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "H2O2 Detox", "Feed WAD CN", "mg/L", 50, 50, 50, 50, [10, 200], source="D"),
            _c("02", "H2O2 Detox", "H2O2 dosage", "g/g CNWAD", 7, 7, 7, 7, [3, 15], source="D"),
            _c("03", "H2O2 Detox", "CuSO4 catalyst dosage", "mg/L", 30, 30, 30, 30, [10, 80], source="D"),
            _c("04", "H2O2 Detox", "Residence time", "min", 45, 45, 45, 45, [15, 90], source="D"),
            _c("05", "H2O2 Detox", "Target WAD CN discharge", "mg/L", 5, 5, 5, 5, [0.5, 10], source="D"),
        ],
    },
    {
        "op_code": "DETOX_BERLINER",
        "category": "detoxification",
        "label": "Berliner process",
        "sort_order": 1040,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Berliner Detox", "Feed WAD CN", "mg/L", 50, 50, 50, 50, [10, 200], source="D"),
            _c("02", "Berliner Detox", "FeSO4 dosage", "mg/L", 200, 200, 200, 200, [50, 500], source="D"),
            _c("03", "Berliner Detox", "Residence time", "min", 60, 60, 60, 60, [30, 120], source="D"),
            _c("04", "Berliner Detox", "Target WAD CN discharge", "mg/L", 5, 5, 5, 5, [0.5, 10], source="D"),
        ],
    },
    {
        "op_code": "DETOX_OZONE",
        "category": "detoxification",
        "label": "Ozone treatment",
        "sort_order": 1050,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Ozone Detox", "Feed WAD CN", "mg/L", 50, 50, 50, 50, [10, 200], source="D"),
            _c("02", "Ozone Detox", "O3 dosage", "g/g CNWAD", 6, 6, 6, 6, [3, 12], source="D"),
            _c("03", "Ozone Detox", "Contact time", "min", 20, 20, 20, 20, [10, 45], source="D"),
            _c("04", "Ozone Detox", "O3 generator capacity", "kg/h", 20, 20, 20, 20, [5, 60], source="D"),
        ],
    },
    {
        "op_code": "DETOX_BIO",
        "category": "detoxification",
        "label": "Biological CN treatment",
        "sort_order": 1060,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Bio Detox", "Feed WAD CN", "mg/L", 50, 50, 50, 50, [10, 200], source="D"),
            _c("02", "Bio Detox", "HRT", "h", 24, 24, 24, 24, [12, 72], source="D"),
            _c("03", "Bio Detox", "Reactor volume", "m3", 5000, 5000, 5000, 5000, [1000, 15000], source="D"),
            _c("04", "Bio Detox", "Target WAD CN discharge", "mg/L", 5, 5, 5, 5, [0.5, 10], source="D"),
        ],
    },
    {
        "op_code": "DETOX_NEUTRALISATION",
        "category": "detoxification",
        "label": "Simple lime neutralisation",
        "sort_order": 1070,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Lime Neutralisation", "Target pH", "-", 9.5, 9.5, 9.5, 9.5, [7, 11], source="D"),
            _c("02", "Lime Neutralisation", "Lime dosage", "kg/t", 2, 2, 2, 2, [0.5, 6], source="D"),
            _c("03", "Lime Neutralisation", "Mixing tank volume", "m3", 50, 50, 50, 50, [10, 200], source="D"),
        ],
    },

    # ------------------------------------------------------------------
    # RESIDUS  (1100-1199)
    # ------------------------------------------------------------------
    {
        "op_code": "TSF_CONVENTIONNEL",
        "category": "residus",
        "label": "Conventional TSF",
        "sort_order": 1110,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Conventional TSF", "Total capacity", "Mt", 200, 200, 200, 200, [20, 1000], source="D"),
            _c("02", "Conventional TSF", "Deposition type", "-", None, None, None, None, [], source="D"),
            _c("03", "Conventional TSF", "U/F density", "% w/w", 55, 55, 55, 55, [45, 65], source="D"),
            _c("04", "Conventional TSF", "Settled bed % solids", "%", 62, 62, 62, 62, [50, 72], source="D"),
            _c("05", "Conventional TSF", "Beach slope", "%", 1.5, 1.5, 1.5, 1.5, [0.5, 3], source="D"),
        ],
    },
    {
        "op_code": "TSF_DRY_STACK",
        "category": "residus",
        "label": "Dry stack / filtered tailings",
        "sort_order": 1120,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Dry Stack TSF", "Filter type", "-", None, None, None, None, [], source="D"),
            _c("02", "Dry Stack TSF", "Filter cake moisture", "%", 15, 15, 15, 15, [10, 22], source="D"),
            _c("03", "Dry Stack TSF", "Filter area", "m2", 200, 200, 200, 200, [50, 500], source="D"),
            _c("04", "Dry Stack TSF", "Number of filters", "-", 6, 6, 6, 6, [2, 12], source="D"),
            _c("05", "Dry Stack TSF", "Stacking capacity", "t/h", 1517, 1517, 1517, 1517, [500, 3000], source="D"),
        ],
    },
    {
        "op_code": "PASTE_THICKENING",
        "category": "residus",
        "label": "Paste thickener",
        "sort_order": 1130,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Paste Thickener", "U/F density", "% w/w", 72, 72, 72, 72, [65, 78], source="D"),
            _c("02", "Paste Thickener", "Yield stress", "Pa", 200, 200, 200, 200, [50, 500], source="D"),
            _c("03", "Paste Thickener", "Diameter", "m", 24, 24, 24, 24, [12, 40], source="D"),
            _c("04", "Paste Thickener", "Flocculant dosage", "g/t", 50, 50, 50, 50, [20, 100], source="D"),
        ],
    },

    # ------------------------------------------------------------------
    # EAU  (1200-1299)
    # ------------------------------------------------------------------
    {
        "op_code": "BASSIN_EAU",
        "category": "eau",
        "label": "Process water pond/tank",
        "sort_order": 1210,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Process Water", "Fresh water source", "-", None, None, None, None, [], source="D"),
            _c("02", "Process Water", "Fresh water requirement", "m3/h", 350, 350, 350, 350, [50, 1000], source="D"),
            _c("03", "Process Water", "Process water tank volume", "m3", 5000, 5000, 5000, 5000, [1000, 15000], source="D"),
            _c("04", "Process Water", "Recirculation rate", "%", 70, 70, 70, 70, [40, 90], source="D"),
            _c("05", "Process Water", "Total water balance", "m3/h", 1200, 1200, 1200, 1200, [200, 3000], source="D"),
        ],
    },
    {
        "op_code": "TRAITEMENT_EFFLUENT",
        "category": "eau",
        "label": "Effluent treatment plant",
        "sort_order": 1220,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Effluent Treatment", "Treatment type", "-", None, None, None, None, [], source="D"),
            _c("02", "Effluent Treatment", "Design flow", "m3/h", 200, 200, 200, 200, [50, 800], source="D"),
            _c("03", "Effluent Treatment", "Target pH", "-", 7.5, 7.5, 7.5, 7.5, [6.5, 8.5], source="D"),
            _c("04", "Effluent Treatment", "Target TSS", "mg/L", 25, 25, 25, 25, [10, 50], source="D"),
        ],
    },

    # ------------------------------------------------------------------
    # REACTIFS  (1300-1399)
    # ------------------------------------------------------------------
    {
        "op_code": "REACTIF_PAX",
        "category": "reactifs",
        "label": "PAX collector",
        "sort_order": 1310,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Reagent PAX", "Reagent type", "-", None, None, None, None, [], source="D"),
            _c("02", "Reagent PAX", "Total dosage", "g/t", 80, 80, 80, 80, [20, 200], source="D"),
            _c("03", "Reagent PAX", "Mixing strength", "%", 10, 10, 10, 10, [5, 20], source="D"),
            _c("04", "Reagent PAX", "Mixing tank volume", "m3", 5, 5, 5, 5, [1, 15], source="D"),
            _c("05", "Reagent PAX", "Mixing autonomy", "h", 8, 8, 8, 8, [4, 24], source="D"),
            _c("06", "Reagent PAX", "Day tank volume", "m3", 10, 10, 10, 10, [2, 30], source="D"),
            _c("07", "Reagent PAX", "Day tank autonomy", "h", 24, 24, 24, 24, [8, 48], source="D"),
            _c("08", "Reagent PAX", "Annual consumption", "t/y", 1050, 1050, 1050, 1050, [100, 3000], source="D"),
        ],
    },
    {
        "op_code": "REACTIF_MIBC",
        "category": "reactifs",
        "label": "MIBC frother",
        "sort_order": 1320,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Reagent MIBC", "Reagent type", "-", None, None, None, None, [], source="D"),
            _c("02", "Reagent MIBC", "Total dosage", "g/t", 25, 25, 25, 25, [10, 60], source="D"),
            _c("03", "Reagent MIBC", "Storage tank volume", "m3", 20, 20, 20, 20, [5, 50], source="D"),
            _c("04", "Reagent MIBC", "Storage autonomy", "d", 30, 30, 30, 30, [14, 60], source="D"),
            _c("05", "Reagent MIBC", "Dosing pump capacity", "L/h", 50, 50, 50, 50, [10, 150], source="D"),
            _c("06", "Reagent MIBC", "Annual consumption", "t/y", 330, 330, 330, 330, [50, 1000], source="D"),
        ],
    },
    {
        "op_code": "REACTIF_FLOCCULANT",
        "category": "reactifs",
        "label": "Flocculant",
        "sort_order": 1330,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Reagent Flocculant", "Flocculant type", "-", None, None, None, None, [], source="D"),
            _c("02", "Reagent Flocculant", "Dosage", "g/t", 25, 25, 25, 25, [10, 60], source="D"),
            _c("03", "Reagent Flocculant", "Mixing strength", "%", 0.5, 0.5, 0.5, 0.5, [0.1, 1.0], source="D"),
            _c("04", "Reagent Flocculant", "Mixing cone volume", "m3", 2, 2, 2, 2, [0.5, 5], source="D"),
            _c("05", "Reagent Flocculant", "Dilution factor", "-", 10, 10, 10, 10, [5, 20], source="D"),
            _c("06", "Reagent Flocculant", "Make-up tank volume", "m3", 5, 5, 5, 5, [1, 15], source="D"),
            _c("07", "Reagent Flocculant", "Day tank volume", "m3", 10, 10, 10, 10, [2, 30], source="D"),
            _c("08", "Reagent Flocculant", "Annual consumption", "t/y", 330, 330, 330, 330, [50, 800], source="D"),
        ],
    },
    {
        "op_code": "REACTIF_LIME",
        "category": "reactifs",
        "label": "Lime (CaO / Ca(OH)2)",
        "sort_order": 1340,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Reagent Lime", "Lime type", "-", None, None, None, None, [], source="D"),
            _c("02", "Reagent Lime", "Total dosage", "kg/t", 3, 3, 3, 3, [1, 10], source="D"),
            _c("03", "Reagent Lime", "Daily consumption", "t/d", 109, 109, 109, 109, [20, 400], source="D"),
            _c("04", "Reagent Lime", "Slaker capacity", "t/h", 8, 8, 8, 8, [2, 20], source="D"),
            _c("05", "Reagent Lime", "Silo capacity", "t", 500, 500, 500, 500, [100, 2000], source="D"),
            _c("06", "Reagent Lime", "Silo autonomy", "d", 5, 5, 5, 5, [3, 14], source="D"),
            _c("07", "Reagent Lime", "Slurry concentration", "%", 20, 20, 20, 20, [10, 30], source="D"),
            _c("08", "Reagent Lime", "Storage tank volume", "m3", 30, 30, 30, 30, [5, 80], source="D"),
            _c("09", "Reagent Lime", "Distribution pump capacity", "m3/h", 25, 25, 25, 25, [5, 60], source="D"),
            _c("10", "Reagent Lime", "Annual consumption", "t/y", 39800, 39800, 39800, 39800, [5000, 100000], source="D"),
        ],
    },
    {
        "op_code": "REACTIF_NACN",
        "category": "reactifs",
        "label": "Sodium cyanide",
        "sort_order": 1350,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Reagent NaCN", "NaCN form", "-", None, None, None, None, [], source="D"),
            _c("02", "Reagent NaCN", "Total dosage", "kg/t", 0.5, 0.5, 0.5, 0.5, [0.2, 2.0], source="D"),
            _c("03", "Reagent NaCN", "Daily consumption", "t/d", 18, 18, 18, 18, [3, 60], source="D"),
            _c("04", "Reagent NaCN", "Solution concentration", "%", 20, 20, 20, 20, [10, 30], source="D"),
            _c("05", "Reagent NaCN", "Mixing tank volume", "m3", 15, 15, 15, 15, [3, 50], source="D"),
            _c("06", "Reagent NaCN", "Storage tank volume", "m3", 60, 60, 60, 60, [10, 200], source="D"),
            _c("07", "Reagent NaCN", "Dosing pump capacity", "L/h", 300, 300, 300, 300, [50, 1000], source="D"),
            _c("08", "Reagent NaCN", "Annual consumption", "t/y", 6600, 6600, 6600, 6600, [500, 20000], source="D"),
        ],
    },
    {
        "op_code": "REACTIF_CUSO4",
        "category": "reactifs",
        "label": "Copper sulphate",
        "sort_order": 1360,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Reagent CuSO4", "Reagent type", "-", None, None, None, None, [], source="D"),
            _c("02", "Reagent CuSO4", "Dosage", "mg Cu2+/L", 30, 30, 30, 30, [10, 80], source="D"),
            _c("03", "Reagent CuSO4", "Daily consumption", "kg/d", 250, 250, 250, 250, [50, 800], source="D"),
            _c("04", "Reagent CuSO4", "Mixing strength", "%", 10, 10, 10, 10, [5, 20], source="D"),
            _c("05", "Reagent CuSO4", "Mixing tank volume", "m3", 3, 3, 3, 3, [0.5, 8], source="D"),
            _c("06", "Reagent CuSO4", "Annual consumption", "t/y", 91, 91, 91, 91, [10, 300], source="D"),
        ],
    },
    {
        "op_code": "REACTIF_NAOH",
        "category": "reactifs",
        "label": "Caustic soda (NaOH)",
        "sort_order": 1370,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Reagent NaOH", "NaOH form", "-", None, None, None, None, [], source="D"),
            _c("02", "Reagent NaOH", "Concentration", "%", 50, 50, 50, 50, [30, 50], source="D"),
            _c("03", "Reagent NaOH", "Daily consumption", "kg/d", 500, 500, 500, 500, [50, 2000], source="D"),
            _c("04", "Reagent NaOH", "Storage tank volume", "m3", 30, 30, 30, 30, [5, 80], source="D"),
            _c("05", "Reagent NaOH", "Annual consumption", "t/y", 183, 183, 183, 183, [20, 600], source="D"),
        ],
    },
    {
        "op_code": "REACTIF_ACID",
        "category": "reactifs",
        "label": "Acid (HCl / HNO3)",
        "sort_order": 1380,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Reagent Acid", "Acid type", "-", None, None, None, None, [], source="D"),
            _c("02", "Reagent Acid", "Concentration", "%", 32, 32, 32, 32, [10, 70], source="D"),
            _c("03", "Reagent Acid", "Daily consumption", "L/d", 500, 500, 500, 500, [50, 2000], source="D"),
            _c("04", "Reagent Acid", "Storage tank volume", "m3", 20, 20, 20, 20, [5, 60], source="D"),
            _c("05", "Reagent Acid", "Storage autonomy", "d", 30, 30, 30, 30, [14, 60], source="D"),
            _c("06", "Reagent Acid", "Annual consumption", "t/y", 60, 60, 60, 60, [10, 300], source="D"),
        ],
    },
    {
        "op_code": "REACTIF_SO2",
        "category": "reactifs",
        "label": "Liquid SO2",
        "sort_order": 1390,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Reagent SO2", "Delivery method", "-", None, None, None, None, [], source="D"),
            _c("02", "Reagent SO2", "Purity", "%", 99.9, 99.9, 99.9, 99.9, [99, 100], source="D"),
            _c("03", "Reagent SO2", "Daily consumption", "t/d", 3.5, 3.5, 3.5, 3.5, [0.5, 15], source="D"),
            _c("04", "Reagent SO2", "Operating consumption", "kg/h", 146, 146, 146, 146, [20, 600], source="D"),
            _c("05", "Reagent SO2", "Storage vessel volume", "m3", 25, 25, 25, 25, [5, 80], source="D"),
            _c("06", "Reagent SO2", "Storage vessel diameter", "m", 2.4, 2.4, 2.4, 2.4, [1.2, 3.5], source="D"),
            _c("07", "Reagent SO2", "Storage vessel length", "m", 6, 6, 6, 6, [3, 12], source="D"),
            _c("08", "Reagent SO2", "Annual consumption", "t/y", 1280, 1280, 1280, 1280, [100, 5000], source="D"),
        ],
    },
    {
        "op_code": "REACTIF_OXYGEN",
        "category": "reactifs",
        "label": "Oxygen plant / supply",
        "sort_order": 1395,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Reagent O2", "Supply type", "-", None, None, None, None, [], source="D"),
            _c("02", "Reagent O2", "Total dosage", "kg/t", 2, 2, 2, 2, [0.5, 6], source="D"),
            _c("03", "Reagent O2", "Plant capacity", "t/d", 73, 73, 73, 73, [10, 200], source="D"),
            _c("04", "Reagent O2", "Purity", "%", 93, 93, 93, 93, [85, 99.5], source="D"),
            _c("05", "Reagent O2", "Operating pressure", "kPa", 600, 600, 600, 600, [200, 1000], source="D"),
        ],
    },
    {
        "op_code": "REACTIF_CARBON",
        "category": "reactifs",
        "label": "Activated carbon",
        "sort_order": 1398,
        "dependencies": [],
        "lims_triggers": {},
        "default_criteria": [
            _c("01", "Reagent Carbon", "Wet SG", "-", 1.45, 1.45, 1.45, 1.45, [1.3, 1.6], source="D"),
            _c("02", "Reagent Carbon", "Dry SG", "-", 0.50, 0.50, 0.50, 0.50, [0.4, 0.6], source="D"),
            _c("03", "Reagent Carbon", "Mesh size", "mesh", 6, 6, 6, 6, [6, 12], source="D"),
            _c("04", "Reagent Carbon", "Consumption", "kg/t ore", 0.04, 0.04, 0.04, 0.04, [0.02, 0.10], source="D"),
            _c("05", "Reagent Carbon", "Annual consumption", "t/y", 530, 530, 530, 530, [50, 1500], source="D"),
            _c("06", "Reagent Carbon", "Inventory in circuit", "t", 120, 120, 120, 120, [20, 300], source="D"),
        ],
    },
]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def get_all_op_codes():
    """Return set of all op_codes in the catalog."""
    return {entry["op_code"] for entry in CATALOG}


def validate_catalog():
    """Run basic integrity checks on the catalog. Returns list of error strings."""
    errors = []
    op_codes = get_all_op_codes()
    seen_sort = {}
    for entry in CATALOG:
        code = entry["op_code"]
        # Check unique sort_order
        so = entry["sort_order"]
        if so in seen_sort:
            errors.append(f"Duplicate sort_order {so}: {code} and {seen_sort[so]}")
        seen_sort[so] = code
        # Check dependencies reference valid op_codes
        for dep in entry.get("dependencies", []):
            if dep not in op_codes:
                errors.append(f"{code}: dependency '{dep}' not found in catalog")
        # Check minimum criteria count
        criteria = entry.get("default_criteria", [])
        if len(criteria) < 3:
            errors.append(f"{code}: only {len(criteria)} criteria (minimum 3)")
    return errors


# ---------------------------------------------------------------------------
# Database seeder
# ---------------------------------------------------------------------------

def seed_catalog(cursor):
    """Insert or upsert all catalog entries into unit_operations_catalog."""
    try:
        for entry in CATALOG:
            cursor.execute("""
                INSERT INTO unit_operations_catalog
                    (op_code, category, label, sort_order, dependencies, lims_triggers, default_criteria)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
                ON CONFLICT (op_code) DO UPDATE SET
                    category = EXCLUDED.category,
                    label = EXCLUDED.label,
                    sort_order = EXCLUDED.sort_order,
                    dependencies = EXCLUDED.dependencies,
                    lims_triggers = EXCLUDED.lims_triggers,
                    default_criteria = EXCLUDED.default_criteria
            """, (
                entry["op_code"],
                entry["category"],
                entry["label"],
                entry["sort_order"],
                json.dumps(entry.get("dependencies", [])),
                json.dumps(entry.get("lims_triggers", {})),
                json.dumps(entry.get("default_criteria", [])),
            ))
    except Exception as e:
        logger.error("seed_catalog failed: %s", e)
        raise RuntimeError("seed_catalog failed") from e


if __name__ == "__main__":
    errs = validate_catalog()
    if errs:
        print("Catalog validation errors:")
        for e in errs:
            print(f"  - {e}")
    else:
        print(f"Catalog OK: {len(CATALOG)} operations, "
              f"{sum(len(e['default_criteria']) for e in CATALOG)} total criteria")
