"""
MPDPMS -- Stage-Gate & Checklists routes.
6-phase EPCM framework for gold processing plant design projects.
Each phase includes: objectives, key activities, deliverables, gate criteria,
stakeholders, and phase-specific checklist items.
"""
from __future__ import annotations
import json
import logging
import psycopg2
import uuid

from fastapi import APIRouter, HTTPException, Depends

logger = logging.getLogger(__name__)

try:
    from ..auth import project_user, require_project_role
    from ..db import qone, qall, execute, build_update_sets, get_cursor
    from ..models import ChecklistItemIn, ChecklistItemPatch, StageGateApproval
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user, require_project_role
    from db import qone, qall, execute, build_update_sets, get_cursor
    from models import ChecklistItemIn, ChecklistItemPatch, StageGateApproval


router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["stage-gates"])



def _resolve_assigned_user_id(assigned_to: str | None) -> str | None:
    if not assigned_to:
        return None

    raw = assigned_to.strip()
    if not raw:
        return None

    try:
        return str(uuid.UUID(raw))
    except (ValueError, TypeError):
        pass

    row = qone("SELECT id FROM users WHERE email = %s", (raw,))
    if row:
        return str(row["id"])

    row = qone("SELECT id FROM users WHERE full_name = %s", (raw,))
    if row:
        return str(row["id"])

    raise HTTPException(
        400,
        "assigned_to invalide: fournissez un UUID utilisateur, un email, ou un nom exact.",
    )

# =============================================================================
# COMPLETE 6-PHASE EPCM STAGE DEFINITIONS
# =============================================================================

STAGES = [
    # ── Stage 1: Conceptual / Scoping ──────────────────────────────────────────
    {
        "stage_name": "Stage 1 - Phase Conceptuelle / Scoping",
        "stage_order": 1,
        "completion_pct": 15,
        "description": (
            "Evaluation initiale du potentiel du gisement aurifere. "
            "Definition du perimetre du projet, identification des ressources inferees "
            "et determination de la viabilite economique preliminaire."
        ),
        "objectives": json.dumps([
            "Evaluer le potentiel economique du gisement aurifere",
            "Definir le perimetre et la portee du projet",
            "Identifier les options de traitement metallurgique possibles (CIL, CIP, flottation, gravite)",
            "Estimer les ressources minerales (NI 43-101 inferred)",
            "Produire un ordre de grandeur des couts (AACE Class 5, +/-50%)",
        ]),
        "activities": json.dumps([
            "Revue des donnees geologiques et mineralogiques existantes",
            "Tests metallurgiques preliminaires (bottle roll, tests de cyanuration basiques)",
            "Identification des contraintes environnementales et sociales majeures",
            "Benchmark avec des projets auriferes similaires",
            "Etude de marche et prix de l'or a long terme",
            "Analyse preliminaire des options de site et d'infrastructure",
        ]),
        "deliverables": json.dumps([
            "Rapport de Scoping Study (NI 43-101 conforme)",
            "Process Flow Diagram (PFD) bloc simplifie",
            "Estimation des ressources (Inferred category)",
            "MTO (Material Take-Off) basique",
            "Estimation budgetaire AACE Class 5 (+/-30-50%)",
            "Analyse Go/No-Go preliminaire",
        ]),
        "gate_criteria": (
            "Potentiel economique valide (NPV indicatif positif). "
            "Ressources inferees suffisantes pour justifier des etudes complementaires. "
            "Aucun fatal flaw identifie (environnemental, social, technique)."
        ),
        "stakeholders": json.dumps([
            "Geologue principal",
            "Ingenieur procedes senior",
            "Directeur de projet",
            "Analyste financier",
            "Specialiste environnemental",
        ]),
    },
    # ── Stage 2: Pre-Feasibility Study (PFS) ──────────────────────────────────
    {
        "stage_name": "Stage 2 - Etude de Pre-Faisabilite (PFS)",
        "stage_order": 2,
        "completion_pct": 35,
        "description": (
            "Evaluation approfondie de la viabilite technique et economique. "
            "Testwork batch systematique, selection du flowsheet de traitement, "
            "et estimation des couts a +/-25%."
        ),
        "objectives": json.dumps([
            "Confirmer la viabilite technique du traitement aurifere",
            "Selectionner le flowsheet de traitement optimal",
            "Convertir les ressources inferees en mesurees/indiquees",
            "Estimer les couts CAPEX/OPEX (AACE Class 4, +/-15-25%)",
            "Evaluer la sensibilite economique (prix Au, grade, recuperation)",
            "Identifier et quantifier les risques majeurs du projet",
        ]),
        "activities": json.dumps([
            "Testwork metallurgique batch complet (gravite, flottation, lixiviation CIL/CIP)",
            "Tests de comminution (Bond Work Index, SMC, JK Drop Weight)",
            "Caracterisation mineralogique detaillee (QEMSCAN, MLA)",
            "Elaboration du Process Flow Diagram (PFD) detaille",
            "Dimensionnement preliminaire des equipements majeurs (SAG, broyeurs, cuves CIL)",
            "Etude geotechnique preliminaire du site",
            "Etude hydrologique et gestion des eaux",
            "Evaluation environnementale de base (EIE preliminaire)",
            "Analyse economique NPV/IRR a 5% avec sensibilites",
        ]),
        "deliverables": json.dumps([
            "Rapport PFS (NI 43-101 conforme)",
            "Resultats de testwork metallurgique complets",
            "PFD (Process Flow Diagram) detaille avec bilans massiques",
            "Liste des equipements majeurs avec dimensionnement",
            "Estimation AACE Class 4 (+/-15-25%)",
            "Modele economique NPV/IRR avec analyse de sensibilite",
            "Plan de gestion des residus miniers (TSF conceptuel)",
            "Registre des risques initial",
            "Plan de testwork pour la phase FS",
        ]),
        "gate_criteria": (
            "NPV positif a 5% de taux d'actualisation. "
            "Flowsheet de traitement techniquement viable demontre par testwork. "
            "Risques majeurs identifies et strategies de mitigation definies. "
            "Ressources mesurees/indiquees suffisantes pour supporter le plan minier."
        ),
        "stakeholders": json.dumps([
            "Ingenieur procedes principal (Lead Process Engineer)",
            "Metallurgiste senior",
            "Geologue des ressources (QP NI 43-101)",
            "Ingenieur geotechnique",
            "Estimateur de couts",
            "Directeur de projet",
            "Specialiste environnement et permis",
        ]),
    },
    # ── Stage 3: Feasibility Study (FS) ───────────────────────────────────────
    {
        "stage_name": "Stage 3 - Etude de Faisabilite (FS / BFS)",
        "stage_order": 3,
        "completion_pct": 60,
        "description": (
            "Finalisation de l'etude bancaire (Bankable Feasibility Study). "
            "Testwork continu et pilote, design detaille du flowsheet, "
            "estimation AACE Class 3 pour la decision finale d'investissement (FID)."
        ),
        "objectives": json.dumps([
            "Produire une etude bancable pour le financement du projet",
            "Finaliser le design du flowsheet via testwork continu et pilote",
            "Atteindre une estimation AACE Class 3 (+/-10-15%)",
            "Demontrer la robustesse economique a $1,600/oz Au",
            "Obtenir les approbations reglementaires cles",
            "Preparer la decision finale d'investissement (FID)",
        ]),
        "activities": json.dumps([
            "Testwork metallurgique continu (circuit ferme, variabilite du minerai)",
            "Campagne pilote si requis (gravite + CIL, flottation-regrind-CIL)",
            "Production des P&ID (Piping & Instrumentation Diagrams) initiaux",
            "Design detaille des equipements majeurs avec vendor quotes",
            "Etude de constructabilite et plan de construction",
            "Etude d'impact environnemental complete (EIE/EIES)",
            "Plan de gestion des residus (TSF) detaille avec analyse de stabilite",
            "Modelisation 3D preliminaire de l'usine",
            "Negociations avec les fournisseurs d'equipements longs delais",
            "HAZID (Hazard Identification) complet",
            "Plan minier detaille (LOM - Life of Mine)",
        ]),
        "deliverables": json.dumps([
            "Rapport BFS/FS complet (NI 43-101 conforme)",
            "Resultats testwork continu et pilote avec bilans metallurgiques",
            "P&ID initiaux pour tous les circuits",
            "Equipment datasheets et specifications techniques",
            "Estimation AACE Class 3 (+/-10-15%)",
            "Modele financier complet avec sensibilites et Monte Carlo",
            "Etude d'impact environnemental deposee",
            "Plan de gestion des residus miniers approuve",
            "Registre des risques mis a jour et approuve",
            "Plan de constructabilite",
            "Rapport HAZID",
            "Reserves minerales prouvees et probables (NI 43-101)",
        ]),
        "gate_criteria": (
            "Estimation AACE Class 3 validee. "
            "NPV robuste a $1,600/oz avec IRR > hurdle rate. "
            "Testwork continu/pilote confirmant les recuperations du design. "
            "EIE deposee et permis environnemental en cours. "
            "Registre des risques approuve sans bloquant non mitige. "
            "Reserves prouvees/probables suffisantes (NI 43-101)."
        ),
        "stakeholders": json.dumps([
            "Ingenieur procedes principal (Lead Process Engineer)",
            "Metallurgiste senior / Responsable testwork",
            "Directeur de projet",
            "Ingenieur estimateur (Cost Engineer)",
            "QP NI 43-101 (Qualified Person)",
            "Responsable environnement et permis",
            "Ingenieur geotechnique (TSF)",
            "Directeur financier / Investisseurs",
            "Comite de direction (FID approval)",
        ]),
    },
    # ── Stage 4: Basic Engineering (Ingenierie de Base) ───────────────────────
    {
        "stage_name": "Stage 4 - Ingenierie de Base (Basic Engineering)",
        "stage_order": 4,
        "completion_pct": 80,
        "description": (
            "Developpement de l'ingenierie de base post-FID. "
            "Production des documents techniques fondamentaux, "
            "specifications des equipements, et preparation de l'ingenierie detaillee."
        ),
        "objectives": json.dumps([
            "Developper l'ingenierie de base complete pour tous les systemes",
            "Finaliser les specifications des equipements majeurs",
            "Lancer les commandes d'equipements longs delais (SAG mill, agitateurs, etc.)",
            "Affiner l'estimation a AACE Class 2 (+/-5-10%)",
            "Produire le Basis of Design (BoD) final",
            "Preparer la documentation pour l'ingenierie detaillee",
        ]),
        "activities": json.dumps([
            "Production des P&ID finaux pour tous les circuits (broyage, lixiviation, elution, etc.)",
            "Design detaille des structures (civil, mecanique, electricite)",
            "Specifications techniques completes des equipements",
            "Revue 3D de l'implantation usine (layout optimization)",
            "Etude HAZOP preliminaire sur les circuits critiques (cyanure, elution)",
            "Plan d'approvisionnement et strategie de sous-traitance",
            "Design des systemes de controle et d'instrumentation (P&ID finaux, boucles de controle)",
            "Specifications des systemes electriques (HV/MV/LV, MCC, transformateurs)",
            "Design du TSF (Tailings Storage Facility) detaille",
            "Plan d'execution du projet (PEP) finalise",
        ]),
        "deliverables": json.dumps([
            "Basis of Design (BoD) final et approuve",
            "P&ID finaux tous circuits",
            "Specifications techniques des equipements (datasheets, RFQ)",
            "General Arrangement Drawings (GA) de l'usine",
            "Estimation AACE Class 2 (+/-5-10%)",
            "Modele 3D preliminaire de l'usine",
            "Rapport HAZOP preliminaire",
            "Plan d'approvisionnement (Procurement Plan)",
            "Commandes d'equipements longs delais passees",
            "Plan d'execution du projet (PEP)",
            "Calendrier de construction detaille (Level 3)",
        ]),
        "gate_criteria": (
            "Basis of Design final approuve par toutes les disciplines. "
            "P&ID finaux revus et approuves. "
            "Equipements longs delais commandes. "
            "Estimation AACE Class 2 validee dans le budget approuve. "
            "HAZOP preliminaire complete sans findings critiques non resolus. "
            "Plan d'execution approuve."
        ),
        "stakeholders": json.dumps([
            "Ingenieur procedes principal (Lead Process Engineer)",
            "Ingenieur mecanique senior",
            "Ingenieur electricite / instrumentation",
            "Ingenieur civil / structures",
            "Responsable approvisionnement (Procurement Manager)",
            "Planificateur de projet",
            "Responsable HSE",
            "Directeur de projet",
            "Ingenieur controle et automatisation",
        ]),
    },
    # ── Stage 5: Detailed Engineering / FEED ──────────────────────────────────
    {
        "stage_name": "Stage 5 - Ingenierie Detaillee (FEED / Detailed Design)",
        "stage_order": 5,
        "completion_pct": 95,
        "description": (
            "Production de tous les documents IFC (Issued for Construction). "
            "HAZOP complet, finalisation des contrats EPC, "
            "et preparation de la phase de construction."
        ),
        "objectives": json.dumps([
            "Produire les documents IFC (Issued For Construction) complets",
            "Realiser le HAZOP complet sur tous les systemes",
            "Finaliser les contrats d'execution (EPC/EPCM)",
            "Atteindre une estimation AACE Class 1 (+/-3-5%)",
            "Completer tous les permis de construction",
            "Preparer le dossier de pre-commissioning",
        ]),
        "activities": json.dumps([
            "Production des dessins IFC (isometriques, plans d'installation, coupes)",
            "HAZOP complet sur tous les systemes de l'usine",
            "SIL (Safety Integrity Level) assessment des boucles de securite",
            "Design detaille des systemes de detection et extinction incendie",
            "Specifications de construction et procedures de soudage",
            "Finalisation des dessins d'armoire electrique et schemas de cablage",
            "Programmation du systeme de controle (DCS/PLC/SCADA)",
            "Plans de piping detailles et stress analysis",
            "Production du modele 3D final et clash detection",
            "Finalisation des contrats de construction",
            "Plan de commissioning et pre-commissioning detaille",
            "Preparation des manuels d'operation et de maintenance",
        ]),
        "deliverables": json.dumps([
            "Dessins IFC complets (toutes disciplines)",
            "Rapport HAZOP complet avec actions fermees",
            "Rapport SIL assessment",
            "Modele 3D final valide (zero clash)",
            "Estimation AACE Class 1 (+/-3-5%)",
            "Contrats EPC/EPCM signes",
            "Permis de construction obtenus",
            "Manuels d'operation et de maintenance (O&M)",
            "Plan de commissioning detaille",
            "Calendrier de construction final (Level 4)",
            "Specifications de controle qualite (QA/QC)",
        ]),
        "gate_criteria": (
            "Tous les dessins IFC emis et approuves. "
            "HAZOP complete, toutes les actions critiques fermees. "
            "Contrats EPC/EPCM signes et budget confirme. "
            "Estimation AACE Class 1 dans la tolerance du budget. "
            "Permis de construction obtenus. "
            "Plan de commissioning approuve."
        ),
        "stakeholders": json.dumps([
            "Directeur de projet",
            "Ingenieur procedes principal",
            "Lead Engineer (toutes disciplines)",
            "Responsable construction",
            "Responsable HSE / HAZOP Chairman",
            "Responsable QA/QC",
            "Responsable approvisionnement",
            "Responsable commissioning",
            "Contractant EPC/EPCM",
            "Autorites regulatoires",
        ]),
    },
    # ── Stage 6: Construction & Commissioning ─────────────────────────────────
    {
        "stage_name": "Stage 6 - Construction & Commissioning",
        "stage_order": 6,
        "completion_pct": 100,
        "description": (
            "Realisation physique du projet, installation des equipements, "
            "pre-commissioning, commissioning (dry et wet), "
            "et ramp-up vers la capacite nominale."
        ),
        "objectives": json.dumps([
            "Construire l'usine de traitement selon les specifications IFC",
            "Realiser le pre-commissioning et le commissioning de tous les systemes",
            "Atteindre la capacite nominale de traitement (nameplate capacity)",
            "Transferer l'usine aux operations (Handover)",
            "Demontrer les performances garanties (Performance Test Run)",
            "Clore le projet et capitaliser les lecons apprises",
        ]),
        "activities": json.dumps([
            "Genie civil: fondations, structures beton et acier",
            "Installation mecanique: equipements, piping, charpente",
            "Installation electrique: cablage, MCC, transformateurs",
            "Installation instrumentation et systemes de controle",
            "Pre-commissioning: verifications, alignements, tests d'etancheite",
            "Dry commissioning: rotations a vide, tests moteurs, boucles de controle",
            "Wet commissioning: mise en eau, tests hydrauliques, premier minerai",
            "Ramp-up: montee en puissance progressive vers la capacite nominale",
            "Performance Test Run (72h / 30 jours selon contrat)",
            "Punch list et correction des deficiences",
            "Formation du personnel d'exploitation",
            "Handover aux operations avec documentation complete",
        ]),
        "deliverables": json.dumps([
            "Usine construite et operationnelle",
            "Certificats de pre-commissioning et commissioning signes",
            "Rapport de Performance Test Run",
            "As-Built Drawings (dessins conformes a l'execution)",
            "Manuels O&M finaux mis a jour",
            "Certificat de Handover aux operations",
            "Rapport de cloture de projet (lecons apprises)",
            "Dossier de garanties fournisseurs",
            "Punch list complete et fermee",
            "Documentation reglementaire finale",
        ]),
        "gate_criteria": (
            "Performance Test Run reussi (debit, recuperation, qualite produit). "
            "Tous les items de punch list critiques fermes. "
            "Documentation As-Built complete et remise. "
            "Personnel d'exploitation forme et certifie. "
            "Handover formel aux operations signe. "
            "Budget final dans la tolerance approuvee."
        ),
        "stakeholders": json.dumps([
            "Directeur de projet",
            "Responsable construction",
            "Responsable commissioning",
            "Directeur des operations",
            "Responsable HSE",
            "Responsable QA/QC",
            "Contractant EPC/EPCM",
            "Equipe d'exploitation (Operations)",
            "Responsable maintenance",
            "Directeur general / Comite de direction",
        ]),
    },
]

# =============================================================================
# PHASE-SPECIFIC GATE CHECKLIST ITEMS
# Each stage gets its own relevant gate checklist items for gold processing
# =============================================================================

GATE_CHECKLISTS = {
    1: [  # Stage 1 - Conceptual
        {"domain": "Metallurgie & Testwork", "target_pct": 95, "items": [
            "Resultats preliminaires de tests bottle roll / cyanuration documentes",
            "Options de traitement identifiees (CIL, CIP, gravite, flottation)",
            "Mineralogie basique du minerai documentee",
        ]},
        {"domain": "Ingenierie Procedes", "target_pct": 90, "items": [
            "PFD bloc simplifie produit",
            "Bilan massique indicatif elabore",
            "Options de dimensionnement (throughput) evaluees",
        ]},
        {"domain": "Estimation & Economie", "target_pct": 85, "items": [
            "Estimation AACE Class 5 (+/-30-50%) completee",
            "Analyse NPV/IRR preliminaire positive",
            "Benchmark avec projets comparables realise",
        ]},
        {"domain": "Environnement & Permis", "target_pct": 80, "items": [
            "Contraintes environnementales majeures identifiees",
            "Fatal flaws analyses et absents",
            "Engagement communautaire initial realise",
        ]},
        {"domain": "Ressources & Geologie", "target_pct": 90, "items": [
            "Ressources inferees estimees (NI 43-101)",
            "Donnees geologiques existantes compilees et revues",
            "Programme d'exploration futur defini",
        ]},
    ],
    2: [  # Stage 2 - PFS
        {"domain": "Metallurgie & Testwork", "target_pct": 95, "items": [
            "Testwork batch complet realise (gravite, flottation, CIL/CIP)",
            "Tests de comminution completes (Bond WI, SMC, JK Drop Weight)",
            "Caracterisation mineralogique detaillee (QEMSCAN/MLA)",
            "Variabilite du minerai testee (oxyde, transition, sulfure)",
            "Recuperations metallurgiques par type de minerai etablies",
        ]},
        {"domain": "Ingenierie Procedes", "target_pct": 90, "items": [
            "PFD detaille avec bilans massiques complets",
            "Flowsheet de traitement selectionne et justifie",
            "Dimensionnement preliminaire des equipements majeurs",
            "Consommation de reactifs estimee (cyanure, chaux, floculant)",
        ]},
        {"domain": "Estimation & Economie", "target_pct": 88, "items": [
            "Estimation AACE Class 4 (+/-15-25%) completee",
            "Modele economique NPV/IRR avec sensibilites",
            "OPEX detaille par poste (energie, reactifs, main d'oeuvre)",
            "Analyse de sensibilite prix Au / grade / recuperation",
        ]},
        {"domain": "Environnement & Permis", "target_pct": 85, "items": [
            "Etude environnementale de base (baseline) realisee",
            "Plan conceptuel de gestion des residus (TSF)",
            "Gestion des eaux cyanuriques evaluee (circuit INCO/Caro's acid)",
            "Exigences reglementaires inventoriees",
        ]},
        {"domain": "Risques & Planning", "target_pct": 80, "items": [
            "Registre des risques initial etabli",
            "Calendrier preliminaire du projet (Level 2)",
            "Strategie d'execution du projet definie",
            "Equipements longs delais identifies",
        ]},
    ],
    3: [  # Stage 3 - FS / BFS
        {"domain": "Metallurgie & Testwork", "target_pct": 95, "items": [
            "Testwork continu (locked cycle) complete avec bilans",
            "Campagne pilote realisee si requise",
            "Recuperations de design confirmees par testwork",
            "Variabilite geometallurgique validee",
            "Consommation de reactifs optimisee et confirmee",
            "Tests de detoxification des rejets cyanures valides",
        ]},
        {"domain": "Ingenierie Procedes", "target_pct": 95, "items": [
            "P&ID initiaux produits pour tous les circuits",
            "Equipment datasheets et specifications techniques completes",
            "Bilan massique final valide",
            "Design criteria document approuve",
            "Layout general de l'usine (General Arrangement) produit",
        ]},
        {"domain": "Estimation & Economie", "target_pct": 90, "items": [
            "Estimation AACE Class 3 (+/-10-15%) completee",
            "Vendor quotes obtenus pour equipements majeurs",
            "Modele financier complet avec Monte Carlo",
            "Robustesse demontree a $1,600/oz",
            "Plan de financement elabore",
        ]},
        {"domain": "Environnement & Permis", "target_pct": 90, "items": [
            "EIE complete deposee aupres des autorites",
            "Plan de gestion des residus (TSF) detaille approuve",
            "Analyse de stabilite du TSF realisee",
            "Plan de fermeture de mine (closure plan) elabore",
            "Permis environnemental en cours d'obtention",
        ]},
        {"domain": "Risques & HAZID", "target_pct": 90, "items": [
            "Rapport HAZID complet",
            "Registre des risques mis a jour et approuve",
            "Tous les risques gate-blocker resolus ou mitigues",
            "Plan de gestion de crise defini",
        ]},
        {"domain": "Ressources & Plan Minier", "target_pct": 95, "items": [
            "Reserves prouvees et probables (NI 43-101) publiees",
            "Plan minier LOM (Life of Mine) detaille",
            "Schedule d'alimentation usine optimise",
        ]},
    ],
    4: [  # Stage 4 - Basic Engineering
        {"domain": "Ingenierie Procedes", "target_pct": 95, "items": [
            "P&ID finaux approuves pour tous les circuits",
            "Basis of Design (BoD) final signe",
            "Boucles de controle definies et documentees",
            "Liste des instruments complete",
            "Specifications de tuyauterie et des materiaux",
        ]},
        {"domain": "Ingenierie Mecanique & Civile", "target_pct": 90, "items": [
            "General Arrangement Drawings (GA) finalises",
            "Specifications techniques des equipements approuvees",
            "Design des fondations et structures complete",
            "Modele 3D preliminaire avec revue de constructabilite",
            "Plan de manutention lourde (rigging plan) defini",
        ]},
        {"domain": "Electricite & Instrumentation", "target_pct": 90, "items": [
            "Single Line Diagrams (SLD) electriques approuves",
            "Specifications MCC, transformateurs, variateurs",
            "Philosophie de controle et d'instrumentation approuvee",
            "Bilan electrique (electrical load list) finalise",
        ]},
        {"domain": "Approvisionnement", "target_pct": 88, "items": [
            "Equipements longs delais commandes (SAG mill, agitateurs, etc.)",
            "Plan d'approvisionnement (Procurement Plan) approuve",
            "RFQ/RFP emis pour les packages critiques",
            "Strategie de sous-traitance definie",
        ]},
        {"domain": "HAZOP & Securite", "target_pct": 85, "items": [
            "HAZOP preliminaire complete sur circuits critiques",
            "Actions HAZOP critiques traitees",
            "Plan HSE du projet mis a jour",
            "Estimation AACE Class 2 (+/-5-10%) validee",
        ]},
        {"domain": "Planning & Execution", "target_pct": 85, "items": [
            "Plan d'execution du projet (PEP) approuve",
            "Calendrier Level 3 de construction produit",
            "Strategie de constructabilite finalisee",
        ]},
    ],
    5: [  # Stage 5 - Detailed Engineering / FEED
        {"domain": "Documents IFC", "target_pct": 95, "items": [
            "Dessins isometriques de piping IFC emis",
            "Plans d'installation mecanique IFC emis",
            "Schemas electriques et de cablage IFC emis",
            "Plans d'instrumentation et boucles de controle IFC emis",
            "Plans civil / structures IFC emis",
            "Modele 3D final valide (clash detection = zero critical)",
        ]},
        {"domain": "HAZOP & SIL", "target_pct": 95, "items": [
            "HAZOP complet realise sur tous les systemes",
            "Toutes les actions HAZOP critiques fermees",
            "SIL assessment des boucles de securite complete",
            "Systemes de detection et extinction incendie specifies",
        ]},
        {"domain": "Contrats & Budget", "target_pct": 90, "items": [
            "Contrats EPC/EPCM signes",
            "Estimation AACE Class 1 (+/-3-5%) dans le budget",
            "Budget de contingence valide",
            "Calendrier contractuel de construction approuve",
        ]},
        {"domain": "Commissioning Prep", "target_pct": 85, "items": [
            "Plan de commissioning detaille approuve",
            "Manuels d'operation et de maintenance produits",
            "Procedures de pre-commissioning elaborees",
            "Equipe de commissioning identifiee et mobilisee",
        ]},
        {"domain": "Permis & Reglementaire", "target_pct": 90, "items": [
            "Permis de construction obtenus",
            "Autorisations de stockage de cyanure obtenues",
            "Permis d'exploitation (ou en cours avance)",
            "QA/QC specifications et plan qualite approuves",
        ]},
    ],
    6: [  # Stage 6 - Construction & Commissioning
        {"domain": "Construction", "target_pct": 95, "items": [
            "Genie civil et fondations completes et inspectees",
            "Equipements majeurs installes et alignes",
            "Piping installe, teste et approuve (hydrotest)",
            "Installation electrique complete et megaree",
            "Instrumentation installee et calibree",
        ]},
        {"domain": "Pre-Commissioning", "target_pct": 95, "items": [
            "Check-lists de pre-commissioning completees par systeme",
            "Tests d'etancheite (leak tests) realises",
            "Verification des rotations et alignements moteurs",
            "Boucles de controle verifiees (loop checks)",
            "Systemes de securite testes et operationnels",
        ]},
        {"domain": "Commissioning", "target_pct": 90, "items": [
            "Dry commissioning complet (rotations a vide, sequences)",
            "Wet commissioning complet (mise en eau, tests hydrauliques)",
            "Premier minerai introduit dans le circuit",
            "Circuit de lixiviation CIL/CIP en operation",
            "Premiere coulee d'or (gold room operationnel)",
        ]},
        {"domain": "Ramp-up & Performance", "target_pct": 85, "items": [
            "Montee en puissance progressive vers capacite nominale",
            "Performance Test Run reussi (debit + recuperation)",
            "KPI operationnels dans les cibles de design",
            "Consommation de reactifs dans les normes",
        ]},
        {"domain": "Handover & Cloture", "target_pct": 90, "items": [
            "Punch list complete et items critiques fermes",
            "As-Built Drawings remis et archives",
            "Personnel d'exploitation forme et certifie",
            "Handover formel aux operations signe",
            "Rapport de cloture de projet redige (lecons apprises)",
            "Dossier de garanties fournisseurs compile",
        ]},
    ],
}


# =============================================================================
# AUTO-INITIALIZE STAGES FOR A PROJECT
# =============================================================================

def _init_stages(pid: str):
    """Create the 6 EPCM stages and populate phase-specific checklists.

    Uses a single transaction for all stages + checklist items to avoid
    partial initialization and reduce round-trips.
    """
    existing = qall("SELECT id FROM stage_gates WHERE project_id = %s", (pid,))
    if existing:
        return

    with get_cursor(commit=True) as cur:
        for stage_def in STAGES:
            cur.execute(
                "INSERT INTO stage_gates "
                "(project_id, stage_name, stage_order, completion_pct, "
                "description, objectives, activities, deliverables, gate_criteria, stakeholders) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    pid,
                    stage_def["stage_name"],
                    stage_def["stage_order"],
                    stage_def["completion_pct"],
                    stage_def["description"],
                    stage_def["objectives"],
                    stage_def["activities"],
                    stage_def["deliverables"],
                    stage_def["gate_criteria"],
                    stage_def["stakeholders"],
                ),
            )
            row = cur.fetchone()
            stage_id = row["id"]
            stage_order = stage_def["stage_order"]

            # Batch insert phase-specific checklist items
            sort_order = 0
            for domain_def in GATE_CHECKLISTS.get(stage_order, []):
                for item_name in domain_def["items"]:
                    cur.execute(
                        "INSERT INTO checklist_items (stage_id, domain, item_name, target_pct, sort_order) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (stage_id, domain_def["domain"], item_name,
                         domain_def["target_pct"], sort_order),
                    )
                    sort_order += 1


# =============================================================================
# ENDPOINTS: Stage-Gates
# =============================================================================

@router.get("/stage-gates")
def list_stages(pid: str, limit: int = 100, offset: int = 0, user=Depends(project_user)):
    """List all 6 EPCM stages for a project. Auto-initialises on first access."""
    try:
        _init_stages(pid)
        stages = qall(
            "SELECT * FROM stage_gates WHERE project_id = %s ORDER BY stage_order",
            (pid,),
        )
        for s in stages:
            # Parse JSON fields
            for field in ("objectives", "activities", "deliverables", "stakeholders"):
                if s.get(field) and isinstance(s[field], str):
                    try:
                        s[field] = json.loads(s[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            # Compute checklist progress
            items = qall(
                "SELECT status, is_done FROM checklist_items WHERE stage_id = %s",
                (s["id"],),
            )
            total = len(items)
            done = sum(1 for it in items if it["status"] == "Approved" or it["is_done"])
            s["checklist_total"] = total
            s["checklist_done"] = done
            s["checklist_progress"] = round(done / total * 100) if total else 0
            # Count gate-blocking risks
            blockers = qone(
                "SELECT COUNT(*) as cnt FROM risks WHERE stage_id = %s AND is_gate_blocker = TRUE AND status != 'Closed'",
                (s["id"],),
            )
            s["blocking_risks"] = blockers["cnt"] if blockers else 0
        return stages
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/stage-gates/{gate_id}")
def get_stage(pid: str, gate_id: str, user=Depends(project_user)):
    """Get a single stage with full checklist details."""
    try:
        stage = qone(
            "SELECT * FROM stage_gates WHERE id = %s AND project_id = %s",
            (gate_id, pid),
        )
        if not stage:
            raise HTTPException(404, "Stage introuvable")
        # Parse JSON fields
        for field in ("objectives", "activities", "deliverables", "stakeholders"):
            if stage.get(field) and isinstance(stage[field], str):
                try:
                    stage[field] = json.loads(stage[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        # Attach checklists grouped by domain
        items = qall(
            "SELECT * FROM checklist_items WHERE stage_id = %s ORDER BY sort_order",
            (gate_id,),
        )
        domains = {}
        for it in items:
            d = it["domain"]
            if d not in domains:
                domains[d] = {"domain": d, "target_pct": it["target_pct"], "items": []}
            domains[d]["items"].append(it)
        stage["checklists"] = list(domains.values())
        total = len(items)
        done = sum(1 for it in items if it["status"] == "Approved" or it["is_done"])
        stage["checklist_total"] = total
        stage["checklist_done"] = done
        stage["checklist_progress"] = round(done / total * 100) if total else 0
        return stage
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# =============================================================================
# ENDPOINTS: Checklists
# =============================================================================

@router.get("/stage-gates/{gate_id}/checklists")
def list_checklists(pid: str, gate_id: str, user=Depends(project_user)):
    """List checklist items for a stage, grouped by domain."""
    try:
        items = qall(
            "SELECT ci.*, u.full_name as assigned_name "
            "FROM checklist_items ci "
            "LEFT JOIN users u ON ci.assigned_to = u.id "
            "WHERE ci.stage_id = %s ORDER BY ci.sort_order",
            (gate_id,),
        )
        domains = {}
        for it in items:
            d = it["domain"]
            if d not in domains:
                domains[d] = {"domain": d, "target_pct": it["target_pct"], "items": []}
            domains[d]["items"].append(it)
        return list(domains.values())
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/stage-gates/{gate_id}/checklists", status_code=201)
def add_checklist_item(
    pid: str, gate_id: str, body: ChecklistItemIn,
    user=Depends(require_project_role("Project Manager", "Process Engineer", "Metallurgist")),
):
    """Add a custom checklist item to a stage."""
    try:
        stage = qone("SELECT id FROM stage_gates WHERE id = %s AND project_id = %s", (gate_id, pid))
        if not stage:
            raise HTTPException(404, "Stage introuvable")
        max_order = qone(
            "SELECT COALESCE(MAX(sort_order), 0) + 1 as next_order FROM checklist_items WHERE stage_id = %s",
            (gate_id,),
        )
        assigned_user_id = _resolve_assigned_user_id(body.assigned_to)
        row = execute(
            "INSERT INTO checklist_items (stage_id, domain, item_name, target_pct, notes, assigned_to, sort_order) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (gate_id, body.domain, body.item_name, body.target_pct, body.notes, assigned_user_id, max_order["next_order"]),
        )
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.patch("/stage-gates/{gate_id}/checklists/{item_id}")
def update_checklist_item(
    pid: str, gate_id: str, item_id: str, body: ChecklistItemPatch,
    user=Depends(require_project_role("Project Manager", "Process Engineer", "Metallurgist", "Reviewer")),
):
    """Update checklist item status, proof link, notes, or assignment."""
    try:
        payload = body.model_dump(exclude_none=True)
        if "assigned_to" in payload:
            payload["assigned_to"] = _resolve_assigned_user_id(payload.get("assigned_to"))
        fields, vals = build_update_sets(payload, allowed=frozenset(type(body).model_fields.keys()))
        if not fields:
            raise HTTPException(400, "Aucune donnee a mettre a jour")
        vals.append(item_id)
        vals.append(gate_id)
        row = execute(
            f"UPDATE checklist_items SET {', '.join(fields)} "
            "WHERE id = %s AND stage_id = %s RETURNING *",
            vals,
        )
        if not row:
            raise HTTPException(404, "Item de checklist introuvable")
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


# =============================================================================
# ENDPOINTS: Gate approval
# =============================================================================

@router.post("/stage-gates/{gate_id}/approve")
def approve_gate(
    pid: str, gate_id: str, body: StageGateApproval,
    user=Depends(require_project_role("Project Manager", "Reviewer")),
):
    """Approve a stage gate. Checks all checklists are complete and no blocking risks."""
    try:
        stage = qone(
            "SELECT * FROM stage_gates WHERE id = %s AND project_id = %s",
            (gate_id, pid),
        )
        if not stage:
            raise HTTPException(404, "Stage introuvable")

        blockers = qone(
            "SELECT COUNT(*) as cnt FROM risks WHERE stage_id = %s AND is_gate_blocker = TRUE AND status != 'Closed'",
            (gate_id,),
        )
        if blockers and blockers["cnt"] > 0:
            raise HTTPException(
                400,
                f"Impossible d'approuver: {blockers['cnt']} risque(s) bloquant(s) non resolus",
            )

        items = qall(
            "SELECT status, is_done FROM checklist_items WHERE stage_id = %s",
            (gate_id,),
        )
        total = len(items)
        done = sum(1 for it in items if it["status"] == "Approved" or it["is_done"])
        if total > 0 and done < total:
            raise HTTPException(
                400,
                f"Checklist incomplete: {done}/{total} items approuves",
            )

        # Check NI 43-101 readiness for this stage
        stage_name = (stage.get("stage_name") or "").lower()
        ni43101_stage_map = {
            "conceptual": "scoping",
            "scoping": "scoping",
            "pre-feasibility": "pfs",
            "feasibility": "fs",
            "basic engineering": "dfs",
            "detailed engineering": "dfs",
        }
        ni_stage = ni43101_stage_map.get(stage_name)
        if ni_stage:
            try:
                from .ni43101 import check_readiness
                from .lims import LIMS_TABLES, safe_table_name
                test_counts = {}
                for code, table in LIMS_TABLES.items():
                    tbl = safe_table_name(table)
                    row = qone(f"SELECT COUNT(*) as cnt FROM {tbl} WHERE project_id = %s", (pid,))
                    test_counts[code] = row["cnt"] if row else 0
                dc_rows = qall("SELECT source FROM design_criteria WHERE project_id = %s", (pid,))
                dc_sources = {}
                for r in (dc_rows or []):
                    s = (r.get("source") or "D").upper()[:1]
                    dc_sources[s] = dc_sources.get(s, 0) + 1
                mb = qone("SELECT COUNT(*) as cnt FROM mass_balance_streams WHERE project_id = %s", (pid,))
                sim = qone("SELECT COUNT(*) as cnt FROM simulation_runs WHERE project_id = %s", (pid,))
                readiness = check_readiness(
                    ni_stage, test_counts, dc_sources,
                    has_mass_balance=(mb["cnt"] if mb else 0) > 0,
                    has_simulation=(sim["cnt"] if sim else 0) > 0,
                )
                if not readiness["ready"]:
                    failures = [c for c in readiness["checklist"] if c["status"] == "fail"]
                    raise HTTPException(
                        400,
                        f"NI 43-101 readiness incomplete ({readiness['score_pct']}%): "
                        + "; ".join(f["item"] for f in failures),
                    )
            except ImportError:
                pass

        row = execute(
            "UPDATE stage_gates SET status = 'Approved', approved_by = %s, approved_at = NOW(), updated_at = NOW() "
            "WHERE id = %s RETURNING *",
            (user["id"], gate_id),
        )
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.post("/stage-gates/{gate_id}/reset")
def reset_gate(
    pid: str, gate_id: str,
    user=Depends(require_project_role("Project Manager")),
):
    """Reset a gate approval (PM only)."""
    try:
        row = execute(
            "UPDATE stage_gates SET status = 'Not started', approved_by = NULL, approved_at = NULL, updated_at = NOW() "
            "WHERE id = %s AND project_id = %s RETURNING *",
            (gate_id, pid),
        )
        if not row:
            raise HTTPException(404, "Stage introuvable")
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
