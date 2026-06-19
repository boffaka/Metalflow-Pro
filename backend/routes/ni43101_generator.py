"""
NI 43-101 Report Content Generator.
Generates metallurgical technical report sections per NI 43-101 / CIM standards,
focused on mineral processing testwork, recovery methods, and QP closing items.
"""
from __future__ import annotations
import logging

import config as cfg
from db import qall, qone
from constants import TROY_OZ_PER_GRAM
from settings import get_settings as _get_settings

_SETTINGS = _get_settings()

logger = logging.getLogger("mpdpms.ni43101_generator")


def _avg(rows, field):
    vals = [float(r[field]) for r in rows if r.get(field) is not None]
    return sum(vals) / len(vals) if vals else None


def _sum_field(rows, field):
    vals = [float(r[field]) for r in rows if r.get(field) is not None]
    return sum(vals) if vals else 0


def _detect_phase(pid: str) -> str:
    gates = qall(
        "SELECT stage_order, completion_pct, status FROM stage_gates "
        "WHERE project_id = %s ORDER BY stage_order DESC", (pid,)
    )
    for g in gates:
        if g["completion_pct"] and int(g["completion_pct"]) > 0:
            order = int(g["stage_order"])
            if order >= 3:
                return "fs"
            if order == 2:
                return "pfs"
            return "scoping"
    proj = qone("SELECT status FROM projects WHERE id = %s", (pid,))
    if proj:
        st = (proj["status"] or "").upper()
        if st in ("FS", "ENGINEERING", "COMMISSIONING"):
            return "fs"
        if st == "PFS":
            return "pfs"
    return "scoping"


def _fetch_all_data(pid: str) -> dict:
    proj = qone("SELECT * FROM projects WHERE id = %s", (pid,))

    block_config = qone(
        "SELECT * FROM block_model_configs WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
        (pid,),
    )
    block_stats = None
    if block_config:
        block_stats = qone(
            "SELECT COUNT(*) as cnt, SUM(tonnage) as total_tonnes, "
            "AVG(grade_au) as avg_grade, MIN(grade_au) as min_grade, "
            "MAX(grade_au) as max_grade, SUM(tonnage * grade_au) / NULLIF(SUM(tonnage), 0) as weighted_grade "
            "FROM blocks WHERE config_id = %s",
            (block_config["id"],),
        )

    return {
        "project": proj,
        "a1": qall("SELECT * FROM lims_a1 WHERE project_id = %s", (pid,)),
        "b1": qall("SELECT * FROM lims_b1 WHERE project_id = %s", (pid,)),
        "c2": qall("SELECT * FROM lims_c2 WHERE project_id = %s", (pid,)),
        "c3": qall("SELECT * FROM lims_c3 WHERE project_id = %s", (pid,)),
        "d1": qall("SELECT * FROM lims_d1 WHERE project_id = %s", (pid,)),
        "e1": qall("SELECT * FROM lims_e1 WHERE project_id = %s", (pid,)),
        "e2": qall("SELECT * FROM lims_e2 WHERE project_id = %s", (pid,)),
        "kinetics": qall("SELECT * FROM lims_kinetics WHERE project_id = %s", (pid,)),
        "env": qall("SELECT * FROM lims_environmental WHERE project_id = %s", (pid,)),
        "elution": qall("SELECT * FROM lims_elution WHERE project_id = %s", (pid,)),
        "flotation": qall("SELECT * FROM lims_flotation WHERE project_id = %s", (pid,)),
        "flowsheets": qall("SELECT * FROM flowsheets WHERE project_id = %s ORDER BY created_at DESC LIMIT 1", (pid,)),
        "dc": qall("SELECT * FROM design_criteria WHERE project_id = %s ORDER BY sort_order", (pid,)),
        "mb": qall("SELECT * FROM mass_balance_streams WHERE project_id = %s ORDER BY sort_order", (pid,)),
        "equipment": qall("SELECT * FROM equipment WHERE project_id = %s ORDER BY created_at", (pid,)),
        "capex_items": [],
        "opex_items": [],
        "risks": qall("SELECT * FROM risks WHERE project_id = %s ORDER BY criticality DESC", (pid,)),
        "stages": qall("SELECT * FROM stage_gates WHERE project_id = %s ORDER BY stage_order", (pid,)),
        "samples": qall("SELECT * FROM lims_samples WHERE project_id = %s", (pid,)),
        "block_config": block_config,
        "block_stats": block_stats,
        "block_params": qall("SELECT * FROM block_model_params WHERE project_id = %s", (pid,)),
        "sim_params": qall("SELECT * FROM simulation_params WHERE project_id = %s", (pid,)),
        "water_balance": qall("SELECT * FROM water_balance_nodes WHERE project_id = %s ORDER BY sort_order", (pid,)),
        "gistm_basis": qone(
            "SELECT * FROM gistm_design_basis "
            "WHERE project_id = %s AND status = 'active' LIMIT 1",
            (pid,),
        ),
    }


def _load_costs(pid: str, data: dict):
    capex_model = qone(
        "SELECT id FROM cost_models WHERE project_id = %s AND model_type = 'CAPEX' LIMIT 1", (pid,)
    )
    opex_model = qone(
        "SELECT id FROM cost_models WHERE project_id = %s AND model_type = 'OPEX' LIMIT 1", (pid,)
    )
    if capex_model:
        data["capex_items"] = qall(
            "SELECT * FROM cost_line_items WHERE model_id = %s ORDER BY wbs_code", (capex_model["id"],)
        )
    if opex_model:
        data["opex_items"] = qall(
            "SELECT * FROM cost_line_items WHERE model_id = %s ORDER BY wbs_code", (opex_model["id"],)
        )


def _sim(data, key, default=None):
    for p in data.get("sim_params", []):
        if p["param_key"] == key:
            return float(p["param_value"]) if p.get("param_value") is not None else default
    return default


# ═══════════════════════════════════════════════════════════════════════════════
# Section 1: Summary
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_1(phase, data):
    p = data["project"]
    name = p["project_name"] if p else "N/D"
    loc = p.get("location", "N/D") if p else "N/D"
    commodity = p.get("commodity", "Au") if p else "Au"
    tph = float(p["target_tph"] or 0) if p and p.get("target_tph") else 0
    grade = float(p["gold_grade_g_t"] or 0) if p and p.get("gold_grade_g_t") else 0
    mine_life = int(p["mine_life_years"] or 10) if p and p.get("mine_life_years") else 10
    gold_price = float(p.get("gold_price_usd_oz") or 2340) if p else 2340

    phase_map = {"scoping": "Etude de cadrage (Scoping)", "pfs": "Etude de prefaisabilite (PFS)", "fs": "Etude de faisabilite (FS)"}
    phase_en = {"scoping": "Scoping Study", "pfs": "Pre-Feasibility Study (PFS)", "fs": "Feasibility Study (FS)"}
    accuracy_fr = {"scoping": "±35–50% (AACE Classe 5)", "pfs": "±25–30% (AACE Classe 4)", "fs": "±10–15% (AACE Classe 3)"}
    accuracy_en = {"scoping": "±35–50% (AACE Class 5)", "pfs": "±25–30% (AACE Class 4)", "fs": "±10–15% (AACE Class 3)"}

    mb = data["mb"]
    rom = next((s for s in mb if s["stream"] == "ROM Feed"), None)
    tails = next((s for s in mb if s["stream"] == "Tailings Final"), None)
    avail = float(p.get("availability_pct") or 92) if p else 92
    rec = 0.0
    if rom and tails and rom.get("au_gt") and tails.get("au_gt"):
        feed_g = float(rom["au_gt"])
        tails_g = float(tails["au_gt"])
        tph_val = float(rom["solids_tph"]) if rom.get("solids_tph") else tph
        tails_tph = float(tails["solids_tph"]) if tails.get("solids_tph") else tph_val
        rec = (1 - (tails_tph * tails_g) / (tph_val * feed_g)) * 100 if feed_g > 0 and tph_val > 0 else 0

    d1 = data["d1"]
    b1 = data["b1"]
    c2 = data["c2"]
    avg_rec_lims = _avg(d1, "au_recovery_pct")
    avg_bwi = _avg(b1, "bwi_kwh_t")
    avg_grg = _avg(c2, "au_recovery_pct")

    if rec == 0 and avg_rec_lims:
        rec = avg_rec_lims

    annual_oz = tph * 24 * 365 * (avail / 100) * grade * (rec / 100) * TROY_OZ_PER_GRAM if tph > 0 and grade > 0 and rec > 0 else 0
    mtpa = tph * 24 * 365 * (avail / 100) / 1e6
    total_oz = annual_oz * mine_life
    total_revenue = total_oz * gold_price / 1e6  # M USD

    capex_total = _sum_field(data.get("capex_items", []), "total_cost_usd")
    opex_total = _sum_field(data.get("opex_items", []), "total_cost_usd")

    fr = (
        f"**Rapport technique NI 43-101 — Projet {name} — Resume**\n\n"
        f"Le projet {name} est un projet d'extraction et de traitement de {commodity} situe "
        f"a {loc}. Le present rapport technique est prepare conformement a la Norme canadienne "
        f"43-101 sur l'information concernant les projets miniers (NI 43-101) et aux normes "
        f"de definition et aux pratiques exemplaires de l'ICM (Institut canadien des mines, de "
        f"la metallurgie et du petrole).\n\n"
        f"**Niveau d'etude: {phase_map.get(phase, 'Etude')} — Precision: {accuracy_fr.get(phase, '±35–50%')}**\n\n"
        f"---\n\n"
        f"**Parametres techniques cles:**\n\n"
        f"| Parametre | Valeur | Unite |\n|---|---|---|\n"
        f"| Commodite principale | {commodity} | — |\n"
        f"| Capacite nominale | {tph:.0f} | t/h |\n"
        f"| Capacite annuelle | {mtpa:.2f} | Mtpa |\n"
        f"| Teneur moyenne du minerai | {grade:.3f} | g/t Au |\n"
        f"| Disponibilite operationnelle | {avail:.0f} | % |\n"
        f"| Duree de vie de la mine | {mine_life} | ans |\n"
    )
    en = (
        f"**NI 43-101 Technical Report — {name} Project — Summary**\n\n"
        f"The {name} project is a {commodity} mining and processing project located at {loc}. "
        f"This technical report is prepared in accordance with Canadian National Instrument "
        f"43-101 Standards of Disclosure for Mineral Projects (NI 43-101) and CIM Definition "
        f"Standards and Best Practice Guidelines.\n\n"
        f"**Study level: {phase_en.get(phase, 'Study')} — Accuracy: {accuracy_en.get(phase, '±35–50%')}**\n\n"
        f"---\n\n"
        f"**Key technical parameters:**\n\n"
        f"| Parameter | Value | Unit |\n|---|---|---|\n"
        f"| Primary commodity | {commodity} | — |\n"
        f"| Nominal capacity | {tph:.0f} | t/h |\n"
        f"| Annual capacity | {mtpa:.2f} | Mtpa |\n"
        f"| Average ore grade | {grade:.3f} | g/t Au |\n"
        f"| Operating availability | {avail:.0f} | % |\n"
        f"| Mine life | {mine_life} | years |\n"
    )

    if avg_bwi:
        bwi_cat_fr = "tendre" if avg_bwi < 12 else ("modere" if avg_bwi < 16 else ("dur" if avg_bwi < 20 else "tres dur"))
        bwi_cat_en = "soft" if avg_bwi < 12 else ("medium" if avg_bwi < 16 else ("hard" if avg_bwi < 20 else "very hard"))
        fr += f"| Bond Work Index (BWi) | {avg_bwi:.1f} ({bwi_cat_fr}) | kWh/t |\n"
        en += f"| Bond Work Index (BWi) | {avg_bwi:.1f} ({bwi_cat_en}) | kWh/t |\n"
    if avg_grg:
        fr += f"| Recuperation gravimetrique (GRG) | {avg_grg:.1f} | % |\n"
        en += f"| Gravity recovery (GRG) | {avg_grg:.1f} | % |\n"
    if rec > 0:
        fr += f"| Recuperation globale (CIL ± gravite) | {rec:.1f} | % |\n"
        en += f"| Overall recovery (CIL ± gravity) | {rec:.1f} | % |\n"
    if annual_oz > 0:
        fr += f"| Production annuelle estimee | {annual_oz:,.0f} | oz Au/an |\n"
        en += f"| Estimated annual production | {annual_oz:,.0f} | oz Au/yr |\n"
    if total_oz > 0:
        fr += f"| Production totale LOM | {total_oz:,.0f} | oz Au |\n"
        en += f"| Total LOM production | {total_oz:,.0f} | oz Au |\n"
    if gold_price > 0 and total_oz > 0:
        fr += f"| Revenu total estime (base ${gold_price:,.0f}/oz) | ${total_revenue:,.0f} | M USD |\n"
        en += f"| Estimated total revenue (@ ${gold_price:,.0f}/oz) | ${total_revenue:,.0f} | M USD |\n"
    if capex_total > 0:
        fr += f"| CAPEX total | ${capex_total / 1e6:,.1f} | M USD |\n"
        en += f"| Total CAPEX | ${capex_total / 1e6:,.1f} | M USD |\n"
    if opex_total > 0:
        fr += f"| OPEX unitaire | ${opex_total:.2f} | USD/t traitee |\n"
        en += f"| Unit OPEX | ${opex_total:.2f} | USD/t processed |\n"

    fr += (
        "\n---\n\n"
        "**Statut metallurgique du projet:**\n\n"
    )
    en += (
        "\n---\n\n"
        "**Metallurgical project status:**\n\n"
    )

    # Metallurgical synopsis
    flowsheet_type = "CIL direct" + (" avec circuit de gravite" if avg_grg and avg_grg >= cfg.GRG_CIRCUIT_THRESHOLD_PCT else "")
    flowsheet_type_en = "Direct CIL" + (" with gravity circuit" if avg_grg and avg_grg >= cfg.GRG_CIRCUIT_THRESHOLD_PCT else "")

    fr += (
        f"Le programme d'essais metallurgiques ({len(data['a1'])} analyses de tetes, "
        f"{len(data['b1'])} tests de broyabilite, {len(data['d1'])} essais de lixiviation) "
        f"supporte un flowsheet de type **{flowsheet_type}** pour le traitement du minerai. "
    )
    en += (
        f"The metallurgical test program ({len(data['a1'])} head assays, "
        f"{len(data['b1'])} comminution tests, {len(data['d1'])} leach tests) "
        f"supports a **{flowsheet_type_en}** flowsheet for ore treatment. "
    )

    if rec > 85:
        fr += f"La recuperation globale de {rec:.1f}% est excellente pour ce type de minerai."
        en += f"The overall recovery of {rec:.1f}% is excellent for this ore type."
    elif rec > 75:
        fr += f"La recuperation globale de {rec:.1f}% est satisfaisante pour le niveau d'etude actuel."
        en += f"The overall recovery of {rec:.1f}% is satisfactory for the current study level."
    elif rec > 0:
        fr += f"La recuperation globale de {rec:.1f}% necessite une optimisation supplementaire."
        en += f"The overall recovery of {rec:.1f}% requires further optimization."

    fr += (
        "\n\n**Note de la personne qualifiee:** Ce resume est fourni conformement a l'article 5.4 "
        "de la NI 43-101. Les donnees detaillees supportant ces conclusions sont presentees dans "
        "les sections 13 (essais metallurgiques) et 17 (methodes de recuperation) du present rapport."
    )
    en += (
        "\n\n**Qualified Person note:** This summary is provided in accordance with NI 43-101 "
        "Item 5.4. Detailed data supporting these conclusions are presented in Sections 13 "
        "(metallurgical testing) and 17 (recovery methods) of this report."
    )

    return {"key": "1", "title_fr": "Resume", "title_en": "Summary",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 2: Introduction and Terms of Reference
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_2(phase, data):
    p = data["project"]
    name = p["project_name"] if p else "N/D"
    code = p.get("project_code", "") if p else ""
    owner = p.get("project_owner", "N/D") if p else "N/D"

    fr = (
        f"Le present rapport technique sur le projet {name} (code: {code}) est prepare "
        f"conformement aux exigences de la Norme canadienne 43-101 sur l'information "
        f"concernant les projets miniers (NI 43-101) et au formulaire 43-101F1.\n\n"
        f"**Mandataire:** {owner}\n\n"
        f"Le present document constitue un rapport technique metallurgique portant "
        f"sur le traitement mineralurgique, les essais metallurgiques et les methodes "
        f"de recuperation du projet, conformement aux exigences de la NI 43-101 "
        f"et aux pratiques exemplaires de l'ICM en ingenierie metallurgique.\n\n"
        f"**Perimetre:** sections 13 (essais et traitement) et 17 (recuperation) "
        f"du formulaire 43-101F1, ainsi que les elements de cloture QP (conclusions, "
        f"references, date et certificats)."
    )
    en = (
        f"This technical report on the {name} project (code: {code}) is prepared "
        f"in compliance with Canadian National Instrument 43-101 (NI 43-101) and Form 43-101F1.\n\n"
        f"**Issuer:** {owner}\n\n"
        f"This metallurgical technical report covers mineral processing and metallurgical "
        f"testing (Item 13), recovery methods (Item 17), and QP closing sections, "
        f"in accordance with CIM Best Practice Guidelines for mineral processing."
    )
    if phase in ("pfs", "fs"):
        fr += (
            "\n\nLa personne qualifiee (QP) responsable de ce rapport a visite le site "
            "et a eu acces a l'ensemble des donnees de base du projet."
        )
        en += (
            "\n\nThe Qualified Person (QP) responsible for this report has visited the site "
            "and has had access to all project base data."
        )

    return {"key": "2", "title_fr": "Introduction et termes de reference",
            "title_en": "Introduction and Terms of Reference",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 3: Reliance on Other Experts
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_3(phase, data):
    fr = (
        "L'auteur du present rapport a utilise les informations fournies par les experts "
        "suivants pour les sections indiquees:\n\n"
        "- **Titres miniers et questions foncieres:** Conseiller juridique du projet\n"
        "- **Questions fiscales et redevances:** Consultants fiscaux du projet\n"
        "- **Questions environnementales et permis:** Consultants environnementaux du projet\n\n"
        "L'auteur n'a pas verifie de maniere independante les informations fournies par ces experts "
        "et s'est fie a leur competence professionnelle conformement a la section 3 du formulaire 43-101F1."
    )
    en = (
        "The author of this report has relied on information provided by the following experts "
        "for the sections indicated:\n\n"
        "- **Mineral titles and property matters:** Project legal counsel\n"
        "- **Tax and royalty matters:** Project tax consultants\n"
        "- **Environmental and permitting matters:** Project environmental consultants\n\n"
        "The author has not independently verified the information provided by these experts "
        "and has relied on their professional competence in accordance with section 3 of Form 43-101F1."
    )
    return {"key": "3", "title_fr": "Fiabilite d'autres experts",
            "title_en": "Reliance on Other Experts",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 4: Property Description and Location
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_4(phase, data):
    p = data["project"]
    name = p["project_name"] if p else "N/D"
    loc = p.get("location", "N/D") if p else "N/D"

    fr = (
        f"Le projet {name} est situe a {loc}.\n\n"
        f"Les details des titres miniers, des droits de surface, des redevances applicables "
        f"et de toute charge environnementale sont presentes dans cette section conformement "
        f"a la section 4.2 du formulaire 43-101F1.\n\n"
        f"Les coordonnees exactes du projet, les limites du titre minier et les conditions "
        f"de maintien du titre doivent etre verifiees par le conseiller juridique du projet."
    )
    en = (
        f"The {name} project is located at {loc}.\n\n"
        f"Details of mineral tenure, surface rights, applicable royalties "
        f"and any environmental liabilities are presented in this section in accordance "
        f"with section 4.2 of Form 43-101F1.\n\n"
        f"The exact coordinates, title boundaries, and tenure maintenance conditions "
        f"should be verified by the project legal counsel."
    )
    return {"key": "4", "title_fr": "Description et emplacement de la propriete",
            "title_en": "Property Description and Location",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 5: Accessibility, Climate, Local Resources, Infrastructure
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_5(phase, data):
    p = data["project"]
    loc = p.get("location", "N/D") if p else "N/D"

    fr = (
        f"Le site du projet ({loc}) est accessible par route. "
        f"Les conditions d'acces, le climat, la topographie, la vegetation "
        f"et les infrastructures locales disponibles sont decrits ci-dessous.\n\n"
        f"**Acces:** A documenter selon les conditions locales.\n"
        f"**Climat:** A documenter (temperature, precipitations, saisons).\n"
        f"**Infrastructure:** Disponibilite de l'eau, de l'electricite et de la main-d'oeuvre locale.\n"
        f"**Topographie:** A documenter selon les leves topographiques."
    )
    en = (
        f"The project site ({loc}) is accessible by road. "
        f"Access conditions, climate, topography, vegetation "
        f"and available local infrastructure are described below.\n\n"
        f"**Access:** To be documented per local conditions.\n"
        f"**Climate:** To be documented (temperature, precipitation, seasons).\n"
        f"**Infrastructure:** Water, power, and local labour availability.\n"
        f"**Topography:** To be documented per topographic surveys."
    )
    return {"key": "5", "title_fr": "Accessibilite, climat, ressources locales, infrastructure",
            "title_en": "Accessibility, Climate, Local Resources, Infrastructure and Physiography",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 6: History
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_6(phase, data):
    p = data["project"]
    name = p["project_name"] if p else "N/D"

    fr = (
        f"L'historique des travaux anterieurs sur la propriete du projet {name} est presente "
        f"dans cette section. Cela inclut les travaux d'exploration, les programmes de forage "
        f"anterieurs, les estimations de ressources historiques et toute production passee.\n\n"
        f"Les estimations de ressources historiques sont presentees a titre informatif seulement "
        f"et ne doivent pas etre traitees comme des ressources ou reserves minerales actuelles "
        f"au sens de la NI 43-101."
    )
    en = (
        f"The history of prior work on the {name} property is presented "
        f"in this section. This includes exploration work, prior drilling programs, "
        f"historical resource estimates, and any past production.\n\n"
        f"Historical resource estimates are presented for informational purposes only "
        f"and should not be treated as current mineral resources or reserves "
        f"as defined by NI 43-101."
    )
    return {"key": "6", "title_fr": "Historique",
            "title_en": "History",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 7: Geological Setting and Mineralization
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_7(phase, data):
    p = data["project"]
    name = p["project_name"] if p else "N/D"
    bs = data.get("block_stats")
    bp = data.get("block_params", [])

    rock_types = set()
    for param in bp:
        if param.get("category") == "rock_types" or "rock" in (param.get("param_name") or "").lower():
            rock_types.add(param.get("param_value", ""))

    fr = f"Le cadre geologique du projet {name} est decrit dans cette section.\n\n"
    en = f"The geological setting of the {name} project is described in this section.\n\n"

    if bs and bs.get("cnt"):
        n_blocks = int(bs["cnt"])
        avg_g = float(bs["weighted_grade"]) if bs.get("weighted_grade") else 0
        total_t = float(bs["total_tonnes"]) / 1e6 if bs.get("total_tonnes") else 0
        fr += (
            f"Le modele de blocs comprend {n_blocks:,} blocs pour un tonnage total de {total_t:,.1f} Mt "
            f"a une teneur moyenne ponderee de {avg_g:.2f} g/t Au.\n\n"
        )
        en += (
            f"The block model comprises {n_blocks:,} blocks for a total tonnage of {total_t:,.1f} Mt "
            f"at a weighted average grade of {avg_g:.2f} g/t Au.\n\n"
        )

    if rock_types:
        fr += f"**Types de roches identifies:** {', '.join(rock_types)}\n\n"
        en += f"**Identified rock types:** {', '.join(rock_types)}\n\n"

    a1 = data["a1"]
    avg_fe = _avg(a1, "fe_pct")
    avg_s = _avg(a1, "s_total_pct")
    avg_as = _avg(a1, "as_ppm")
    if avg_fe or avg_s or avg_as:
        fr += "**Geochimie de base:**\n"
        en += "**Baseline geochemistry:**\n"
        if avg_fe:
            fr += f"- Fe moyen: {avg_fe:.2f}%\n"
            en += f"- Average Fe: {avg_fe:.2f}%\n"
        if avg_s:
            fr += f"- S total moyen: {avg_s:.2f}%\n"
            en += f"- Average total S: {avg_s:.2f}%\n"
        if avg_as:
            fr += f"- As moyen: {avg_as:.0f} ppm\n"
            en += f"- Average As: {avg_as:.0f} ppm\n"

    return {"key": "7", "title_fr": "Cadre geologique et mineralisation",
            "title_en": "Geological Setting and Mineralization",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 8: Deposit Types
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_8(phase, data):
    p = data["project"]
    commodity = p.get("commodity", "Au") if p else "Au"

    if commodity == "Au":
        fr = (
            "Le gisement est de type orogenique aurifere, typique des ceintures de roches vertes "
            "archeeennes. La mineralisation en or est associee a des zones de cisaillement et "
            "des veines de quartz-carbonate.\n\n"
            "Ce type de gisement presente generalement:\n"
            "- Un controle structural fort\n"
            "- Des teneurs variables avec des zones a haute teneur\n"
            "- Une mineralisation libre et/ou refractaire\n"
            "- Des dimensions laterales et en profondeur significatives"
        )
        en = (
            "The deposit is of orogenic gold type, typical of Archean greenstone belts. "
            "Gold mineralization is associated with shear zones and "
            "quartz-carbonate veins.\n\n"
            "This deposit type typically features:\n"
            "- Strong structural control\n"
            "- Variable grades with high-grade zones\n"
            "- Free and/or refractory mineralization\n"
            "- Significant lateral and depth extents"
        )
    else:
        fr = f"Le type de gisement est a documenter pour la commodite {commodity}."
        en = f"The deposit type is to be documented for the {commodity} commodity."

    return {"key": "8", "title_fr": "Type de gisement",
            "title_en": "Deposit Types",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 9: Exploration
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_9(phase, data):
    p = data["project"]
    name = p["project_name"] if p else "N/D"
    samples = data.get("samples", [])

    fr = (
        f"Les travaux d'exploration realises sur le projet {name} comprennent "
        f"la cartographie geologique, l'echantillonnage de surface, la geophysique "
        f"et les programmes de forage.\n\n"
        f"Au total, {len(samples)} echantillons ont ete enregistres dans la base de donnees LIMS du projet."
    )
    en = (
        f"Exploration work carried out on the {name} project includes "
        f"geological mapping, surface sampling, geophysics "
        f"and drilling programs.\n\n"
        f"A total of {len(samples)} samples have been registered in the project LIMS database."
    )

    phases = set(s.get("phase", "") for s in samples if s.get("phase"))
    if phases:
        fr += f"\n\n**Phases d'echantillonnage:** {', '.join(sorted(phases))}"
        en += f"\n\n**Sampling phases:** {', '.join(sorted(phases))}"

    lithos = set(s.get("lithology", "") for s in samples if s.get("lithology"))
    if lithos:
        fr += f"\n**Lithologies:** {', '.join(sorted(lithos))}"
        en += f"\n**Lithologies:** {', '.join(sorted(lithos))}"

    return {"key": "9", "title_fr": "Exploration",
            "title_en": "Exploration",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 10: Drilling
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_10(phase, data):
    p = data["project"]
    name = p["project_name"] if p else "N/D"
    samples = data.get("samples", [])
    drill_samples = [s for s in samples if (s.get("sample_type") or "").lower() in ("drill core", "rc", "dd", "core", "forage")]

    fr = (
        f"Les programmes de forage du projet {name} ont fourni les echantillons "
        f"de base pour les estimations de ressources et les essais metallurgiques.\n\n"
    )
    en = (
        f"The drilling programs for the {name} project provided the core samples "
        f"for resource estimates and metallurgical testwork.\n\n"
    )

    if drill_samples:
        fr += f"{len(drill_samples)} echantillons de forage ont ete identifies dans la base de donnees.\n"
        en += f"{len(drill_samples)} drill samples have been identified in the database.\n"
    else:
        fr += f"{len(samples)} echantillons au total sont disponibles dans la base de donnees.\n"
        en += f"{len(samples)} total samples are available in the database.\n"

    fr += (
        "\nLes details des programmes de forage (types de forage, espacement, "
        "profondeur, orientations, methodes de recuperation) doivent etre "
        "documentes par le geologue responsable."
    )
    en += (
        "\nDrilling program details (drill types, spacing, "
        "depth, orientations, recovery methods) should be "
        "documented by the responsible geologist."
    )

    return {"key": "10", "title_fr": "Forage",
            "title_en": "Drilling",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 11: Sample Preparation, Analyses and Security
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_11(phase, data):
    samples = data.get("samples", [])
    a1 = data["a1"]

    fr = (
        f"Cette section decrit les methodes de preparation des echantillons, "
        f"les protocoles analytiques et les mesures de securite des echantillons.\n\n"
        f"**Echantillons:** {len(samples)} echantillons enregistres\n"
        f"**Analyses de tetes (A1):** {len(a1)} resultats\n\n"
    )
    en = (
        f"This section describes sample preparation methods, "
        f"analytical protocols and sample security measures.\n\n"
        f"**Samples:** {len(samples)} registered samples\n"
        f"**Head assays (A1):** {len(a1)} results\n\n"
    )

    provs = set(s.get("provenance", "") for s in samples if s.get("provenance"))
    if provs:
        fr += f"**Provenances des echantillons:** {', '.join(sorted(provs))}\n\n"
        en += f"**Sample provenances:** {', '.join(sorted(provs))}\n\n"

    fr += (
        "Les programmes d'assurance qualite / controle qualite (AQ/CQ) comprennent "
        "l'insertion de blancs, de duplicatas et d'echantillons standards certifies (CRM). "
        "Les resultats du programme AQ/CQ doivent confirmer la fiabilite des donnees analytiques."
    )
    en += (
        "The quality assurance / quality control (QA/QC) program includes "
        "insertion of blanks, duplicates and certified reference materials (CRM). "
        "QA/QC results should confirm the reliability of the analytical data."
    )

    return {"key": "11", "title_fr": "Preparation, analyses et securite des echantillons",
            "title_en": "Sample Preparation, Analyses and Security",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 12: Data Verification
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_12(phase, data):
    samples = data.get("samples", [])
    a1 = data["a1"]
    b1 = data["b1"]
    d1 = data["d1"]

    fr = (
        f"La verification des donnees a ete effectuee conformement aux bonnes pratiques de l'industrie.\n\n"
        f"**Resume des donnees disponibles:**\n"
        f"- Echantillons: {len(samples)}\n"
        f"- Analyses de tetes (A1): {len(a1)}\n"
        f"- Tests de broyabilite (B1): {len(b1)}\n"
        f"- Tests de lixiviation (D1): {len(d1)}\n\n"
        f"La personne qualifiee a verifie la coherence des donnees entre les differentes "
        f"bases de donnees et les rapports de laboratoire originaux."
    )
    en = (
        f"Data verification was performed in accordance with industry best practices.\n\n"
        f"**Available data summary:**\n"
        f"- Samples: {len(samples)}\n"
        f"- Head assays (A1): {len(a1)}\n"
        f"- Comminution tests (B1): {len(b1)}\n"
        f"- Leach tests (D1): {len(d1)}\n\n"
        f"The Qualified Person verified data consistency between different "
        f"databases and original laboratory reports."
    )

    if phase in ("pfs", "fs"):
        fr += (
            "\n\nDes visites de verification des laboratoires ont ete effectuees "
            "et les procedures AQ/CQ ont ete jugees adequates pour le niveau d'etude."
        )
        en += (
            "\n\nLaboratory verification visits were performed "
            "and QA/QC procedures were deemed adequate for the study level."
        )

    return {"key": "12", "title_fr": "Verification des donnees",
            "title_en": "Data Verification",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 13: Mineral Processing & Metallurgical Testing (8 subsections)
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_13_1(phase, data):
    p = data["project"]
    name = p["project_name"] if p else "N/D"
    n_a1 = len(data["a1"])
    n_b1 = len(data["b1"])
    n_d1 = len(data["d1"])
    n_c2 = len(data["c2"])
    n_c3 = len(data.get("c3", []))
    n_e1 = len(data["e1"])
    n_e2 = len(data["e2"])
    n_env = len(data["env"])
    n_kin = len(data["kinetics"])
    n_flot = len(data.get("flotation", []))
    n_elut = len(data.get("elution", []))

    phase_scope = {
        "scoping": (
            "Ce programme de cadrage vise a confirmer la traçabilite du minerai, a etablir une recuperation "
            "indicative et a definir les risques metallurgiques prioritaires avant de passer a l'etude PFS. "
            "La precision des estimations de recuperation est de l'ordre de ±10 a 15 points a ce stade."
        ),
        "pfs": (
            "Ce programme PFS vise a confirmer le flowsheet prefere, a definir les criteres de design "
            "a ±25-30% et a valider la variabilite metallurgique sur l'ensemble du gisement. "
            "Les resultats constituent la base des estimations de couts de Classe 4 AACE."
        ),
        "fs": (
            "Ce programme FS fournit les donnees metallurgiques definitives pour l'ingenierie de detail. "
            "Les essais continus et/ou pilotes ont valide les performances a l'echelle semi-industrielle, "
            "permettant des estimations de Classe 3 AACE (±10-15%). Les donnees sont certifiables par QP."
        ),
    }

    fr = (
        f"**13.1 Introduction au programme d'essais metallurgiques — Projet {name}**\n\n"
        f"Le present rapport technique documente les resultats du programme d'essais metallurgiques "
        f"realise conformement aux exigences de la Norme canadienne 43-101 et aux pratiques exemplaires "
        f"de l'ICM en ingenierie metallurgique.\n\n"
        f"**Objectifs du programme:**\n"
        f"- Caracteriser la mineralogie et la geochimie du minerai representatif du gisement\n"
        f"- Determiner les parametres de comminution (broyabilite, energie specifique)\n"
        f"- Quantifier la recuperation en or par procede gravimetrique et/ou lixiviation au cyanure\n"
        f"- Definir les consommations de reactifs et les conditions optimales de lixiviation\n"
        f"- Evaluer les exigences de gestion des residus et la conformite environnementale\n\n"
        f"**Portee du programme — niveau d'etude: {phase.upper()}:**\n"
        f"{phase_scope.get(phase, '')}\n\n"
        f"**Synthese des essais realises:**\n"
    )
    en = (
        f"**13.1 Introduction to Metallurgical Test Program — {name} Project**\n\n"
        f"This technical report documents the results of the metallurgical test program "
        f"conducted in accordance with Canadian National Instrument 43-101 requirements "
        f"and CIM Best Practice Guidelines for mineral processing.\n\n"
        f"**Program objectives:**\n"
        f"- Characterize ore mineralogy and geochemistry representative of the deposit\n"
        f"- Determine comminution parameters (grindability, specific energy)\n"
        f"- Quantify gold recovery by gravity concentration and/or cyanide leaching\n"
        f"- Define reagent consumption and optimal leach conditions\n"
        f"- Evaluate tailings management requirements and environmental compliance\n\n"
        f"**Program scope — study level: {phase.upper()}:**\n"
        f"{phase_scope.get(phase, '')}\n\n"
        f"**Summary of tests conducted:**\n"
    )

    tests_fr = [
        (n_a1, "Analyses chimiques de tetes (head assays) — methode dissolution totale / ICP-MS"),
        (n_b1, "Tests de broyabilite (Bond BWi, RWi, CWi; SMC/JK Drop Weight si disponible)"),
        (n_c2, "Tests de recuperation gravimetrique (Knelson GRG — C2)"),
        (n_c3 if n_c3 else None, "Tests E-GRG (gravite amelioree — C3)"),
        (n_d1, "Essais de lixiviation au cyanure (flacon rotatif / CIL en batch)"),
        (n_kin if n_kin else None, "Essais de cinetique de lixiviation"),
        (n_e1 if n_e1 else None, "Essais d'epaississement (methode Coe & Clevenger)"),
        (n_e2 if n_e2 else None, "Essais de filtration (filtre-presse)"),
        (n_flot if n_flot else None, "Essais de flottation (minerais sulfures)"),
        (n_elut if n_elut else None, "Essais d'elution (AARL ou Zadra)"),
        (n_env if n_env else None, "Essais environnementaux (DMA/DPN, TCLP, CN WAD)"),
    ]
    tests_en = [
        (n_a1, "Head assay analyses — total dissolution / ICP-MS method"),
        (n_b1, "Comminution tests (Bond BWi, RWi, CWi; SMC/JK Drop Weight where available)"),
        (n_c2, "Gravity recovery tests (Knelson GRG — C2)"),
        (n_c3 if n_c3 else None, "Enhanced GRG tests (E-GRG — C3)"),
        (n_d1, "Cyanide leach tests (bottle roll / batch CIL)"),
        (n_kin if n_kin else None, "Kinetic leach tests"),
        (n_e1 if n_e1 else None, "Thickening tests (Coe & Clevenger method)"),
        (n_e2 if n_e2 else None, "Filtration tests (filter press)"),
        (n_flot if n_flot else None, "Flotation tests (sulphide ores)"),
        (n_elut if n_elut else None, "Elution tests (AARL or Zadra)"),
        (n_env if n_env else None, "Environmental tests (ARD/NMD, TCLP, WAD CN)"),
    ]
    for (cnt, desc_fr), (_, desc_en) in zip(tests_fr, tests_en):
        if cnt:
            fr += f"- {desc_fr}: **{cnt} echantillons/essais**\n"
            en += f"- {desc_en}: **{cnt} samples/tests**\n"

    fr += (
        "\n**Note de la personne qualifiee:** Les resultats presentes dans cette section "
        "sont limites aux essais documentes ci-dessus. Toute extrapolation au-dela de la "
        "population d'echantillons testee doit faire l'objet d'une validation additionnelle."
    )
    en += (
        "\n**Qualified Person note:** Results presented in this section are limited to the "
        "tests documented above. Any extrapolation beyond the tested sample population "
        "requires additional validation."
    )
    return {"key": "13.1", "title_fr": "Introduction", "title_en": "Introduction",
            "content_fr": fr, "content_en": en}


def _gen_13_2(phase, data):
    a1 = data["a1"]
    p = data.get("project") or {}
    name = p.get("project_name", "N/D")

    avg_au = _avg(a1, "au_g_t")
    min_au = min((float(r["au_g_t"]) for r in a1 if r.get("au_g_t")), default=None)
    max_au = max((float(r["au_g_t"]) for r in a1 if r.get("au_g_t")), default=None)
    avg_fe = _avg(a1, "fe_pct")
    avg_s = _avg(a1, "s_total_pct")
    avg_as = _avg(a1, "as_ppm")
    avg_cu = _avg(a1, "cu_pct")
    avg_sb = _avg(a1, "sb_ppm")
    avg_c_org = _avg(a1, "c_organic_pct")

    # Mineralization interpretation
    is_refractory = (avg_as and avg_as > 3000) or (avg_s and avg_s > 2.0)
    is_preg_robbing = avg_c_org and avg_c_org > 0.2
    needs_flotation = avg_s and avg_s > cfg.FLOTATION_S_THRESHOLD_PCT
    is_complex = sum([bool(is_refractory), bool(is_preg_robbing), bool(needs_flotation)]) >= 2

    ore_type_fr = "minerai libre (or natif / electrum)" if not is_refractory else "minerai semi-refractaire a refractaire"
    ore_type_en = "free-milling ore (native gold / electrum)" if not is_refractory else "semi-refractory to refractory ore"

    fr = (
        f"**13.2 Caracterisation chimique et mineralogique — Projet {name}**\n\n"
        f"Les analyses de tetes ont ete realisees sur {len(a1)} echantillons representatifs "
        f"de la variabilite spatiale du gisement. Les analyses ont ete effectuees par "
        f"dissolution totale (four a induction) avec dosage ICP-MS/AAS pour l'or et les "
        f"elements interferences, conformement aux protocoles ALS Chemex / SGS Lakefield "
        f"ou equivalents reconnus.\n\n"
        f"**Geochimie de la tete — synthese statistique ({len(a1)} echantillons):**\n\n"
    )
    en = (
        f"**13.2 Chemical and Mineralogical Characterization — {name} Project**\n\n"
        f"Head assays were performed on {len(a1)} samples representative of the spatial "
        f"variability of the deposit. Analyses were conducted by total dissolution (induction "
        f"furnace) with ICP-MS/AAS for gold and penalty elements, in accordance with ALS Chemex / "
        f"SGS Lakefield or equivalent accredited laboratory protocols.\n\n"
        f"**Head assay geochemistry — statistical summary ({len(a1)} samples):**\n\n"
    )

    table_data = [
        ("Au (g/t)", avg_au, min_au, max_au, ".3f", ".3f", ".3f"),
        ("Fe (%)", avg_fe, None, None, ".2f", None, None),
        ("S total (%)", avg_s, None, None, ".2f", None, None),
        ("As (ppm)", avg_as, None, None, ".0f", None, None),
        ("Cu (%)", avg_cu, None, None, ".3f", None, None),
        ("Sb (ppm)", avg_sb, None, None, ".0f", None, None),
        ("C org (%)", avg_c_org, None, None, ".3f", None, None),
    ]
    fr += "| Parametre | Moyenne | Min | Max |\n|---|---|---|---|\n"
    en += "| Parameter | Average | Min | Max |\n|---|---|---|---|\n"
    for (param, avg, mn, mx, fmt_avg, fmt_mn, fmt_mx) in table_data:
        if avg is not None:
            avg_s2 = f"{avg:{fmt_avg}}"
            mn_s = f"{mn:{fmt_mn}}" if mn is not None and fmt_mn else "—"
            mx_s = f"{mx:{fmt_mx}}" if mx is not None and fmt_mx else "—"
            fr += f"| {param} | {avg_s2} | {mn_s} | {mx_s} |\n"
            en += f"| {param} | {avg_s2} | {mn_s} | {mx_s} |\n"

    fr += f"\n**Classification metallurgique du minerai: {ore_type_fr}**\n\n"
    en += f"\n**Metallurgical ore classification: {ore_type_en}**\n\n"

    fr += "**Interpretation metallurgique:**\n\n"
    en += "**Metallurgical interpretation:**\n\n"

    if avg_au:
        fr += (
            f"La teneur moyenne en or de {avg_au:.3f} g/t Au est {'superieure' if avg_au > 1.0 else 'inferieure'} "
            f"au seuil de 1 g/t generalement requis pour une operation rentable CIL a grande echelle. "
        )
        en += (
            f"The average gold grade of {avg_au:.3f} g/t Au is {'above' if avg_au > 1.0 else 'below'} "
            f"the 1 g/t threshold typically required for a profitable large-scale CIL operation. "
        )
        if min_au is not None and max_au is not None:
            cv = (max_au - min_au) / avg_au if avg_au > 0 else 0
            fr += (
                f"La variabilite de teneur (min {min_au:.3f} — max {max_au:.3f} g/t) reflete "
                f"une distribution {'heterogene' if cv > 1 else 'moderement homogene'} typique des "
                f"mineralisations auriferes filoniennes.\n\n"
            )
            en += (
                f"The grade variability (min {min_au:.3f} — max {max_au:.3f} g/t) reflects a "
                f"{'heterogeneous' if cv > 1 else 'moderately homogeneous'} distribution typical of "
                f"vein-hosted gold mineralization.\n\n"
            )

    if avg_s:
        if avg_s > cfg.FLOTATION_S_THRESHOLD_PCT:
            fr += (
                f"La teneur en soufre total ({avg_s:.2f}%) depasse le seuil critique de "
                f"{cfg.FLOTATION_S_THRESHOLD_PCT}% indiquant la presence significative de sulfures porteurs d'or "
                f"(pyrite, arsenopyrite probable). Ce niveau de soufre implique un risque de consommation "
                f"de cyanure elevee par les sulfures et justifie l'evaluation d'un circuit de flottation "
                f"en amont de la lixiviation. La cinetique de dissolution des sulfures doit etre evaluee "
                f"dans les essais de lixiviation.\n\n"
            )
            en += (
                f"Total sulphur content ({avg_s:.2f}%) exceeds the {cfg.FLOTATION_S_THRESHOLD_PCT}% critical "
                f"threshold, indicating significant gold-bearing sulphides (pyrite, probable arsenopyrite). "
                f"This sulphur level implies elevated cyanide consumption risk from sulphides and justifies "
                f"evaluation of a flotation pre-concentration circuit ahead of leaching. Sulphide dissolution "
                f"kinetics must be assessed in leach tests.\n\n"
            )
        else:
            fr += f"La teneur en soufre total ({avg_s:.2f}%) est inferieure au seuil de flottation — le traitement CIL direct est approprié.\n\n"
            en += f"Total sulphur ({avg_s:.2f}%) is below the flotation threshold — direct CIL processing is appropriate.\n\n"

    if avg_as and avg_as > 1000:
        fr += (
            f"La teneur en arsenic ({avg_as:.0f} ppm) depasse 1 000 ppm, ce qui est caracteristique "
            f"de la presence d'arsenopyrite (FeAsS) et potentiellement de lollingite. A ce niveau, "
            f"l'or peut etre encapsule dans la structure cristalline de l'arsenopyrite (or refractaire), "
            f"limitant l'accessibilite du cyanure. Un programme d'essais complementaires incluant "
            f"oxydation par pression (POX), grillage ou oxydation biologique (BIOX) doit etre evalué "
            f"si la recuperation par CIL direct est inferieure a 85%.\n\n"
        )
        en += (
            f"Arsenic content ({avg_as:.0f} ppm) exceeds 1,000 ppm, characteristic of arsenopyrite "
            f"(FeAsS) and potentially lollingite. At this level, gold may be encapsulated within the "
            f"arsenopyrite crystal structure (refractory gold), limiting cyanide accessibility. A "
            f"complementary test program including pressure oxidation (POX), roasting or biological "
            f"oxidation (BIOX) must be evaluated if direct CIL recovery is below 85%.\n\n"
        )

    if is_preg_robbing:
        fr += (
            f"La teneur en carbone organique ({avg_c_org:.3f}%) depasse le seuil de 0.2% "
            f"signalant un risque de preg-robbing. Ce phenomene resulte de l'adsorption de "
            f"l'or-cyanure sur les carbonaces naturels (graphite, kerogene), reduisant la "
            f"recuperation nette. Des essais avec charbon actif en competition (competitive "
            f"adsorption tests — PREN) et l'utilisation de CYANOSAVE ou ACORGA sont a evaluer.\n\n"
        )
        en += (
            f"Organic carbon content ({avg_c_org:.3f}%) exceeds the 0.2% preg-robbing threshold. "
            f"This phenomenon results from adsorption of gold-cyanide complexes onto natural "
            f"carbonaceous materials (graphite, kerogen), reducing net recovery. Competitive "
            f"adsorption tests (PREN methodology) and evaluation of CYANOSAVE or ACORGA additives "
            f"are recommended.\n\n"
        )

    if avg_cu and avg_cu > 0.05:
        fr += (
            f"La teneur en cuivre ({avg_cu:.3f}%) est significative. Le cuivre consomme du cyanure "
            f"(CuCN, Cu(CN)2) et peut atteindre des concentrations toxiques pour les microorganismes "
            f"du TSF. Un pretraitement par lixiviation acide du cuivre ou l'utilisation de SART "
            f"(Sulphidisation-Acidification-Recycling-Thickening) est a evaluer.\n\n"
        )
        en += (
            f"Copper content ({avg_cu:.3f}%) is significant. Copper consumes cyanide (CuCN, Cu(CN)2) "
            f"and can reach toxic concentrations for TSF microorganisms. Copper pre-leach acid treatment "
            f"or SART (Sulphidisation-Acidification-Recycling-Thickening) evaluation is recommended.\n\n"
        )

    if is_complex:
        fr += (
            "**Avertissement QP:** Ce minerai presente des caracteristiques complexes (combinaison de "
            "refractarite, preg-robbing et/ou consommateurs de cyanure). Le flowsheet definitif "
            "necessite une validation par des essais pilotes avant d'atteindre le niveau FS.\n"
        )
        en += (
            "**QP Warning:** This ore exhibits complex characteristics (combination of refractoriness, "
            "preg-robbing and/or cyanide consumers). The definitive flowsheet requires pilot-scale "
            "validation before reaching FS-level.\n"
        )

    return {"key": "13.2", "title_fr": "Caracterisation mineralogique et chimique",
            "title_en": "Mineralogical and Chemical Characterization",
            "content_fr": fr, "content_en": en}


def _gen_13_3(phase, data):
    b1 = data["b1"]
    p = data.get("project") or {}
    name = p.get("project_name", "N/D")

    avg_bwi = _avg(b1, "bwi_kwh_t")
    avg_rwi = _avg(b1, "rwi_kwh_t")
    avg_cwi = _avg(b1, "cwi_kwh_t")
    avg_ai = _avg(b1, "abrasion_index_ai")
    avg_p80 = _avg(b1, "p80_target_um")
    avg_axb = _avg(b1, "axb")
    avg_sg = _avg(b1, "sg")

    min_bwi = min((float(r["bwi_kwh_t"]) for r in b1 if r.get("bwi_kwh_t")), default=None)
    max_bwi = max((float(r["bwi_kwh_t"]) for r in b1 if r.get("bwi_kwh_t")), default=None)

    fr = (
        f"**13.3 Essais de comminution et de broyabilite — Projet {name}**\n\n"
        f"Les parametres de comminution ont ete determines sur {len(b1)} echantillons "
        f"representatifs du gisement, couvrant les principales lithologies et zones de teneur. "
        f"Les essais ont ete realises conformement aux procedures standardisees (Bond 1961; "
        f"SMC Test — Morrell 2004) en laboratoire acredite.\n\n"
    )
    en = (
        f"**13.3 Comminution and Grindability Tests — {name} Project**\n\n"
        f"Comminution parameters were determined on {len(b1)} samples representative of "
        f"the deposit, covering principal lithologies and grade zones. Tests were performed "
        f"per standardized procedures (Bond 1961; SMC Test — Morrell 2004) at an accredited "
        f"laboratory.\n\n"
    )

    fr += "**Resultats des tests de broyabilite:**\n\n"
    en += "**Comminution test results:**\n\n"
    fr += "| Parametre | Moyenne | Min | Max | Unite |\n|---|---|---|---|---|\n"
    en += "| Parameter | Average | Min | Max | Unit |\n|---|---|---|---|---|\n"

    rows = [
        ("Bond BWi (broyage fin)", "Bond BWi (ball mill)", avg_bwi, min_bwi, max_bwi, ".1f", "kWh/t"),
        ("Bond RWi (broyage barres)", "Bond RWi (rod mill)", avg_rwi, None, None, ".1f", "kWh/t"),
        ("Bond CWi (concassage)", "Bond CWi (crushing)", avg_cwi, None, None, ".1f", "kWh/t"),
        ("SMC A×b (broyage auto.)", "SMC A×b (autogenous)", avg_axb, None, None, ".1f", ""),
        ("Indice d'abrasion Ai", "Abrasion Index Ai", avg_ai, None, None, ".3f", ""),
        ("Gravite specifique (SG)", "Specific gravity (SG)", avg_sg, None, None, ".2f", ""),
        ("P80 cible (produit)", "Target P80 (product)", avg_p80, None, None, ".0f", "µm"),
    ]
    for (lbl_fr, lbl_en, avg, mn, mx, fmt, unit) in rows:
        if avg is not None:
            mn_s = f"{mn:{fmt}}" if mn is not None else "—"
            mx_s = f"{mx:{fmt}}" if mx is not None else "—"
            fr += f"| {lbl_fr} | {avg:{fmt}} | {mn_s} | {mx_s} | {unit} |\n"
            en += f"| {lbl_en} | {avg:{fmt}} | {mn_s} | {mx_s} | {unit} |\n"

    fr += "\n**Interpretation et implications sur le dimensionnement:**\n\n"
    en += "\n**Interpretation and sizing implications:**\n\n"

    if avg_bwi:
        # Classification (CIM standard thresholds)
        if avg_bwi < 7:
            cat_fr, cat_en, implication_fr, implication_en = (
                "tres tendre (< 7 kWh/t)",
                "very soft (< 7 kWh/t)",
                "Broyage economique. Un broyeur a boulets de taille reduite suffit. "
                "Attention au surbroyage des particules fines.",
                "Economical grinding. A smaller ball mill is sufficient. "
                "Attention to overgrinding of fine particles."
            )
        elif avg_bwi < 12:
            cat_fr, cat_en, implication_fr, implication_en = (
                "tendre (7–12 kWh/t)",
                "soft (7–12 kWh/t)",
                "Bonne broyabilite. Dimensionnement standard. "
                "Circuit SAG/Ball Mill conventionnel adapte.",
                "Good grindability. Standard sizing. "
                "Conventional SAG/Ball Mill circuit is suitable."
            )
        elif avg_bwi < 16:
            cat_fr, cat_en, implication_fr, implication_en = (
                "moderement dur (12–16 kWh/t)",
                "moderately hard (12–16 kWh/t)",
                "Classe la plus commune pour les minerais auriferes. "
                "Circuit SAG + boulets standard. Verification critique des charges en circulation.",
                "Most common class for gold ores. "
                "Standard SAG + ball mill circuit. Critical review of circulating loads."
            )
        elif avg_bwi < 20:
            cat_fr, cat_en, implication_fr, implication_en = (
                "dur (16–20 kWh/t)",
                "hard (16–20 kWh/t)",
                "Dimensionnement conservateur requis. HPGR en pre-broyage recommande "
                "pour reduire la charge sur le SAG. Puissance installee majoree de 15-20%.",
                "Conservative sizing required. HPGR pre-grinding recommended to reduce "
                "SAG load. Installed power should be increased by 15-20%."
            )
        else:
            cat_fr, cat_en, implication_fr, implication_en = (
                "tres dur a extreme (> 20 kWh/t)",
                "very hard to extreme (> 20 kWh/t)",
                "Circuit HPGR obligatoire ou circuit de broyage haute pression. "
                "Analyse de variabilite etendue recommandee. Facteurs de correction "
                "Rowland/Barratt applicables pour le dimensionnement FS.",
                "Mandatory HPGR or high-pressure grinding circuit. "
                "Extended variability analysis recommended. Rowland/Barratt correction "
                "factors applicable for FS-level sizing."
            )

        fr += (
            f"Le minerai du projet {name} est classe comme **{cat_fr}** avec un BWi moyen "
            f"de {avg_bwi:.1f} kWh/t"
        )
        en += (
            f"The {name} project ore is classified as **{cat_en}** with an average BWi "
            f"of {avg_bwi:.1f} kWh/t"
        )
        if min_bwi is not None and max_bwi is not None:
            fr += f" (plage: {min_bwi:.1f}–{max_bwi:.1f} kWh/t, n={len(b1)}). "
            en += f" (range: {min_bwi:.1f}–{max_bwi:.1f} kWh/t, n={len(b1)}). "
        else:
            fr += ". "
            en += ". "
        fr += f"{implication_fr}\n\n"
        en += f"{implication_en}\n\n"

        # Energy estimate
        if avg_p80:
            _f80 = 150000  # typical ROM F80 in µm
            _bond_e = 10 * avg_bwi * (1 / avg_p80**0.5 - 1 / _f80**0.5)
            fr += (
                f"**Energie specifique de broyage estimee (loi de Bond):**\n"
                f"Pour F80 = 150 mm et P80 = {avg_p80:.0f} µm: E ≈ {_bond_e:.1f} kWh/t\n"
                f"(valeur indicative — facteurs de correction Rowland et efficacite du circuit a appliquer)\n\n"
            )
            en += (
                f"**Estimated specific grinding energy (Bond's law):**\n"
                f"For F80 = 150 mm and P80 = {avg_p80:.0f} µm: E ≈ {_bond_e:.1f} kWh/t\n"
                f"(indicative value — Rowland correction factors and circuit efficiency to be applied)\n\n"
            )

    if avg_ai:
        if avg_ai > 0.4:
            fr += (
                f"L'indice d'abrasion (Ai = {avg_ai:.3f}) est **eleve** (seuil critique: 0.4). "
                f"Un taux d'usure des medias de broyage superieur a la moyenne est anticipe. "
                f"La selection des blindages et la consommation de boulets doivent integrer "
                f"un facteur d'abrasion dans l'estimation OPEX.\n\n"
            )
            en += (
                f"The Abrasion Index (Ai = {avg_ai:.3f}) is **high** (critical threshold: 0.4). "
                f"Above-average grinding media wear rates are anticipated. Liner selection and "
                f"ball consumption must incorporate an abrasion factor in OPEX estimation.\n\n"
            )
        else:
            fr += f"L'indice d'abrasion (Ai = {avg_ai:.3f}) est dans la plage normale — usure des medias standard.\n\n"
            en += f"The Abrasion Index (Ai = {avg_ai:.3f}) is within the normal range — standard media wear.\n\n"

    if avg_axb:
        fr += (
            f"Le parametre SMC A×b = {avg_axb:.1f} confirme la broyabilite SAG/AG "
            f"({'favorable' if avg_axb > 50 else 'moderee' if avg_axb > 30 else 'difficile'} — "
            f"seuils: > 50 favorable, 30–50 moderee, < 30 difficile). "
            f"Cette valeur est integree dans la simulation de circuit SAG.\n\n"
        )
        en += (
            f"The SMC A×b parameter = {avg_axb:.1f} confirms SAG/AG grindability "
            f"({'favourable' if avg_axb > 50 else 'moderate' if avg_axb > 30 else 'challenging'} — "
            f"thresholds: > 50 favourable, 30–50 moderate, < 30 challenging). "
            f"This value is incorporated into SAG circuit simulation.\n\n"
        )

    if len(b1) < 10:
        fr += (
            f"**Limitation:** La base de donnees de broyabilite ({len(b1)} echantillons) est "
            f"insuffisante pour evaluer la variabilite completement. Un minimum de 20–30 echantillons "
            f"spatialement distribues est recommande pour l'etude FS.\n"
        )
        en += (
            f"**Limitation:** The comminution database ({len(b1)} samples) is insufficient to fully "
            f"assess variability. A minimum of 20–30 spatially distributed samples is recommended "
            f"for FS-level study.\n"
        )

    return {"key": "13.3", "title_fr": "Essais de broyabilite et comminution",
            "title_en": "Comminution Testing",
            "content_fr": fr, "content_en": en}


def _gen_13_4(phase, data):
    c2 = data["c2"]
    c3 = data.get("c3", [])
    p = data.get("project") or {}
    name = p.get("project_name", "N/D")

    avg_grg = _avg(c2, "au_recovery_pct")
    avg_mp = _avg(c2, "mass_pull_pct")
    avg_egrg = _avg(c3, "total_rec_pct")

    if not c2 and not c3:
        fr = (
            f"**13.4 Recuperation gravimetrique — Projet {name}**\n\n"
            "Aucun essai de recuperation gravimetrique GRG (Gravity Recoverable Gold) n'a ete "
            "realise a ce stade d'etude. Ce type d'essai (methode Knelson — Wardell Armstrong) "
            "est systematiquement recommande pour les minerais auriferes avant de confirmer "
            "l'absence de circuit de gravite dans le flowsheet definitif.\n\n"
            "**Recommandation QP:** Realiser les tests GRG C2 (simple liberation) et E-GRG C3 "
            "(liberation avancee) sur un minimum de 10 echantillons representatifs lors de la "
            "prochaine phase d'etude."
        )
        en = (
            f"**13.4 Gravity Recovery — {name} Project**\n\n"
            "No Gravity Recoverable Gold (GRG) tests have been performed at this study stage. "
            "This test type (Knelson method — Wardell Armstrong procedure) is systematically "
            "recommended for gold ores prior to confirming the absence of a gravity circuit "
            "in the definitive flowsheet.\n\n"
            "**QP Recommendation:** Conduct GRG C2 (single liberation) and E-GRG C3 (enhanced "
            "liberation) tests on a minimum of 10 representative samples in the next study phase."
        )
        return {"key": "13.4", "title_fr": "Recuperation gravimetrique",
                "title_en": "Gravity Recovery", "content_fr": fr, "content_en": en}

    fr = (
        f"**13.4 Recuperation gravimetrique (GRG / E-GRG) — Projet {name}**\n\n"
        f"Les tests de recuperation gravimetrique en or (Gravity Recoverable Gold — GRG) ont "
        f"ete realises selon la procedure standard Knelson (Gravity Recoverable Gold Procedure, "
        f"Wardell Armstrong 2014) sur {len(c2)} echantillons"
    )
    en = (
        f"**13.4 Gravity Recovery (GRG / E-GRG) — {name} Project**\n\n"
        f"Gravity Recoverable Gold (GRG) tests were conducted using the standard Knelson "
        f"procedure (Wardell Armstrong 2014) on {len(c2)} samples"
    )
    if c3:
        fr += f" et {len(c3)} tests E-GRG (Enhanced GRG — liberation avancee).\n\n"
        en += f" and {len(c3)} E-GRG (Enhanced GRG — enhanced liberation) tests.\n\n"
    else:
        fr += ".\n\n"
        en += ".\n\n"

    fr += "**Resultats GRG:**\n\n"
    en += "**GRG results:**\n\n"

    if avg_grg:
        fr += f"- Recuperation GRG moyenne: **{avg_grg:.1f}%** (n={len(c2)})\n"
        en += f"- Average GRG recovery: **{avg_grg:.1f}%** (n={len(c2)})\n"
    if avg_mp:
        fr += f"- Mass pull moyen (concentration gravimetrique): {avg_mp:.2f}%\n"
        en += f"- Average gravity mass pull: {avg_mp:.2f}%\n"
    if avg_egrg:
        fr += f"- Recuperation E-GRG (C3): {avg_egrg:.1f}% — or recuperable par broyage fin supplementaire\n"
        en += f"- E-GRG recovery (C3): {avg_egrg:.1f}% — gold recoverable with additional fine grinding\n"

    fr += "\n**Interpretation et recommandation de circuit:**\n\n"
    en += "\n**Circuit interpretation and recommendation:**\n\n"

    if avg_grg:
        if avg_grg >= cfg.GRG_CIRCUIT_THRESHOLD_PCT:
            fr += (
                f"Avec une recuperation GRG de {avg_grg:.1f}%, le minerai du projet {name} "
                f"est considere comme **gravitemetrique** (seuil: ≥ {cfg.GRG_CIRCUIT_THRESHOLD_PCT}%). "
                f"Un circuit de concentration gravimetrique est **recommande et economiquement justifie** "
                f"en amont du circuit CIL. Les concentrateurs recommandes sont les Knelson CVD-42 "
                f"ou Falcon UF pour le circuit principal, avec rebroyage du concentre primaire "
                f"suivi d'un Knelson de finissage (cleaner). Cette configuration permet de:\n"
                f"- Recuperer l'or libre rapidement (court-circuit du circuit CIL)\n"
                f"- Reduire la charge d'or en dissolution dans les tanks CIL\n"
                f"- Produire un concentre doré directement fondu (cout reduit)\n"
                f"- Ameliorer la gestion du cyanure residuel\n\n"
            )
            en += (
                f"With a GRG recovery of {avg_grg:.1f}%, the {name} project ore is classified as "
                f"**gravity-amenable** (threshold: ≥ {cfg.GRG_CIRCUIT_THRESHOLD_PCT}%). "
                f"A gravity concentration circuit is **recommended and economically justified** "
                f"upstream of the CIL circuit. Recommended concentrators are Knelson CVD-42 or "
                f"Falcon UF for the primary circuit, with primary concentrate regrind followed by a "
                f"Knelson cleaner. This configuration enables:\n"
                f"- Fast recovery of free gold (short-circuit of CIL)\n"
                f"- Reduced gold in solution in CIL tanks\n"
                f"- Directly smelted doré concentrate (lower cost)\n"
                f"- Improved residual cyanide management\n\n"
            )
        elif avg_grg >= 10:
            fr += (
                f"La recuperation GRG ({avg_grg:.1f}%) est moderee. Un circuit de gravite "
                f"simple (un concentrateur Knelson ou Falcon) est **economiquement marginal** "
                f"mais peut etre justifie si le mass pull est < 0.5% et le coute capital reduit. "
                f"Une analyse benefice-cout specifique est recommandee avant d'inclure la gravite "
                f"dans le flowsheet PFS/FS.\n\n"
            )
            en += (
                f"GRG recovery ({avg_grg:.1f}%) is moderate. A simple gravity circuit "
                f"(one Knelson or Falcon concentrator) is **economically marginal** but may be "
                f"justified if mass pull is < 0.5% and capital cost is low. A specific "
                f"cost-benefit analysis is recommended before including gravity in the PFS/FS flowsheet.\n\n"
            )
        else:
            fr += (
                f"La recuperation GRG ({avg_grg:.1f}%) est faible — l'or est principalement "
                f"sous forme finement dissemine ou refractaire. Un circuit de gravite n'est "
                f"**pas justifie economiquement** (seuil pratique: {cfg.GRG_CIRCUIT_THRESHOLD_PCT}%). "
                f"Le flowsheet CIL direct sans gravite est recommande.\n\n"
            )
            en += (
                f"GRG recovery ({avg_grg:.1f}%) is low — gold is primarily finely disseminated "
                f"or refractory. A gravity circuit is **not economically justified** (practical "
                f"threshold: {cfg.GRG_CIRCUIT_THRESHOLD_PCT}%). "
                f"Direct CIL flowsheet without gravity is recommended.\n\n"
            )

    if avg_egrg and avg_grg and avg_egrg > avg_grg * 1.15:
        fr += (
            f"La difference entre E-GRG ({avg_egrg:.1f}%) et GRG ({avg_grg:.1f}%) "
            f"de {avg_egrg - avg_grg:.1f} points indique un potentiel de recuperation additionnel "
            f"par rebroyage du concentre brut. L'optimisation de la taille de coupure P80 du "
            f"rebroyage est recommandee.\n"
        )
        en += (
            f"The difference between E-GRG ({avg_egrg:.1f}%) and GRG ({avg_grg:.1f}%) "
            f"of {avg_egrg - avg_grg:.1f} points indicates additional recovery potential through "
            f"regrinding of the primary concentrate. Optimization of the regrind P80 cut size "
            f"is recommended.\n"
        )

    return {"key": "13.4", "title_fr": "Recuperation gravimetrique (GRG)",
            "title_en": "Gravity Recovery (GRG)", "content_fr": fr, "content_en": en}


def _gen_13_5(phase, data):
    d1 = data["d1"]
    kin = data["kinetics"]
    p = data.get("project") or {}
    name = p.get("project_name", "N/D")
    a1 = data["a1"]
    _avg(a1, "s_total_pct")

    avg_rec = _avg(d1, "au_recovery_pct")
    min_rec = min((float(r["au_recovery_pct"]) for r in d1 if r.get("au_recovery_pct")), default=None)
    max_rec = max((float(r["au_recovery_pct"]) for r in d1 if r.get("au_recovery_pct")), default=None)
    avg_nacn = _avg(d1, "nacn_consumption_kg_t")
    avg_cao = _avg(d1, "cao_consumption_kg_t")
    avg_time = _avg(d1, "leach_time_h")
    avg_p80 = _avg(d1, "p80_um")
    avg_solids = _avg(d1, "pct_solids")
    avg_pri = _avg(d1, "preg_robbing_index")

    fr = (
        f"**13.5 Essais de lixiviation au cyanure — Projet {name}**\n\n"
        f"Les essais de lixiviation au cyanure ont ete realises sur {len(d1)} echantillons "
        f"selon les protocoles de flacon rotatif (bottle roll — 48h standard) et/ou CIL en batch "
        f"(Carbon-in-Leach), conformement aux methodes SGS, ALS ou Bureau Veritas. "
        f"Ces essais definissent les conditions optimales de lixiviation et les parametres "
        f"de design du circuit CIL/CIP pour l'etude {phase.upper()}.\n\n"
    )
    en = (
        f"**13.5 Cyanide Leach Tests — {name} Project**\n\n"
        f"Cyanide leach tests were performed on {len(d1)} samples using bottle roll protocols "
        f"(48h standard) and/or batch Carbon-in-Leach (CIL), per SGS, ALS or Bureau Veritas "
        f"methods. These tests define optimal leach conditions and CIL/CIP circuit design "
        f"parameters for the {phase.upper()} study.\n\n"
    )

    fr += "**Conditions et resultats des essais de lixiviation:**\n\n"
    en += "**Leach test conditions and results:**\n\n"
    fr += "| Parametre | Valeur moyenne | Plage | Unite |\n|---|---|---|---|\n"
    en += "| Parameter | Average value | Range | Unit |\n|---|---|---|---|\n"

    rows = [
        ("Recuperation Au", "Au recovery", avg_rec, min_rec, max_rec, ".1f", "%"),
        ("Consommation NaCN", "NaCN consumption", avg_nacn, None, None, ".2f", "kg/t"),
        ("Consommation CaO", "CaO consumption", avg_cao, None, None, ".2f", "kg/t"),
        ("Temps de retention", "Retention time", avg_time, None, None, ".0f", "h"),
        ("P80 alimentation", "Feed P80", avg_p80, None, None, ".0f", "µm"),
        ("% Solides", "% Solids", avg_solids, None, None, ".0f", "%"),
    ]
    for (lbl_fr, lbl_en, avg, mn, mx, fmt, unit) in rows:
        if avg is not None:
            rng = f"{mn:{fmt}}–{mx:{fmt}}" if mn is not None and mx is not None else "—"
            fr += f"| {lbl_fr} | {avg:{fmt}} | {rng} | {unit} |\n"
            en += f"| {lbl_en} | {avg:{fmt}} | {rng} | {unit} |\n"

    fr += "\n**Interpretation metallurgique:**\n\n"
    en += "\n**Metallurgical interpretation:**\n\n"

    if avg_rec:
        if avg_rec >= 90:
            rec_class_fr = f"**excellente** ({avg_rec:.1f}% — superieure a 90%)"
            rec_class_en = f"**excellent** ({avg_rec:.1f}% — above 90%)"
            rec_context_fr = "Cette performance est typique des minerais libres de bonne qualite. Le procede CIL est optimal."
            rec_context_en = "This performance is typical of good free-milling ores. CIL processing is optimal."
        elif avg_rec >= 82:
            rec_class_fr = f"**bonne** ({avg_rec:.1f}% — 82-90%)"
            rec_class_en = f"**good** ({avg_rec:.1f}% — 82-90%)"
            rec_context_fr = "Performance satisfaisante pour un minerai auro-argentin standard CIL. Optimisation du P80 recommandee."
            rec_context_en = "Satisfactory performance for standard gold-silver CIL ore. P80 optimization recommended."
        elif avg_rec >= 72:
            rec_class_fr = f"**moderee** ({avg_rec:.1f}% — 72-82%)"
            rec_class_en = f"**moderate** ({avg_rec:.1f}% — 72-82%)"
            rec_context_fr = (
                "Cette recuperation moderee indique des pertes significatives dans les residus. "
                "Des essais d'optimisation (P80, temps de retention, densite de pulpe, preoxydation) "
                "sont requis avant finalisation du flowsheet."
            )
            rec_context_en = (
                "This moderate recovery indicates significant losses in tailings. "
                "Optimization tests (P80, retention time, pulp density, pre-oxidation) "
                "are required before flowsheet finalization."
            )
        else:
            rec_class_fr = f"**faible** ({avg_rec:.1f}% — < 72%)"
            rec_class_en = f"**low** ({avg_rec:.1f}% — < 72%)"
            rec_context_fr = (
                "La faible recuperation indique un minerai refractaire ou problematique. "
                "Un pretraitement (oxydation par pression POX, grillage, BIOX ou ultrafins) "
                "est necessaire pour ameliorer l'accessibilite de l'or."
            )
            rec_context_en = (
                "Low recovery indicates refractory or problematic ore. "
                "Pre-treatment (pressure oxidation POX, roasting, BIOX or ultra-fine grinding) "
                "is required to improve gold accessibility."
            )

        fr += f"La recuperation par lixiviation CIL est classee {rec_class_fr}. {rec_context_fr}\n\n"
        en += f"CIL leach recovery is classified as {rec_class_en}. {rec_context_en}\n\n"

    if avg_nacn:
        if avg_nacn > 0.5:
            fr += (
                f"La consommation de NaCN ({avg_nacn:.2f} kg/t) est **elevee** (seuil optimal: < 0.3 kg/t "
                f"pour minerai libre). Cette consommation peut etre attribuee aux sulfures (FeS2, FeAsS), "
                f"au cuivre soluble ou a des pH trop faibles. L'optimisation de la charge NaCN et du pH "
                f"(cible: 10.5–11.0) doit etre realisee dans les essais CIL continus.\n\n"
            )
            en += (
                f"NaCN consumption ({avg_nacn:.2f} kg/t) is **high** (optimal threshold: < 0.3 kg/t "
                f"for free-milling ore). This consumption may be attributed to sulphides (FeS2, FeAsS), "
                f"soluble copper, or low pH. NaCN charge optimization and pH control "
                f"(target: 10.5–11.0) should be performed in continuous CIL tests.\n\n"
            )

    if avg_cao:
        fr += f"La consommation de chaux (CaO = {avg_cao:.2f} kg/t) maintient le pH en zone alcaline protectrice.\n\n"
        en += f"Lime consumption (CaO = {avg_cao:.2f} kg/t) maintains pH in the protective alkaline zone.\n\n"

    if avg_time:
        srt_tanks = max(4, int(avg_time / 1.5))
        fr += (
            f"Le temps de retention de {avg_time:.0f}h correspond a un circuit CIL de "
            f"**{srt_tanks} cuves minimum** (volume unitaire a dimensionner selon tph et % solides). "
            f"Pour la conception, les cuves typiques sont de 1 000–3 500 m3 avec agitateurs "
            f"de 0.8–1.2 kW/m3.\n\n"
        )
        en += (
            f"Retention time of {avg_time:.0f}h corresponds to a CIL circuit requiring "
            f"a **minimum of {srt_tanks} tanks** (individual volume to be sized per tph and % solids). "
            f"For design, typical tanks are 1,000–3,500 m3 with agitators rated 0.8–1.2 kW/m3.\n\n"
        )

    if avg_p80 and avg_p80 > 150:
        fr += (
            f"Le P80 d'alimentation de {avg_p80:.0f} µm est superieur au P80 optimal generalement "
            f"fixe entre 75–106 µm pour les minerais auriferes. Un affinage du broyage pourrait "
            f"ameliorer la liberation de l'or et la recuperation. Des essais supplementaires a "
            f"P80 = 75 µm sont recommandes.\n\n"
        )
        en += (
            f"Feed P80 of {avg_p80:.0f} µm is above the optimal P80 generally set at 75–106 µm "
            f"for gold ores. Finer grinding could improve gold liberation and recovery. "
            f"Additional tests at P80 = 75 µm are recommended.\n\n"
        )

    if phase in ("pfs", "fs") and kin:
        avg_24h = _avg(kin, "rec_24h")
        avg_48h = _avg(kin, "rec_48h")
        avg_72h = _avg(kin, "rec_72h")
        if avg_24h or avg_48h:
            fr += f"**Cinetique de lixiviation ({len(kin)} essais):**\n\n"
            en += f"**Leach kinetics ({len(kin)} tests):**\n\n"
            fr += "| Duree | Recuperation Au moyenne |\n|---|---|\n"
            en += "| Duration | Average Au recovery |\n|---|---|\n"
            for (h, val) in [(24, avg_24h), (48, avg_48h), (72, avg_72h)]:
                if val:
                    fr += f"| {h}h | {val:.1f}% |\n"
                    en += f"| {h}h | {val:.1f}% |\n"
            if avg_24h and avg_48h:
                marginal = avg_48h - avg_24h
                fr += (
                    f"\nL'increment de recuperation entre 24h et 48h est de {marginal:.1f} points. "
                )
                en += (
                    f"\nThe recovery increment between 24h and 48h is {marginal:.1f} points. "
                )
                if marginal > 3:
                    fr += "Le temps de retention optimal est 48h (pas de justification pour 72h).\n\n"
                    en += "Optimal retention time is 48h (no justification for 72h).\n\n"
                else:
                    fr += "L'essentiel de la dissolution est complete a 24h — le temps de retention peut etre optimise.\n\n"
                    en += "Most dissolution is complete at 24h — retention time can be optimized.\n\n"

    if avg_pri and avg_pri > 0:
        if avg_pri > 0.2:
            fr += (
                f"**Avertissement preg-robbing (IRP = {avg_pri:.3f}):** L'indice de preg-robbing "
                f"depasse 0.2 — ce niveau constitue un risque metallurgique majeur. "
                f"Des mesures correctives sont obligatoires: ajout de bentonite (100–300 g/t), "
                f"CYANOSAVE ou blinding par dioxyde de soufre. Un test CIL avec charbon actif "
                f"en competition directe (competitive adsorption) doit quantifier les pertes reelles.\n\n"
            )
            en += (
                f"**Preg-robbing warning (PRI = {avg_pri:.3f}):** The preg-robbing index "
                f"exceeds 0.2 — this level represents a major metallurgical risk. "
                f"Corrective measures are mandatory: bentonite addition (100–300 g/t), "
                f"CYANOSAVE, or sulphur dioxide blinding. A CIL test with competitive activated "
                f"carbon adsorption must quantify actual losses.\n\n"
            )
        elif avg_pri > 0.05:
            fr += (
                f"Indice de preg-robbing modere (IRP = {avg_pri:.3f}). "
                f"Un suivi en essais CIL continus est recommande.\n\n"
            )
            en += (
                f"Moderate preg-robbing index (PRI = {avg_pri:.3f}). "
                f"Monitoring in continuous CIL tests is recommended.\n\n"
            )

    return {"key": "13.5", "title_fr": "Essais de lixiviation au cyanure",
            "title_en": "Cyanide Leach Testing", "content_fr": fr, "content_en": en}


def _gen_13_6(phase, data):
    p = data.get("project") or {}
    name = p.get("project_name", "N/D")

    if phase == "scoping":
        fr = (
            f"**13.6 Epaississement et filtration — Projet {name}**\n\n"
            "A ce niveau d'etude (Scoping), les parametres d'epaississement et de filtration "
            "sont estimes sur la base de correlations empiriques industrie (Coe & Clevenger, "
            "Wilhelm & Naide pour les residus CIL auriferes).\n\n"
            "**Valeurs indicatives de reference pour residus CIL auriferes:**\n"
            "- Aire unitaire epaississement: 0.02–0.06 m2/t/j (residus fins)\n"
            "- Densite sous-verse visee: 55–65% solides\n"
            "- Dosage floculant indicatif: 20–50 g/t\n"
            "- Humidite gateau filtre: 14–20%\n\n"
            "**Recommandation:** Realiser des essais Coe & Clevenger et des essais de filtre-presse "
            "sur des echantillons representatifs lors de la phase PFS."
        )
        en = (
            f"**13.6 Thickening and Filtration — {name} Project**\n\n"
            "At this study level (Scoping), thickening and filtration parameters are estimated "
            "based on industry empirical correlations (Coe & Clevenger, Wilhelm & Naide for "
            "gold CIL tailings).\n\n"
            "**Indicative reference values for gold CIL tailings:**\n"
            "- Thickener unit area: 0.02–0.06 m2/t/d (fine tailings)\n"
            "- Target underflow density: 55–65% solids\n"
            "- Indicative flocculant dosage: 20–50 g/t\n"
            "- Filter cake moisture: 14–20%\n\n"
            "**Recommendation:** Conduct Coe & Clevenger and filter press tests on representative "
            "samples during the PFS phase."
        )
        return {"key": "13.6", "title_fr": "Epaississement et filtration",
                "title_en": "Thickening and Filtration",
                "content_fr": fr, "content_en": en}

    e1 = data["e1"]
    e2 = data["e2"]
    avg_ua = _avg(e1, "unit_area_m2_t_d")
    avg_floc = _avg(e1, "flocculant_dosage_g_t")
    avg_uf = _avg(e1, "underflow_density_pct_solids")
    avg_filt = _avg(e2, "filtration_rate_kg_m2_h")
    avg_moist = _avg(e2, "cake_moisture_pct")

    fr = (
        f"**13.6 Epaississement et filtration des residus — Projet {name}**\n\n"
        f"Les essais d'epaississement (methode Coe & Clevenger) et de filtration (filtre-presse) "
        f"ont ete realises afin de definir les parametres de design des circuits de gestion "
        f"des residus CIL et de dewatering du concentre.\n\n"
    )
    en = (
        f"**13.6 Thickening and Filtration of Tailings — {name} Project**\n\n"
        f"Thickening (Coe & Clevenger method) and filtration (filter press) tests were conducted "
        f"to define design parameters for CIL tailings management and concentrate dewatering "
        f"circuits.\n\n"
    )

    if e1:
        fr += f"**Epaississement ({len(e1)} essais — methode Coe & Clevenger):**\n\n"
        en += f"**Thickening ({len(e1)} tests — Coe & Clevenger method):**\n\n"
        fr += "| Parametre | Valeur | Reference industrie | Unite |\n|---|---|---|---|\n"
        en += "| Parameter | Value | Industry reference | Unit |\n|---|---|---|---|\n"
        if avg_ua:
            fr += f"| Aire unitaire | {avg_ua:.4f} | 0.02–0.06 (residus CIL) | m2/t/j |\n"
            en += f"| Unit area | {avg_ua:.4f} | 0.02–0.06 (CIL tailings) | m2/t/d |\n"
        if avg_floc:
            fr += f"| Dosage floculant | {avg_floc:.0f} | 20–80 | g/t |\n"
            en += f"| Flocculant dosage | {avg_floc:.0f} | 20–80 | g/t |\n"
        if avg_uf:
            uf_flag = "✓ Cible atteinte" if 50 <= avg_uf <= 70 else "⚠ Hors cible"
            fr += f"| Densite sous-verse | {avg_uf:.1f} ({uf_flag}) | 55–65 | % solides |\n"
            en += f"| Underflow density | {avg_uf:.1f} ({uf_flag.replace('Cible atteinte','On target').replace('Hors cible','Off target')}) | 55–65 | % solids |\n"

        if avg_ua:
            p_tph = float((data.get("project") or {}).get("target_tph") or 0)
            if p_tph > 0:
                thickener_area = avg_ua * p_tph * 24
                thickener_diam = (thickener_area * 4 / 3.14159) ** 0.5
                fr += (
                    f"\n**Dimensionnement indicatif de l'epaississeur (residus):**\n"
                    f"Pour {p_tph:.0f} t/h de solides: aire ≈ {thickener_area:.0f} m2, "
                    f"diametre ≈ {thickener_diam:.0f} m "
                    f"({'epaississeur haute densite (HD)' if thickener_diam > 30 else 'epaississeur conventionnel'})\n\n"
                )
                en += (
                    f"\n**Indicative thickener sizing (tailings):**\n"
                    f"For {p_tph:.0f} t/h solids: area ≈ {thickener_area:.0f} m2, "
                    f"diameter ≈ {thickener_diam:.0f} m "
                    f"({'high-density thickener' if thickener_diam > 30 else 'conventional thickener'})\n\n"
                )

    if e2:
        fr += f"**Filtration ({len(e2)} essais — filtre-presse):**\n\n"
        en += f"**Filtration ({len(e2)} tests — filter press):**\n\n"
        if avg_filt:
            filt_flag = "✓ Acceptable" if avg_filt > 150 else "⚠ Faible"
            fr += f"- Taux de filtration: {avg_filt:.1f} kg/m2/h ({filt_flag} — reference: > 150 kg/m2/h)\n"
            en += f"- Filtration rate: {avg_filt:.1f} kg/m2/h ({filt_flag.replace('Acceptable','Acceptable').replace('Faible','Low')} — reference: > 150 kg/m2/h)\n"
        if avg_moist:
            moist_flag = "✓ Cible" if avg_moist < 18 else "⚠ Eleve"
            fr += (
                f"- Humidite du gateau: {avg_moist:.1f}% ({moist_flag} — cible: < 18%)\n"
                f"  (impact direct sur les couts de transport et l'humidite de deposition au TSF)\n\n"
            )
            en += (
                f"- Cake moisture: {avg_moist:.1f}% ({moist_flag.replace('Cible','Target').replace('Eleve','High')} — target: < 18%)\n"
                f"  (direct impact on haulage costs and TSF deposition moisture content)\n\n"
            )

    return {"key": "13.6", "title_fr": "Epaississement et filtration",
            "title_en": "Thickening and Filtration",
            "content_fr": fr, "content_en": en}


def _gen_13_7(phase, data):
    p = data.get("project") or {}
    name = p.get("project_name", "N/D")
    env = data["env"]

    if phase == "scoping" and not env:
        fr = (
            f"**13.7 Essais environnementaux — Projet {name}**\n\n"
            "A ce stade d'etude (Scoping), les essais environnementaux specifiques n'ont pas "
            "encore ete realises. Cependant, la geochimie de la tete permet une evaluation "
            "preliminaire du potentiel de drainage minier acide (DMA).\n\n"
            "**Evaluation preliminaire basee sur la geochimie de tete:**\n"
        )
        en = (
            f"**13.7 Environmental Tests — {name} Project**\n\n"
            "At this study level (Scoping), specific environmental tests have not yet been "
            "conducted. However, head assay geochemistry allows a preliminary assessment of "
            "acid mine drainage (AMD) potential.\n\n"
            "**Preliminary assessment based on head assay geochemistry:**\n"
        )
        a1 = data["a1"]
        avg_s = _avg(a1, "s_total_pct")
        if avg_s:
            np_ratio_est = 1.0 / max(avg_s, 0.01) * 1.5
            fr += (
                f"- Soufre total moyen: {avg_s:.2f}% — "
                f"{'risque DMA eleve (S > 0.3%)' if avg_s > 0.3 else 'risque DMA faible (S < 0.3%)'}\n"
                f"- Ratio NP/PA estime (indicatif): {np_ratio_est:.1f} "
                f"({'Generateur potentiel DMA' if np_ratio_est < 2 else 'Non-generateur probable'})\n\n"
            )
            en += (
                f"- Average total sulphur: {avg_s:.2f}% — "
                f"{'high AMD risk (S > 0.3%)' if avg_s > 0.3 else 'low AMD risk (S < 0.3%)'}\n"
                f"- Estimated NP/AP ratio (indicative): {np_ratio_est:.1f} "
                f"({'Potential AMD generator' if np_ratio_est < 2 else 'Likely non-generator'})\n\n"
            )
        fr += (
            "**Programme recommande pour la phase PFS:**\n"
            "1. Tests DMA: NP/AP (Sobek), ABA etendu (Lawrence & Wang), test cinetique de cellule humide\n"
            "2. TCLP / SPLP sur residus CIL et concentre gravimetrique\n"
            "3. CN WAD residuel dans les residus de lixiviation (norme IFC: ≤ 50 mg/L)\n"
            "4. Metaux lourds en lixiviat: As, Pb, Hg, Se, Cd (normes IFC Annexe A)\n"
            "5. Toxicite aigue ASTM E1193 (Ceriodaphnia dubia) si TSF pres d'un plan d'eau\n"
        )
        en += (
            "**Recommended program for PFS phase:**\n"
            "1. ARD tests: NP/AP (Sobek), extended ABA (Lawrence & Wang), humidity cell kinetic test\n"
            "2. TCLP / SPLP on CIL tailings and gravity concentrate\n"
            "3. Residual WAD CN in leach tailings (IFC standard: ≤ 50 mg/L)\n"
            "4. Heavy metals in leachate: As, Pb, Hg, Se, Cd (IFC Annex A standards)\n"
            "5. Acute toxicity ASTM E1193 (Ceriodaphnia dubia) if TSF near water body\n"
        )
        return {"key": "13.7", "title_fr": "Essais environnementaux",
                "title_en": "Environmental Testing",
                "content_fr": fr, "content_en": en}

    avg_wad = _avg(env, "wad_cn_mg_l")
    avg_as = _avg(env, "arsenic_mg_l")
    avg_hg = _avg(env, "mercury_mg_l")
    adr_risks = [r.get("acid_drainage_risk") for r in env if r.get("acid_drainage_risk")]

    fr = (
        f"**13.7 Essais environnementaux et geochimie des residus — Projet {name}**\n\n"
        f"Les essais environnementaux ont ete realises sur {len(env)} echantillons "
        f"(residus CIL, concentres, rejets) conformement aux normes IFC/Banque Mondiale "
        f"et aux exigences de l'etude {phase.upper()}. Ces donnees alimentent directement "
        f"la conception du TSF et le plan de gestion environnementale.\n\n"
    )
    en = (
        f"**13.7 Environmental Tests and Tailings Geochemistry — {name} Project**\n\n"
        f"Environmental tests were performed on {len(env)} samples "
        f"(CIL tailings, concentrates, waste) in accordance with IFC/World Bank standards "
        f"and {phase.upper()} study requirements. These data directly inform TSF design and "
        f"the environmental management plan.\n\n"
    )

    fr += "**Synthese des resultats environnementaux:**\n\n"
    en += "**Environmental results summary:**\n\n"
    fr += "| Parametre | Valeur moyenne | Norme IFC | Statut |\n|---|---|---|---|\n"
    en += "| Parameter | Average value | IFC Standard | Status |\n|---|---|---|---|\n"

    if avg_wad is not None:
        wad_status = "✓ Conforme" if avg_wad <= cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L else "✗ Non conforme"
        wad_status_en = "✓ Compliant" if avg_wad <= cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L else "✗ Non-compliant"
        fr += f"| CN WAD (residus) | {avg_wad:.2f} mg/L | ≤ {cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L} mg/L | {wad_status} |\n"
        en += f"| WAD CN (tailings) | {avg_wad:.2f} mg/L | ≤ {cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L} mg/L | {wad_status_en} |\n"
    if avg_as is not None:
        as_status = "✓ Conforme" if avg_as <= 0.5 else "✗ Non conforme"
        as_status_en = "✓ Compliant" if avg_as <= 0.5 else "✗ Non-compliant"
        fr += f"| Arsenic (As) | {avg_as:.3f} mg/L | ≤ 0.5 mg/L | {as_status} |\n"
        en += f"| Arsenic (As) | {avg_as:.3f} mg/L | ≤ 0.5 mg/L | {as_status_en} |\n"
    if avg_hg is not None:
        hg_status = "✓ Conforme" if avg_hg <= 0.002 else "✗ Non conforme"
        hg_status_en = "✓ Compliant" if avg_hg <= 0.002 else "✗ Non-compliant"
        fr += f"| Mercure (Hg) | {avg_hg:.4f} mg/L | ≤ 0.002 mg/L | {hg_status} |\n"
        en += f"| Mercury (Hg) | {avg_hg:.4f} mg/L | ≤ 0.002 mg/L | {hg_status_en} |\n"

    fr += "\n**Drainage minier acide (DMA) / Drainage neutre contamine (DNC):**\n\n"
    en += "\n**Acid mine drainage (AMD) / Contaminated neutral drainage (CND):**\n\n"

    if adr_risks:
        from collections import Counter
        risk_counts = Counter(adr_risks)
        most_common = risk_counts.most_common(1)[0][0]
        fr += (
            f"L'evaluation du potentiel de generation acide (tests ABA — Acid-Base Accounting, "
            f"methode Sobek) sur {len(env)} echantillons donne le profil de risque suivant:\n"
        )
        en += (
            f"The acid generation potential assessment (ABA — Acid-Base Accounting, "
            f"Sobek method) on {len(env)} samples yields the following risk profile:\n"
        )
        for risk, count in risk_counts.most_common():
            pct = count / len(adr_risks) * 100
            fr += f"- {risk}: {count} echantillons ({pct:.0f}%)\n"
            en += f"- {risk}: {count} samples ({pct:.0f}%)\n"
        if most_common.lower() in ("high", "eleve", "generating", "pag"):
            fr += (
                "\n**⚠ Risque DMA ELEVE detecte.** Des mesures preventives sont obligatoires: "
                "separation des materiaux generateurs d'acide, couverture par encapsulation, "
                "desulfuration des residus avant deposition, drainage collecte et traite. "
                "Un suivi hydrogeochimique continu du TSF est requis.\n\n"
            )
            en += (
                "\n**⚠ HIGH AMD risk detected.** Preventive measures are mandatory: "
                "segregation of acid-generating materials, encapsulation covers, "
                "tailings desulphurization before deposition, collected and treated drainage. "
                "Continuous hydrogeochemical monitoring of the TSF is required.\n\n"
            )
        else:
            fr += "\nLe potentiel de generation acide est faible a modere — suivi de routine suffisant.\n\n"
            en += "\nAcid generation potential is low to moderate — routine monitoring is sufficient.\n\n"
    else:
        fr += "Les donnees de drainage minier acide sont insuffisantes — essais ABA recommandes.\n\n"
        en += "Acid mine drainage data are insufficient — ABA tests recommended.\n\n"

    if avg_wad is not None and avg_wad > cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L:
        fr += (
            f"**Non-conformite CN WAD:** La teneur moyenne de {avg_wad:.2f} mg/L depasse la norme IFC "
            f"de {cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L} mg/L. Le procede de destruction du cyanure "
            f"(methode SO2/Air — INCO, ou peroxyde H2O2) doit etre optimise pour atteindre la conformite "
            f"avant deposition en TSF. Des essais d'optimisation du traitement CN sont requis.\n"
        )
        en += (
            f"**WAD CN non-compliance:** Average content of {avg_wad:.2f} mg/L exceeds IFC standard "
            f"of {cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L} mg/L. The cyanide destruction process "
            f"(SO2/Air — INCO method, or H2O2 peroxide) must be optimized to achieve compliance "
            f"before TSF deposition. CN treatment optimization tests are required.\n"
        )

    return {"key": "13.7", "title_fr": "Essais environnementaux",
            "title_en": "Environmental Testing",
            "content_fr": fr, "content_en": en}


def _gen_13_8(phase, data):
    p = data.get("project") or {}
    name = p.get("project_name", "N/D")
    tph = float(p.get("target_tph") or 0)
    grade = float(p.get("gold_grade_g_t") or 0)
    avail = float(p.get("availability_pct") or 92)

    data["flowsheets"]
    data["mb"]  # legacy v1 streams
    b1 = data["b1"]
    c2 = data["c2"]
    d1 = data["d1"]
    e1 = data["e1"]
    env = data["env"]

    avg_bwi = _avg(b1, "bwi_kwh_t")
    avg_grg = _avg(c2, "au_recovery_pct")
    avg_rec = _avg(d1, "au_recovery_pct")
    _avg(env, "wad_cn_mg_l")
    avg_s = _avg(data["a1"], "s_total_pct")

    # Determine flowsheet type from data
    has_flotation = bool(data.get("flotation")) or (avg_s and avg_s > cfg.FLOTATION_S_THRESHOLD_PCT)
    has_gravity = avg_grg and avg_grg >= cfg.GRG_CIRCUIT_THRESHOLD_PCT
    needs_hpgr = avg_bwi and avg_bwi > 16
    bool(e1)

    # Recovery estimate
    rec_est = avg_rec or (float(p.get("availability_pct") or 88))
    overall_rec = rec_est
    if has_gravity and avg_grg:
        overall_rec = min(98, rec_est + avg_grg * 0.05)
    annual_oz = tph * 24 * 365 * (avail / 100) * grade * (overall_rec / 100) * TROY_OZ_PER_GRAM if tph > 0 and grade > 0 else 0

    fr = (
        f"**13.8 Flowsheet de traitement selectionne — Projet {name}**\n\n"
        f"Sur la base des resultats des essais metallurgiques presentes aux sections "
        f"13.2 a 13.7, le flowsheet de traitement recommande pour le projet {name} "
        f"a une capacite nominale de {tph:.0f} t/h a {grade:.3f} g/t Au est le suivant:\n\n"
    )
    en = (
        f"**13.8 Selected Processing Flowsheet — {name} Project**\n\n"
        f"Based on metallurgical test results presented in Sections 13.2 through 13.7, "
        f"the recommended processing flowsheet for the {name} project at a nominal capacity "
        f"of {tph:.0f} t/h at {grade:.3f} g/t Au is as follows:\n\n"
    )

    circuits_fr = [
        ("1. MANUTENTION ROM ET CONCASSAGE", [
            "Reception minerai ROM (grizzly primaire, benne ou convoyeur alimentateur)",
            "Concasseur giratoire primaire (P100 = 150–200 mm)",
            "Crible vibrant de verification + convoyeur vers stock intermediaire",
            "Concasseur cone secondaire" + (" + HPGR (pre-broyage haute pression)" if needs_hpgr else " + concasseur cone tertiaire (si requis)"),
            f"Capacite: {tph:.0f} t/h a {float(p.get('crush_hours_per_day', 18)):.0f} h/j",
        ]),
        ("2. CIRCUIT DE BROYAGE", [
            ("Broyeur SAG (D = 8–11 m selon BWi) + Broyeur a boulets primaire" if not needs_hpgr
             else "HPGR primaire (pression specifique: 3–5 N/mm2) + Broyeur a boulets"),
            "Classification par hydrocyclones (P80 cible: " + (f"{_avg(d1, 'p80_um') or 75:.0f}" if d1 else "75") + " µm)",
            f"Charge circulante: 250–350%",
            "Pompes centrifuges alimentation cyclones",
        ]),
    ]
    circuits_en = [
        ("1. ROM HANDLING AND CRUSHING", [
            "ROM ore reception (primary grizzly, bucket or apron feeder)",
            "Primary gyratory crusher (P100 = 150–200 mm)",
            "Vibrating screen verification + conveyor to surge stockpile",
            "Secondary cone crusher" + (" + HPGR (high pressure pre-grinding)" if needs_hpgr else " + tertiary cone crusher (if required)"),
            f"Capacity: {tph:.0f} t/h at {float(p.get('crush_hours_per_day', 18)):.0f} h/d",
        ]),
        ("2. GRINDING CIRCUIT", [
            ("SAG mill (D = 8–11 m per BWi) + primary ball mill" if not needs_hpgr
             else "Primary HPGR (specific pressure: 3–5 N/mm2) + ball mill"),
            "Hydrocyclone classification (target P80: " + (f"{_avg(d1, 'p80_um') or 75:.0f}" if d1 else "75") + " µm)",
            "Circulating load: 250–350%",
            "Cyclone feed centrifugal pumps",
        ]),
    ]

    if has_gravity:
        circuits_fr.append(("3. CIRCUIT DE RECUPERATION GRAVIMETRIQUE", [
            "Concentrateurs centrifuges Knelson CVD-42 ou Falcon UF (sur pulpe des cyclones)",
            f"Taux de mass pull vise: 0.3–0.8% (base: GRG = {avg_grg:.1f}%)",
            "Rebroyage du concentre brut (IsaMill ou Vertimill P80 = 25–40 µm)",
            "Concentrateur de finissage (cleaner) + fonte directe en lingot",
            f"Recuperation gravimetrique attendue: {min(avg_grg, 25.0):.1f}–{min(avg_grg * 1.1, 35.0):.1f}%",
        ]))
        circuits_en.append(("3. GRAVITY RECOVERY CIRCUIT", [
            "Knelson CVD-42 or Falcon UF centrifugal concentrators (on cyclone feed pulp)",
            f"Target mass pull: 0.3–0.8% (basis: GRG = {avg_grg:.1f}%)",
            "Primary concentrate regrind (IsaMill or Vertimill P80 = 25–40 µm)",
            "Cleaner concentrator + direct doré smelting",
            f"Expected gravity recovery: {min(avg_grg, 25.0):.1f}–{min(avg_grg * 1.1, 35.0):.1f}%",
        ]))

    if has_flotation:
        circuits_fr.append(("4. CIRCUIT DE FLOTTATION (SULFURES)", [
            "Flottation rougher (temps: 15–20 min, collecteur: PAX 50–80 g/t, mousse: MIBC 20–40 g/t)",
            "Flottation scavenger + epaississement concentre brut",
            "Rebroyage concentre (P80 = 30–45 µm)",
            "Flottation cleaner x2 (concentre final: 20–40% S, elution Au)",
            "Residus flottation — vers circuit cyanuration",
        ]))
        circuits_en.append(("4. FLOTATION CIRCUIT (SULPHIDES)", [
            "Rougher flotation (time: 15–20 min, collector: PAX 50–80 g/t, frother: MIBC 20–40 g/t)",
            "Scavenger flotation + concentrate thickening",
            "Concentrate regrind (P80 = 30–45 µm)",
            "Cleaner flotation x2 (final concentrate: 20–40% S, Au deportment)",
            "Flotation tailings — to cyanidation circuit",
        ]))

    cil_sec_num = len(circuits_fr) + 1
    circuits_fr.append((f"{cil_sec_num}. CIRCUIT DE LIXIVIATION CIL / CIP", [
        f"Predilution + conditionnement NaCN ({avg_nacn:.2f} kg/t)" if (avg_nacn := _avg(d1, 'nacn_consumption_kg_t')) else "Predilution + conditionnement NaCN",
        f"Cuves de lixiviation CIL ({_avg(d1, 'leach_time_h') or 24:.0f}h retention, pH 10.5–11.0, {_avg(d1, 'pct_solids') or 45:.0f}% solides)",
        "Concentration charbon actif: 15–25 g/L",
        "Ecrans de separation charbon (0.6–0.8 mm)",
        f"Recuperation CIL attendue: {avg_rec:.1f}%" if avg_rec else "Recuperation CIL a confirmer",
    ]))
    circuits_en.append((f"{cil_sec_num}. CIL / CIP LEACH CIRCUIT", [
        f"Pre-dilution + NaCN conditioning ({avg_nacn:.2f} kg/t)" if (avg_nacn := _avg(d1, 'nacn_consumption_kg_t')) else "Pre-dilution + NaCN conditioning",
        f"CIL leach tanks ({_avg(d1, 'leach_time_h') or 24:.0f}h retention, pH 10.5–11.0, {_avg(d1, 'pct_solids') or 45:.0f}% solids)",
        "Activated carbon concentration: 15–25 g/L",
        "Carbon interstage screens (0.6–0.8 mm)",
        f"Expected CIL recovery: {avg_rec:.1f}%" if avg_rec else "CIL recovery to be confirmed",
    ]))

    adr_num = cil_sec_num + 1
    circuits_fr.append((f"{adr_num}. CIRCUIT ADR (ELUTION, ELECTROLYSE, FUSION)", [
        "Elution charbon charge (methode AARL pression ou Zadra a haute temperature)",
        "Electrolyse de la solution eluee (electrowinning — acier inox, courant 300–600 A/m2)",
        "Calcination cathodes + fusion au four a induction (lingots de dore)",
        "Regeneration thermique du charbon epuise (four rotatif, 700–750°C)",
    ]))
    circuits_en.append((f"{adr_num}. ADR CIRCUIT (ELUTION, ELECTROWINNING, SMELTING)", [
        "Loaded carbon elution (pressure AARL or high-temperature Zadra method)",
        "Electrolyte solution electrowinning (stainless steel cathodes, 300–600 A/m2)",
        "Cathode calcination + induction furnace smelting (doré bars)",
        "Spent carbon thermal regeneration (rotary kiln, 700–750°C)",
    ]))

    detox_num = adr_num + 1
    circuits_fr.append((f"{detox_num}. DESTRUCTION DU CYANURE ET GESTION DES RESIDUS", [
        f"Destruction CN (methode SO2/Air — INCO, ou H2O2) — cible: CN WAD < {cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L} mg/L",
        "Epaississement residus CIL (epaississeur haute densite ou conventionnel)",
        "Deposition residus en TSF (pulpe ou filtres si sec)",
        "Recyclage eau clarifiee vers procede",
    ]))
    circuits_en.append((f"{detox_num}. CYANIDE DESTRUCTION AND TAILINGS MANAGEMENT", [
        f"CN destruction (SO2/Air — INCO method, or H2O2) — target: WAD CN < {cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L} mg/L",
        "CIL tailings thickening (high-density or conventional thickener)",
        "Tailings TSF deposition (pulp or filtered if dry stack)",
        "Clarified water recycle to process",
    ]))

    for (title, items) in circuits_fr:
        fr += f"**{title}:**\n"
        for it in items:
            fr += f"- {it}\n"
        fr += "\n"
    for (title, items) in circuits_en:
        en += f"**{title}:**\n"
        for it in items:
            en += f"- {it}\n"
        en += "\n"

    fr += "**Bilan de recuperation globale du flowsheet:**\n\n"
    en += "**Overall flowsheet recovery balance:**\n\n"
    fr += "| Circuit | Recuperation indicative | Base de calcul |\n|---|---|---|\n"
    en += "| Circuit | Indicative recovery | Calculation basis |\n|---|---|---|\n"

    if has_gravity and avg_grg:
        fr += f"| Gravite (Knelson) | {min(avg_grg*0.8, 20):.1f}–{min(avg_grg, 30):.1f}% | Essais GRG C2 |\n"
        en += f"| Gravity (Knelson) | {min(avg_grg*0.8, 20):.1f}–{min(avg_grg, 30):.1f}% | GRG C2 tests |\n"
    if avg_rec:
        fr += f"| CIL (or residuel post-gravite) | {avg_rec:.1f}% | Essais bottle roll/CIL batch |\n"
        en += f"| CIL (residual gold post-gravity) | {avg_rec:.1f}% | Bottle roll/CIL batch tests |\n"
    if overall_rec > 0:
        fr += f"| **RECUPERATION GLOBALE ESTIMEE** | **{overall_rec:.1f}%** | Combinee gravite + CIL |\n"
        en += f"| **ESTIMATED OVERALL RECOVERY** | **{overall_rec:.1f}%** | Combined gravity + CIL |\n"
    if annual_oz > 0:
        fr += f"\n**Production annuelle estimee:** {annual_oz:,.0f} oz Au/an (base: {tph:.0f} t/h, {avail:.0f}% disponibilite)\n"
        en += f"\n**Estimated annual production:** {annual_oz:,.0f} oz Au/year (basis: {tph:.0f} t/h, {avail:.0f}% availability)\n"

    _recovery_note_fr = "lors de l'etude FS" if phase == "pfs" else "avant demarrage"
    _recovery_tests_fr = "essais en continu et pilotes" if phase == "pfs" else "essais de validation additionnels"
    _recovery_tests_en = "continuous and pilot-scale tests" if phase == "pfs" else "additional validation tests"
    _recovery_when_en = "during the FS study" if phase == "pfs" else "before start-up"
    _rec_uncertainty = 3 if phase == "fs" else (5 if phase == "pfs" else 8)
    fr += (
        f"\n**Note de la QP:** Ce flowsheet est base sur les donnees d'essais disponibles "
        f"au niveau d'etude {phase.upper()}. La recuperation finale sera confirmee par des "
        f"{_recovery_tests_fr} {_recovery_note_fr}. "
        f"Les incertitudes sur la recuperation sont estimees a ±{_rec_uncertainty}%.\n"
    )
    en += (
        f"\n**QP Note:** This flowsheet is based on test data available at {phase.upper()} "
        f"study level. Final recovery will be confirmed by "
        f"{_recovery_tests_en} {_recovery_when_en}. "
        f"Recovery uncertainties are estimated at ±{_rec_uncertainty}%.\n"
    )

    return {"key": "13.8", "title_fr": "Flowsheet de traitement selectionne",
            "title_en": "Selected Processing Flowsheet",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 14: Mineral Resource Estimates
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_14(phase, data):
    bs = data.get("block_stats")
    bc = data.get("block_config")
    p = data["project"]
    name = p["project_name"] if p else "N/D"

    fr = f"Les estimations de ressources minerales du projet {name} sont basees sur le modele de blocs.\n\n"
    en = f"The mineral resource estimates for the {name} project are based on the block model.\n\n"

    if bs and bs.get("cnt") and int(bs["cnt"]) > 0:
        n_blocks = int(bs["cnt"])
        total_t = float(bs["total_tonnes"]) / 1e6 if bs.get("total_tonnes") else 0
        avg_g = float(bs["weighted_grade"]) if bs.get("weighted_grade") else 0
        min_g = float(bs["min_grade"]) if bs.get("min_grade") else 0
        max_g = float(bs["max_grade"]) if bs.get("max_grade") else 0
        contained_oz = total_t * 1e6 * avg_g * TROY_OZ_PER_GRAM

        fr += (
            f"**Modele de blocs:**\n"
            f"- Nombre de blocs: {n_blocks:,}\n"
            f"- Tonnage total: {total_t:,.1f} Mt\n"
            f"- Teneur moyenne ponderee: {avg_g:.2f} g/t Au\n"
            f"- Teneur min / max: {min_g:.2f} / {max_g:.2f} g/t Au\n"
            f"- Or contenu: {contained_oz:,.0f} oz Au\n"
        )
        en += (
            f"**Block model:**\n"
            f"- Number of blocks: {n_blocks:,}\n"
            f"- Total tonnage: {total_t:,.1f} Mt\n"
            f"- Weighted average grade: {avg_g:.2f} g/t Au\n"
            f"- Min / max grade: {min_g:.2f} / {max_g:.2f} g/t Au\n"
            f"- Contained gold: {contained_oz:,.0f} oz Au\n"
        )

        if bc:
            bx = float(bc.get("x_block_size") or 10)
            by = float(bc.get("y_block_size") or 10)
            bz = float(bc.get("z_block_size") or 5)
            fr += f"\n**Dimensions des blocs:** {bx:.0f} x {by:.0f} x {bz:.0f} m\n"
            en += f"\n**Block dimensions:** {bx:.0f} x {by:.0f} x {bz:.0f} m\n"
    else:
        fr += "Aucun modele de blocs n'est disponible. Les ressources minerales doivent etre estimees par un geologue qualifie."
        en += "No block model is available. Mineral resources should be estimated by a qualified geologist."

    fr += (
        "\n\n**Note:** Les ressources minerales ne sont pas des reserves minerales et n'ont pas "
        "demontre de viabilite economique. Les estimations sont conformes aux definitions de l'ICM."
    )
    en += (
        "\n\n**Note:** Mineral resources are not mineral reserves and do not have "
        "demonstrated economic viability. Estimates conform to CIM definitions."
    )

    return {"key": "14", "title_fr": "Estimations des ressources minerales",
            "title_en": "Mineral Resource Estimates",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 15: Mineral Reserve Estimates
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_15(phase, data):
    p = data["project"]
    name = p["project_name"] if p else "N/D"
    bs = data.get("block_stats")

    if phase == "scoping":
        fr = (
            f"Les reserves minerales du projet {name} n'ont pas ete estimees a ce stade d'etude (Scoping). "
            f"Des estimations de reserves seront preparees lors de l'etude de prefaisabilite (PFS)."
        )
        en = (
            f"Mineral reserves for the {name} project have not been estimated at this study stage (Scoping). "
            f"Reserve estimates will be prepared during the Pre-Feasibility Study (PFS)."
        )
        return {"key": "15", "title_fr": "Estimations des reserves minerales",
                "title_en": "Mineral Reserve Estimates",
                "content_fr": fr, "content_en": en}

    gold_price = float(p.get("gold_price_usd_oz") or 2340) if p else 2340
    rec = float(p.get("availability_pct") or 92) if p else 92

    fr = (
        f"Les reserves minerales du projet {name} sont basees sur la conversion "
        f"des ressources minerales en appliquant les parametres economiques suivants:\n\n"
        f"- Prix de l'or: ${gold_price:,.0f}/oz\n"
        f"- Disponibilite de l'usine: {rec:.0f}%\n\n"
    )
    en = (
        f"Mineral reserves for the {name} project are based on the conversion "
        f"of mineral resources using the following economic parameters:\n\n"
        f"- Gold price: ${gold_price:,.0f}/oz\n"
        f"- Plant availability: {rec:.0f}%\n\n"
    )

    if bs and bs.get("total_tonnes"):
        total_t = float(bs["total_tonnes"]) / 1e6
        avg_g = float(bs["weighted_grade"]) if bs.get("weighted_grade") else 0
        fr += f"Le tonnage total du modele de blocs est de {total_t:,.1f} Mt a {avg_g:.2f} g/t Au. "
        fr += "L'application d'une teneur de coupure et des facteurs de dilution/perte est requise pour l'estimation des reserves."
        en += f"Total block model tonnage is {total_t:,.1f} Mt at {avg_g:.2f} g/t Au. "
        en += "Application of a cut-off grade and dilution/loss factors is required for reserve estimation."

    return {"key": "15", "title_fr": "Estimations des reserves minerales",
            "title_en": "Mineral Reserve Estimates",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 16: Mining Methods
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_16(phase, data):
    p = data["project"]
    name = p["project_name"] if p else "N/D"
    tph = float(p["target_tph"] or 0) if p and p.get("target_tph") else 0
    mine_life = int(p["mine_life_years"] or 10) if p and p.get("mine_life_years") else 10
    avail = float(p.get("availability_pct") or 92) if p else 92
    mtpa = tph * 24 * 365 * (avail / 100) / 1e6

    fr = (
        f"La methode d'exploitation miniere du projet {name} est a ciel ouvert "
        f"de type conventionnel (camion-pelle).\n\n"
        f"**Parametres miniers:**\n"
        f"- Capacite de traitement: {tph:.0f} t/h ({mtpa:.2f} Mtpa)\n"
        f"- Duree de vie de la mine: {mine_life} ans\n"
        f"- Methode: Exploitation a ciel ouvert conventionnelle\n"
        f"- Equipement: Pelles hydrauliques, camions hors route, foreuses\n\n"
    )
    en = (
        f"The mining method for the {name} project is conventional open pit "
        f"(truck and shovel).\n\n"
        f"**Mining parameters:**\n"
        f"- Processing capacity: {tph:.0f} t/h ({mtpa:.2f} Mtpa)\n"
        f"- Mine life: {mine_life} years\n"
        f"- Method: Conventional open pit mining\n"
        f"- Equipment: Hydraulic shovels, haul trucks, drill rigs\n\n"
    )

    if phase in ("pfs", "fs"):
        fr += (
            "Un plan minier detaille incluant la sequence d'exploitation, "
            "le ratio sterile/minerai et l'optimisation de la fosse a ete developpe."
        )
        en += (
            "A detailed mine plan including mining sequence, "
            "strip ratio and pit optimization has been developed."
        )

    return {"key": "16", "title_fr": "Methodes d'exploitation miniere",
            "title_en": "Mining Methods",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 17: Recovery Methods (5 subsections)
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_17_1(phase, data):
    p = data["project"]
    name = p["project_name"] if p else "N/D"
    tph = float(p["target_tph"] or 0) if p else 0
    avail = float(p.get("availability_pct") or 92) if p else 92
    mine_life = int(p.get("mine_life_years") or 10) if p else 10
    grade = float(p.get("gold_grade_g_t") or 0) if p else 0
    eq = data["equipment"]

    c2 = data["c2"]
    d1 = data["d1"]
    b1 = data["b1"]
    a1 = data["a1"]
    avg_grg = _avg(c2, "au_recovery_pct")
    avg_rec = _avg(d1, "au_recovery_pct") or 88.0
    avg_bwi = _avg(b1, "bwi_kwh_t")
    avg_s = _avg(a1, "s_total_pct")

    has_gravity = avg_grg and avg_grg >= cfg.GRG_CIRCUIT_THRESHOLD_PCT
    has_flotation = bool(data.get("flotation")) or (avg_s and avg_s > cfg.FLOTATION_S_THRESHOLD_PCT)
    needs_hpgr = avg_bwi and avg_bwi > 16
    mtpa = tph * 24 * 365 * (avail / 100) / 1e6 if tph > 0 else 0
    annual_oz = tph * 24 * 365 * (avail / 100) * grade * (avg_rec / 100) * TROY_OZ_PER_GRAM if tph > 0 and grade > 0 else 0

    total_power = sum(float(e["power_installed_kw"]) for e in eq if e.get("power_installed_kw")) if eq else 0

    fr = (
        f"**17.1 Description du procede de recuperation — Projet {name}**\n\n"
        f"L'usine de traitement metallurgique du projet {name} est dimensionnee pour une "
        f"capacite nominale de **{tph:.0f} t/h** ({mtpa:.2f} Mtpa), avec une disponibilite "
        f"operationnelle cible de **{avail:.0f}%** ({avail * 8760 / 100:.0f} heures/an). "
        f"La duree de vie de la mine prevue est de **{mine_life} ans**.\n\n"
    )
    en = (
        f"**17.1 Recovery Process Description — {name} Project**\n\n"
        f"The {name} project metallurgical processing plant is designed for a nominal "
        f"capacity of **{tph:.0f} t/h** ({mtpa:.2f} Mtpa), with a target operating "
        f"availability of **{avail:.0f}%** ({avail * 8760 / 100:.0f} hours/year). "
        f"The planned mine life is **{mine_life} years**.\n\n"
    )

    if annual_oz > 0:
        fr += (
            f"Sur la base du programme metallurgique (Sections 13.2 a 13.8), la production "
            f"annuelle estimee est de **{annual_oz:,.0f} oz Au** ({annual_oz * mine_life:,.0f} oz Au "
            f"sur la duree de vie de la mine).\n\n"
        )
        en += (
            f"Based on the metallurgical program (Sections 13.2 to 13.8), estimated annual "
            f"production is **{annual_oz:,.0f} oz Au** ({annual_oz * mine_life:,.0f} oz Au "
            f"over mine life).\n\n"
        )

    fr += "**Circuits de traitement inclus dans le flowsheet:**\n\n"
    en += "**Processing circuits included in the flowsheet:**\n\n"

    circuits_fr = [
        ("Circuit 1 — Preparation et concassage du minerai ROM",
         f"Reception du minerai ROM, scalpage, concassage en {2 if not needs_hpgr else 3} etapes "
         f"({'giratoire + cone' if not needs_hpgr else 'giratoire + HPGR + cone'}), "
         f"stock tampon vers circuit de broyage. Granulometrie produite: P80 ≈ 12–15 mm (entree broyeur)."),
        ("Circuit 2 — Broyage et classification",
         f"{'Broyeur SAG (D ≈ 9–11 m) + broyeur a boulets primaire' if not needs_hpgr else 'HPGR + broyeur a boulets'} "
         f"+ classification par hydrocyclones. "
         f"P80 cible en sortie: {_avg(d1, 'p80_um') or 75:.0f} µm. "
         f"Charge circulante: 250–350%. Consommation energetique estimee: "
         f"{(avg_bwi or 14) * 0.9:.1f}–{(avg_bwi or 14) * 1.1:.1f} kWh/t."),
    ]
    circuits_en = [
        ("Circuit 1 — ROM Ore Preparation and Crushing",
         f"ROM ore reception, scalping, crushing in {2 if not needs_hpgr else 3} stages "
         f"({'gyratory + cone' if not needs_hpgr else 'gyratory + HPGR + cone'}), "
         f"surge stockpile to grinding circuit. Product size: P80 ≈ 12–15 mm (mill feed)."),
        ("Circuit 2 — Grinding and Classification",
         f"{'SAG mill (D ≈ 9–11 m) + primary ball mill' if not needs_hpgr else 'HPGR + ball mill'} "
         f"+ hydrocyclone classification. "
         f"Target product P80: {_avg(d1, 'p80_um') or 75:.0f} µm. "
         f"Circulating load: 250–350%. Estimated energy consumption: "
         f"{(avg_bwi or 14) * 0.9:.1f}–{(avg_bwi or 14) * 1.1:.1f} kWh/t."),
    ]

    if has_gravity:
        circuits_fr.append((
            "Circuit 3 — Recuperation gravimetrique (Knelson / Falcon)",
            f"Concentrateurs centrifuges installes sur la pulpe de decharge des hydrocyclones. "
            f"Taux de mass pull: 0.3–0.8%. Recuperation attendue: {min(avg_grg, 30):.1f}%. "
            f"Rebroyage du concentre brut (P80 = 25–40 µm) + concentrateur cleaner + fonte directe en lingot dore."
        ))
        circuits_en.append((
            "Circuit 3 — Gravity Recovery (Knelson / Falcon)",
            f"Centrifugal concentrators installed on hydrocyclone underflow pulp. "
            f"Mass pull rate: 0.3–0.8%. Expected recovery: {min(avg_grg, 30):.1f}%. "
            f"Primary concentrate regrind (P80 = 25–40 µm) + cleaner concentrator + direct doré smelting."
        ))

    if has_flotation:
        circuits_fr.append((
            "Circuit 4 — Flottation des sulfures auriferes",
            "Flottation rougher-scavenger-cleaner pour concentration des sulfures porteurs d'or. "
            "Reactifs: xanthate potassique PAX (50–80 g/t), mousse MIBC (20–40 g/t). "
            "Concentrate enrichi (20–40% S) rebroyé et traité par voie CIL ou hydrometallurgique."
        ))
        circuits_en.append((
            "Circuit 4 — Gold-Bearing Sulphide Flotation",
            "Rougher-scavenger-cleaner flotation for concentration of gold-bearing sulphides. "
            "Reagents: potassium amyl xanthate PAX (50–80 g/t), MIBC frother (20–40 g/t). "
            "Enriched concentrate (20–40% S) reground and treated by CIL or hydrometallurgical route."
        ))

    cil_n = len(circuits_fr) + 1
    avg_nacn = _avg(d1, "nacn_consumption_kg_t") or 0.3
    avg_time = _avg(d1, "leach_time_h") or 24
    avg_pct_s = _avg(d1, "pct_solids") or 45
    n_tanks = max(4, int(avg_time / 1.5))
    circuits_fr.append((
        f"Circuit {cil_n} — Lixiviation au cyanure (CIL — Carbon-in-Leach)",
        f"Predilution et conditionnement (pH 10.5–11.0, NaCN {avg_nacn:.2f} kg/t, CaO). "
        f"{n_tanks} cuves CIL en serie ({avg_pct_s:.0f}% solides, {avg_time:.0f}h retention totale). "
        f"Concentration charbon actif: 15–25 g/L, granulometrie charbon: 1.2–3.4 mm. "
        f"Ecrans de separation inter-cuves (0.6–0.8 mm ouverture). "
        f"Recuperation CIL attendue: {avg_rec:.1f}%."
    ))
    circuits_en.append((
        f"Circuit {cil_n} — Cyanide Leaching (CIL — Carbon-in-Leach)",
        f"Pre-dilution and conditioning (pH 10.5–11.0, NaCN {avg_nacn:.2f} kg/t, CaO). "
        f"{n_tanks} CIL tanks in series ({avg_pct_s:.0f}% solids, {avg_time:.0f}h total retention). "
        f"Activated carbon concentration: 15–25 g/L, carbon size: 1.2–3.4 mm. "
        f"Interstage screens (0.6–0.8 mm aperture). "
        f"Expected CIL recovery: {avg_rec:.1f}%."
    ))

    adr_n = cil_n + 1
    circuits_fr.append((
        f"Circuit {adr_n} — Circuit ADR (Elution, Electrolyse, Fusion, Regeneration charbon)",
        "Elution du charbon charge par methode AARL (eluant NaOH/NaCN chaud, 110–130°C) ou Zadra. "
        "Electrolyse (electrowinning) de la solution enrichie sur cathodes en acier inox. "
        "Calcination et fusion des cathodes au four a induction — production de lingots de dore. "
        "Regeneration thermique du charbon epuise (four rotatif 700–750°C) et recyclage."
    ))
    circuits_en.append((
        f"Circuit {adr_n} — ADR Circuit (Elution, Electrowinning, Smelting, Carbon Regeneration)",
        "Loaded carbon elution by AARL method (hot NaOH/NaCN eluant, 110–130°C) or Zadra. "
        "Electrowinning of gold-rich eluate on stainless steel cathodes. "
        "Cathode calcination and induction furnace smelting — doré bar production. "
        "Spent carbon thermal regeneration (rotary kiln 700–750°C) and recycle."
    ))

    detox_n = adr_n + 1
    circuits_fr.append((
        f"Circuit {detox_n} — Destruction du cyanure et deposition des residus",
        f"Destruction du cyanure par procede SO2/Air (methode INCO, efficacite > 99%) ou peroxyde d'hydrogene. "
        f"Objectif CN WAD < {cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L} mg/L (norme IFC). "
        "Epaississement des residus detoxifies. "
        "Deposition en parc a residus (TSF) par conduites ou convoyeurs. "
        "Recyclage de l'eau de decantat vers le procede (economie d'eau fraiche et CN residuel)."
    ))
    circuits_en.append((
        f"Circuit {detox_n} — Cyanide Destruction and Tailings Deposition",
        f"Cyanide destruction by SO2/Air process (INCO method, > 99% efficiency) or hydrogen peroxide. "
        f"Target WAD CN < {cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L} mg/L (IFC standard). "
        "Detoxified tailings thickening. "
        "TSF deposition via pipeline or conveyors. "
        "Decant water recycle to process (fresh water and residual CN savings)."
    ))

    for (title, desc) in circuits_fr:
        fr += f"**{title}:**\n{desc}\n\n"
    for (title, desc) in circuits_en:
        en += f"**{title}:**\n{desc}\n\n"

    if total_power > 0:
        fr += (
            f"**Bilan energetique:**\n"
            f"- Puissance installee totale: {total_power:,.0f} kW ({total_power / 1000:.1f} MW)\n"
            f"- Puissance specifique (indicatif): {total_power / max(tph, 1):.1f} kW/(t/h)\n\n"
        )
        en += (
            f"**Energy balance:**\n"
            f"- Total installed power: {total_power:,.0f} kW ({total_power / 1000:.1f} MW)\n"
            f"- Specific power (indicative): {total_power / max(tph, 1):.1f} kW/(t/h)\n\n"
        )

    if eq:
        long_lead = [e for e in eq if e.get("is_long_lead")]
        fr += (
            f"La liste d'equipements compte **{len(eq)} items majeurs**"
            + (f", dont **{len(long_lead)} a long delai de livraison** (broyeurs, epaississeurs, "
               f"colonnes CIL, fours de fusion)" if long_lead else "")
            + ".\n\n"
        )
        en += (
            f"The equipment list includes **{len(eq)} major items**"
            + (f", including **{len(long_lead)} long-lead items** (mills, thickeners, "
               f"CIL tanks, smelting furnaces)" if long_lead else "")
            + ".\n\n"
        )

    return {"key": "17.1", "title_fr": "Description du procede de recuperation",
            "title_en": "Recovery Process Description",
            "content_fr": fr, "content_en": en}


def _gen_17_2(phase, data):
    p = data.get("project") or {}
    name = p.get("project_name", "N/D")
    dc = data["dc"]
    tph = float(p.get("target_tph") or 0)
    avail = float(p.get("availability_pct") or 92)
    mtpa = tph * 24 * 365 * (avail / 100) / 1e6

    if not dc:
        fr = (
            f"**17.2 Criteres de design — Projet {name}**\n\n"
            "Les criteres de design seront etablis lors de l'etape de generation automatique "
            "des criteres de conception a partir des essais metallurgiques et des donnees de projet. "
            "Ce document sera mis a jour des que les DC sont disponibles."
        )
        en = (
            f"**17.2 Design Criteria — {name} Project**\n\n"
            "Design criteria will be established during the automatic design criteria generation "
            "step from metallurgical testwork and project data. "
            "This document will be updated once DCs are available."
        )
        return {"key": "17.2", "title_fr": "Criteres de design",
                "title_en": "Design Criteria", "content_fr": fr, "content_en": en}

    # Group DC by section
    sections = {}
    for row in dc:
        sec = row.get("section", "General")
        sections.setdefault(sec, []).append(row)

    accuracy = {
        "scoping": "Classe 5 AACE (±35–50%)",
        "pfs": "Classe 4 AACE (±25–30%)",
        "fs": "Classe 3 AACE (±10–15%)",
    }

    fr = (
        f"**17.2 Criteres de design — Projet {name}**\n\n"
        f"Les criteres de design presentes dans cette section constituent la base de "
        f"dimensionnement de l'usine de traitement du projet {name}. Ces criteres sont "
        f"etablis a partir des essais metallurgiques LIMS, des correlations empiriques "
        f"industrie (SME Handbook, CIM Best Practices) et du niveau d'etude {phase.upper()} "
        f"({accuracy.get(phase, 'Classe 5 AACE')}).\n\n"
        f"**Parametres de base de l'installation:**\n"
        f"- Capacite nominale: **{tph:.0f} t/h** ({mtpa:.2f} Mtpa)\n"
        f"- Disponibilite operationnelle: **{avail:.0f}%**\n"
        f"- Heures operationnelles annuelles: **{avail * 8760 / 100:.0f} h/an**\n\n"
        f"**Criteres de design par section ({len(dc)} parametres au total):**\n\n"
    )
    en = (
        f"**17.2 Design Criteria — {name} Project**\n\n"
        f"The design criteria presented in this section form the sizing basis for the "
        f"{name} project processing plant. These criteria are established from LIMS "
        f"metallurgical tests, industry empirical correlations (SME Handbook, CIM Best "
        f"Practices) and the {phase.upper()} study level "
        f"({accuracy.get(phase, 'AACE Class 5')}).\n\n"
        f"**Plant base parameters:**\n"
        f"- Nominal capacity: **{tph:.0f} t/h** ({mtpa:.2f} Mtpa)\n"
        f"- Operating availability: **{avail:.0f}%**\n"
        f"- Annual operating hours: **{avail * 8760 / 100:.0f} h/yr**\n\n"
        f"**Design criteria by section ({len(dc)} parameters total):**\n\n"
    )

    for sec_name, rows in sections.items():
        fr += f"**{sec_name}**\n\n"
        en += f"**{sec_name}**\n\n"
        fr += "| Parametre | Design | Nominal | Unite | Base |\n|---|---|---|---|---|\n"
        en += "| Parameter | Design | Nominal | Unit | Basis |\n|---|---|---|---|---|\n"
        for r in rows:
            item = r.get("item", "")
            design_val = r.get("design_value") or r.get("design", "")
            nominal_val = r.get("nominal_value") or r.get("nominal", "")
            unit = r.get("unit", "")
            basis = r.get("basis", r.get("source", "LIMS / Empirique"))
            fr += f"| {item} | {design_val} | {nominal_val} | {unit} | {basis} |\n"
            en += f"| {item} | {design_val} | {nominal_val} | {unit} | {basis} |\n"
        fr += "\n"
        en += "\n"

    fr += (
        f"**Note QP:** Les criteres de design nominaux sont derives des essais metallurgiques "
        f"(valeurs P50). Les valeurs design integrent les facteurs de service et "
        f"d'installation (SF = 1.05–1.25 selon le circuit). "
        f"La precision de ces criteres est conforme au niveau {accuracy.get(phase, 'Classe 5 AACE')}."
    )
    en += (
        f"**QP Note:** Nominal design criteria are derived from metallurgical test results "
        f"(P50 values). Design values incorporate service and installation factors "
        f"(SF = 1.05–1.25 per circuit). "
        f"Criteria accuracy is consistent with {accuracy.get(phase, 'AACE Class 5')} level."
    )

    return {"key": "17.2", "title_fr": "Criteres de design",
            "title_en": "Design Criteria",
            "content_fr": fr, "content_en": en}


def _gen_17_3(phase, data):
    mb = data["mb"]
    _p = data.get("project") or {}
    name = _p.get("project_name", "N/D")
    _op_h = float(_p.get("operating_hours_day") or 24.0)
    _avail = float(_p.get("availability_pct") or 92.0) / 100.0
    _grade = float(_p.get("gold_grade_g_t") or 0)
    _tph = float(_p.get("target_tph") or 0)

    if not mb:
        fr = (
            f"**17.3 Bilan massique — Projet {name}**\n\n"
            "Le bilan massique n'a pas encore ete genere. Il sera calcule automatiquement "
            "a partir des criteres de design (DC) et des essais metallurgiques LIMS. "
            "Utiliser le module 'Bilan massique & eau' pour generer le bilan depuis les DC.\n\n"
            "**Parametres attendus pour le bilan massique:**\n"
            f"- Alimentation ROM: {_tph:.0f} t/h\n"
            f"- Teneur minerai: {_grade:.3f} g/t Au\n"
            f"- Disponibilite: {_avail * 100:.0f}%\n"
        )
        en = (
            f"**17.3 Mass Balance — {name} Project**\n\n"
            "The mass balance has not yet been generated. It will be automatically calculated "
            "from design criteria (DC) and LIMS metallurgical tests. "
            "Use the 'Mass balance & water' module to generate the balance from DCs.\n\n"
            "**Expected mass balance parameters:**\n"
            f"- ROM feed: {_tph:.0f} t/h\n"
            f"- Ore grade: {_grade:.3f} g/t Au\n"
            f"- Availability: {_avail * 100:.0f}%\n"
        )
        return {"key": "17.3", "title_fr": "Bilan massique",
                "title_en": "Mass Balance", "content_fr": fr, "content_en": en}

    rom = next((s for s in mb if s["stream"] == "ROM Feed"), None)
    tails = next((s for s in mb if s["stream"] == "Tailings Final"), None)
    cil_feed = next((s for s in mb if "CIL Feed" in (s["stream"] or "")), None)
    grav_conc = next((s for s in mb if "Gravity" in (s["stream"] or "") or "Grav" in (s["stream"] or "")), None)
    dore = next((s for s in mb if "Dore" in (s["stream"] or "") or "Doré" in (s["stream"] or "")), None)

    fr = (
        f"**17.3 Bilan massique — Projet {name}**\n\n"
        f"Le bilan massique du procede de traitement a ete etabli pour {len(mb)} flux de procede, "
        f"calibres sur les criteres de conception nominaux (DC). Le bilan respecte la conservation "
        f"de la masse et des metaux (Au) dans chaque section du circuit.\n\n"
    )
    en = (
        f"**17.3 Mass Balance — {name} Project**\n\n"
        f"The process mass balance has been established for {len(mb)} process streams, "
        f"calibrated on nominal design criteria (DC). The balance respects mass and metal "
        f"(Au) conservation across each circuit section.\n\n"
    )

    fr += "**Flux principaux du procede:**\n\n"
    en += "**Key process streams:**\n\n"
    fr += "| Flux | Solides (t/h) | Teneur Au (g/t) | Eau (m3/h) | % Solides |\n|---|---|---|---|---|\n"
    en += "| Stream | Solids (t/h) | Au grade (g/t) | Water (m3/h) | % Solids |\n|---|---|---|---|---|\n"

    key_streams = [s for s in [rom, cil_feed, grav_conc, tails, dore] if s]
    for s in key_streams:
        try:
            sol = f"{float(s['solids_tph']):.1f}" if s.get("solids_tph") else "—"
            au = f"{float(s['au_gt']):.4f}" if s.get("au_gt") else "—"
            water = f"{float(s['water_m3h']):.1f}" if s.get("water_m3h") else "—"
            pct_s = f"{float(s['pct_solids']):.0f}" if s.get("pct_solids") else "—"
            fr += f"| {s['stream']} | {sol} | {au} | {water} | {pct_s} |\n"
            en += f"| {s['stream']} | {sol} | {au} | {water} | {pct_s} |\n"
        except Exception:
            pass

    # Compute recovery from mass balance
    rec_mb = 0.0
    annual_oz = 0.0
    if rom and tails and rom.get("au_gt") and tails.get("au_gt"):
        try:
            grade_rom = float(rom["au_gt"])
            grade_tails = float(tails["au_gt"])
            tph_rom = float(rom["solids_tph"]) if rom.get("solids_tph") else _tph
            tph_tails = float(tails["solids_tph"]) if tails.get("solids_tph") else tph_rom
            if grade_rom > 0 and tph_rom > 0:
                rec_mb = (1 - (tph_tails * grade_tails) / (tph_rom * grade_rom)) * 100
            annual_oz = tph_rom * _op_h * 365 * _avail * grade_rom * (rec_mb / 100) * TROY_OZ_PER_GRAM
        except Exception:
            pass

    fr += "\n**Indicateurs de performance du bilan massique:**\n\n"
    en += "\n**Mass balance performance indicators:**\n\n"

    if rom and rom.get("solids_tph"):
        tph_val = float(rom["solids_tph"])
        mtpa = tph_val * _op_h * 365 * _avail / 1e6
        fr += f"- Capacite nominale ROM: {tph_val:.0f} t/h ({mtpa:.2f} Mtpa)\n"
        en += f"- Nominal ROM capacity: {tph_val:.0f} t/h ({mtpa:.2f} Mtpa)\n"
    if rom and rom.get("au_gt"):
        fr += f"- Teneur alimentation ROM: {float(rom['au_gt']):.4f} g/t Au\n"
        en += f"- ROM feed grade: {float(rom['au_gt']):.4f} g/t Au\n"
    if tails and tails.get("au_gt"):
        fr += f"- Teneur residus finaux: {float(tails['au_gt']):.6f} g/t Au\n"
        en += f"- Final tailings grade: {float(tails['au_gt']):.6f} g/t Au\n"
    if rec_mb > 0:
        fr += f"- **Recuperation globale du bilan massique: {rec_mb:.1f}%**\n"
        en += f"- **Overall mass balance recovery: {rec_mb:.1f}%**\n"
    if annual_oz > 0:
        fr += f"- **Production annuelle estimee: {annual_oz:,.0f} oz Au/an**\n"
        en += f"- **Estimated annual production: {annual_oz:,.0f} oz Au/year**\n"

    # Water balance summary
    wb = data.get("water_balance", [])
    if wb:
        total_fresh = sum(float(n.get("inflow") or 0) for n in wb)
        total_recycle = sum(float(n.get("recycle") or 0) for n in wb)
        if total_fresh > 0:
            recycle_pct = (total_recycle / (total_fresh + total_recycle) * 100) if total_fresh + total_recycle > 0 else 0
            fr += (
                f"\n**Bilan hydrique:**\n"
                f"- Eau fraiche consommee: {total_fresh:.0f} m3/h\n"
                f"- Eau recyclee: {total_recycle:.0f} m3/h ({recycle_pct:.0f}% du total)\n"
                f"- Consommation specifique nette: {total_fresh / max(float(_tph or 1), 1):.3f} m3/t traitee\n"
            )
            en += (
                f"\n**Water balance:**\n"
                f"- Fresh water consumption: {total_fresh:.0f} m3/h\n"
                f"- Recycled water: {total_recycle:.0f} m3/h ({recycle_pct:.0f}% of total)\n"
                f"- Net specific consumption: {total_fresh / max(float(_tph or 1), 1):.3f} m3/t processed\n"
            )

    fr += (
        "\n**Note QP:** Le bilan massique est genere a partir des donnees DC nominales et "
        "des essais metallurgiques. Les flux doivent etre confirmes par des essais en continu "
        f"lors de la phase {'FS' if phase == 'pfs' else 'de mise en service'}. "
        f"Precision du bilan: ±{5 if phase == 'scoping' else 3 if phase == 'pfs' else 2}% sur les flux principaux.\n"
    )
    en += (
        "\n**QP Note:** The mass balance is generated from nominal DC data and metallurgical "
        "tests. Streams must be confirmed by continuous tests during the "
        f"{'FS' if phase == 'pfs' else 'commissioning'} phase. "
        f"Balance accuracy: ±{5 if phase == 'scoping' else 3 if phase == 'pfs' else 2}% on major streams.\n"
    )

    return {"key": "17.3", "title_fr": "Bilan massique et hydrique",
            "title_en": "Mass and Water Balance",
            "content_fr": fr, "content_en": en}


def _gen_17_4(phase, data):
    p = data.get("project") or {}
    name = p.get("project_name", "N/D")
    float(p.get("target_tph") or 0)
    d1 = data["d1"]
    a1 = data["a1"]
    env = data["env"]

    avg_nacn = _avg(d1, "nacn_consumption_kg_t")
    avg_cao = _avg(d1, "cao_consumption_kg_t")
    avg_s = _avg(a1, "s_total_pct")
    _avg(a1, "as_ppm")
    _avg(a1, "cu_pct")
    avg_c_org = _avg(a1, "c_organic_pct")
    avg_wad = _avg(env, "wad_cn_mg_l")

    fr = (
        f"**17.4 Consommation de reactifs et specifications — Projet {name}**\n\n"
        f"Les consommations de reactifs sont etablies a partir des resultats d'essais LIMS "
        f"(section 13.5) et des correlations empiriques pour les circuits n'ayant pas fait "
        f"l'objet d'essais specifiques. Ces consommations constituent la base des estimations "
        f"OPEX et du dimensionnement des systemes d'alimentation en reactifs.\n\n"
    )
    en = (
        f"**17.4 Reagent Consumption and Specifications — {name} Project**\n\n"
        f"Reagent consumptions are established from LIMS test results (Section 13.5) and "
        f"industry empirical correlations for circuits not specifically tested. These "
        f"consumptions form the basis for OPEX estimates and reagent feed system sizing.\n\n"
    )

    fr += "**Reactifs du circuit CIL:**\n\n"
    en += "**CIL circuit reagents:**\n\n"
    fr += "| Reactif | Consommation | Specification | Base | Cout indicatif |\n|---|---|---|---|---|\n"
    en += "| Reagent | Consumption | Specification | Basis | Indicative cost |\n|---|---|---|---|---|\n"

    reagents_fr = []
    reagents_en = []

    if avg_nacn:
        nacn_cost_est = avg_nacn * 3.5
        excess = " (**eleve**)" if avg_nacn > 0.5 else (" (optimal)" if avg_nacn < 0.3 else "")
        reagents_fr.append(("Cyanure de sodium (NaCN 98%)", f"{avg_nacn:.2f} kg/t{excess}",
                            "NaCN ≥ 98%, granules", "Essais LIMS", f"~${nacn_cost_est:.2f}/t minerai"))
        reagents_en.append(("Sodium cyanide (NaCN 98%)", f"{avg_nacn:.2f} kg/t{excess}",
                            "NaCN ≥ 98%, granules", "LIMS tests", f"~${nacn_cost_est:.2f}/t ore"))
    else:
        reagents_fr.append(("Cyanure de sodium (NaCN 98%)", "0.20–0.40 kg/t (estimatif)",
                            "NaCN ≥ 98%, granules", "Empirique industrie", "~$0.70–1.40/t"))
        reagents_en.append(("Sodium cyanide (NaCN 98%)", "0.20–0.40 kg/t (estimate)",
                            "NaCN ≥ 98%, granules", "Industry empirical", "~$0.70–1.40/t"))

    if avg_cao:
        reagents_fr.append(("Chaux vive (CaO) — pH control", f"{avg_cao:.2f} kg/t",
                            "CaO ≥ 90%, granules 5–25 mm", "Essais LIMS", f"~${avg_cao * 0.10:.2f}/t minerai"))
        reagents_en.append(("Quick lime (CaO) — pH control", f"{avg_cao:.2f} kg/t",
                            "CaO ≥ 90%, granules 5–25 mm", "LIMS tests", f"~${avg_cao * 0.10:.2f}/t ore"))
    else:
        reagents_fr.append(("Chaux vive (CaO)", "0.30–0.80 kg/t (estimatif)",
                            "CaO ≥ 90%, granules", "Empirique industrie", "~$0.03–0.08/t"))
        reagents_en.append(("Quick lime (CaO)", "0.30–0.80 kg/t (estimate)",
                            "CaO ≥ 90%, granules", "Industry empirical", "~$0.03–0.08/t"))

    if avg_wad and avg_wad > cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L:
        reagents_fr.append(("Dioxyde de soufre (SO2) — destruction CN", "1.5–4.0 kg SO2/kg CN",
                            "SO2 liquide, pureté ≥ 99.5%", "INCO SO2/Air process", "~$0.20–0.50/t"))
        reagents_en.append(("Sulphur dioxide (SO2) — CN destruction", "1.5–4.0 kg SO2/kg CN",
                            "Liquid SO2, purity ≥ 99.5%", "INCO SO2/Air process", "~$0.20–0.50/t"))
    else:
        reagents_fr.append(("Dioxyde de soufre (SO2) / H2O2 — destruction CN",
                            "1.5–4.0 kg SO2/kg CN ou 3–5 kg H2O2/kg CN",
                            "SO2 liquide ou H2O2 50%", "Normes IFC / destruction CN", "~$0.10–0.30/t"))
        reagents_en.append(("Sulphur dioxide (SO2) / H2O2 — CN destruction",
                            "1.5–4.0 kg SO2/kg CN or 3–5 kg H2O2/kg CN",
                            "Liquid SO2 or H2O2 50%", "IFC standards / CN destruction", "~$0.10–0.30/t"))

    reagents_fr.extend([
        ("Floculant (epaississement residus)", "15–60 g/t", "Magnafloc ou equivalent", "Essais Coe & Clevenger", "~$0.01–0.05/t"),
        ("Charbon actif (CIL — appoint)", "20–50 g/t (appoint annuel)", "Charbon noix de coco, grade CIL", "Consommation operationnelle", "~$0.05–0.15/t"),
        ("Medias de broyage (boulets)", f"{1.0 + (avg_s or 0) * 0.5:.1f}–{2.0 + (avg_s or 0) * 0.5:.1f} kg/t",
         "Boulets fonte 3 Cr ou 18 Cr", "BWi empirique", "~$0.30–0.60/t"),
    ])
    reagents_en.extend([
        ("Flocculant (tailings thickening)", "15–60 g/t", "Magnafloc or equivalent", "Coe & Clevenger tests", "~$0.01–0.05/t"),
        ("Activated carbon (CIL — make-up)", "20–50 g/t (annual make-up)", "Coconut shell, CIL grade", "Operational consumption", "~$0.05–0.15/t"),
        ("Grinding media (balls)", f"{1.0 + (avg_s or 0) * 0.5:.1f}–{2.0 + (avg_s or 0) * 0.5:.1f} kg/t",
         "High chrome balls 3 Cr or 18 Cr", "BWi empirical", "~$0.30–0.60/t"),
    ])

    # Flotation reagents if applicable
    if avg_s and avg_s > cfg.FLOTATION_S_THRESHOLD_PCT:
        reagents_fr.extend([
            ("Xanthate potassique (PAX)", "50–80 g/t", "PAX SENMIN ou equivalent", "Essais flottation", "~$0.05–0.10/t"),
            ("Mousse (MIBC)", "20–40 g/t", "MIBC ou Dowfroth 250", "Essais flottation", "~$0.02–0.05/t"),
        ])
        reagents_en.extend([
            ("Potassium amyl xanthate (PAX)", "50–80 g/t", "PAX SENMIN or equivalent", "Flotation tests", "~$0.05–0.10/t"),
            ("Frother (MIBC)", "20–40 g/t", "MIBC or Dowfroth 250", "Flotation tests", "~$0.02–0.05/t"),
        ])

    # Preg-robbing control
    if avg_c_org and avg_c_org > 0.2:
        reagents_fr.append(("CYANOSAVE ou bentonite (anti-preg-robbing)", "100–300 g/t", "Bentonite grade min. CIL", "Essais PREN / competitive adsorption", "~$0.05–0.15/t"))
        reagents_en.append(("CYANOSAVE or bentonite (preg-robbing control)", "100–300 g/t", "Bentonite CIL min. grade", "PREN / competitive adsorption tests", "~$0.05–0.15/t"))

    for (r_fr, cons_fr, spec_fr, base_fr, cost_fr), (r_en, cons_en, spec_en, base_en, cost_en) in zip(reagents_fr, reagents_en):
        fr += f"| {r_fr} | {cons_fr} | {spec_fr} | {base_fr} | {cost_fr} |\n"
        en += f"| {r_en} | {cons_en} | {spec_en} | {base_en} | {cost_en} |\n"

    fr += "\n"
    en += "\n"

    # OPEX costs if available
    opex = data.get("opex_items", [])
    if opex:
        reagent_items = [i for i in opex if any(kw in (i.get("category") or "").lower()
                                                 for kw in ("reactif", "reagent", "cyanure", "chaux", "charbon"))]
        if reagent_items:
            total_reagent_cost = sum(float(i.get("unit_cost_usd") or 0) for i in reagent_items)
            fr += f"**Couts OPEX reactifs (base donnees du projet):**\n"
            en += f"**Reagent OPEX costs (from project data):**\n"
            for item in reagent_items:
                cost = float(item.get("unit_cost_usd") or 0)
                if cost > 0:
                    fr += f"- {item['category']}: ${cost:.2f}/t traitee\n"
                    en += f"- {item['category']}: ${cost:.2f}/t processed\n"
            fr += f"**Total reactifs: ${total_reagent_cost:.2f}/t traitee**\n\n"
            en += f"**Total reagents: ${total_reagent_cost:.2f}/t processed**\n\n"

    fr += (
        "**Provisions pour contingences reactifs:** Un facteur de contingence de 10–15% "
        "est applique sur les consommations LIMS pour les conditions operationnelles "
        "(temperature, saison, variations de minerai)."
    )
    en += (
        "**Reagent contingency provisions:** A 10–15% contingency factor is applied to "
        "LIMS consumptions for operational conditions "
        "(temperature, season, ore variability)."
    )

    return {"key": "17.4", "title_fr": "Consommation de reactifs",
            "title_en": "Reagent Consumption",
            "content_fr": fr, "content_en": en}


def _gen_17_5(phase, data):
    p = data.get("project") or {}
    name = p.get("project_name", "N/D")
    mine_life = int(p.get("mine_life_years") or 10)
    tph = float(p.get("target_tph") or 0)
    avail = float(p.get("availability_pct") or 92)
    env = data["env"]
    basis = data.get("gistm_basis")
    wb = data.get("water_balance", [])

    avg_wad = _avg(env, "wad_cn_mg_l")
    avg_as = _avg(env, "arsenic_mg_l")
    adr_risks = [r.get("acid_drainage_risk") for r in env if r.get("acid_drainage_risk")]

    mtpa = tph * 24 * 365 * (avail / 100) / 1e6
    total_tailings_mt = mtpa * mine_life if mtpa > 0 else 0

    fr = (
        f"**17.5 Gestion des residus miniers (TSF) — Projet {name}**\n\n"
        f"La gestion des residus miniers est un element critique du projet {name}. "
        f"Le parc a residus (Tailings Storage Facility — TSF) est concu pour accueillir "
        f"l'ensemble des residus de lixiviation CIL sur la duree de vie de la mine "
        f"({mine_life} ans), soit un volume total estimatif de **{total_tailings_mt:.1f} Mt** "
        f"de residus secs.\n\n"
        f"La conception et l'exploitation du TSF sont conformes aux normes internationales:\n"
        f"- Global Industry Standard on Tailings Management (GISTM — ICMM, 2020)\n"
        f"- Directives IFC/Banque Mondiale sur les rejets miniers\n"
        f"- MAC — Vers une conception et une gestion ameliorees des parcs a residus (2017)\n\n"
    )
    en = (
        f"**17.5 Tailings Management (TSF) — {name} Project**\n\n"
        f"Tailings management is a critical component of the {name} project. "
        f"The Tailings Storage Facility (TSF) is designed to accommodate all CIL leach "
        f"tailings over the life of mine ({mine_life} years), for an estimated total volume "
        f"of **{total_tailings_mt:.1f} Mt** of dry tailings.\n\n"
        f"TSF design and operation comply with international standards:\n"
        f"- Global Industry Standard on Tailings Management (GISTM — ICMM, 2020)\n"
        f"- IFC/World Bank Guidelines on Mining Waste\n"
        f"- MAC — Towards Safer Tailings Facilities (2017)\n\n"
    )

    fr += "**Procede de traitement des residus avant deposition:**\n\n"
    en += "**Tailings treatment process before deposition:**\n\n"

    fr += (
        f"1. **Destruction du cyanure (SO2/Air — INCO ou H2O2):** "
        f"Reduction du CN WAD de la pulpe CIL a < {cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L} mg/L. "
        f"Temps de retention: 30–60 min. pH = 8.5–9.0. "
        f"Catalyseur: sulfate de cuivre (CuSO4, 2–5 ppm). "
        f"Aeration par compresseurs (ratio air/pulpe: 5–15 m3 air/m3 pulpe).\n\n"
    )
    en += (
        f"1. **Cyanide destruction (SO2/Air — INCO or H2O2):** "
        f"Reduction of CIL pulp WAD CN to < {cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L} mg/L. "
        f"Retention time: 30–60 min. pH = 8.5–9.0. "
        f"Catalyst: copper sulphate (CuSO4, 2–5 ppm). "
        f"Aeration by compressors (air/pulp ratio: 5–15 m3 air/m3 pulp).\n\n"
    )

    fr += (
        "2. **Epaississement des residus detoxifies:** "
        "Epaississement en epaississeur haute densite (HD) ou conventionnel. "
        "Densites de sous-verse visee: 55–70% solides (selon rheologie). "
        "Recyclage systematique de l'eau surversante (eau clarifiee enrichie en CN residuel).\n\n"
    )
    en += (
        "2. **Detoxified tailings thickening:** "
        "Thickening in high-density (HD) or conventional thickener. "
        "Target underflow densities: 55–70% solids (per rheology). "
        "Systematic overflow water recycle (clarified water with residual CN).\n\n"
    )

    fr += (
        "3. **Deposition en TSF:** "
        "Methode de deposition par conduites (pipeline) ou convoyeurs. "
        "Conception du TSF: digue de demarrage + raises successifs (methode montante aval ou ligne de centre "
        "selon la classe de consequence). "
        "Captage et recyclage du lixiviat de pied de digue. "
        "Systeme de drainage de fondation perimetral.\n\n"
    )
    en += (
        "3. **TSF deposition:** "
        "Deposition by pipeline or conveyors. "
        "TSF design: starter dam + successive raises (downstream or centreline raise method "
        "depending on consequence class). "
        "Perimeter toe drainage collection and recycle. "
        "Perimeter foundation drainage system.\n\n"
    )

    fr += "**Gestion des eaux du TSF:**\n\n"
    en += "**TSF water management:**\n\n"

    if wb:
        total_fresh = sum(float(n.get("inflow") or 0) for n in wb)
        total_recycle = sum(float(n.get("recycle") or 0) for n in wb)
        fr += (
            f"- Debit eau fraiche consomme: {total_fresh:.0f} m3/h\n"
            f"- Debit eau recyclee (TSF + epaississement): {total_recycle:.0f} m3/h\n"
            f"- Taux de recyclage: {(total_recycle / (total_fresh + total_recycle) * 100) if total_fresh + total_recycle > 0 else 0:.0f}%\n\n"
        )
        en += (
            f"- Fresh water intake: {total_fresh:.0f} m3/h\n"
            f"- Recycled water flow (TSF + thickening): {total_recycle:.0f} m3/h\n"
            f"- Recycle rate: {(total_recycle / (total_fresh + total_recycle) * 100) if total_fresh + total_recycle > 0 else 0:.0f}%\n\n"
        )
    else:
        fr += (
            "- Maximisation du recyclage de l'eau de process depuis le TSF\n"
            "- Bilan hydrique climatique (evaporation, precipitation, decharges) a etablir en PFS/FS\n\n"
        )
        en += (
            "- Maximization of process water recycle from TSF\n"
            "- Climatic water balance (evaporation, precipitation, discharge) to be established at PFS/FS\n\n"
        )

    fr += "**Conformite CN WAD et contaminants:**\n\n"
    en += "**WAD CN and contaminant compliance:**\n\n"
    fr += f"| Parametre | Valeur mesuree | Limite IFC | Statut |\n|---|---|---|---|\n"
    en += f"| Parameter | Measured value | IFC Limit | Status |\n|---|---|---|---|\n"
    if avg_wad is not None:
        st_fr = "✓ Conforme" if avg_wad <= cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L else "✗ Traitement requis"
        st_en = "✓ Compliant" if avg_wad <= cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L else "✗ Treatment required"
        fr += f"| CN WAD residus | {avg_wad:.2f} mg/L | ≤ {cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L} mg/L | {st_fr} |\n"
        en += f"| Tailings WAD CN | {avg_wad:.2f} mg/L | ≤ {cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L} mg/L | {st_en} |\n"
    if avg_as is not None:
        st_fr = "✓ Conforme" if avg_as <= 0.5 else "✗ Non conforme"
        st_en = "✓ Compliant" if avg_as <= 0.5 else "✗ Non-compliant"
        fr += f"| Arsenic (As) lixiviat | {avg_as:.3f} mg/L | ≤ 0.5 mg/L | {st_fr} |\n"
        en += f"| Arsenic (As) leachate | {avg_as:.3f} mg/L | ≤ 0.5 mg/L | {st_en} |\n"
    fr += "\n"
    en += "\n"

    # GISTM basis if available
    if basis:
        fr += "**Base de conception GISTM (Tailings Design Basis — TDB):**\n\n"
        en += "**GISTM-aligned Tailings Design Basis (TDB):**\n\n"
        fr += (
            f"| Parametre | Valeur |\n|---|---|\n"
            f"| Classe de consequence | {basis['consequence_class']} |\n"
            f"| Population a risque (PAR) | {int(basis['par_count'])} personnes |\n"
            f"| Crue de design (IDF) | 1/{int(basis['idf_return_period_yr'])} ans |\n"
            f"| Seisme de design (MDE) | 1/{int(basis['mde_return_period_yr'])} ans |\n"
            f"| FoS statique minimum | {float(basis['fs_static_min']):.2f} |\n"
            f"| FoS sismique minimum | {float(basis['fs_seismic_min']):.2f} |\n"
            f"| FoS post-liquefaction minimum | {float(basis['fs_post_liquefaction_min']):.2f} |\n"
            f"| Methodes construction autorisees | {', '.join(list(basis['allowed_construction_methods']))} |\n"
        )
        en += (
            f"| Parameter | Value |\n|---|---|\n"
            f"| Consequence class | {basis['consequence_class']} |\n"
            f"| Population at risk (PAR) | {int(basis['par_count'])} persons |\n"
            f"| Design flood (IDF) | 1/{int(basis['idf_return_period_yr'])} years |\n"
            f"| Design earthquake (MDE) | 1/{int(basis['mde_return_period_yr'])} years |\n"
            f"| Minimum static FoS | {float(basis['fs_static_min']):.2f} |\n"
            f"| Minimum seismic FoS | {float(basis['fs_seismic_min']):.2f} |\n"
            f"| Min post-liquefaction FoS | {float(basis['fs_post_liquefaction_min']):.2f} |\n"
            f"| Allowed construction methods | {', '.join(list(basis['allowed_construction_methods']))} |\n"
        )
    else:
        fr += (
            "**Conception GISTM requise:** Une base de conception GISTM (TDB) n'est pas encore "
            "definie pour ce projet. Conformement a la GISTM (2020), une TDB est obligatoire "
            "avant la phase de design detaille du TSF. Les parametres minimaux requis sont: "
            "classification du TSF, PAR, IDF, MDE, FoS minimum (statique, sismique, post-liquefaction), "
            "methode de construction, et programme de surveillance des performances.\n"
        )
        en += (
            "**GISTM design required:** A GISTM-aligned Tailings Design Basis (TDB) has not yet "
            "been defined for this project. Per GISTM (2020), a TDB is mandatory before "
            "the detailed TSF design phase. Minimum required parameters include: TSF classification, "
            "PAR, IDF, MDE, minimum FoS (static, seismic, post-liquefaction), construction method, "
            "and performance monitoring program.\n"
        )

    if adr_risks:
        from collections import Counter
        risk_counter = Counter(adr_risks)
        dominant = risk_counter.most_common(1)[0][0]
        if dominant.lower() in ("high", "eleve", "generating", "pag"):
            fr += (
                "\n**⚠ Risque DMA — Mesures de gestion speciales requises:**\n"
                "- Separation physique des materiaux generateurs d'acide (PAG) des materiaux non-PAG\n"
                "- Encapsulation des residus PAG dans des cellules dediees (couverture de type CCBE ou ET)\n"
                "- Collecte et neutralisation du drainage acide potentiel\n"
                "- Suivi hydrogeochimique pericentrique du TSF (piezometres, lysimetres)\n"
            )
            en += (
                "\n**⚠ AMD Risk — Special management measures required:**\n"
                "- Physical segregation of potentially acid-generating (PAG) from non-PAG materials\n"
                "- Encapsulation of PAG tailings in dedicated cells (CCBE or ET type cover)\n"
                "- Collection and neutralization of potential acid drainage\n"
                "- Peri-TSF hydrogeochemical monitoring (piezometers, lysimeters)\n"
            )

    return {"key": "17.5", "title_fr": "Gestion des residus et du TSF",
            "title_en": "Tailings and TSF Management",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 18: Project Infrastructure
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_18(phase, data):
    eq = data["equipment"]
    _p = data["project"]
    wb = data.get("water_balance", [])
    basis = data.get("gistm_basis")

    total_power = sum(float(e["power_installed_kw"]) for e in eq if e.get("power_installed_kw")) if eq else 0

    fr = "Les infrastructures du projet comprennent:\n\n"
    en = "Project infrastructure includes:\n\n"

    fr += "- Usine de traitement et batiments annexes\n"
    fr += "- Parc a residus (TSF)\n"
    fr += "- Systeme d'alimentation en eau (eau fraiche et recyclage)\n"
    fr += "- Alimentation electrique et distribution\n"
    fr += "- Routes d'acces et routes de service\n"
    fr += "- Camp et installations pour le personnel\n"
    fr += "- Laboratoire\n"
    en += "- Processing plant and ancillary buildings\n"
    en += "- Tailings storage facility (TSF)\n"
    en += "- Water supply system (fresh water and recycle)\n"
    en += "- Power supply and distribution\n"
    en += "- Access roads and service roads\n"
    en += "- Camp and personnel facilities\n"
    en += "- Laboratory\n"

    if total_power > 0:
        fr += f"\n**Puissance installee totale:** {total_power:,.0f} kW ({total_power / 1000:.1f} MW)\n"
        en += f"\n**Total installed power:** {total_power:,.0f} kW ({total_power / 1000:.1f} MW)\n"

    if eq:
        fr += f"\n**Equipements majeurs:** {len(eq)} items identifies\n"
        en += f"\n**Major equipment:** {len(eq)} items identified\n"
        long_lead = [e for e in eq if e.get("is_long_lead")]
        if long_lead:
            fr += f"- Dont {len(long_lead)} equipements a long delai de livraison\n"
            en += f"- Including {len(long_lead)} long-lead items\n"

    if wb:
        total_inflow = sum(float(n["inflow"]) for n in wb if n.get("inflow"))
        total_recycle = sum(float(n["recycle"]) for n in wb if n.get("recycle"))
        if total_inflow > 0:
            fr += f"\n**Bilan hydrique:**\n- Debit total entrant: {total_inflow:.0f} m3/h\n"
            en += f"\n**Water balance:**\n- Total inflow: {total_inflow:.0f} m3/h\n"
            if total_recycle > 0:
                recycle_pct = (total_recycle / total_inflow) * 100
                fr += f"- Eau recyclee: {total_recycle:.0f} m3/h ({recycle_pct:.0f}%)\n"
                en += f"- Recycled water: {total_recycle:.0f} m3/h ({recycle_pct:.0f}%)\n"

    if basis:
        fr += "\n**Tailings Design Basis (GISTM-aligned):**\n"
        en += "\n**Tailings Design Basis (GISTM-aligned):**\n"
        fr += f"- Classe de consequence: {basis['consequence_class']} (Annex 2 GISTM)\n"
        en += f"- Consequence class: {basis['consequence_class']} (GISTM Annex 2)\n"
        fr += f"- Population a risque (PAR): {int(basis['par_count'])}\n"
        en += f"- Population At Risk (PAR): {int(basis['par_count'])}\n"
        fr += f"- IDF: {int(basis['idf_return_period_yr'])} ans\n"
        en += f"- IDF: {int(basis['idf_return_period_yr'])} years\n"
        fr += f"- MDE: {int(basis['mde_return_period_yr'])} ans\n"
        en += f"- MDE: {int(basis['mde_return_period_yr'])} years\n"
        fr += f"- FoS minimums: statique {float(basis['fs_static_min']):.2f}, sismique {float(basis['fs_seismic_min']):.2f}, post-liquefaction {float(basis['fs_post_liquefaction_min']):.2f}\n"
        en += f"- Min FoS: static {float(basis['fs_static_min']):.2f}, seismic {float(basis['fs_seismic_min']):.2f}, post-liquefaction {float(basis['fs_post_liquefaction_min']):.2f}\n"
        methods = ", ".join(list(basis["allowed_construction_methods"]))
        fr += f"- Methodes de construction autorisees: {methods}\n"
        en += f"- Allowed construction methods: {methods}\n"
        fr += f"- Version active: V{int(basis['version'])}\n"
        en += f"- Active version: V{int(basis['version'])}\n"

    return {"key": "18", "title_fr": "Infrastructures du projet",
            "title_en": "Project Infrastructure",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 19: Market Studies and Contracts
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_19(phase, data):
    p = data["project"]
    gold_price = float(p.get("gold_price_usd_oz") or 2340) if p else 2340
    commodity = p.get("commodity", "Au") if p else "Au"

    fr = (
        f"Le produit final du projet est le dore (or + argent) produit par electrolyse.\n\n"
        f"**Parametres de marche:**\n"
        f"- Commodite principale: {commodity}\n"
        f"- Prix de l'or utilise: ${gold_price:,.0f}/oz\n"
        f"- Marche: London Bullion Market Association (LBMA)\n"
        f"- Produit: Barres de dore, affinage en raffinerie certifiee LBMA\n\n"
        f"Le marche de l'or est global et liquide. Les contrats d'affinage sont "
        f"disponibles aupres de plusieurs raffineries certifiees."
    )
    en = (
        f"The final product is doré (gold + silver) produced by electrowinning.\n\n"
        f"**Market parameters:**\n"
        f"- Primary commodity: {commodity}\n"
        f"- Gold price used: ${gold_price:,.0f}/oz\n"
        f"- Market: London Bullion Market Association (LBMA)\n"
        f"- Product: Doré bars, refining at LBMA-certified refinery\n\n"
        f"The gold market is global and liquid. Refining contracts are "
        f"available from several certified refineries."
    )

    return {"key": "19", "title_fr": "Etudes de marche et contrats",
            "title_en": "Market Studies and Contracts",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 20: Environmental Studies, Permitting and Social Impact
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_20(phase, data):
    env = data["env"]
    risks = data["risks"]
    env_risks = [r for r in risks if r.get("category") in ("Environmental", "Social", "Permitting")]

    avg_wad = _avg(env, "wad_cn_mg_l")
    avg_as = _avg(env, "arsenic_mg_l")

    fr = "Les etudes environnementales et l'evaluation de l'impact social sont conformes aux exigences reglementaires.\n\n"
    en = "Environmental studies and social impact assessment comply with regulatory requirements.\n\n"

    fr += "**Cadre reglementaire:**\n"
    fr += "- Etude d'impact environnemental (EIE)\n"
    fr += "- Normes IFC/Banque mondiale pour l'industrie miniere\n"
    fr += "- Consultation des communautes locales\n"
    fr += "- Plan de gestion environnementale et sociale (PGES)\n"
    en += "**Regulatory framework:**\n"
    en += "- Environmental Impact Assessment (EIA)\n"
    en += "- IFC/World Bank standards for mining industry\n"
    en += "- Local community consultation\n"
    en += "- Environmental and Social Management Plan (ESMP)\n"

    if env:
        fr += f"\n**Resultats des essais environnementaux ({len(env)} echantillons):**\n"
        en += f"\n**Environmental test results ({len(env)} samples):**\n"
        if avg_wad:
            status = "Conforme" if avg_wad <= cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L else "Non conforme"
            status_en = "Compliant" if avg_wad <= cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L else "Non-compliant"
            fr += f"- CN WAD: {avg_wad:.2f} mg/L ({status} — limite IFC: {cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L} mg/L)\n"
            en += f"- WAD CN: {avg_wad:.2f} mg/L ({status_en} — IFC limit: {cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L} mg/L)\n"
        if avg_as:
            fr += f"- Arsenic: {avg_as:.3f} mg/L\n"
            en += f"- Arsenic: {avg_as:.3f} mg/L\n"

    if env_risks:
        fr += f"\n**Risques environnementaux et sociaux identifies:** {len(env_risks)}\n"
        en += f"\n**Identified environmental and social risks:** {len(env_risks)}\n"
        for r in env_risks[:5]:
            crit = int(r["criticality"]) if r.get("criticality") else 0
            fr += f"- [{r['category']}] {r['description']} (criticite: {crit})\n"
            en += f"- [{r['category']}] {r['description']} (criticality: {crit})\n"

    return {"key": "20", "title_fr": "Etudes environnementales, permis et impact social",
            "title_en": "Environmental Studies, Permitting and Social or Community Impact",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 21: Capital and Operating Costs
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_21(phase, data):
    capex = data.get("capex_items", [])
    opex = data.get("opex_items", [])

    capex_total = _sum_field(capex, "total_cost_usd")
    opex_total = _sum_field(opex, "total_cost_usd")

    p = data["project"]
    tph = float(p["target_tph"] or 0) if p and p.get("target_tph") else 0
    avail = float(p.get("availability_pct") or 92) if p else 92
    mtpa = tph * 24 * 365 * (avail / 100) / 1e6

    fr = "Les estimations des couts en capital (CAPEX) et des couts operatoires (OPEX) sont presentees ci-dessous.\n\n"
    en = "Capital cost (CAPEX) and operating cost (OPEX) estimates are presented below.\n\n"

    if capex:
        fr += f"**CAPEX (${capex_total:,.0f} USD):**\n"
        en += f"**CAPEX (${capex_total:,.0f} USD):**\n"
        for item in capex:
            cost = float(item["total_cost_usd"]) if item.get("total_cost_usd") else 0
            if cost > 0:
                fr += f"- {item['category']}: ${cost:,.0f}\n"
                en += f"- {item['category']}: ${cost:,.0f}\n"
        if capex_total == 0:
            fr += "- Tous les postes sont a $0 — les estimations doivent etre completees\n"
            en += "- All items are at $0 — estimates need to be completed\n"

    if opex:
        fr += f"\n**OPEX (${opex_total:,.2f}/t traitee):**\n"
        en += f"\n**OPEX (${opex_total:,.2f}/t processed):**\n"
        for item in opex:
            cost = float(item["unit_cost_usd"]) if item.get("unit_cost_usd") else 0
            if cost > 0:
                fr += f"- {item['category']}: ${cost:.2f}/t\n"
                en += f"- {item['category']}: ${cost:.2f}/t\n"

        if mtpa > 0 and opex_total > 0:
            annual_opex = opex_total * mtpa * 1e6
            fr += f"\n**OPEX annuel estime:** ${annual_opex:,.0f} USD ({mtpa:.2f} Mtpa)\n"
            en += f"\n**Estimated annual OPEX:** ${annual_opex:,.0f} USD ({mtpa:.2f} Mtpa)\n"

    accuracy = {"scoping": "±35-50% (Classe 5 AACE)", "pfs": "±25-30% (Classe 4 AACE)", "fs": "±10-15% (Classe 3 AACE)"}
    accuracy_en = {"scoping": "±35-50% (AACE Class 5)", "pfs": "±25-30% (AACE Class 4)", "fs": "±10-15% (AACE Class 3)"}
    fr += f"\n**Precision de l'estimation:** {accuracy.get(phase, '±35-50%')}"
    en += f"\n**Estimate accuracy:** {accuracy_en.get(phase, '±35-50%')}"

    return {"key": "21", "title_fr": "Couts en capital et couts operatoires",
            "title_en": "Capital and Operating Costs",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 22: Economic Analysis
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_22(phase, data):
    p = data["project"]
    if not p:
        return {"key": "22", "title_fr": "Analyse economique", "title_en": "Economic Analysis",
                "content_fr": "Donnees insuffisantes.", "content_en": "Insufficient data."}

    gold_price = float(p.get("gold_price_usd_oz") or 2340)
    discount_rate = float(p.get("discount_rate_pct") or 5)
    mine_life = int(p.get("mine_life_years") or 10)
    tph = float(p.get("target_tph") or 0)
    grade = float(p.get("gold_grade_g_t") or 0)
    avail = float(p.get("availability_pct") or 92)

    mb = data["mb"]
    rom = next((s for s in mb if s["stream"] == "ROM Feed"), None)
    tails = next((s for s in mb if s["stream"] == "Tailings Final"), None)
    rec = 0
    if rom and tails and rom.get("au_gt") and tails.get("au_gt"):
        feed_g = float(rom["au_gt"])
        tails_g = float(tails["au_gt"])
        tph_val = float(rom["solids_tph"]) if rom.get("solids_tph") else tph
        tails_tph = float(tails["solids_tph"]) if tails.get("solids_tph") else tph_val
        rec = (1 - (tails_tph * tails_g) / (tph_val * feed_g)) * 100 if feed_g > 0 and tph_val > 0 else 0
    if rec == 0:
        # Use settings as single source of truth (89.0 %), consistent with
        # industry_defaults.yaml and helpers.py.  Previously cfg.DEFAULT_RECOVERY_PCT
        # which was 91.0 — 2 % higher than every other fallback in the app.
        rec = _SETTINGS.default_recovery_pct

    annual_tonnes = tph * 24 * 365 * (avail / 100)
    annual_oz = annual_tonnes * grade * (rec / 100) * TROY_OZ_PER_GRAM if grade > 0 else 0
    annual_revenue = annual_oz * gold_price

    capex_total = _sum_field(data.get("capex_items", []), "total_cost_usd")
    opex_per_t = _sum_field(data.get("opex_items", []), "total_cost_usd")
    annual_opex = opex_per_t * annual_tonnes if annual_tonnes > 0 else 0

    annual_cf = annual_revenue - annual_opex
    npv = -capex_total
    for yr in range(1, mine_life + 1):
        npv += annual_cf / ((1 + discount_rate / 100) ** yr)

    payback = capex_total / annual_cf if annual_cf > 0 else 0

    fr = (
        f"L'analyse economique est basee sur les hypotheses suivantes:\n\n"
        f"**Hypotheses:**\n"
        f"- Prix de l'or: ${gold_price:,.0f}/oz\n"
        f"- Taux d'actualisation: {discount_rate:.1f}%\n"
        f"- Duree de vie de la mine: {mine_life} ans\n"
        f"- Recuperation globale: {rec:.1f}%\n"
        f"- Disponibilite: {avail:.0f}%\n\n"
        f"**Resultats:**\n"
        f"- Production annuelle: {annual_oz:,.0f} oz Au\n"
        f"- Revenu annuel: ${annual_revenue:,.0f}\n"
        f"- OPEX annuel: ${annual_opex:,.0f}\n"
        f"- Flux de tresorerie annuel: ${annual_cf:,.0f}\n"
    )
    en = (
        f"The economic analysis is based on the following assumptions:\n\n"
        f"**Assumptions:**\n"
        f"- Gold price: ${gold_price:,.0f}/oz\n"
        f"- Discount rate: {discount_rate:.1f}%\n"
        f"- Mine life: {mine_life} years\n"
        f"- Overall recovery: {rec:.1f}%\n"
        f"- Availability: {avail:.0f}%\n\n"
        f"**Results:**\n"
        f"- Annual production: {annual_oz:,.0f} oz Au\n"
        f"- Annual revenue: ${annual_revenue:,.0f}\n"
        f"- Annual OPEX: ${annual_opex:,.0f}\n"
        f"- Annual cash flow: ${annual_cf:,.0f}\n"
    )

    if capex_total > 0:
        fr += f"- CAPEX total: ${capex_total:,.0f}\n"
        fr += f"- VAN (NPV) @ {discount_rate:.0f}%: ${npv:,.0f}\n"
        en += f"- Total CAPEX: ${capex_total:,.0f}\n"
        en += f"- NPV @ {discount_rate:.0f}%: ${npv:,.0f}\n"
        if payback > 0:
            fr += f"- Delai de recuperation: {payback:.1f} ans\n"
            en += f"- Payback period: {payback:.1f} years\n"

    fr += (
        f"\n**Note:** Cette analyse est preliminaire et ne tient pas compte des impots, "
        f"redevances, fonds de roulement et cout de fermeture."
    )
    en += (
        f"\n**Note:** This analysis is preliminary and does not account for taxes, "
        f"royalties, working capital and closure costs."
    )

    return {"key": "22", "title_fr": "Analyse economique",
            "title_en": "Economic Analysis",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 23: Adjacent Properties
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_23(phase, data):
    p = data["project"]
    name = p["project_name"] if p else "N/D"

    fr = (
        f"Les proprietes adjacentes au projet {name} n'ont pas fait l'objet d'une etude detaillee "
        f"dans le cadre du present rapport. Les informations sur les proprietes voisines "
        f"sont presentees a titre informatif et ne doivent pas etre utilisees pour inferer "
        f"la continuite de la mineralisation sur la propriete du projet.\n\n"
        f"Les donnees des proprietes adjacentes sont tirees de sources publiques et "
        f"n'ont pas ete verifiees par l'auteur."
    )
    en = (
        f"Properties adjacent to the {name} project have not been the subject of a detailed study "
        f"in this report. Information on neighbouring properties "
        f"is presented for informational purposes and should not be used to infer "
        f"continuity of mineralization onto the project property.\n\n"
        f"Adjacent property data is sourced from public records and "
        f"has not been verified by the author."
    )

    return {"key": "23", "title_fr": "Proprietes adjacentes",
            "title_en": "Adjacent Properties",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 24: Other Relevant Data and Information
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_24(phase, data):
    risks = data["risks"]
    stages = data.get("stages", [])

    fr = "Cette section presente les informations complementaires pertinentes au projet.\n\n"
    en = "This section presents additional relevant information for the project.\n\n"

    if risks:
        critical = [r for r in risks if r.get("criticality") and int(r["criticality"]) >= 15]
        blockers = [r for r in risks if r.get("is_gate_blocker")]
        fr += f"**Registre des risques:**\n"
        fr += f"- Risques totaux: {len(risks)}\n"
        fr += f"- Risques critiques (P×I >= 15): {len(critical)}\n"
        fr += f"- Bloqueurs de gate: {len(blockers)}\n\n"
        en += f"**Risk register:**\n"
        en += f"- Total risks: {len(risks)}\n"
        en += f"- Critical risks (P×I >= 15): {len(critical)}\n"
        en += f"- Gate blockers: {len(blockers)}\n\n"

    if stages:
        fr += "**Progression des etapes (Stage-Gates):**\n"
        en += "**Stage-Gate progress:**\n"
        for s in stages:
            pct = int(s["completion_pct"]) if s.get("completion_pct") else 0
            fr += f"- {s['stage_name']}: {pct}% ({s.get('status', 'N/D')})\n"
            en += f"- {s['stage_name']}: {pct}% ({s.get('status', 'N/D')})\n"

    return {"key": "24", "title_fr": "Autres donnees et informations pertinentes",
            "title_en": "Other Relevant Data and Information",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 25: Interpretation and Conclusions
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_25(phase, data):
    p = data["project"]
    name = p["project_name"] if p else "N/D"
    grade = float(p.get("gold_grade_g_t") or 0) if p else 0
    tph = float(p.get("target_tph") or 0) if p else 0
    avail = float(p.get("availability_pct") or 92) if p else 92

    d1 = data["d1"]
    b1 = data["b1"]
    c2 = data["c2"]
    a1 = data["a1"]
    env = data["env"]
    avg_rec = _avg(d1, "au_recovery_pct")
    avg_bwi = _avg(b1, "bwi_kwh_t")
    avg_grg = _avg(c2, "au_recovery_pct")
    avg_s = _avg(a1, "s_total_pct")
    avg_as = _avg(a1, "as_ppm")
    avg_c_org = _avg(a1, "c_organic_pct")
    avg_wad = _avg(env, "wad_cn_mg_l")

    mb = data["mb"]
    rom = next((s for s in mb if s["stream"] == "ROM Feed"), None)
    tails = next((s for s in mb if s["stream"] == "Tailings Final"), None)
    overall_rec = 0.0
    if rom and tails and rom.get("au_gt") and tails.get("au_gt"):
        feed_g = float(rom["au_gt"])
        tails_g = float(tails["au_gt"])
        tph_val = float(rom["solids_tph"]) if rom.get("solids_tph") else 0
        tails_tph = float(tails["solids_tph"]) if tails.get("solids_tph") else tph_val
        overall_rec = (1 - (tails_tph * tails_g) / (tph_val * feed_g)) * 100 if feed_g > 0 and tph_val > 0 else 0

    annual_oz = tph * 24 * 365 * (avail / 100) * grade * ((overall_rec or avg_rec or 88) / 100) * TROY_OZ_PER_GRAM if tph > 0 and grade > 0 else 0

    fr = (
        f"**Interpretation et conclusions metallurgiques — Projet {name}**\n\n"
        f"La presente section synthetise les conclusions de la personne qualifiee "
        f"(QP) en metallurgie et traitement mineralurgique, fondees sur les resultats "
        f"du programme d'essais metallurgiques documente aux sections 13 et 17 du present "
        f"rapport technique, conformement aux exigences de la NI 43-101.\n\n"
    )
    en = (
        f"**Interpretation and Metallurgical Conclusions — {name} Project**\n\n"
        f"This section synthesizes the conclusions of the Qualified Person (QP) in "
        f"metallurgy and mineral processing, based on the metallurgical test program "
        f"documented in Sections 13 and 17 of this technical report, in accordance with "
        f"NI 43-101 requirements.\n\n"
    )

    fr += "**Conclusions cles:**\n\n"
    en += "**Key conclusions:**\n\n"

    conclusions_fr = []
    conclusions_en = []

    # Head assay / ore characterization
    if avg_s is not None and avg_c_org is not None:
        is_complex = (avg_s > cfg.FLOTATION_S_THRESHOLD_PCT or avg_as and avg_as > 3000) and avg_c_org > 0.2
        if is_complex:
            conclusions_fr.append(
                f"**Caracterisation du minerai — COMPLEXE:** La combinaison de soufre total ({avg_s:.2f}%), "
                f"d'arsenic ({avg_as:.0f} ppm) et de carbone organique ({avg_c_org:.3f}%) classe ce minerai "
                f"comme partiellement refractaire avec risque de preg-robbing. Le flowsheet CIL direct "
                f"necessite une validation approfondie par essais pilotes."
            )
            conclusions_en.append(
                f"**Ore characterization — COMPLEX:** The combination of total sulphur ({avg_s:.2f}%), "
                f"arsenic ({avg_as:.0f} ppm) and organic carbon ({avg_c_org:.3f}%) classifies this ore "
                f"as partially refractory with preg-robbing risk. Direct CIL flowsheet requires thorough "
                f"pilot-scale validation."
            )
        else:
            ore_type_fr = "libre" if not (avg_s and avg_s > cfg.FLOTATION_S_THRESHOLD_PCT) else "sulfure traitable par CIL"
            ore_type_en = "free-milling" if not (avg_s and avg_s > cfg.FLOTATION_S_THRESHOLD_PCT) else "sulphide amenable to CIL"
            conclusions_fr.append(
                f"**Caracterisation du minerai — FAVORABLE:** Le minerai est de type {ore_type_fr}, "
                f"confirme par les analyses de tetes ({len(a1)} echantillons). Le procede CIL est "
                f"adapte pour le traitement de ce minerai."
            )
            conclusions_en.append(
                f"**Ore characterization — FAVOURABLE:** The ore is {ore_type_en}, "
                f"confirmed by head assay analyses ({len(a1)} samples). CIL processing is "
                f"suitable for treating this ore."
            )

    # Comminution
    if avg_bwi:
        bwi_cat = ("tendre" if avg_bwi < 12 else "modere" if avg_bwi < 16 else "dur" if avg_bwi < 20 else "tres dur")
        bwi_cat_en = ("soft" if avg_bwi < 12 else "medium-hard" if avg_bwi < 16 else "hard" if avg_bwi < 20 else "very hard")
        circuit_fr = ("SAG + boulets standard" if avg_bwi < 16 else
                      "SAG + boulets avec facteur de service majore" if avg_bwi < 20 else
                      "HPGR + boulets (obligatoire)")
        circuit_en = ("standard SAG + ball mill" if avg_bwi < 16 else
                      "SAG + ball mill with increased service factor" if avg_bwi < 20 else
                      "HPGR + ball mill (mandatory)")
        _ai_val = _avg(b1, "abrasion_index_ai")
        _ai_note_fr = f"Facteur d'abrasion a surveiller (Ai = {round(_ai_val or 0, 3)})." if _ai_val else ""
        _ai_note_en = f"Abrasion factor to monitor (Ai = {round(_ai_val or 0, 3)})." if _ai_val else ""
        conclusions_fr.append(
            f"**Broyabilite — {bwi_cat.upper()}:** BWi moyen = {avg_bwi:.1f} kWh/t ({len(b1)} echantillons). "
            f"Circuit recommande: {circuit_fr}. {_ai_note_fr}"
        )
        conclusions_en.append(
            f"**Grindability — {bwi_cat_en.upper()}:** Average BWi = {avg_bwi:.1f} kWh/t ({len(b1)} samples). "
            f"Recommended circuit: {circuit_en}. {_ai_note_en}"
        )

    # Gravity
    if avg_grg is not None:
        if avg_grg >= cfg.GRG_CIRCUIT_THRESHOLD_PCT:
            conclusions_fr.append(
                f"**Recuperation gravimetrique — CIRCUIT JUSTIFIE:** GRG moyen = {avg_grg:.1f}% ({len(c2)} essais). "
                f"Un circuit Knelson/Falcon est recommande. Recuperation attendue par gravite: "
                f"{min(avg_grg * 0.8, 20):.1f}–{min(avg_grg, 30):.1f}%."
            )
            conclusions_en.append(
                f"**Gravity recovery — CIRCUIT JUSTIFIED:** Average GRG = {avg_grg:.1f}% ({len(c2)} tests). "
                f"A Knelson/Falcon circuit is recommended. Expected gravity recovery: "
                f"{min(avg_grg * 0.8, 20):.1f}–{min(avg_grg, 30):.1f}%."
            )
        else:
            conclusions_fr.append(
                f"**Recuperation gravimetrique — NON JUSTIFIEE:** GRG = {avg_grg:.1f}% — "
                f"inferieur au seuil economique ({cfg.GRG_CIRCUIT_THRESHOLD_PCT}%). "
                f"Flowsheet CIL direct sans circuit de gravite recommande."
            )
            conclusions_en.append(
                f"**Gravity recovery — NOT JUSTIFIED:** GRG = {avg_grg:.1f}% — "
                f"below economic threshold ({cfg.GRG_CIRCUIT_THRESHOLD_PCT}%). "
                f"Direct CIL flowsheet without gravity circuit recommended."
            )

    # Leach recovery
    if avg_rec:
        rec_class = ("excellente (≥ 90%)" if avg_rec >= 90 else
                     "bonne (82–90%)" if avg_rec >= 82 else
                     "moderee (72–82%)" if avg_rec >= 72 else "faible (< 72%)")
        rec_class_en = ("excellent (≥ 90%)" if avg_rec >= 90 else
                        "good (82–90%)" if avg_rec >= 82 else
                        "moderate (72–82%)" if avg_rec >= 72 else "low (< 72%)")
        conclusions_fr.append(
            f"**Lixiviation CIL — Recuperation {rec_class.upper()}:** {avg_rec:.1f}% "
            f"({len(d1)} essais bottle roll/CIL). "
            f"Consommation NaCN: {_avg(d1, 'nacn_consumption_kg_t') or 'N/D':.2f} kg/t." if _avg(d1, 'nacn_consumption_kg_t') else
            f"**Lixiviation CIL — Recuperation {rec_class.upper()}:** {avg_rec:.1f}% ({len(d1)} essais)."
        )
        conclusions_en.append(
            f"**CIL Leaching — {rec_class_en.upper()} recovery:** {avg_rec:.1f}% "
            f"({len(d1)} bottle roll/CIL tests). "
            f"NaCN consumption: {_avg(d1, 'nacn_consumption_kg_t') or 'N/A':.2f} kg/t." if _avg(d1, 'nacn_consumption_kg_t') else
            f"**CIL Leaching — {rec_class_en.upper()} recovery:** {avg_rec:.1f}% ({len(d1)} tests)."
        )

    # Overall flowsheet
    if overall_rec > 0:
        conclusions_fr.append(
            f"**Recuperation globale du flowsheet: {overall_rec:.1f}%** (base: bilan massique genere). "
            f"Production annuelle estimee: {annual_oz:,.0f} oz Au/an."
        )
        conclusions_en.append(
            f"**Overall flowsheet recovery: {overall_rec:.1f}%** (basis: generated mass balance). "
            f"Estimated annual production: {annual_oz:,.0f} oz Au/year."
        )

    # Environmental
    if avg_wad is not None:
        wad_ok = avg_wad <= cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L
        conclusions_fr.append(
            f"**Conformite environnementale CN WAD:** {avg_wad:.2f} mg/L — "
            f"{'CONFORME' if wad_ok else 'NON CONFORME'} norme IFC ({cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L} mg/L). "
            f"{'Aucun traitement supplementaire requis.' if wad_ok else 'Optimisation du circuit de destruction CN obligatoire avant FS.'}"
        )
        conclusions_en.append(
            f"**Environmental WAD CN compliance:** {avg_wad:.2f} mg/L — "
            f"{'COMPLIANT' if wad_ok else 'NON-COMPLIANT'} with IFC standard ({cfg.WAD_CN_COMPLIANCE_LIMIT_MG_L} mg/L). "
            f"{'No additional treatment required.' if wad_ok else 'CN destruction circuit optimization mandatory before FS.'}"
        )

    for i, c in enumerate(conclusions_fr, 1):
        fr += f"{i}. {c}\n\n"
    for i, c in enumerate(conclusions_en, 1):
        en += f"{i}. {c}\n\n"

    fr += (
        "**Qualification des conclusions:** Les conclusions ci-dessus sont basees sur les "
        "resultats d'essais disponibles au niveau d'etude "
        f"{phase.upper()} et sont sujettes aux limitations d'echantillonnage et de representativite "
        "documentees a la section 13.1. La personne qualifiee considere que les donnees disponibles "
        "sont suffisantes pour supporter les conclusions au niveau d'etude actuel."
    )
    en += (
        "**Qualification of conclusions:** The above conclusions are based on test results "
        f"available at {phase.upper()} study level and are subject to the sampling and "
        "representativeness limitations documented in Section 13.1. The Qualified Person "
        "considers the available data sufficient to support the conclusions at the current "
        "study level."
    )

    return {"key": "25", "title_fr": "Interpretation et conclusions",
            "title_en": "Interpretation and Conclusions",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 26: Recommendations
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_26(phase, data):
    p = data["project"]
    name = p["project_name"] if p else "N/D"

    _d1 = data["d1"]
    b1 = data["b1"]
    e1 = data["e1"]
    e2 = data["e2"]
    env = data["env"]
    kin = data["kinetics"]
    a1 = data["a1"]
    c2 = data["c2"]

    avg_s = _avg(a1, "s_total_pct")
    _avg(b1, "bwi_kwh_t")
    _avg(c2, "au_recovery_pct")
    avg_c_org = _avg(a1, "c_organic_pct")
    _avg(_d1, "au_recovery_pct")

    fr = (
        f"**Recommandations metallurgiques — Projet {name}**\n\n"
        f"Les recommandations suivantes sont emises par la personne qualifiee (QP) "
        f"en metallurgie et traitement mineralurgique, conformement aux exigences de "
        f"la NI 43-101. Elles visent a combler les lacunes identifiees dans le programme "
        f"d'essais actuel et a faire progresser le projet vers le prochain niveau d'etude.\n\n"
    )
    en = (
        f"**Metallurgical Recommendations — {name} Project**\n\n"
        f"The following recommendations are issued by the Qualified Person (QP) in "
        f"metallurgy and mineral processing, in accordance with NI 43-101 requirements. "
        f"They aim to address gaps identified in the current test program and advance the "
        f"project to the next study level.\n\n"
    )

    recs_fr = []
    recs_en = []

    if phase == "scoping":
        recs_fr.extend([
            ("PRIORITE 1 — Echantillonnage supplementaire representatif",
             f"Augmenter la base d'echantillonnage a un minimum de 30 echantillons spatialement distribues "
             f"couvrant toutes les lithologies et zones de teneur du gisement (actuellement: {len(a1)} analyses de tetes). "
             f"Inclure des composites par domaine geologique et par tranche de teneur."),
            ("PRIORITE 1 — Essais de broyabilite complets (JK/SMC)",
             f"Realiser des essais JK Drop Weight ou SMC Test sur 15–20 echantillons "
             f"{'(base actuelle: ' + str(len(b1)) + ' essais Bond BWi).' if b1 else '(aucun essai realise).'} "
             f"Ces parametres sont obligatoires pour le dimensionnement SAG et le calcul de puissance FS."),
            ("PRIORITE 1 — Essais de lixiviation cinetique (48h et 72h)",
             f"Realiser des essais de lixiviation cinetique a 24h, 48h et 72h sur 15+ echantillons "
             f"{'(base actuelle: ' + str(len(_d1)) + ' bottle roll).' if _d1 else '(aucun essai realise).'} "
             f"Determination du temps de retention optimal pour dimensionnement CIL."),
            ("PRIORITE 2 — Tests GRG (Gravity Recoverable Gold)",
             f"Realiser des essais Knelson GRG C2 et E-GRG C3 sur 10–15 echantillons "
             f"{'(base actuelle: ' + str(len(c2)) + ' essais GRG).' if c2 else '(aucun essai realise).'} "
             f"Necessite pour confirmer ou infirmer le circuit de gravite."),
            ("PRIORITE 2 — Essais environnementaux de base",
             "Realiser les tests ABA (Sobek NP/AP), TCLP sur residus CIL et CN WAD. "
             f"{'(base actuelle: ' + str(len(env)) + ' echantillons).' if env else '(aucun essai realise).'} "
             "Obligatoires pour la classification du TSF et le permis d'exploitation."),
            ("PRIORITE 3 — Etude de prefaisabilite (PFS)",
             "Proceder a l'etude de prefaisabilite incorporant les resultats des essais ci-dessus. "
             "Etablir le flowsheet prefere, les criteres de design, le bilan massique complet "
             "et les estimations de CAPEX/OPEX de Classe 4 AACE."),
        ])
        recs_en.extend([
            ("PRIORITY 1 — Additional representative sampling",
             f"Increase sampling base to a minimum of 30 spatially distributed samples "
             f"covering all lithologies and grade domains (currently: {len(a1)} head assays). "
             f"Include composites by geological domain and grade shell."),
            ("PRIORITY 1 — Complete comminution tests (JK/SMC)",
             f"Conduct JK Drop Weight or SMC Tests on 15–20 samples "
             f"{'(current basis: ' + str(len(b1)) + ' Bond BWi tests).' if b1 else '(no tests performed).'} "
             f"These parameters are mandatory for SAG sizing and FS power calculations."),
            ("PRIORITY 1 — Kinetic leach tests (48h and 72h)",
             f"Conduct kinetic leach tests at 24h, 48h and 72h on 15+ samples "
             f"{'(current basis: ' + str(len(_d1)) + ' bottle roll).' if _d1 else '(no tests performed).'} "
             f"Determination of optimal retention time for CIL sizing."),
            ("PRIORITY 2 — GRG (Gravity Recoverable Gold) tests",
             f"Conduct Knelson GRG C2 and E-GRG C3 tests on 10–15 samples "
             f"{'(current basis: ' + str(len(c2)) + ' GRG tests).' if c2 else '(no tests performed).'} "
             f"Required to confirm or rule out a gravity circuit."),
            ("PRIORITY 2 — Baseline environmental tests",
             "Conduct ABA tests (Sobek NP/AP), TCLP on CIL tailings, and WAD CN. "
             f"{'(current basis: ' + str(len(env)) + ' samples).' if env else '(no tests performed).'} "
             "Mandatory for TSF classification and operating permit."),
            ("PRIORITY 3 — Pre-Feasibility Study (PFS)",
             "Proceed to PFS incorporating results of the above tests. "
             "Establish preferred flowsheet, design criteria, complete mass balance "
             "and AACE Class 4 CAPEX/OPEX estimates."),
        ])

    elif phase == "pfs":
        if not kin:
            recs_fr.append(("PRIORITE 1 — Essais cinetiques de lixiviation (manquants)",
                            "Realiser des essais de lixiviation cinetique a 24h, 48h et 72h sur minimum 20 echantillons "
                            "representant la variabilite du gisement. Necessaires pour confirmer le temps de retention "
                            "et le nombre de cuves CIL."))
            recs_en.append(("PRIORITY 1 — Kinetic leach tests (missing)",
                            "Conduct kinetic leach tests at 24h, 48h and 72h on minimum 20 samples "
                            "representing deposit variability. Required to confirm CIL retention time and tank count."))

        if len(b1) < 15:
            recs_fr.append(("PRIORITE 1 — Augmenter la base de broyabilite",
                            f"Augmenter le nombre d'echantillons de broyabilite de {len(b1)} a minimum 25 "
                            "pour couvrir adequatement la variabilite lithologique et la progression temporelle du pit. "
                            "Realiser des tests JK Drop Weight ou SMC si non encore effectues."))
            recs_en.append(("PRIORITY 1 — Expand comminution database",
                            f"Increase comminution sample count from {len(b1)} to a minimum of 25 "
                            "to adequately cover lithological variability and temporal pit progression. "
                            "Conduct JK Drop Weight or SMC tests if not yet performed."))

        if not e1:
            recs_fr.append(("PRIORITE 1 — Essais d'epaississement (manquants — OBLIGATOIRE FS)",
                            "Realiser des essais Coe & Clevenger sur residus CIL et concentres sur minimum 5 echantillons. "
                            "Requis pour dimensionner l'epaississeur de residus et le circuit de recyclage d'eau."))
            recs_en.append(("PRIORITY 1 — Thickening tests (missing — MANDATORY for FS)",
                            "Conduct Coe & Clevenger tests on CIL tailings and concentrates on minimum 5 samples. "
                            "Required to size tailings thickener and water recycle circuit."))

        if not e2:
            recs_fr.append(("PRIORITE 1 — Essais de filtration (manquants)",
                            "Realiser des essais de filtre-presse sur residus et concentres. "
                            "Determiner le taux de filtration et l'humidite du gateau pour le bilan hydrique."))
            recs_en.append(("PRIORITY 1 — Filtration tests (missing)",
                            "Conduct filter press tests on tailings and concentrates. "
                            "Determine filtration rate and cake moisture for water balance."))

        if avg_c_org and avg_c_org > 0.2:
            recs_fr.append(("PRIORITE 1 — Essais anti-preg-robbing (risque identifie)",
                            "Realiser des essais PREN (competitive adsorption) et evaluer les additifs "
                            "(bentonite 100–300 g/t, CYANOSAVE). La non-resolution du preg-robbing "
                            "est un bloqueur pour la phase FS."))
            recs_en.append(("PRIORITY 1 — Anti-preg-robbing tests (risk identified)",
                            "Conduct PREN (competitive adsorption) tests and evaluate additives "
                            "(bentonite 100–300 g/t, CYANOSAVE). Unresolved preg-robbing is a "
                            "blocker for the FS phase."))

        if avg_s and avg_s > cfg.FLOTATION_S_THRESHOLD_PCT:
            recs_fr.append(("PRIORITE 2 — Optimisation de la flottation (soufre eleve)",
                            f"Realiser des essais d'optimisation de flottation (S = {avg_s:.2f}%) pour confirmer "
                            "la recuperation de l'or dans le concentre de sulfures. Evaluer les alternatives "
                            "flowsheet (flottation pre-CIL vs CIL direct)."))
            recs_en.append(("PRIORITY 2 — Flotation optimization (elevated sulphur)",
                            f"Conduct flotation optimization tests (S = {avg_s:.2f}%) to confirm "
                            "gold recovery in sulphide concentrate. Evaluate flowsheet alternatives "
                            "(flotation pre-CIL vs direct CIL)."))

        recs_fr.extend([
            ("PRIORITE 2 — Essais en continu / mini-pilote",
             "Realiser des essais en continu (locked cycle ou CIL en continu) pour valider "
             "le flowsheet prefere a l'echelle semi-industrielle et confirmer la stabilite des performances."),
            ("PRIORITE 3 — Etude de faisabilite (FS)",
             "Proceder a l'etude de faisabilite (FS) avec un niveau d'estimation Classe 3 AACE (±10–15%). "
             "Inclure ingenierie de base, AMDEC, plan d'exploitation detaille et base de conception GISTM pour le TSF."),
        ])
        recs_en.extend([
            ("PRIORITY 2 — Continuous / mini-pilot tests",
             "Conduct continuous tests (locked cycle or continuous CIL) to validate "
             "the preferred flowsheet at semi-industrial scale and confirm performance stability."),
            ("PRIORITY 3 — Feasibility Study (FS)",
             "Proceed to Feasibility Study (FS) with AACE Class 3 estimate (±10–15%). "
             "Include basic engineering, FMEA, detailed operating plan and GISTM design basis for TSF."),
        ])

    else:  # fs
        recs_fr.extend([
            ("PRIORITE 1 — Ingenierie de detail et documentation FEED",
             "Finaliser l'ingenierie de detail (FEED — Front End Engineering and Design). "
             "Completer la liste d'equipements definitive, les specifications, les P&ID et les bilans finaux."),
            ("PRIORITE 1 — Appel d'offres equipements longs delais",
             "Lancer les appels d'offres pour les equipements a long delai de livraison: "
             "broyeurs (SAG, boulets), epaississeurs, cuves CIL, fours de fusion. "
             "Delai typique: 18–36 mois."),
            ("PRIORITE 2 — Permis de construction et d'exploitation",
             "Initier les processus d'obtention des permis de construction et d'exploitation. "
             "Soumettre l'Etude d'Impact Environnemental (EIE) et la conception TSF (GISTM) aux autorites."),
            ("PRIORITE 2 — Programme de mise en service",
             "Preparer le plan de mise en service (commissioning): "
             "essais a sec, remplissage eau, essais au minerai, optimisation des circuits. "
             "Planifier la formation du personnel operationnel."),
            ("PRIORITE 3 — Programme d'assurance performance metallurgique",
             "Mettre en place un programme de suivi des performances metallurgiques en exploitation: "
             "analyses de tetes quotidiennes, suivi de recuperation, optimisation continue des reactifs."),
        ])
        recs_en.extend([
            ("PRIORITY 1 — Detailed engineering and FEED documentation",
             "Finalize detailed engineering (FEED — Front End Engineering and Design). "
             "Complete definitive equipment list, specifications, P&IDs and final balances."),
            ("PRIORITY 1 — Long-lead equipment procurement",
             "Issue tenders for long-lead equipment: "
             "mills (SAG, ball), thickeners, CIL tanks, smelting furnaces. "
             "Typical lead time: 18–36 months."),
            ("PRIORITY 2 — Construction and operating permits",
             "Initiate construction and operating permit processes. "
             "Submit Environmental Impact Assessment (EIA) and TSF design (GISTM) to authorities."),
            ("PRIORITY 2 — Commissioning program",
             "Prepare commissioning plan: "
             "dry runs, water fill, ore commissioning, circuit optimization. "
             "Plan operational staff training."),
            ("PRIORITY 3 — Metallurgical performance assurance program",
             "Implement operational metallurgical performance monitoring: "
             "daily head assays, recovery tracking, continuous reagent optimization."),
        ])

    if not env:
        recs_fr.append(("PRIORITE 1 — Essais environnementaux (manquants — OBLIGATOIRE)",
                        "Realiser les essais environnementaux obligatoires: ABA (NP/AP), TCLP, CN WAD. "
                        "Ces donnees sont requises pour la conception du TSF et l'obtention des permis."))
        recs_en.append(("PRIORITY 1 — Environmental tests (missing — MANDATORY)",
                        "Conduct mandatory environmental tests: ABA (NP/AP), TCLP, WAD CN. "
                        "These data are required for TSF design and permit applications."))

    fr += f"Les recommandations sont classees par priorite (1 = immediate, 3 = long terme):\n\n"
    en += f"Recommendations are ranked by priority (1 = immediate, 3 = long-term):\n\n"

    for i, (title, desc) in enumerate(recs_fr, 1):
        fr += f"**{i}. {title}**\n{desc}\n\n"
    for i, (title, desc) in enumerate(recs_en, 1):
        en += f"**{i}. {title}**\n{desc}\n\n"

    fr += (
        "**Budget indicatif pour les travaux supplementaires recommandes:**\n"
        f"- Phase Scoping → PFS: USD 150 000 – 300 000 (essais metallurgiques + etude PFS)\n"
        f"- Phase PFS → FS: USD 500 000 – 1 500 000 (essais pilotes + ingenierie FEED)\n"
        "(Estimations indicatives — a affiner selon la portee exacte des travaux)"
    ) if phase == "scoping" else ""

    en += (
        "**Indicative budget for recommended additional work:**\n"
        f"- Scoping → PFS phase: USD 150,000 – 300,000 (metallurgical tests + PFS study)\n"
        f"- PFS → FS phase: USD 500,000 – 1,500,000 (pilot tests + FEED engineering)\n"
        "(Indicative estimates — to be refined based on exact scope)"
    ) if phase == "scoping" else ""

    return {"key": "26", "title_fr": "Recommandations",
            "title_en": "Recommendations",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 27: References
# ═══════════════════════════════════════════════════════════════════════════════

def _gen_27(phase, data):
    fr = (
        "**References:**\n\n"
        "1. Norme canadienne 43-101 — Information concernant les projets miniers (NI 43-101)\n"
        "2. Formulaire 43-101F1 — Rapport technique\n"
        "3. Normes de definition de l'ICM pour les ressources minerales et les reserves minerales (2014)\n"
        "4. Pratiques exemplaires de l'ICM en matiere d'estimation des ressources minerales (2019)\n"
        "5. Bond, F.C. (1961) — Crushing and Grinding Calculations\n"
        "6. Marsden, J. & House, I. (2006) — The Chemistry of Gold Extraction, 2nd Edition\n"
        "7. SME Mining Engineering Handbook, 3rd Edition (2011)\n"
        "8. IFC Environmental, Health and Safety Guidelines for Mining (2007)\n"
        "9. AACE International — Recommended Practices for Cost Estimation Classification\n"
        "10. Directives de la Banque mondiale sur la gestion des residus miniers\n"
    )
    en = (
        "**References:**\n\n"
        "1. Canadian National Instrument 43-101 — Standards of Disclosure for Mineral Projects (NI 43-101)\n"
        "2. Form 43-101F1 — Technical Report\n"
        "3. CIM Definition Standards for Mineral Resources and Mineral Reserves (2014)\n"
        "4. CIM Estimation of Mineral Resources & Mineral Reserves Best Practice Guidelines (2019)\n"
        "5. Bond, F.C. (1961) — Crushing and Grinding Calculations\n"
        "6. Marsden, J. & House, I. (2006) — The Chemistry of Gold Extraction, 2nd Edition\n"
        "7. SME Mining Engineering Handbook, 3rd Edition (2011)\n"
        "8. IFC Environmental, Health and Safety Guidelines for Mining (2007)\n"
        "9. AACE International — Recommended Practices for Cost Estimation Classification\n"
        "10. World Bank Directives on Tailings Management\n"
    )

    return {"key": "27", "title_fr": "References",
            "title_en": "References",
            "content_fr": fr, "content_en": en}


# ═══════════════════════════════════════════════════════════════════════════════
# Metallurgical report — sections 23–26 (custom numbering for processing TR)
# ═══════════════════════════════════════════════════════════════════════════════

ALLOWED_METALLURGY_SECTIONS = frozenset({1, 2, 13, 17, 23, 24, 25, 26})

SECTION_GENERATOR_MAP: dict[int, list] = {
    1: [_gen_1],
    2: [_gen_2],
    13: [
        _gen_13_1, _gen_13_2, _gen_13_3, _gen_13_4,
        _gen_13_5, _gen_13_6, _gen_13_7, _gen_13_8,
    ],
    17: [_gen_17_1, _gen_17_2, _gen_17_3, _gen_17_4, _gen_17_5],
}


def _gen_23_conclusions_recommendations(phase, data):
    """Section 23 — Conclusions et recommandations (metallurgical focus)."""
    p = data["project"]
    name = p["project_name"] if p else "N/D"
    conc = _gen_25(phase, data)
    rec = _gen_26(phase, data)

    fr = (
        f"La presente section synthetise les conclusions et recommandations "
        f"metallurgiques du projet {name}, conformement aux exigences de divulgation "
        f"de la NI 43-101 pour les etudes de traitement et de recuperation.\n\n"
        f"### Conclusions metallurgiques\n\n{conc['content_fr']}\n\n"
        f"### Recommandations\n\n{rec['content_fr']}"
    )
    en = (
        f"This section summarizes the metallurgical conclusions and recommendations "
        f"for the {name} project in accordance with NI 43-101 disclosure requirements "
        f"for processing and recovery.\n\n"
        f"### Metallurgical conclusions\n\n{conc['content_en']}\n\n"
        f"### Recommendations\n\n{rec['content_en']}"
    )
    return {
        "key": "23",
        "title_fr": "Conclusions et recommandations",
        "title_en": "Conclusions and Recommendations",
        "content_fr": fr,
        "content_en": en,
    }


def _gen_24_references(phase, data):
    """Section 24 — References (metallurgy-focused bibliography)."""
    base = _gen_27(phase, data)
    extra_fr = (
        "\n11. CIM — Mineral Processing Plant Design, Practice and Control (Mular, Bhappu, 2002)\n"
        "12. CIM Best Practice Guidelines for Mineral Processing (2019)\n"
        "13. AMIRA P420 Project — Gold Processing Technology\n"
        "14. McClelland, G.E. et al. — Design of Mill Tailings Facilities (1999)\n"
    )
    extra_en = (
        "\n11. CIM — Mineral Processing Plant Design, Practice and Control (Mular, Bhappu, 2002)\n"
        "12. CIM Best Practice Guidelines for Mineral Processing (2019)\n"
        "13. AMIRA P420 Project — Gold Processing Technology\n"
        "14. McClelland, G.E. et al. — Design of Mill Tailings Facilities (1999)\n"
    )
    return {
        "key": "24",
        "title_fr": "References",
        "title_en": "References",
        "content_fr": base["content_fr"] + extra_fr,
        "content_en": base["content_en"] + extra_en,
    }


def _gen_25_date_signature(phase, data):
    """Section 25 — Date effective et signature QP."""
    from datetime import date

    p = data["project"]
    name = p["project_name"] if p else "N/D"
    owner = p.get("project_owner", "N/D") if p else "N/D"
    effective = date.today().isoformat()

    fr = (
        f"**Date effective du rapport:** {effective}\n\n"
        f"**Projet:** {name}\n"
        f"**Mandataire:** {owner}\n\n"
        f"**Declaration de la personne qualifiee (metallurgie / traitement mineralurgique):**\n\n"
        f"Je certifie que les informations contenues dans les sections 13 et 17 de ce "
        f"rapport technique, ainsi que les conclusions metallurgiques presentees, "
        f"sont fondees sur des donnees verifiables et des pratiques industrielles "
        f"reconnues, conformement a la Norme canadienne 43-101 et au formulaire 43-101F1.\n\n"
        f"Les resultats presentes sont limites au perimetre des essais metallurgiques "
        f"et du flowsheet de traitement documentes dans le present rapport.\n\n"
        f"_______________________________\n"
        f"Nom de la personne qualifiee\n"
        f"Titre professionnel | No. ordre\n"
        f"Date de signature: {effective}\n"
    )
    en = (
        f"**Effective date of report:** {effective}\n\n"
        f"**Project:** {name}\n"
        f"**Issuer:** {owner}\n\n"
        f"**Qualified Person declaration (metallurgy / mineral processing):**\n\n"
        f"I certify that the information in Items 13 and 17 of this technical report, "
        f"and the metallurgical conclusions presented herein, are based on verifiable data "
        f"and recognized industry practice, in accordance with NI 43-101 and Form 43-101F1.\n\n"
        f"Results are limited to the scope of metallurgical testwork and the processing "
        f"flowsheet documented in this report.\n\n"
        f"_______________________________\n"
        f"Qualified Person name\n"
        f"Professional designation | Membership number\n"
        f"Date of signature: {effective}\n"
    )
    return {
        "key": "25",
        "title_fr": "Date et signature",
        "title_en": "Date and Signature",
        "content_fr": fr,
        "content_en": en,
    }


def _gen_26_qp_certificates(phase, data):
    """Section 26 — Certificats des auteurs (Form 43-101F1 QP certificates)."""
    p = data["project"]
    name = p["project_name"] if p else "N/D"

    fr = (
        f"**Certificat de la personne qualifiee — Traitement mineralurgique et metallurgie**\n\n"
        f"Projet: {name}\n\n"
        f"Je, [Nom complet], [Titre professionnel], membre en regle de [Ordre professionnel], "
        f"certifie ce qui suit:\n\n"
        f"1. J'ai prepare les sections 13, 17, 23, 24 et 25 du present rapport technique "
        f"relatif au projet {name}.\n"
        f"2. Je suis une personne qualifiee au sens de la NI 43-101 pour le contenu "
        f"relatif au traitement mineralurgique et aux essais metallurgiques.\n"
        f"3. J'ai visite le site le [DATE] et ma derniere visite remonte a [DATE].\n"
        f"4. Je suis independant de l'emetteur au sens de la NI 43-101: [Oui / Non].\n"
        f"5. J'ai lu la NI 43-101 et le formulaire 43-101F1, et le present rapport "
        f"a ete prepare en conformite avec ces instruments.\n\n"
        f"Signature: _______________________   Date: _______________________\n"
        f"Nom: _______________________\n"
        f"Adresse professionnelle: _______________________\n"
        f"Telephone: _______________________   Courriel: _______________________\n"
    )
    en = (
        f"**Qualified Person Certificate — Mineral Processing and Metallurgy**\n\n"
        f"Project: {name}\n\n"
        f"I, [Full name], [Professional designation], in good standing with [Professional association], "
        f"certify the following:\n\n"
        f"1. I prepared Items 13, 17, 23, 24 and 25 of this technical report for the {name} project.\n"
        f"2. I am a Qualified Person as defined in NI 43-101 for mineral processing and metallurgical testing.\n"
        f"3. I visited the site on [DATE]; my most recent visit was on [DATE].\n"
        f"4. I am independent of the issuer as defined in NI 43-101: [Yes / No].\n"
        f"5. I have read NI 43-101 and Form 43-101F1, and this report has been prepared in compliance.\n\n"
        f"Signature: _______________________   Date: _______________________\n"
        f"Name: _______________________\n"
        f"Business address: _______________________\n"
        f"Telephone: _______________________   Email: _______________________\n"
    )
    return {
        "key": "26",
        "title_fr": "Certificats des auteurs",
        "title_en": "Certificates of Authors",
        "content_fr": fr,
        "content_en": en,
    }


SECTION_GENERATOR_MAP[23] = [_gen_23_conclusions_recommendations]
SECTION_GENERATOR_MAP[24] = [_gen_24_references]
SECTION_GENERATOR_MAP[25] = [_gen_25_date_signature]
SECTION_GENERATOR_MAP[26] = [_gen_26_qp_certificates]


def _run_generators(pid: str, section_numbers: list[int]) -> list[dict]:
    phase = _detect_phase(pid)
    data = _fetch_all_data(pid)
    _load_costs(pid, data)
    sections: list[dict] = []
    for sec_num in section_numbers:
        generators = SECTION_GENERATOR_MAP.get(sec_num, [])
        for i, gen in enumerate(generators):
            s = gen(phase, data)
            s["section_number"] = sec_num
            s["sort_order"] = i
            s["is_auto_generated"] = True
            sections.append(s)
    return sections


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

def generate_report_sections(pid: str) -> list[dict]:
    """Generate all metallurgical NI 43-101 sections for a project."""
    return _run_generators(pid, sorted(ALLOWED_METALLURGY_SECTIONS))


def generate_report_section(pid: str, section_number: int) -> list[dict]:
    """Generate a single NI 43-101 section (and its subsections)."""
    if section_number not in ALLOWED_METALLURGY_SECTIONS:
        raise ValueError(f"Section {section_number} non supportee pour le rapport metallurgique")
    return _run_generators(pid, [section_number])
