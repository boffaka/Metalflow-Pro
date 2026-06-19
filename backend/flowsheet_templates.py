"""
MPDPMS — 28 flowsheet templates for gold processing plants.

Organised in 8 families covering the realistic gold-processing landscape.
Each template defines a tree of nodes; on apply, the backend creates a
fresh `circuit_template` + `circuit_template_operations` rows for the project.

Format per template:
  {
    "code":        "AU_CIL_OXIDE",         # unique code
    "family":      "A. CIL/CIP",
    "name":        "Au-CIL Minerai oxydé free-milling",
    "description": "Standard PFS Afrique de l'Ouest…",
    "nodes": [
      {"id": "n1", "op_code": "FEED", "label": "Minerai brut", "parent": None, "sort": 0,
       "product_kind": None},
      {"id": "n2", "op_code": "CRUSH_GYRATORY", "label": "Concassage primaire",
       "parent": "n1", "sort": 0},
      …
    ]
  }
"""
from __future__ import annotations


def _node(id: str, op: str, label: str, parent: str | None,
          sort: int = 0, product: str | None = None) -> dict:
    return {"id": id, "op_code": op, "label": label, "parent": parent,
            "sort": sort, "product_kind": product}


# ─── Helper builders for common chains ─────────────────────────────────────

def _crushing_3stage(prefix: str, parent: str) -> list[dict]:
    """3-stage crush: gyratory → cone → cone."""
    return [
        _node(f"{prefix}_crush1", "CRUSH_GYRATORY", "Concassage primaire", parent),
        _node(f"{prefix}_crush2", "CRUSH_CONE",     "Concassage secondaire", f"{prefix}_crush1"),
        _node(f"{prefix}_crush3", "CRUSH_CONE",     "Concassage tertiaire",  f"{prefix}_crush2"),
    ]


def _sabc(prefix: str, parent: str) -> list[dict]:
    """SAG + Ball Mill + Cyclone classification."""
    return [
        _node(f"{prefix}_sag",     "SAG_MILL", "SAG Mill",      parent),
        _node(f"{prefix}_ball",    "BALL_MILL","Ball Mill",     f"{prefix}_sag"),
        _node(f"{prefix}_cyclone", "CYCLONE",  "Hydrocyclone",  f"{prefix}_ball"),
    ]


def _cil_chain(prefix: str, parent: str, with_thickener: bool = True) -> list[dict]:
    """CIL → Élution → Doré → Bullion / Tailings."""
    nodes = []
    head = parent
    if with_thickener:
        nodes.append(_node(f"{prefix}_thick", "THICKENER_PRE_LEACH",
                           "Épaississeur pré-lixiviation", head))
        head = f"{prefix}_thick"
    nodes += [
        _node(f"{prefix}_cil",     "LEACH_CIL",       "Lixiviation CIL",     head),
        _node(f"{prefix}_elut",    "ELUTION_ZADRA",   "Élution Zadra",       f"{prefix}_cil"),
        _node(f"{prefix}_ew",      "ELECTROWINNING",  "Électrolyse",         f"{prefix}_elut"),
        _node(f"{prefix}_refine",  "REFINING_FURNACE","Fonderie",            f"{prefix}_ew"),
        _node(f"{prefix}_bull",    "BULLION",         "Lingot doré",
              f"{prefix}_refine", product="bullion"),
        _node(f"{prefix}_detox",   "DETOX_INCO",      "Détox INCO",          f"{prefix}_cil",
              sort=1),
        _node(f"{prefix}_tsf",     "TSF",             "Parc à résidus",      f"{prefix}_detox",
              product="tailings"),
    ]
    return nodes


# ─── Helpers étendus pour la famille I (combinaisons modernes) ─────────────

def _hpgr_ball(prefix: str, parent: str) -> list[dict]:
    """HPGR + Ball Mill + Cyclone (sans SAG)."""
    return [
        _node(f"{prefix}_hpgr",     "CRUSH_HPGR",       "HPGR",           parent),
        _node(f"{prefix}_screen",   "SCREEN_VIBRATING", "Crible",         f"{prefix}_hpgr"),
        _node(f"{prefix}_ball",     "BALL_MILL",        "Ball Mill",      f"{prefix}_screen"),
        _node(f"{prefix}_cyclone",  "CYCLONE",          "Hydrocyclone",   f"{prefix}_ball"),
    ]


def _cip_chain(prefix: str, parent: str, with_thickener: bool = True) -> list[dict]:
    """Lixiviation + CIP (carbon-in-pulp séparé) → Élution → Doré → Bullion / Tailings."""
    nodes = []
    head = parent
    if with_thickener:
        nodes.append(_node(f"{prefix}_thick", "THICKENER_PRE_LEACH",
                           "Épaississeur pré-lixiviation", head))
        head = f"{prefix}_thick"
    nodes += [
        _node(f"{prefix}_leach",   "LEACH_TANK",      "Lixiviation",         head),
        _node(f"{prefix}_cip",     "LEACH_CIP",       "Adsorption CIP",      f"{prefix}_leach"),
        _node(f"{prefix}_elut",    "ELUTION_ZADRA",   "Élution Zadra",       f"{prefix}_cip"),
        _node(f"{prefix}_ew",      "ELECTROWINNING",  "Électrolyse",         f"{prefix}_elut"),
        _node(f"{prefix}_refine",  "REFINING_FURNACE","Fonderie",            f"{prefix}_ew"),
        _node(f"{prefix}_bull",    "BULLION",         "Lingot doré",
              f"{prefix}_refine", product="bullion"),
        _node(f"{prefix}_detox",   "DETOX_INCO",      "Détox INCO",          f"{prefix}_cip",
              sort=1),
        _node(f"{prefix}_tsf",     "TSF",             "Parc à résidus",      f"{prefix}_detox",
              product="tailings"),
    ]
    return nodes


def _gravity_double(prefix: str, parent: str) -> list[dict]:
    """Knelson + Vertimill regrind + Knelson (2 stages gravimétriques),
    avec un circuit Doré séparé via intensive cyanidation."""
    return [
        _node(f"{prefix}_kn1",      "KNELSON",         "Knelson 1ère passe", parent),
        _node(f"{prefix}_verti",    "VERTIMILL",       "Vertimill regrind",  f"{prefix}_kn1"),
        _node(f"{prefix}_kn2",      "KNELSON",         "Knelson 2ème passe", f"{prefix}_verti"),
        _node(f"{prefix}_intensive","LEACH_TANK",      "Intensive cyanidation","{0}_kn2".format(prefix)),
        _node(f"{prefix}_ew_g",     "ELECTROWINNING",  "Électrolyse gravity",f"{prefix}_intensive"),
        _node(f"{prefix}_refine_g", "REFINING_FURNACE","Fonderie gravity",   f"{prefix}_ew_g"),
        _node(f"{prefix}_bull_g",   "BULLION",         "Lingot doré (gravity)",
              f"{prefix}_refine_g", product="bullion"),
    ]


def _flotation_chain(prefix: str, parent: str) -> list[dict]:
    """Conditionner + Rougher + Cleaner + (regrind) → suite à brancher en aval."""
    return [
        _node(f"{prefix}_cond",     "CONDITIONER",       "Conditionneur",      parent),
        _node(f"{prefix}_rougher",  "FLOTATION_ROUGHER", "Flot rougher",       f"{prefix}_cond"),
        _node(f"{prefix}_cleaner",  "FLOTATION_CLEANER", "Flot cleaner",       f"{prefix}_rougher"),
        _node(f"{prefix}_regrind",  "BALL_REGRIND",      "Regrind concentré",  f"{prefix}_cleaner"),
    ]


# ─── 28 Templates ──────────────────────────────────────────────────────────

TEMPLATES: list[dict] = [

    # ═══════════ Famille A — CIL / CIP (5) ═══════════════════════════════
    {
        "code": "AU_CIL_OXIDE",
        "family": "A. CIL/CIP",
        "name": "Au-CIL — Minerai oxydé free-milling",
        "description": "Standard PFS Afrique de l'Ouest. 3-stage crush + SABC + CIL + Élution Zadra.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            *_cil_chain("l", "g_cyclone"),
        ],
    },
    {
        "code": "AU_CIP_OXIDE",
        "family": "A. CIL/CIP",
        "name": "Au-CIP — Minerai oxydé charbon en pulpe",
        "description": "Variante CIP (carbon-in-pulp) pour minerai oxydé. Lixiviation puis adsorption séparées.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("leach",  "LEACH_TANK",      "Lixiviation",          "g_cyclone"),
            _node("cip",    "LEACH_CIP",       "Adsorption CIP",       "leach"),
            _node("elut",   "ELUTION_ZADRA",   "Élution Zadra",        "cip"),
            _node("ew",     "ELECTROWINNING",  "Électrolyse",          "elut"),
            _node("refine", "REFINING_FURNACE","Fonderie",             "ew"),
            _node("bull",   "BULLION",         "Lingot doré",          "refine", product="bullion"),
            _node("detox",  "DETOX_INCO",      "Détox INCO",           "cip",    sort=1),
            _node("tsf",    "TSF",             "Parc à résidus",       "detox",  product="tailings"),
        ],
    },
    {
        "code": "AU_FLOT_CIL",
        "family": "A. CIL/CIP",
        "name": "Au-Flottation + CIL des concentrés",
        "description": "Sulfuré fin libérable. Flottation rougher/cleaner puis CIL appliquée au concentré.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("cond",     "CONDITIONER",         "Conditionneur",        "g_cyclone"),
            _node("rougher",  "FLOTATION_ROUGHER",   "Flot rougher",         "cond"),
            _node("cleaner",  "FLOTATION_CLEANER",   "Flot cleaner",         "rougher"),
            _node("regrind",  "BALL_REGRIND",        "Regrind",              "cleaner"),
            *_cil_chain("l", "regrind", with_thickener=False),
            _node("scav",     "FLOTATION_SCAVENGER", "Flot scavenger",       "rougher", sort=1),
            _node("scav_tsf", "TSF",                 "Stériles flot",        "scav",    product="tailings"),
        ],
    },
    {
        "code": "AU_FLOT_CIL_TAILS",
        "family": "A. CIL/CIP",
        "name": "Au-Flottation + CIL des stériles",
        "description": "Polymétallique avec Au by-product. Flot principal puis CIL des tailings pour récupération secondaire.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("cond",      "CONDITIONER",       "Conditionneur",        "g_cyclone"),
            _node("rougher",   "FLOTATION_ROUGHER", "Flot rougher",         "cond"),
            _node("cleaner",   "FLOTATION_CLEANER", "Flot cleaner",         "rougher"),
            _node("conc_bull", "BULLION",           "Concentré (Au+Cu)",    "cleaner", product="bullion"),
            *_cil_chain("l", "rougher", with_thickener=True),
        ],
    },
    {
        "code": "AU_GRAVITY_CIL",
        "family": "A. CIL/CIP",
        "name": "Au-Gravimétrie (Knelson) + CIL",
        "description": "Au natif libérable, gros grains. Knelson en tête capture l'or grossier, CIL pour le reste.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("knelson",   "KNELSON",          "Knelson",              "g_cyclone"),
            _node("intensive", "LEACH_TANK",       "Intensive cyanidation","knelson"),
            _node("ew_grav",   "ELECTROWINNING",   "Électrolyse gravity",  "intensive"),
            _node("ref_grav",  "REFINING_FURNACE", "Fonderie gravity",     "ew_grav"),
            _node("bull_grav", "BULLION",          "Lingot doré (gravity)","ref_grav", product="bullion"),
            *_cil_chain("l", "knelson", with_thickener=True),
        ],
    },

    # ═══════════ Famille B — Heap leach (4) ══════════════════════════════
    {
        "code": "HEAP_OXIDE_STD",
        "family": "B. Heap leach",
        "name": "Heap leach oxide standard",
        "description": "Crush 3-stage + agglomération + percolation + ADR. Cas standard low-grade oxide.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            _node("agglo",    "MIXING_TANK", "Agglomération chaux/cyanure", "c_crush3"),
            _node("heap",     "HEAP_LEACH",  "Tas de lixiviation",          "agglo"),
            _node("adr_cic",  "LEACH_CIP",   "Adsorption ADR (CIC)",        "heap"),
            _node("elut",     "ELUTION_ZADRA","Élution",                    "adr_cic"),
            _node("ew",       "ELECTROWINNING","Électrolyse",               "elut"),
            _node("refine",   "REFINING_FURNACE","Fonderie",                "ew"),
            _node("bull",     "BULLION",     "Lingot doré",                 "refine", product="bullion"),
        ],
    },
    {
        "code": "HEAP_ROM",
        "family": "B. Heap leach",
        "name": "Heap leach Run-of-Mine",
        "description": "Très bas grade, sans concassage. Minerai versé directement sur la pad.",
        "nodes": [
            _node("feed",     "FEED",         "Minerai brut ROM",        None),
            _node("heap",     "HEAP_LEACH",   "Tas ROM",                 "feed"),
            _node("adr_cic",  "LEACH_CIP",    "Adsorption ADR",          "heap"),
            _node("elut",     "ELUTION_ZADRA","Élution",                 "adr_cic"),
            _node("ew",       "ELECTROWINNING","Électrolyse",            "elut"),
            _node("refine",   "REFINING_FURNACE","Fonderie",             "ew"),
            _node("bull",     "BULLION",      "Lingot doré",             "refine", product="bullion"),
        ],
    },
    {
        "code": "HEAP_PERMANENT",
        "family": "B. Heap leach",
        "name": "Heap leach permanent (cellules empilées)",
        "description": "Permanent leach pad avec empilement multi-niveaux. Minerai à teneur moyenne.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            _node("crush_jaw",  "CRUSH_JAW",   "Concassage primaire", "feed"),
            _node("crush_cone", "CRUSH_CONE",  "Concassage secondaire","crush_jaw"),
            _node("agglo",      "MIXING_TANK", "Agglomération",        "crush_cone"),
            _node("heap",       "HEAP_LEACH",  "Pad permanent multi-lift","agglo"),
            _node("adr_cic",    "LEACH_CIP",   "Adsorption ADR",       "heap"),
            _node("elut",       "ELUTION_ZADRA","Élution",             "adr_cic"),
            _node("ew",         "ELECTROWINNING","Électrolyse",         "elut"),
            _node("refine",     "REFINING_FURNACE","Fonderie",          "ew"),
            _node("bull",       "BULLION",      "Lingot doré",         "refine", product="bullion"),
        ],
    },
    {
        "code": "HEAP_VALLEY_FILL",
        "family": "B. Heap leach",
        "name": "Heap leach Valley Fill",
        "description": "Vallée comblée. Topographie naturelle utilisée pour confiner le tas.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            _node("crush",      "CRUSH_GYRATORY","Concassage",          "feed"),
            _node("crush2",     "CRUSH_CONE",    "Concassage secondaire","crush"),
            _node("agglo",      "MIXING_TANK",   "Agglomération",        "crush2"),
            _node("heap",       "HEAP_LEACH",    "Valley fill heap",     "agglo"),
            _node("adr_cic",    "LEACH_CIP",     "Adsorption ADR",       "heap"),
            _node("elut",       "ELUTION_ZADRA", "Élution",              "adr_cic"),
            _node("ew",         "ELECTROWINNING","Électrolyse",          "elut"),
            _node("refine",     "REFINING_FURNACE","Fonderie",           "ew"),
            _node("bull",       "BULLION",       "Lingot doré",          "refine", product="bullion"),
        ],
    },

    # ═══════════ Famille C — Réfractaire (4) ═════════════════════════════
    {
        "code": "AU_POX_CIL",
        "family": "C. Réfractaire",
        "name": "Au-POX (Pressure Oxidation) + CIL",
        "description": "Sulfuré réfractaire. Autoclave HP/HT pré-traitement pour libérer l'or.",
        "nodes": [
            _node("feed", "FEED", "Minerai sulfuré réfractaire", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("cond",     "CONDITIONER",        "Conditionneur flot",   "g_cyclone"),
            _node("rougher",  "FLOTATION_ROUGHER",  "Flot rougher",         "cond"),
            _node("cleaner",  "FLOTATION_CLEANER",  "Flot cleaner",         "rougher"),
            _node("pox",      "AUTOCLAVE",          "POX (autoclave)",      "cleaner"),
            *_cil_chain("l", "pox", with_thickener=True),
        ],
    },
    {
        "code": "AU_BIOX_CIL",
        "family": "C. Réfractaire",
        "name": "Au-BIOX (oxydation bactérienne) + CIL",
        "description": "Réfractaire, alternative low-temp à POX. Bactéries oxydent les sulfures.",
        "nodes": [
            _node("feed", "FEED", "Minerai sulfuré réfractaire", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("cond",     "CONDITIONER",        "Conditionneur flot",     "g_cyclone"),
            _node("rougher",  "FLOTATION_ROUGHER",  "Flot rougher",           "cond"),
            _node("cleaner",  "FLOTATION_CLEANER",  "Flot cleaner",           "rougher"),
            _node("biox",     "DETOX_BIO",          "BIOX (réacteurs bact.)", "cleaner"),
            *_cil_chain("l", "biox", with_thickener=True),
        ],
    },
    {
        "code": "AU_ROAST_CIL",
        "family": "C. Réfractaire",
        "name": "Au-Roasting (grillage) + CIL",
        "description": "Carlin-style. Grillage du concentré sulfuré avant CIL.",
        "nodes": [
            _node("feed", "FEED", "Minerai sulfuré refractory", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("cond",     "CONDITIONER",        "Conditionneur",        "g_cyclone"),
            _node("rougher",  "FLOTATION_ROUGHER",  "Flot rougher",         "cond"),
            _node("cleaner",  "FLOTATION_CLEANER",  "Flot cleaner",         "rougher"),
            _node("roaster",  "KILN_REGEN",         "Grillage (roaster)",   "cleaner"),
            *_cil_chain("l", "roaster", with_thickener=True),
        ],
    },
    {
        "code": "AU_ALBION_CIL",
        "family": "C. Réfractaire",
        "name": "Au-Albion (oxydation atmosphérique fine) + CIL",
        "description": "Albion process. Broyage ultra-fin (IsaMill) puis oxydation atmosphérique.",
        "nodes": [
            _node("feed", "FEED", "Minerai sulfuré réfractaire", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("cond",     "CONDITIONER",        "Conditionneur",        "g_cyclone"),
            _node("rougher",  "FLOTATION_ROUGHER",  "Flot rougher",         "cond"),
            _node("cleaner",  "FLOTATION_CLEANER",  "Flot cleaner",         "rougher"),
            _node("isamill",  "ISAMILL",            "IsaMill (ultra-fin)",  "cleaner"),
            _node("albion",   "MIXING_TANK",        "Albion (oxydation atm.)","isamill"),
            *_cil_chain("l", "albion", with_thickener=True),
        ],
    },

    # ═══════════ Famille D — Polymétalliques (4) ═════════════════════════
    {
        "code": "AU_CU_PORPHYRY",
        "family": "D. Polymétalliques",
        "name": "Au-Cu Porphyry",
        "description": "Cu primaire avec Au by-product. Flottation Cu, concentré vendu fonderie.",
        "nodes": [
            _node("feed", "FEED", "Minerai porphyre Cu-Au", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("cond",     "CONDITIONER",        "Conditionneur",         "g_cyclone"),
            _node("rougher",  "FLOTATION_ROUGHER",  "Flot Cu rougher",       "cond"),
            _node("regrind",  "VERTIMILL",          "Regrind concentré",     "rougher"),
            _node("cleaner",  "FLOTATION_CLEANER",  "Flot Cu cleaner",       "regrind"),
            _node("recleaner","FLOTATION_RECLEANER","Flot Cu recleaner",     "cleaner"),
            _node("conc",     "BULLION",            "Concentré Cu (vente)",  "recleaner", product="bullion"),
            _node("scav",     "FLOTATION_SCAVENGER","Flot scavenger",        "rougher",  sort=1),
            _node("tsf",      "TSF",                "Stériles",              "scav",      product="tailings"),
        ],
    },
    {
        "code": "AU_PB_ZN_AG",
        "family": "D. Polymétalliques",
        "name": "Au-Pb-Zn-Ag (séquentiel + CIL des tails)",
        "description": "Flottation Pb puis Zn puis CIL des tailings pour Au.",
        "nodes": [
            _node("feed", "FEED", "Minerai polymetallique Pb-Zn-Ag-Au", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("cond_pb",   "CONDITIONER",        "Cond. Pb",          "g_cyclone"),
            _node("flot_pb",   "FLOTATION_ROUGHER",  "Flot Pb rougher",   "cond_pb"),
            _node("clean_pb",  "FLOTATION_CLEANER",  "Flot Pb cleaner",   "flot_pb"),
            _node("conc_pb",   "BULLION",            "Concentré Pb (vente)","clean_pb", product="bullion"),
            _node("cond_zn",   "CONDITIONER",        "Cond. Zn",          "flot_pb",   sort=1),
            _node("flot_zn",   "FLOTATION_ROUGHER",  "Flot Zn rougher",   "cond_zn"),
            _node("clean_zn",  "FLOTATION_CLEANER",  "Flot Zn cleaner",   "flot_zn"),
            _node("conc_zn",   "BULLION",            "Concentré Zn (vente)","clean_zn", product="bullion"),
            *_cil_chain("l", "flot_zn", with_thickener=True),
        ],
    },
    {
        "code": "AU_SB_BULK",
        "family": "D. Polymétalliques",
        "name": "Au-Sb (antimoine bulk + CIL des tails)",
        "description": "Flot bulk antimoine, CIL appliquée aux tailings pour récupération Au.",
        "nodes": [
            _node("feed", "FEED", "Minerai Au-Sb", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("cond",     "CONDITIONER",       "Conditionneur",       "g_cyclone"),
            _node("flot_sb",  "FLOTATION_ROUGHER", "Flot Sb rougher",     "cond"),
            _node("clean_sb", "FLOTATION_CLEANER", "Flot Sb cleaner",     "flot_sb"),
            _node("conc_sb",  "BULLION",           "Concentré Sb (vente)","clean_sb", product="bullion"),
            *_cil_chain("l", "flot_sb", with_thickener=True),
        ],
    },
    {
        "code": "CU_AU_DORE",
        "family": "D. Polymétalliques",
        "name": "Cu-Au avec circuit Doré séparé",
        "description": "Flot Cu + Knelson dans le broyage pour produire un Doré directement on-site.",
        "nodes": [
            _node("feed", "FEED", "Minerai Cu-Au", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("knelson",   "KNELSON",          "Knelson",              "g_cyclone"),
            _node("intensive", "LEACH_TANK",       "Intensive cyanidation","knelson"),
            _node("ew_grav",   "ELECTROWINNING",   "Électrolyse",          "intensive"),
            _node("dore",      "REFINING_FURNACE", "Fonderie Doré",        "ew_grav"),
            _node("bull_dore", "BULLION",          "Lingot Doré",          "dore",     product="bullion"),
            _node("cond",      "CONDITIONER",      "Conditionneur Cu",     "knelson",  sort=1),
            _node("flot_cu",   "FLOTATION_ROUGHER","Flot Cu",              "cond"),
            _node("clean_cu",  "FLOTATION_CLEANER","Flot Cu cleaner",      "flot_cu"),
            _node("conc_cu",   "BULLION",          "Concentré Cu (vente)", "clean_cu", product="bullion"),
        ],
    },

    # ═══════════ Famille E — Récupération spécifique (4) ═════════════════
    {
        "code": "AU_AG_MERRILL_CROWE",
        "family": "E. Récupération spécifique",
        "name": "Au-Ag Merrill-Crowe (cémentation Zn)",
        "description": "Riche en Ag. Cémentation au zinc au lieu du carbon-in-leach.",
        "nodes": [
            _node("feed", "FEED", "Minerai riche en Ag", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("leach",     "LEACH_TANK",        "Lixiviation",       "g_cyclone"),
            _node("clarif",    "PRESSURE_FILTER",   "Clarification",     "leach"),
            _node("deox",      "MIXING_TANK",       "Désoxygénation",    "clarif"),
            _node("zn_cement", "MIXING_TANK",       "Cémentation Zn",    "deox"),
            _node("filt",      "PRESSURE_FILTER",   "Filtre presse",     "zn_cement"),
            _node("refine",    "REFINING_FURNACE",  "Fonderie",          "filt"),
            _node("bull",      "BULLION",           "Lingot Doré",       "refine", product="bullion"),
            _node("tsf",       "TSF",               "Parc à résidus",    "leach",  sort=1, product="tailings"),
        ],
    },
    {
        "code": "AU_AG_PARALLEL",
        "family": "E. Récupération spécifique",
        "name": "Au-Ag CIL + Merrill-Crowe en parallèle",
        "description": "CIL pour Au, MC pour Ag, en circuits parallèles.",
        "nodes": [
            _node("feed", "FEED", "Minerai Au-Ag", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("leach",     "LEACH_TANK",        "Lixiviation commune","g_cyclone"),
            _node("split",     "MIXING_TANK",       "Split Au/Ag",        "leach"),
            _node("cil",       "LEACH_CIL",         "CIL pour Au",        "split"),
            _node("elut",      "ELUTION_ZADRA",     "Élution",            "cil"),
            _node("ew_au",     "ELECTROWINNING",    "Électrolyse Au",     "elut"),
            _node("refine_au", "REFINING_FURNACE",  "Fonderie Au",        "ew_au"),
            _node("bull_au",   "BULLION",           "Lingot Au",          "refine_au", product="bullion"),
            _node("mc",        "MIXING_TANK",       "Merrill-Crowe Ag",   "split", sort=1),
            _node("ew_ag",     "ELECTROWINNING",    "Électrolyse Ag",     "mc"),
            _node("refine_ag", "REFINING_FURNACE",  "Fonderie Ag",        "ew_ag"),
            _node("bull_ag",   "BULLION",           "Lingot Ag",          "refine_ag", product="bullion"),
        ],
    },
    {
        "code": "AU_TELLURIDES",
        "family": "E. Récupération spécifique",
        "name": "Au tellurures (flottation + grillage)",
        "description": "Tellurures Au-Ag-Te. Flot spécifique puis grillage avant CIL.",
        "nodes": [
            _node("feed", "FEED", "Minerai à tellurures", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("cond",      "CONDITIONER",        "Conditionneur tellurures","g_cyclone"),
            _node("rougher",   "FLOTATION_ROUGHER",  "Flot rougher",            "cond"),
            _node("cleaner",   "FLOTATION_CLEANER",  "Flot cleaner",            "rougher"),
            _node("roaster",   "KILN_REGEN",         "Grillage tellurures",     "cleaner"),
            *_cil_chain("l", "roaster", with_thickener=True),
        ],
    },
    {
        "code": "AU_HG_AMALGAM",
        "family": "E. Récupération spécifique",
        "name": "Au-Hg amalgamation puis CIL",
        "description": "Minerai porteur de mercure. Étape amalgamation + récupération Hg avant CIL.",
        "nodes": [
            _node("feed", "FEED", "Minerai Au-Hg", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("amalg",    "MIXING_TANK",      "Amalgamation Hg",     "g_cyclone"),
            _node("hg_recov", "PRESSURE_FILTER",  "Récupération Hg",     "amalg"),
            *_cil_chain("l", "amalg", with_thickener=True),
        ],
    },

    # ═══════════ Famille F — Concentré vente (2) ═════════════════════════
    {
        "code": "CONC_AU_PYRITE",
        "family": "F. Concentré vente",
        "name": "Concentré Au-pyrite (vente fonderie)",
        "description": "Aucun CIL on-site. Concentré Au-pyrite vendu directement à une fonderie.",
        "nodes": [
            _node("feed", "FEED", "Minerai sulfuré", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("cond",     "CONDITIONER",        "Conditionneur",       "g_cyclone"),
            _node("rougher",  "FLOTATION_ROUGHER",  "Flot rougher",        "cond"),
            _node("cleaner",  "FLOTATION_CLEANER",  "Flot cleaner",        "rougher"),
            _node("recleaner","FLOTATION_RECLEANER","Flot recleaner",      "cleaner"),
            _node("dewater",  "PRESSURE_FILTER",    "Filtre concentré",    "recleaner"),
            _node("conc",     "BULLION",            "Concentré (vente)",   "dewater", product="bullion"),
            _node("tsf",      "TSF",                "Stériles",            "rougher", sort=1, product="tailings"),
        ],
    },
    {
        "code": "CONC_CU_AU",
        "family": "F. Concentré vente",
        "name": "Concentré Cu-Au (vente fonderie cuivre)",
        "description": "Concentré Cu avec Au by-product, vendu en fonderie.",
        "nodes": [
            _node("feed", "FEED", "Minerai Cu-Au", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("cond",     "CONDITIONER",        "Cond. Cu",            "g_cyclone"),
            _node("rougher",  "FLOTATION_ROUGHER",  "Flot Cu rougher",     "cond"),
            _node("regrind",  "VERTIMILL",          "Regrind",             "rougher"),
            _node("cleaner",  "FLOTATION_CLEANER",  "Flot Cu cleaner",     "regrind"),
            _node("recleaner","FLOTATION_RECLEANER","Flot Cu recleaner",   "cleaner"),
            _node("dewater",  "PRESSURE_FILTER",    "Filtre concentré",    "recleaner"),
            _node("conc_cu",  "BULLION",            "Concentré Cu-Au (vente)","dewater", product="bullion"),
            _node("tsf",      "TSF",                "Stériles",            "rougher", sort=1, product="tailings"),
        ],
    },

    # ═══════════ Famille G — Variantes comminution (3) ═══════════════════
    {
        "code": "HPGR_BALL_CIL",
        "family": "G. Comminution",
        "name": "HPGR + Ball Mill + CIL",
        "description": "Circuit moderne sans SAG. HPGR pour réduction haute pression + Ball Mill final.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            _node("crush_jaw", "CRUSH_JAW",   "Concassage primaire",  "feed"),
            _node("crush_cone","CRUSH_CONE",  "Concassage secondaire","crush_jaw"),
            _node("hpgr",      "CRUSH_HPGR",  "HPGR",                 "crush_cone"),
            _node("screen",    "SCREEN_VIBRATING","Crible",           "hpgr"),
            _node("ball",      "BALL_MILL",   "Ball Mill",            "screen"),
            _node("cyclone",   "CYCLONE",     "Hydrocyclone",         "ball"),
            *_cil_chain("l", "cyclone", with_thickener=True),
        ],
    },
    {
        "code": "SS_SAG_CIL",
        "family": "G. Comminution",
        "name": "Single-Stage SAG + CIL",
        "description": "SAG haute aspect ratio sans Ball Mill aval. Cas low-CAPEX.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            _node("crush_jaw", "CRUSH_JAW",  "Concassage primaire", "feed"),
            _node("sag",       "SAG_MILL",   "Single-Stage SAG",    "crush_jaw"),
            _node("cyclone",   "CYCLONE",    "Hydrocyclone",        "sag"),
            *_cil_chain("l", "cyclone", with_thickener=True),
        ],
    },
    {
        "code": "BALL_2STAGE_CIL",
        "family": "G. Comminution",
        "name": "2-stage Ball Mill (école ancienne) + CIL",
        "description": "Approche conventionnelle sans SAG. 2 Ball Mills en série, encore utilisée pour minerais doux.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            _node("ball1",   "BALL_MILL", "Ball Mill primaire",   "c_crush3"),
            _node("ball2",   "BALL_MILL", "Ball Mill secondaire", "ball1"),
            _node("cyclone", "CYCLONE",   "Hydrocyclone",         "ball2"),
            *_cil_chain("l", "cyclone", with_thickener=True),
        ],
    },

    # ═══════════ Famille H — Variantes tailings (2) ══════════════════════
    {
        "code": "AU_CIL_DRY_STACK",
        "family": "H. Tailings",
        "name": "Au-CIL avec dry stack tailings",
        "description": "CIL standard mais stériles filtrés (dry stack) au lieu de TSF — projets faible disponibilité d'eau.",
        "nodes": [
            _node("feed", "FEED", "Minerai oxydé", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("thick",    "THICKENER_PRE_LEACH","Épaississeur pré-lixiviation","g_cyclone"),
            _node("cil",      "LEACH_CIL",          "CIL",                  "thick"),
            _node("elut",     "ELUTION_ZADRA",      "Élution",              "cil"),
            _node("ew",       "ELECTROWINNING",     "Électrolyse",          "elut"),
            _node("refine",   "REFINING_FURNACE",   "Fonderie",             "ew"),
            _node("bull",     "BULLION",            "Lingot doré",          "refine", product="bullion"),
            _node("detox",    "DETOX_INCO",         "Détox",                "cil",    sort=1),
            _node("tail_thick","TAILINGS_THICKENER","Épaississeur stériles","detox"),
            _node("filter",   "TAILINGS_FILTER",    "Filtre stériles (dry)","tail_thick"),
            _node("dry_stack","TAILINGS_FILTER",    "Dry stack",            "filter", product="tailings"),
        ],
    },
    {
        "code": "AU_CIL_PASTE_BF",
        "family": "H. Tailings",
        "name": "Au-CIL avec paste backfill",
        "description": "CIL standard avec stériles en pâte, retournés en mine sous forme de remblai.",
        "nodes": [
            _node("feed", "FEED", "Minerai oxydé", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("thick",    "THICKENER_PRE_LEACH","Épaississeur pré-lixiviation","g_cyclone"),
            _node("cil",      "LEACH_CIL",          "CIL",                  "thick"),
            _node("elut",     "ELUTION_ZADRA",      "Élution",              "cil"),
            _node("ew",       "ELECTROWINNING",     "Électrolyse",          "elut"),
            _node("refine",   "REFINING_FURNACE",   "Fonderie",             "ew"),
            _node("bull",     "BULLION",            "Lingot doré",          "refine", product="bullion"),
            _node("detox",    "DETOX_INCO",         "Détox",                "cil",    sort=1),
            _node("paste",    "PASTE_BACKFILL",     "Remblai en pâte",      "detox",  product="tailings"),
        ],
    },
    # ═══════════ Famille I — Combinaisons modernes (HPGR/SABC × Gravity/Flot × CIL/CIP) (20) ═════
    {
        "code": "AU_HPGR_GRAVITY_CIP",
        "family": "I. Combinaisons modernes",
        "name": "Crush + HPGR + Ball + Gravité (Knelson+Verti+Knelson) + CIP",
        "description": "HPGR moderne, gravimétrie en double passe (Knelson + Vertimill regrind + Knelson) avec cyanidation intensive, puis CIP du résidu.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_hpgr_ball("g", "c_crush3"),
            *_gravity_double("grav", "g_cyclone"),
            *_cip_chain("l", "grav_kn2", with_thickener=True),
        ],
    },
    {
        "code": "AU_SABC_GRAVITY_CIP",
        "family": "I. Combinaisons modernes",
        "name": "Crush + SAG + Ball + Gravité (Knelson+Verti+Knelson) + CIP",
        "description": "SABC + double gravimétrie (Knelson + Vertimill + Knelson) avec circuit Doré séparé, puis CIP du résidu.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            *_gravity_double("grav", "g_cyclone"),
            *_cip_chain("l", "grav_kn2", with_thickener=True),
        ],
    },
    {
        "code": "AU_HPGR_FLOT_CIP",
        "family": "I. Combinaisons modernes",
        "name": "Crush + HPGR + Ball + Flottation + CIP",
        "description": "HPGR + Ball Mill + flottation rougher/cleaner avec regrind, puis CIP des concentrés.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_hpgr_ball("g", "c_crush3"),
            *_flotation_chain("f", "g_cyclone"),
            *_cip_chain("l", "f_regrind", with_thickener=False),
            _node("scav",     "FLOTATION_SCAVENGER", "Flot scavenger", "f_rougher", sort=1),
            _node("scav_tsf", "TSF",                 "Stériles flot",  "scav",       product="tailings"),
        ],
    },
    {
        "code": "AU_SABC_FLOT_CIP",
        "family": "I. Combinaisons modernes",
        "name": "Crush + SAG + Ball + Flottation + CIP",
        "description": "SABC + flottation rougher/cleaner + regrind, puis CIP des concentrés.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            *_flotation_chain("f", "g_cyclone"),
            *_cip_chain("l", "f_regrind", with_thickener=False),
            _node("scav",     "FLOTATION_SCAVENGER", "Flot scavenger", "f_rougher", sort=1),
            _node("scav_tsf", "TSF",                 "Stériles flot",  "scav",       product="tailings"),
        ],
    },
    {
        "code": "AU_HPGR_GRAVITY_CIL",
        "family": "I. Combinaisons modernes",
        "name": "Crush + HPGR + Ball + Gravité (Knelson+Verti+Knelson) + CIL",
        "description": "HPGR moderne avec double gravimétrie + CIL conventionnel.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_hpgr_ball("g", "c_crush3"),
            *_gravity_double("grav", "g_cyclone"),
            *_cil_chain("l", "grav_kn2", with_thickener=True),
        ],
    },
    {
        "code": "AU_SABC_GRAVITY_CIL",
        "family": "I. Combinaisons modernes",
        "name": "Crush + SAG + Ball + Gravité (Knelson+Verti+Knelson) + CIL",
        "description": "SABC + double gravimétrie (Knelson + Vertimill regrind + Knelson) + CIL.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            *_gravity_double("grav", "g_cyclone"),
            *_cil_chain("l", "grav_kn2", with_thickener=True),
        ],
    },
    {
        "code": "AU_HPGR_FLOT_CIL",
        "family": "I. Combinaisons modernes",
        "name": "Crush + HPGR + Ball + Flottation + CIL",
        "description": "HPGR + Ball + flottation rougher/cleaner + CIL des concentrés.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_hpgr_ball("g", "c_crush3"),
            *_flotation_chain("f", "g_cyclone"),
            *_cil_chain("l", "f_regrind", with_thickener=False),
            _node("scav",     "FLOTATION_SCAVENGER", "Flot scavenger", "f_rougher", sort=1),
            _node("scav_tsf", "TSF",                 "Stériles flot",  "scav",       product="tailings"),
        ],
    },
    {
        "code": "AU_SABC_FLOT_CIL",
        "family": "I. Combinaisons modernes",
        "name": "Crush + SAG + Ball + Flottation + CIL",
        "description": "SABC + flottation rougher/cleaner + regrind + CIL des concentrés.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            *_flotation_chain("f", "g_cyclone"),
            *_cil_chain("l", "f_regrind", with_thickener=False),
            _node("scav",     "FLOTATION_SCAVENGER", "Flot scavenger", "f_rougher", sort=1),
            _node("scav_tsf", "TSF",                 "Stériles flot",  "scav",       product="tailings"),
        ],
    },
    {
        "code": "AU_HPGR_BALL_CIP",
        "family": "I. Combinaisons modernes",
        "name": "Crush + HPGR + Ball + CIP (sans gravité ni flot)",
        "description": "HPGR + Ball Mill direct vers CIP. Adapté aux minerais oxydés sans valeurs en gravimétrie ni flottation.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_hpgr_ball("g", "c_crush3"),
            *_cip_chain("l", "g_cyclone", with_thickener=True),
        ],
    },
    {
        "code": "AU_HPGR_BALL_CIL",
        "family": "I. Combinaisons modernes",
        "name": "Crush + HPGR + Ball + CIL (sans gravité ni flot)",
        "description": "HPGR + Ball Mill direct vers CIL. Variante CIL du précédent.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_hpgr_ball("g", "c_crush3"),
            *_cil_chain("l", "g_cyclone", with_thickener=True),
        ],
    },
    {
        "code": "AU_SABC_CIP_DIRECT",
        "family": "I. Combinaisons modernes",
        "name": "Crush + SAG + Ball + CIP (direct)",
        "description": "SABC direct vers CIP, sans gravimétrie ni flottation. Cas oxydé simple.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            *_cip_chain("l", "g_cyclone", with_thickener=True),
        ],
    },
    {
        "code": "AU_SABC_GRAVITY_FLOT_CIL",
        "family": "I. Combinaisons modernes",
        "name": "Crush + SAG + Ball + Gravité + Flottation + CIL",
        "description": "Triple récupération : gravimétrie en tête, flottation des sulfures, CIL des résidus de flottation.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("kn",        "KNELSON",         "Knelson",              "g_cyclone"),
            _node("intensive", "LEACH_TANK",      "Intensive cyanidation","kn"),
            _node("ew_g",      "ELECTROWINNING",  "Électrolyse gravity",  "intensive"),
            _node("ref_g",     "REFINING_FURNACE","Fonderie gravity",     "ew_g"),
            _node("bull_g",    "BULLION",         "Lingot doré (gravity)","ref_g", product="bullion"),
            *_flotation_chain("f", "kn"),
            *_cil_chain("l", "f_regrind", with_thickener=False),
        ],
    },
    {
        "code": "AU_SABC_GRAVITY_FLOT_CIP",
        "family": "I. Combinaisons modernes",
        "name": "Crush + SAG + Ball + Gravité + Flottation + CIP",
        "description": "Triple récupération avec CIP au lieu de CIL.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("kn",        "KNELSON",         "Knelson",              "g_cyclone"),
            _node("intensive", "LEACH_TANK",      "Intensive cyanidation","kn"),
            _node("ew_g",      "ELECTROWINNING",  "Électrolyse gravity",  "intensive"),
            _node("ref_g",     "REFINING_FURNACE","Fonderie gravity",     "ew_g"),
            _node("bull_g",    "BULLION",         "Lingot doré (gravity)","ref_g", product="bullion"),
            *_flotation_chain("f", "kn"),
            *_cip_chain("l", "f_regrind", with_thickener=False),
        ],
    },
    {
        "code": "AU_HPGR_GRAVITY_FLOT_CIL",
        "family": "I. Combinaisons modernes",
        "name": "Crush + HPGR + Ball + Gravité + Flottation + CIL",
        "description": "HPGR + triple récupération (gravity → flot → CIL).",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_hpgr_ball("g", "c_crush3"),
            _node("kn",        "KNELSON",         "Knelson",              "g_cyclone"),
            _node("intensive", "LEACH_TANK",      "Intensive cyanidation","kn"),
            _node("ew_g",      "ELECTROWINNING",  "Électrolyse gravity",  "intensive"),
            _node("ref_g",     "REFINING_FURNACE","Fonderie gravity",     "ew_g"),
            _node("bull_g",    "BULLION",         "Lingot doré (gravity)","ref_g", product="bullion"),
            *_flotation_chain("f", "kn"),
            *_cil_chain("l", "f_regrind", with_thickener=False),
        ],
    },
    {
        "code": "AU_HPGR_GRAVITY_FLOT_CIP",
        "family": "I. Combinaisons modernes",
        "name": "Crush + HPGR + Ball + Gravité + Flottation + CIP",
        "description": "HPGR + triple récupération avec CIP.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            *_hpgr_ball("g", "c_crush3"),
            _node("kn",        "KNELSON",         "Knelson",              "g_cyclone"),
            _node("intensive", "LEACH_TANK",      "Intensive cyanidation","kn"),
            _node("ew_g",      "ELECTROWINNING",  "Électrolyse gravity",  "intensive"),
            _node("ref_g",     "REFINING_FURNACE","Fonderie gravity",     "ew_g"),
            _node("bull_g",    "BULLION",         "Lingot doré (gravity)","ref_g", product="bullion"),
            *_flotation_chain("f", "kn"),
            *_cip_chain("l", "f_regrind", with_thickener=False),
        ],
    },
    {
        "code": "AU_SS_SAG_CIP",
        "family": "I. Combinaisons modernes",
        "name": "Single-Stage SAG + CIP",
        "description": "SAG haute aspect ratio sans Ball Mill aval + CIP. Faible CAPEX.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            _node("crush_jaw", "CRUSH_JAW",  "Concassage primaire", "feed"),
            _node("sag",       "SAG_MILL",   "Single-Stage SAG",    "crush_jaw"),
            _node("cyclone",   "CYCLONE",    "Hydrocyclone",        "sag"),
            *_cip_chain("l", "cyclone", with_thickener=True),
        ],
    },
    {
        "code": "AU_BALL_2STAGE_CIP",
        "family": "I. Combinaisons modernes",
        "name": "Crush + 2-stage Ball Mill + CIP (école ancienne)",
        "description": "Approche conventionnelle sans SAG ni HPGR + CIP.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            _node("ball1",   "BALL_MILL", "Ball Mill primaire",   "c_crush3"),
            _node("ball2",   "BALL_MILL", "Ball Mill secondaire", "ball1"),
            _node("cyclone", "CYCLONE",   "Hydrocyclone",         "ball2"),
            *_cip_chain("l", "cyclone", with_thickener=True),
        ],
    },
    {
        "code": "AU_HPGR_VERTIMILL_CIL",
        "family": "I. Combinaisons modernes",
        "name": "Crush + HPGR + Vertimill (broyage fin) + CIL",
        "description": "HPGR + broyage fin direct par Vertimill (sans Ball Mill conventionnel) + CIL. Cas minerais durs visant un P80 ultra-fin.",
        "nodes": [
            _node("feed", "FEED", "Minerai brut", None),
            *_crushing_3stage("c", "feed"),
            _node("hpgr",    "CRUSH_HPGR",      "HPGR",         "c_crush3"),
            _node("screen",  "SCREEN_VIBRATING","Crible",       "hpgr"),
            _node("verti",   "VERTIMILL",       "Vertimill",    "screen"),
            _node("cyclone", "CYCLONE",         "Hydrocyclone", "verti"),
            *_cil_chain("l", "cyclone", with_thickener=True),
        ],
    },
    {
        "code": "AU_POX_CIP",
        "family": "I. Combinaisons modernes",
        "name": "Réfractaire POX + CIP",
        "description": "Variante CIP du circuit POX (autoclave) + flottation pré-traitement.",
        "nodes": [
            _node("feed", "FEED", "Minerai sulfuré réfractaire", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("cond",     "CONDITIONER",        "Conditionneur flot",   "g_cyclone"),
            _node("rougher",  "FLOTATION_ROUGHER",  "Flot rougher",         "cond"),
            _node("cleaner",  "FLOTATION_CLEANER",  "Flot cleaner",         "rougher"),
            _node("pox",      "AUTOCLAVE",          "POX (autoclave)",      "cleaner"),
            *_cip_chain("l", "pox", with_thickener=True),
        ],
    },
    {
        "code": "AU_BIOX_CIP",
        "family": "I. Combinaisons modernes",
        "name": "Réfractaire BIOX + CIP",
        "description": "Variante CIP du circuit BIOX (oxydation bactérienne).",
        "nodes": [
            _node("feed", "FEED", "Minerai sulfuré réfractaire", None),
            *_crushing_3stage("c", "feed"),
            *_sabc("g", "c_crush3"),
            _node("cond",     "CONDITIONER",        "Conditionneur flot",     "g_cyclone"),
            _node("rougher",  "FLOTATION_ROUGHER",  "Flot rougher",           "cond"),
            _node("cleaner",  "FLOTATION_CLEANER",  "Flot cleaner",           "rougher"),
            _node("biox",     "DETOX_BIO",          "BIOX (réacteurs bact.)", "cleaner"),
            *_cip_chain("l", "biox", with_thickener=True),
        ],
    },
]


# Sanity check at import time
assert len(TEMPLATES) == 48, f"Expected 48 templates (got {len(TEMPLATES)})"
_codes = {t["code"] for t in TEMPLATES}
assert len(_codes) == 48, f"Duplicate template codes detected ({len(_codes)} unique vs 48 expected)"


def get_template_by_code(code: str) -> dict | None:
    for t in TEMPLATES:
        if t["code"] == code:
            return t
    return None


def get_templates_grouped() -> dict[str, list[dict]]:
    """Return templates grouped by family, with light metadata for the picker."""
    grouped: dict[str, list[dict]] = {}
    for t in TEMPLATES:
        fam = t["family"]
        grouped.setdefault(fam, []).append({
            "code": t["code"],
            "name": t["name"],
            "description": t["description"],
            "node_count": len(t["nodes"]),
        })
    return grouped
