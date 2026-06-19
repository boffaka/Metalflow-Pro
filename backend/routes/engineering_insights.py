"""
Innovations — endpoints **readiness** & **fidélité jumeau numérique**.

Préfixe : ``/api/v1/projects/{pid}/insights/…`` (auth projet obligatoire).
"""
from __future__ import annotations

import logging

import psycopg2
from fastapi import APIRouter, Depends, HTTPException

try:
    from ..auth import project_user
    from ..engines.plant_design_advisor import assess_simulation_qa, assess_testwork_program
    from ..services.engineering_insights import (
        compute_digital_twin_fidelity,
        compute_engineering_readiness,
    )
    from .engineering_insights_schemas import (
        DigitalTwinFidelityOut,
        EngineeringReadinessOut,
    )
except ImportError:  # pragma: no cover
    from auth import project_user
    from engines.plant_design_advisor import assess_simulation_qa, assess_testwork_program
    from routes.engineering_insights_schemas import (
        DigitalTwinFidelityOut,
        EngineeringReadinessOut,
    )
    from services.engineering_insights import (
        compute_digital_twin_fidelity,
        compute_engineering_readiness,
    )

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/projects/{pid}/insights", tags=["engineering-insights"])


@router.get(
    "/engineering-readiness",
    response_model=EngineeringReadinessOut,
    summary="Readiness ingénierie (heuristique)",
    description=(
        "Score 0–100 et gates pondérées : gabarit actif, critères DC, sections bilan massique v2, "
        "MER, paramètres simulation, LIMS (volume + part des échantillons créés sous 90 j), flowsheet. "
        "Les gates utilisent un crédit partiel (fraction 0–1) ; `ok` vaut true si fraction ≥ 0,95."
    ),
)
def get_engineering_readiness(pid: str, user=Depends(project_user)):
    return compute_engineering_readiness(pid)


@router.get(
    "/digital-twin-fidelity",
    response_model=DigitalTwinFidelityOut,
    summary="Fidélité jumeau numérique (heuristique)",
    description=(
        "Indicateur 0–100 basé sur la densité de données utiles au calage : flux bilan, paramètres sim, "
        "MER (volumétrie + tags kW), runs récents, chaîne d’essais LIMS MIN-01a + BWi (`lims_a1` + `lims_b1`). "
        "Moyenne pondérée ; détail dans `weights` et `components`."
    ),
)
def get_digital_twin_fidelity(pid: str, user=Depends(project_user)):
    return compute_digital_twin_fidelity(pid)


@router.get(
    "/testwork-program",
    summary="Programme essais vs niveau d'étude (MetPlant / SLA)",
    description=(
        "Évalue la couverture LIMS (MIN, COM, LIX, FLT, variabilité) par rapport au statut "
        "projet (scoping / PFS / FS) selon SLA Table 1 et hiérarchie comminution MetPlant 2008."
    ),
)
def get_testwork_program(pid: str, user=Depends(project_user)):
    try:
        from ..db import qone as _qone
    except ImportError:
        from db import qone as _qone
    try:
        proj = _qone("SELECT status FROM projects WHERE id=%s", (pid,)) or {}
        return assess_testwork_program(pid, project_status=proj.get("status"))
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except Exception as e:
        logger.exception("testwork-program failed for %s", pid)
        raise HTTPException(500, detail="Programme essais indisponible") from e


@router.get(
    "/simulation-qa",
    summary="Checklist simulation (SLA best practices)",
    description=(
        "Cycle de vie modèle : PFD, essais, caractérisation alimentation, paramètres, "
        "validation bilan massique / runs, scénarios. Pré-run guard GIGO."
    ),
)
def get_simulation_qa(pid: str, user=Depends(project_user)):
    try:
        from ..db import qone as _qone
    except ImportError:
        from db import qone as _qone
    try:
        proj = _qone("SELECT status FROM projects WHERE id=%s", (pid,)) or {}
        return assess_simulation_qa(pid, project_status=proj.get("status"))
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except Exception as e:
        logger.exception("simulation-qa failed for %s", pid)
        return {
            "kind": "simulation_qa",
            "error": str(e)[:500],
            "score": 0,
            "can_run_rigorous": False,
            "stages": [],
            "testwork": {"score": 0, "gaps": [], "lims_counts": {}},
            "warnings": ["Évaluation QA simulation : erreur serveur — voir logs."],
            "blockers": ["server_error"],
        }
