"""
MPDPMS — Equipment catalog (60 codes).

Static reference of metallurgical equipment types available for project
flowsheets. Frontend reads via GET /api/v1/equipment-catalog (groups by
category for the picker UI).

For specific instances of equipment per project (size, vendor, model),
see the `equipment` table — each circuit_template_operations.equipment_id
points there.
"""
from __future__ import annotations


CATEGORIES = [
    "Alimentation",
    "Concassage",
    "Classification",
    "Broyage",
    "Gravimétrie",
    "Flottation",
    "Conditionnement",
    "Épaississement",
    "Filtration",
    "Lixiviation",
    "Élution & Doré",
    "Détox",
    "Stockage rejets",
    "Services",
    "Échantillonnage",
    "Produit final",
]


EQUIPMENT_CATALOG: dict[str, dict] = {
    # ─── Alimentation (3) ────────────────────────────────────────────────
    "FEED":               {"label": "Minerai brut",                         "category": "Alimentation",     "icon": "⛏️"},
    "ROM_BIN":            {"label": "Trémie ROM",                            "category": "Alimentation",     "icon": "📦"},
    "APRON_FEEDER":       {"label": "Apron feeder",                          "category": "Alimentation",     "icon": "🛤️"},
    # ─── Concassage (5) ──────────────────────────────────────────────────
    "CRUSH_JAW":          {"label": "Concasseur à mâchoires",                "category": "Concassage",       "icon": "🔨"},
    "CRUSH_GYRATORY":     {"label": "Concasseur giratoire",                  "category": "Concassage",       "icon": "🔨"},
    "CRUSH_CONE":         {"label": "Concasseur à cône",                     "category": "Concassage",       "icon": "🔨"},
    "CRUSH_HPGR":         {"label": "HPGR (rouleaux haute pression)",        "category": "Concassage",       "icon": "🔨"},
    "CRUSH_IMPACT":       {"label": "Concasseur à percussion",               "category": "Concassage",       "icon": "🔨"},
    # ─── Classification (4) ──────────────────────────────────────────────
    "SCREEN_VIBRATING":   {"label": "Crible vibrant",                        "category": "Classification",   "icon": "📑"},
    "SCREEN_TROMMEL":     {"label": "Trommel",                               "category": "Classification",   "icon": "📑"},
    "CYCLONE":            {"label": "Hydrocyclone",                          "category": "Classification",   "icon": "🌀"},
    "SPIRAL_CLASSIFIER":  {"label": "Classificateur en spirale",             "category": "Classification",   "icon": "🌀"},
    # ─── Broyage (8) ─────────────────────────────────────────────────────
    "SAG_MILL":           {"label": "SAG Mill",                              "category": "Broyage",          "icon": "⚙️"},
    "AG_MILL":            {"label": "AG Mill (autogène)",                    "category": "Broyage",          "icon": "⚙️"},
    "BALL_MILL":          {"label": "Ball Mill",                             "category": "Broyage",          "icon": "⚙️"},
    "ROD_MILL":           {"label": "Rod Mill",                              "category": "Broyage",          "icon": "⚙️"},
    "VERTIMILL":          {"label": "Vertimill",                             "category": "Broyage",          "icon": "⚙️"},
    "ISAMILL":            {"label": "IsaMill",                               "category": "Broyage",          "icon": "⚙️"},
    "BALL_REGRIND":       {"label": "Ball Mill regrind",                     "category": "Broyage",          "icon": "⚙️"},
    "HIGMILL_REGRIND":    {"label": "HIGmill regrind",                       "category": "Broyage",          "icon": "⚙️"},
    # ─── Gravimétrie (5) ─────────────────────────────────────────────────
    "KNELSON":            {"label": "Concentrateur Knelson",                 "category": "Gravimétrie",      "icon": "🌀"},
    "FALCON":             {"label": "Concentrateur Falcon",                  "category": "Gravimétrie",      "icon": "🌀"},
    "JIG":                {"label": "Jig gravimétrique",                     "category": "Gravimétrie",      "icon": "🌀"},
    "SHAKING_TABLE":      {"label": "Table à secousses",                     "category": "Gravimétrie",      "icon": "🌀"},
    "SPIRAL":             {"label": "Spirale gravimétrique",                 "category": "Gravimétrie",      "icon": "🌀"},
    # ─── Flottation (6) ──────────────────────────────────────────────────
    "FLOTATION_ROUGHER":  {"label": "Flottation rougher",                    "category": "Flottation",       "icon": "🫧"},
    "FLOTATION_CLEANER":  {"label": "Flottation cleaner",                    "category": "Flottation",       "icon": "🫧"},
    "FLOTATION_RECLEANER":{"label": "Flottation recleaner",                  "category": "Flottation",       "icon": "🫧"},
    "FLOTATION_SCAVENGER":{"label": "Flottation scavenger",                  "category": "Flottation",       "icon": "🫧"},
    "COLUMN_FLOTATION":   {"label": "Colonne de flottation",                 "category": "Flottation",       "icon": "🫧"},
    "JAMESON":            {"label": "Cellule Jameson",                       "category": "Flottation",       "icon": "🫧"},
    # ─── Conditionnement (2) ─────────────────────────────────────────────
    "CONDITIONER":        {"label": "Conditionneur",                         "category": "Conditionnement",  "icon": "🥽"},
    "MIXING_TANK":        {"label": "Cuve d'agitation",                      "category": "Conditionnement",  "icon": "🥽"},
    # ─── Épaississement (3) + Filtration (2) ─────────────────────────────
    "THICKENER_HRT":      {"label": "Épaississeur HRT",                      "category": "Épaississement",   "icon": "💧"},
    "THICKENER_PRE_LEACH":{"label": "Épaississeur pré-lixiviation",          "category": "Épaississement",   "icon": "💧"},
    "THICKENER_TAILINGS": {"label": "Épaississeur stériles",                 "category": "Épaississement",   "icon": "💧"},
    "PRESSURE_FILTER":    {"label": "Filtre presse",                         "category": "Filtration",       "icon": "💧"},
    "BELT_FILTER":        {"label": "Filtre à bande",                        "category": "Filtration",       "icon": "💧"},
    # ─── Lixiviation (5) ─────────────────────────────────────────────────
    "LEACH_CIL":          {"label": "Lixiviation CIL",                       "category": "Lixiviation",      "icon": "🧪"},
    "LEACH_CIP":          {"label": "Lixiviation CIP",                       "category": "Lixiviation",      "icon": "🧪"},
    "LEACH_TANK":         {"label": "Tank de lixiviation",                   "category": "Lixiviation",      "icon": "🧪"},
    "HEAP_LEACH":         {"label": "Lixiviation en tas",                    "category": "Lixiviation",      "icon": "🧪"},
    "AUTOCLAVE":          {"label": "Autoclave (POX)",                       "category": "Lixiviation",      "icon": "🧪"},
    # ─── Élution & Doré (5) ──────────────────────────────────────────────
    "ELUTION_ZADRA":      {"label": "Élution Zadra",                         "category": "Élution & Doré",   "icon": "⚡"},
    "ELUTION_AARL":       {"label": "Élution AARL",                          "category": "Élution & Doré",   "icon": "⚡"},
    "ELECTROWINNING":     {"label": "Cellule d'électrolyse",                 "category": "Élution & Doré",   "icon": "⚡"},
    "KILN_REGEN":         {"label": "Four de réactivation carbone",          "category": "Élution & Doré",   "icon": "⚡"},
    "REFINING_FURNACE":   {"label": "Four de raffinage doré",                "category": "Élution & Doré",   "icon": "🥇"},
    # ─── Détox (3) ───────────────────────────────────────────────────────
    "DETOX_INCO":         {"label": "Détox SO2/Air (INCO)",                  "category": "Détox",            "icon": "🛡️"},
    "DETOX_BIO":          {"label": "Détox biologique",                      "category": "Détox",            "icon": "🛡️"},
    "ALKALINE_CHLOR":     {"label": "Chloration alcaline",                   "category": "Détox",            "icon": "🛡️"},
    # ─── Stockage rejets (4) ─────────────────────────────────────────────
    "TAILINGS_THICKENER": {"label": "Épaississeur stériles (rejets)",        "category": "Stockage rejets",  "icon": "🗑️"},
    "TAILINGS_FILTER":    {"label": "Filtre stériles (dry stack)",           "category": "Stockage rejets",  "icon": "🗑️"},
    "TSF":                {"label": "Parc à résidus (TSF)",                  "category": "Stockage rejets",  "icon": "🗑️"},
    "PASTE_BACKFILL":     {"label": "Remblai en pâte",                       "category": "Stockage rejets",  "icon": "🗑️"},
    # ─── Services (3) ────────────────────────────────────────────────────
    "WATER_TREATMENT":    {"label": "Traitement d'eau",                      "category": "Services",         "icon": "💦"},
    "REAGENT_PREP":       {"label": "Préparation des réactifs",              "category": "Services",         "icon": "💦"},
    "REAGENT_DOSING":     {"label": "Dosage des réactifs",                   "category": "Services",         "icon": "💦"},
    # ─── Échantillonnage (1) ─────────────────────────────────────────────
    "SAMPLER_PRIMARY":    {"label": "Échantillonneur primaire",              "category": "Échantillonnage",  "icon": "🧫"},
    # ─── Produit final (1) ───────────────────────────────────────────────
    "BULLION":            {"label": "Lingot doré",                           "category": "Produit final",    "icon": "🟡"},
}


assert len(EQUIPMENT_CATALOG) == 60, f"Catalog must have exactly 60 entries (got {len(EQUIPMENT_CATALOG)})"


def get_grouped_catalog() -> dict[str, list[dict]]:
    """Return the catalog grouped by category, ordered by CATEGORIES list."""
    grouped: dict[str, list[dict]] = {cat: [] for cat in CATEGORIES}
    for code, meta in EQUIPMENT_CATALOG.items():
        cat = meta["category"]
        grouped.setdefault(cat, []).append({"code": code, **meta})
    return grouped
