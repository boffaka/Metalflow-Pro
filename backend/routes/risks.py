"""
MPDPMS -- Risk Register routes (EPCM structure).
Full risk CRUD with mandatory fields, P*I scoring, gate-blocker management.
"""
from __future__ import annotations
import logging
import psycopg2

from fastapi import APIRouter, HTTPException, Depends

logger = logging.getLogger(__name__)

try:
    from ..auth import project_user, require_project_role
    from ..db import qone, qall, execute, build_update_sets
    from ..models import RiskIn, RiskPatch
    from ..audit import record_event
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user, require_project_role
    from db import qone, qall, execute, build_update_sets
    from models import RiskIn, RiskPatch
    from audit import record_event


router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["risks"])

ALLOWED_FIELDS_RISK = {
    "risk_number", "description", "cause", "consequence",
    "probability", "impact", "mitigation", "preventive_actions",
    "corrective_actions", "alert_indicators", "owner", "status",
    "category", "phase", "due_date", "review_date", "stage_id",
    "is_gate_blocker",
}

# Valid categories for EPCM risk register
RISK_CATEGORIES = (
    "Technical", "Metallurgical", "HSE", "Financial", "Schedule",
    "Permitting", "Environmental", "Geotechnical",
    "Social", "Process Engineering", "Other",
)


def _next_risk_number(pid: str, category: str) -> str:
    """Generate next risk number like R-TECH-03 based on category prefix.

    Uses MAX on the numeric suffix so deletions never produce duplicate numbers.
    """
    prefix_map = {
        "Technical": "TECH", "Metallurgical": "MET", "HSE": "HSE",
        "Financial": "FIN", "Schedule": "SCH", "Permitting": "PER",
        "Environmental": "ENV", "Geotechnical": "GEO", "Social": "SOC",
        "Process Engineering": "PE", "Other": "OTH",
    }
    prefix = prefix_map.get(category, "OTH")
    pattern = f"R-{prefix}-%"
    row = qone(
        "SELECT COALESCE(MAX("
        "  CASE WHEN risk_number ~ '^R-[A-Z]+-[0-9]+$'"
        "  THEN CAST(SPLIT_PART(risk_number, '-', 3) AS INTEGER)"
        "  ELSE 0 END"
        "), 0) AS max_num"
        " FROM risks WHERE project_id = %s AND risk_number LIKE %s",
        (pid, pattern),
    )
    num = (row["max_num"] if row else 0) + 1
    return f"R-{prefix}-{num:02d}"


def _signal_pipeline(pid: str, module: str, status: str, user_id: str = None) -> None:
    """Signal pipeline status change — never blocks the route on failure."""
    try:
        from .pipeline import set_status, mark_stale_cascade
    except ImportError:
        from pipeline import set_status, mark_stale_cascade
    try:
        set_status(pid, module, status, user_id=user_id, triggered_by="auto_generate")
        if status == "complete":
            mark_stale_cascade(pid, module, user_id=user_id)
    except Exception:  # intentional: ignore optional lookup failure
        pass


def _safe_qall(sql: str, params: tuple) -> list:
    try:
        return qall(sql, params) or []
    except Exception:
        return []


def _nums(rows: list, field: str) -> list[float]:
    out = []
    for r in rows:
        v = r.get(field)
        if v is None:
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _avg(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def _make_risk(pid: str, category: str, description: str, **kwargs) -> dict:
    base = {
        "risk_number": _next_risk_number(pid, category),
        "description": description,
        "cause": kwargs.pop("cause", ""),
        "consequence": kwargs.pop("consequence", ""),
        "probability": kwargs.pop("probability", 3),
        "impact": kwargs.pop("impact", 3),
        "mitigation": kwargs.pop("mitigation", ""),
        "preventive_actions": kwargs.pop("preventive_actions", ""),
        "corrective_actions": kwargs.pop("corrective_actions", ""),
        "alert_indicators": kwargs.pop("alert_indicators", ""),
        "owner": kwargs.pop("owner", "Project Manager"),
        "category": category,
        "phase": kwargs.pop("phase", "Engineering"),
        "is_gate_blocker": kwargs.pop("is_gate_blocker", False),
        "is_auto_generated": True,
    }
    base.update(kwargs)
    return base


def _insert_generated_risks(pid: str, generated_risks: list[dict]) -> list[dict]:
    inserted = []
    for risk in generated_risks:
        row = execute(
            "INSERT INTO risks "
            "(project_id, risk_number, description, cause, consequence, "
            "probability, impact, mitigation, preventive_actions, corrective_actions, "
            "alert_indicators, owner, category, phase, is_gate_blocker, is_auto_generated) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING *",
            (
                pid, risk["risk_number"], risk["description"], risk["cause"], risk["consequence"],
                risk["probability"], risk["impact"], risk["mitigation"],
                risk["preventive_actions"], risk["corrective_actions"],
                risk["alert_indicators"], risk["owner"], risk["category"], risk["phase"],
                risk["is_gate_blocker"], risk["is_auto_generated"],
            ),
        )
        row["criticality"] = row["probability"] * row["impact"]
        inserted.append(row)
    return inserted


@router.post("/risks/auto-generate")
def auto_generate_risks(pid: str, user=Depends(project_user)):
    """Auto-generate risk register based on project data from previous modules."""
    _signal_pipeline(pid, "risks", "generating", user_id=str(user["id"]))
    try:
        result = _do_generate_risks(pid, user)
        _signal_pipeline(pid, "risks", "complete", user_id=str(user["id"]))
        return result
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _do_generate_risks(pid: str, user: dict):
    """Generate risk entries based on project data from previous modules."""
    p = qone("SELECT * FROM projects WHERE id=%s", (pid,))
    if not p:
        raise HTTPException(404, "Projet introuvable")

    execute(
        "DELETE FROM risks WHERE project_id=%s AND is_auto_generated=TRUE",
        (pid,),
    )

    generated_risks: list[dict] = []
    tph = float(p.get("target_tph") or 100)

    def add(category: str, description: str, **kwargs) -> None:
        generated_risks.append(_make_risk(pid, category, description, **kwargs))

    # ── Baseline EPCM (always) ───────────────────────────────────────────────
    add(
        "Permitting",
        "Délais d'obtention des permis environnementaux et d'exploitation",
        cause="Processus administratif complexe et potentiellement long",
        consequence="Impact sur le calendrier de démarrage du projet",
        probability=3,
        impact=5,
        mitigation="Engagement précoce avec les autorités; préparation complète du dossier",
        preventive_actions="Identification des permis requis; consultation préalable",
        corrective_actions="Recours ou ajustement du calendrier",
        alert_indicators="Délai > 18 mois pour permis principal",
        owner="Project Manager",
        phase="Permitting",
        is_gate_blocker=True,
    )
    add(
        "Financial",
        "Volatilité du prix de l'or et sensibilité de la VAN",
        cause="Exposition aux marchés des métaux précieux",
        consequence="Écart matériel sur les indicateurs économiques et le financement",
        probability=4,
        impact=4,
        mitigation="Analyse de sensibilité; couverture partielle; scénarios prix bas/haut",
        preventive_actions="Mise à jour trimestrielle du modèle économique",
        corrective_actions="Révision du plan de mine et des cut-off",
        alert_indicators="Prix spot < -15 % vs hypothèse de faisabilité",
        owner="Cost Engineer",
        phase="Feasibility",
    )

    # ── Circuit / flowsheet ───────────────────────────────────────────────────
    active_tpl = qone(
        "SELECT name FROM circuit_templates "
        "WHERE project_id=%s AND is_active=TRUE ORDER BY updated_at DESC NULLS LAST LIMIT 1",
        (pid,),
    )
    if not active_tpl:
        add(
            "Technical",
            "Aucun circuit de procédé actif — modèle métallurgique incomplet",
            cause="Circuit non sélectionné ou non activé",
            consequence="Incohérence entre LIMS, bilan massique, équipements et simulation",
            probability=4,
            impact=4,
            mitigation="Sélectionner et activer un template de circuit adapté au gisement",
            owner="Process Engineer",
            is_gate_blocker=True,
        )
    else:
        tpl_name = (active_tpl.get("name") or "").lower()
        refractory = any(k in tpl_name for k in ("biox", "pox", "roast", "refractory", "pressure", "autoclave"))
        if refractory:
            add(
                "Metallurgical",
                f"Minerai réfractaire — circuit {active_tpl.get('name', 'réfractaire')}",
                cause="Minéralisation nécessitant oxydation ou traitement à haute température",
                consequence="CAPEX/OPEX élevés; complexité opérationnelle et risques de performance",
                probability=3,
                impact=5,
                mitigation="Programme de test métallurgique étendu; pilote à l'échelle",
                owner="Metallurgist",
                is_gate_blocker=True,
            )

    # ── Design criteria ─────────────────────────────────────────────────────
    dc_rows = _safe_qall("SELECT item, design, unit FROM design_criteria WHERE project_id=%s", (pid,))
    if dc_rows:
        if tph > 500:
            add(
                "Technical",
                "Capacité de traitement élevée (>500 TPH) — équipements majeurs",
                cause=f"Débit cible {tph:.0f} TPH",
                consequence="Risque de sous-capacité ou délais de livraison prolongés",
                probability=3,
                impact=4,
                mitigation="Confirmer délais fournisseurs; commande anticipée des items long-lead",
                owner="Process Engineer",
            )
    else:
        add(
            "Technical",
            "Critères de conception non générés — hypothèses de design non verrouillées",
            cause="Module PDC / critères de conception vide",
            consequence="Dimensionnement et coûts basés sur des hypothèses par défaut",
            probability=3,
            impact=3,
            mitigation="Lancer l'auto-génération des critères de conception depuis LIMS",
            owner="Process Engineer",
        )

    # ── LIMS — comminution (B1) ─────────────────────────────────────────────
    b1_rows = _safe_qall("SELECT bwi_kwh_t FROM lims_b1 WHERE project_id=%s", (pid,))
    b1_vals = _nums(b1_rows, "bwi_kwh_t")
    if len(b1_vals) >= 2:
        bwi_lo, bwi_hi = min(b1_vals), max(b1_vals)
        spread = (bwi_hi - bwi_lo) / max(bwi_lo, 1.0)
        if spread > 0.12:
            add(
                "Metallurgical",
                f"Variabilité BWi ({bwi_lo:.1f}–{bwi_hi:.1f} kWh/t) — dimensionnement broyage",
                cause="Hétérogénéité lithologique ou géométrique",
                consequence="Sur/sous-dimensionnement de l'installation de comminution; OPEX énergie",
                probability=3,
                impact=4,
                mitigation="Inclure enveloppe BWi dans le dimensionnement; tests de variabilité",
                owner="Metallurgist",
            )

    # ── LIMS — géochimie / minéralogie (A1, A2, A3) ─────────────────────────
    a1_rows = _safe_qall(
        "SELECT c_organic_pct, s_sulfide_pct, s_total_pct, as_ppm, sb_ppm, cu_pct "
        "FROM lims_a1 WHERE project_id=%s",
        (pid,),
    )
    if a1_rows:
        c_vals = _nums(a1_rows, "c_organic_pct")
        s_vals = _nums(a1_rows, "s_sulfide_pct")
        cu_vals = _nums(a1_rows, "cu_pct")
        as_vals = _nums(a1_rows, "as_ppm")
        avg_c = _avg(c_vals)
        avg_s = _avg(s_vals)
        avg_cu = _avg(cu_vals)
        avg_as = _avg(as_vals)

        if avg_c is not None and avg_c > 0.3:
            add(
                "HSE",
                f"Carbone organique élevé ({avg_c:.2f}%) — preg-robbing / consommation NaCN",
                cause="Minéralisation carbonatée",
                consequence="Baisse de récupération; coûts réactifs; risques résidus",
                probability=4,
                impact=4,
                mitigation="Pré-traitement; charbon actif; tests CIL vs CIP",
                owner="Metallurgist",
            )
        if avg_s is not None and avg_s > 2.0:
            add(
                "Environmental",
                f"Sulfures élevés ({avg_s:.1f}%) — risque DMA / résidus réactifs",
                cause="Minéralisation sulfureuse",
                consequence="Gestion TSF complexe; coûts neutralisation",
                probability=3,
                impact=5,
                mitigation="Tests cinétiques DMA; plan de gestion des résidus",
                owner="Environmental Engineer",
                is_gate_blocker=True,
            )
        if avg_cu is not None and avg_cu > 0.10:
            add(
                "Metallurgical",
                f"Cuivre soluble ({avg_cu:.2f}%) — interférence cyanuration CIL/CIP",
                cause="Minéraux de cuivre consommateurs de cyanure",
                consequence="OPEX NaCN élevé; instabilité du circuit",
                probability=3,
                impact=3,
                mitigation="Pré-flottation Cu; SART/AVR si applicable; budget réactif majoré",
                owner="Metallurgist",
            )
        if avg_as is not None and avg_as > 500:
            add(
                "HSE",
                f"Arsenic élevé ({avg_as:.0f} ppm) — conformité résidus et effluents",
                cause="Minéralisation arsenifère",
                consequence="Contraintes environnementales; coûts de traitement",
                probability=3,
                impact=4,
                mitigation="Stabilisation résidus; circuit de traitement des effluents",
                owner="Environmental Engineer",
            )
    else:
        sample_count = qone(
            "SELECT COUNT(*)::int AS n FROM samples WHERE project_id=%s", (pid,),
        )
        n_samples = (sample_count or {}).get("n") or 0
        if n_samples == 0:
            add(
                "Metallurgical",
                "Données LIMS absentes — incertitude sur la réponse métallurgique",
                cause="Aucun échantillon / essai importé",
                consequence="Hypothèses de récupération et de procédé non validées",
                probability=4,
                impact=5,
                mitigation="Importer les campagnes LIMS et compléter les essais clés",
                owner="Metallurgist",
                is_gate_blocker=True,
            )

    # ── LIMS — gravité (C2) ─────────────────────────────────────────────────
    c2_rows = _safe_qall("SELECT grg_value, au_recovery_pct FROM lims_c2 WHERE project_id=%s", (pid,))
    grg_vals = _nums(c2_rows, "grg_value")
    if len(grg_vals) >= 2:
        g_lo, g_hi = min(grg_vals), max(grg_vals)
        if (g_hi - g_lo) > 8:
            add(
                "Metallurgical",
                f"Variabilité GRG ({g_lo:.0f}–{g_hi:.0f}%) — circuit gravimétrique",
                cause="Distribution inhomogène de l'or libre",
                consequence="Performance gravité instable; impact sur récupération globale",
                probability=2,
                impact=3,
                mitigation="Dimensionner sur cas défavorable; suivi GRG en production",
                owner="Metallurgist",
            )

    # ── LIMS — lixiviation (D1) ───────────────────────────────────────────────
    d1_rows = _safe_qall("SELECT au_recovery_pct FROM lims_d1 WHERE project_id=%s", (pid,))
    rec_vals = _nums(d1_rows, "au_recovery_pct")
    if len(rec_vals) >= 2:
        r_lo, r_hi = min(rec_vals), max(rec_vals)
        if (r_hi - r_lo) > 5:
            add(
                "Metallurgical",
                f"Sensibilité récupération lixiviation ({r_lo:.1f}–{r_hi:.1f}%)",
                cause="Variabilité minéralogique ou paramètres de broyage",
                consequence="Incertitude sur la récupération réservée et le plan de mine",
                probability=3,
                impact=4,
                mitigation="Matrice de tests de variabilité; optimisation P80",
                owner="Metallurgist",
            )

    # ── LIMS — épaississement (E1) ────────────────────────────────────────────
    e1_rows = _safe_qall(
        "SELECT underflow_density_pct_solids FROM lims_e1 WHERE project_id=%s", (pid,),
    )
    uf_vals = _nums(e1_rows, "underflow_density_pct_solids")
    avg_uf = _avg(uf_vals)
    if avg_uf is not None and avg_uf < 50:
        add(
            "Process Engineering",
            "Densité sous-flux épaississeur basse — risque de viscosité / pompage",
            cause=f"Rheologie défavorable (UF ~{avg_uf:.0f} % solids)",
            consequence="Alimentation CIL perturbée; pertes de résidence",
            probability=2,
            impact=3,
            mitigation="Optimisation floculant; sélection pompes haute viscosité",
            owner="Process Engineer",
        )

    # ── Bilan massique v2 ───────────────────────────────────────────────────
    mb_sections = _safe_qall(
        "SELECT id FROM mass_balance_sections_v2 WHERE project_id=%s LIMIT 1", (pid,),
    )
    if mb_sections:
        try:
            from .massbalance_v2 import _get_mass_balance_impl
        except ImportError:
            from massbalance_v2 import _get_mass_balance_impl
        try:
            mb = _get_mass_balance_impl(pid)
            summary = mb.get("summary") or {}
            rec = float(summary.get("overall_recovery_pct") or 0)
            if rec > 0 and rec < 82:
                add(
                    "Metallurgical",
                    f"Récupération bilan massique faible ({rec:.1f}%)",
                    cause="Circuit ou paramètres LIMS non alignés",
                    consequence="Sous-performance économique du projet",
                    probability=3,
                    impact=5,
                    mitigation="Revoir circuit actif, LIMS et paramètres de simulation",
                    owner="Metallurgist",
                    is_gate_blocker=True,
                )
            elif rec > 96:
                add(
                    "Metallurgical",
                    f"Récupération bilan massique optimiste ({rec:.1f}%) — validation requise",
                    cause="Hypothèses de récupération agressives",
                    consequence="Sur-estimation des réserves récupérables",
                    probability=3,
                    impact=4,
                    mitigation="Campagne de validation métallurgique; verrouillage LIMS",
                    owner="Metallurgist",
                )
        except HTTPException:
            add(
                "Technical",
                "Bilan massique incomplet ou incohérent",
                cause="Sections présentes mais résumé non calculable",
                consequence="Indicateurs dashboard et risques techniques non fiables",
                probability=3,
                impact=3,
                mitigation="Relancer l'auto-génération du bilan massique",
                owner="Process Engineer",
            )
    else:
        add(
            "Technical",
            "Bilan massique non généré",
            cause="Module bilan massique vide",
            consequence="Pas de traçabilité métallurgique procédé par procédé",
            probability=3,
            impact=4,
            mitigation="Générer le bilan depuis le circuit actif",
            owner="Process Engineer",
        )

    # ── Équipements v2 (was wrongly querying legacy `equipment` table) ────────
    eq_rows = _safe_qall(
        "SELECT lead_time_weeks, is_long_lead, eq_type, price_cad "
        "FROM equipment_v2 WHERE project_id=%s AND enabled=TRUE",
        (pid,),
    )
    if eq_rows:
        long_lead = [
            e for e in eq_rows
            if (e.get("is_long_lead") or (e.get("lead_time_weeks") or 0) > 40)
        ]
        if long_lead:
            add(
                "Schedule",
                f"{len(long_lead)} équipement(s) à long délai (>40 semaines)",
                cause="Articles critiques avec délais fournisseurs prolongés",
                consequence="Glissement calendrier construction / mise en service",
                probability=3,
                impact=4,
                mitigation="Commande anticipée; suivi hebdomadaire fournisseurs",
                owner="Project Manager",
                is_gate_blocker=True,
            )
    else:
        add(
            "Schedule",
            "Liste d'équipements (MER) non générée",
            cause="Module équipements vide",
            consequence="CAPEX et planning d'approvisionnement non quantifiés",
            probability=3,
            impact=3,
            mitigation="Générer MER depuis bilan massique / circuit",
            owner="Project Manager",
        )

    # ── OPEX — puissance installée ────────────────────────────────────────────
    opex_rows = _safe_qall("SELECT installed_kw FROM opex_power WHERE project_id=%s", (pid,))
    kw_vals = _nums(opex_rows, "installed_kw")
    total_kw = sum(kw_vals) if kw_vals else 0
    if total_kw > 10000:
        add(
            "Financial",
            f"Puissance installée élevée ({total_kw:.0f} kW) — OPEX énergie",
            cause="Intensité énergétique du procédé",
            consequence="Sensibilité AISC à la tarification électricité",
            probability=4,
            impact=3,
            mitigation="Audit énergétique; récupération chaleur si applicable",
            owner="Cost Engineer",
            phase="Operations",
        )

    # ── Géotechnique ──────────────────────────────────────────────────────────
    geotech_rows = _safe_qall("SELECT id FROM geotech_samples WHERE project_id=%s LIMIT 1", (pid,))
    if geotech_rows:
        add(
            "Geotechnical",
            "Données géotechniques — validation fondations et ouvrages de mine",
            cause="Variabilité des propriétés du sol / roche",
            consequence="Surcoûts fondations, digue TSF ou atelier",
            probability=2,
            impact=4,
            mitigation="Campagne géotech complémentaire si écarts aux hypothèses",
            owner="Geotechnical Engineer",
        )

    # ── Block model ───────────────────────────────────────────────────────────
    bm = qone("SELECT id FROM block_model_configs WHERE project_id=%s LIMIT 1", (pid,))
    if bm:
        add(
            "Technical",
            "Variabilité spatiale du gisement (block model) — planification",
            cause="Hétérogénéité grade / lithologie dans le modèle de blocs",
            consequence="Risque de dilution et d'écart production vs modèle",
            probability=3,
            impact=3,
            mitigation="Classification géométallurgique; courbes de grade-tonnage",
            owner="Mine Planner",
        )

    inserted = _insert_generated_risks(pid, generated_risks)

    record_event(
        user_id=str(user["id"]), project_id=pid,
        entity_type="risks", entity_id=None,
        action="auto_generate",
        new_value={"count": len(inserted)},
        source="risks",
    )

    return {
        "ok": True,
        "generated_count": len(inserted),
        "items": inserted,
        "message": f"{len(inserted)} risque(s) généré(s) automatiquement pour ce projet",
    }


@router.get("/risks")
def list_risks(pid: str, limit: int = 500, offset: int = 0, user=Depends(project_user)):
    """List all risks for a project, ordered by criticality desc."""
    try:
        rows = qall(
            "SELECT r.*, sg.stage_name "
            "FROM risks r "
            "LEFT JOIN stage_gates sg ON r.stage_id = sg.id "
            "WHERE r.project_id = %s "
            "ORDER BY r.criticality DESC NULLS LAST, r.created_at",
            (pid,),
        )
        if offset:
            rows = rows[offset:]
        if limit and len(rows) > limit:
            rows = rows[:limit]
        return rows
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/risks/{risk_id}")
def get_risk(pid: str, risk_id: str, user=Depends(project_user)):
    """Get a single risk detail."""
    try:
        row = qone(
            "SELECT r.*, sg.stage_name "
            "FROM risks r "
            "LEFT JOIN stage_gates sg ON r.stage_id = sg.id "
            "WHERE r.id = %s AND r.project_id = %s",
            (risk_id, pid),
        )
        if not row:
            raise HTTPException(404, "Risque introuvable")
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/risks", status_code=201)
def create_risk(
    pid: str, body: RiskIn,
    user=Depends(require_project_role("Project Manager", "Process Engineer", "Metallurgist", "Cost Engineer")),
):
    """Create a new risk in the EPCM register."""
    try:
        # Auto-generate risk number if not provided
        risk_number = body.risk_number
        if not risk_number and body.category:
            risk_number = _next_risk_number(pid, body.category)
        elif not risk_number:
            risk_number = _next_risk_number(pid, "Other")

        row = execute(
            "INSERT INTO risks "
            "(project_id, risk_number, description, cause, consequence, "
            "probability, impact, mitigation, preventive_actions, corrective_actions, "
            "alert_indicators, owner, category, phase, due_date, review_date, "
            "stage_id, is_gate_blocker) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING *",
            (
                pid, risk_number, body.description, body.cause, body.consequence,
                body.probability, body.impact, body.mitigation,
                body.preventive_actions, body.corrective_actions,
                body.alert_indicators, body.owner, body.category, body.phase,
                body.due_date, body.review_date, body.stage_id, body.is_gate_blocker,
            ),
        )
        row["criticality"] = row["probability"] * row["impact"]
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.patch("/risks/{risk_id}")
def update_risk(
    pid: str, risk_id: str, body: RiskPatch,
    user=Depends(require_project_role("Project Manager", "Process Engineer", "Metallurgist", "Cost Engineer")),
):
    """Update risk fields."""
    try:
        data = body.model_dump(exclude_none=True)
        fields, vals = build_update_sets(data, allowed=frozenset(type(body).model_fields.keys()))
        if not fields:
            raise HTTPException(400, "Aucune donnee a mettre a jour")
        fields.append("updated_at = NOW()")
        vals.append(risk_id)
        vals.append(pid)
        row = execute(
            f"UPDATE risks SET {', '.join(fields)} "
            "WHERE id = %s AND project_id = %s "
            "RETURNING *",
            vals,
        )
        if not row:
            raise HTTPException(404, "Risque introuvable")
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.delete("/risks/{risk_id}")
def delete_risk(
    pid: str, risk_id: str,
    user=Depends(require_project_role("Project Manager")),
):
    """Delete a risk (PM only)."""
    try:
        existing = qone(
            "SELECT id FROM risks WHERE id = %s AND project_id = %s",
            (risk_id, pid),
        )
        if not existing:
            raise HTTPException(404, "Risque introuvable")
        execute("DELETE FROM risks WHERE id = %s AND project_id = %s", (risk_id, pid))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/risks/summary/matrix")
def risk_matrix(pid: str, user=Depends(project_user)):
    """Risk matrix summary: count of risks by probability x impact."""
    try:
        risks = qall(
            "SELECT probability, impact, COUNT(*) as count "
            "FROM risks WHERE project_id = %s AND status != 'Closed' "
            "GROUP BY probability, impact",
            (pid,),
        )
        matrix = {}
        for r in risks:
            key = f"{r['probability']}x{r['impact']}"
            matrix[key] = r["count"]
        # Summary stats
        total = qone("SELECT COUNT(*) as cnt FROM risks WHERE project_id = %s", (pid,))
        critical = qone(
            "SELECT COUNT(*) as cnt FROM risks WHERE project_id = %s AND criticality >= 15 AND status != 'Closed'",
            (pid,),
        )
        blockers = qone(
            "SELECT COUNT(*) as cnt FROM risks WHERE project_id = %s AND is_gate_blocker = TRUE AND status != 'Closed'",
            (pid,),
        )
        return {
            "matrix": matrix,
            "total": total["cnt"] if total else 0,
            "critical": critical["cnt"] if critical else 0,
            "gate_blockers": blockers["cnt"] if blockers else 0,
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
