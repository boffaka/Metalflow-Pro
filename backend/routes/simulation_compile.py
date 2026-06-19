"""Routes: /simulation-v2/compile and /simulation-v2/active-source.

These endpoints bridge the visual flowsheet (module Flowsheet) to the
simulation engine. Compile produces an immutable snapshot; active-source
tracks which flowsheet/scenario is the current basis for simulations.
"""
from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, Depends

try:
    from ..auth import project_user
    from ..db import qone, qall, execute, conn, release
    from ..engines.compile import compile_flowsheet
    from ..models import (
        CompileRequest, CompileResponse, ActiveSourceRequest, ActiveSourceResponse,
        RunByBranchesRequest,
        CompareSetCreateRequest, CompareSetResponse,
        CompareMatrixResponse, CompareDiffResponse,
        CustomFromFlowsheetRequest, CustomFromTemplateRequest, CustomBlankRequest,
        CustomScenarioResponse, ForkSuggestionResponse,
        ScenarioFlowsheetSummary, ScenarioFlowsheetListResponse,
    )
except ImportError:
    from auth import project_user
    from db import qone, qall, execute, conn, release
    from engines.compile import compile_flowsheet
    from models import (
        CompileRequest, CompileResponse, ActiveSourceRequest, ActiveSourceResponse,
        RunByBranchesRequest,
        CompareSetCreateRequest, CompareSetResponse,
        CompareMatrixResponse, CompareDiffResponse,
        CustomFromFlowsheetRequest, CustomFromTemplateRequest, CustomBlankRequest,
        CustomScenarioResponse, ForkSuggestionResponse,
        ScenarioFlowsheetSummary, ScenarioFlowsheetListResponse,
    )

import psycopg2.extras

router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["simulation-v2-compile"])
logger = logging.getLogger("mpdpms.simulation_compile")


# The column alias used for the JSONB extraction; qone returns dicts keyed by
# column name. Using an explicit alias keeps the access key stable across PG
# versions (otherwise the default key may be "?column?").
_ACTIVE_SOURCE_ALIAS = "active_source"


@router.post("/simulation-v2/compile", response_model=CompileResponse)
def compile_endpoint(pid: str, body: CompileRequest, user=Depends(project_user)):
    """Compile the flowsheet (or scenario_flowsheet) into a snapshot template."""
    try:
        result = compile_flowsheet(
            project_id=pid,
            source_type=body.source_type,
            source_id=body.source_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    return result


@router.get("/simulation-v2/active-source", response_model=ActiveSourceResponse)
def get_active_source(pid: str, user=Depends(project_user)):
    """Get the currently active source of truth for simulations.

    Defaults to the project's most recent flowsheet if nothing is set.
    Stored in projects.feature_flags JSONB under key 'sim_active_source'.
    """
    row = qone(
        f"SELECT feature_flags -> 'sim_active_source' AS {_ACTIVE_SOURCE_ALIAS} "
        "FROM projects WHERE id = %s",
        (pid,),
    )
    if row and row.get(_ACTIVE_SOURCE_ALIAS):
        raw = row[_ACTIVE_SOURCE_ALIAS]
        data = raw if isinstance(raw, dict) else json.loads(raw)
        if data.get("source_type") and data.get("source_id"):
            return ActiveSourceResponse(**data)

    # Fallback: latest flowsheet
    fs = qone(
        "SELECT id FROM flowsheets WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
        (pid,),
    )
    if not fs:
        raise HTTPException(404, "No flowsheet exists for this project")
    return ActiveSourceResponse(source_type="flowsheet", source_id=str(fs["id"]))


@router.post("/simulation-v2/active-source", response_model=ActiveSourceResponse)
def set_active_source(pid: str, body: ActiveSourceRequest, user=Depends(project_user)):
    """Persist the selected source in projects.feature_flags."""
    # Validate the source exists and belongs to the project
    if body.source_type == "flowsheet":
        row = qone(
            "SELECT id FROM flowsheets WHERE id = %s AND project_id = %s",
            (body.source_id, pid),
        )
    elif body.source_type == "scenario_flowsheet":
        row = qone(
            "SELECT sf.id FROM scenario_flowsheets sf "
            "JOIN project_scenarios ps ON ps.id = sf.scenario_id "
            "WHERE sf.id = %s AND ps.project_id = %s",
            (body.source_id, pid),
        )
    else:
        raise HTTPException(400, f"Invalid source_type: {body.source_type}")

    if not row:
        raise HTTPException(404, f"{body.source_type} not found for this project")

    payload = {"source_type": body.source_type, "source_id": body.source_id}
    execute(
        "UPDATE projects SET feature_flags = jsonb_set(COALESCE(feature_flags,'{}'::jsonb), "
        "'{sim_active_source}', %s::jsonb) WHERE id = %s",
        (json.dumps(payload), pid),
    )
    return ActiveSourceResponse(**payload)


def _import_simulate_section():
    """Dynamic import to match the pattern used in simulation_v2.py."""
    try:
        from ..engines.process_simulator import simulate_section
    except ImportError:
        from engines.process_simulator import simulate_section
    return simulate_section


def _coerce_json_list(value) -> list:
    """Return a list from a JSONB column which may be list, str, or None."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return json.loads(value or "[]")
    return list(value)


def _normalize_run_ids(raw) -> list[str]:
    """Normalize a Postgres UUID[] column value into a list of string UUIDs.

    psycopg2 may return this as a list of UUID objects, a list of strings,
    or (under some configurations) a curly-brace string like '{uuid1,uuid2}'.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{") and s.endswith("}"):
            inner = s[1:-1]
            return [p.strip('"') for p in inner.split(",") if p.strip()]
        return [s] if s else []
    return [str(r) for r in raw]


@router.post("/simulation-v2/run-by-branches")
def run_by_branches(pid: str, body: RunByBranchesRequest, user=Depends(project_user)):
    """Run simulation on one or more named branches of a compilation.

    Body: {compilation_id, branches: [name], feed_override?, params_override?, label?}
    """
    # 1. Load compilation; must belong to this project
    comp = qone(
        "SELECT template_id, branches_detected FROM circuit_compilations "
        "WHERE id = %s AND project_id = %s",
        (body.compilation_id, pid),
    )
    if not comp:
        raise HTTPException(404, "Compilation not found")

    template_id = str(comp["template_id"])
    branches = _coerce_json_list(comp.get("branches_detected"))
    branch_by_name = {b["name"]: b for b in branches if isinstance(b, dict) and "name" in b}

    # 2. Resolve op_codes from the requested branches (validation before simulate)
    op_codes: list[str] = []
    for name in body.branches:
        if name not in branch_by_name:
            raise HTTPException(400, f"Unknown branch '{name}' in this compilation")
        op_codes.extend(branch_by_name[name].get("op_codes", []))

    if not op_codes:
        raise HTTPException(400, "No operations resolved from requested branches")

    # 3. Run simulation — same orchestration pattern as run_by_sections
    simulate_section = _import_simulate_section()
    feed_override = body.feed_override.model_dump() if body.feed_override else None
    params_override = body.params_override

    db = conn()
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        try:
            result = simulate_section(
                pid, template_id, op_codes,
                feed_override=feed_override,
                params_override=params_override,
                cursor=cur,
            )
            result["mode"] = "multi_branch"

            run_id = result.get("run_id", str(uuid.uuid4()))
            cur.execute(
                "INSERT INTO simulation_runs_v2 "
                "(id, project_id, template_id, run_type, run_mode, ops_simulated, "
                " feed_source, feed_stream, product_stream, params, results, "
                " label, created_by, compilation_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (run_id, pid, template_id, "rigorous", "multi_branch",
                 json.dumps(result.get("ops_simulated") or op_codes),
                 result.get("feed_source"),
                 json.dumps(result.get("feed_stream"), default=str),
                 json.dumps(result.get("product_stream"), default=str),
                 json.dumps(params_override) if params_override else None,
                 json.dumps(result, default=str),
                 body.label,
                 user.get("id") if isinstance(user, dict) else None,
                 body.compilation_id),
            )
            db.commit()
            return {"run_id": run_id, "template_id": template_id, "compilation_id": body.compilation_id, **result}
        except HTTPException:
            db.rollback()
            raise
        except psycopg2.OperationalError:
            raise HTTPException(503, detail="Database temporarily unavailable")
        except psycopg2.IntegrityError as e:
            raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    finally:
        cur.close()
        release(db)


# =============================================================================
# Comparison (Plan 3) — compare 2..5 simulation_runs_v2 of the same project
# =============================================================================


def _extract_kpis(results_jsonb) -> dict:
    """Flatten a simulation_runs_v2.results JSONB into a normalized KPI dict."""
    if results_jsonb is None:
        return {"recovery": None, "energy": None, "capex": None, "opex": None, "score": None}
    data = results_jsonb if isinstance(results_jsonb, dict) else json.loads(results_jsonb)
    overall = data.get("overall") or {}
    # capex may be in musd, normalize to usd
    capex = overall.get("capex_usd")
    if capex is None:
        capex_musd = overall.get("capex_musd")
        if capex_musd is not None:
            try:
                capex = float(capex_musd) * 1e6
            except Exception:  # intentional: fallback to empty/default on optional data
                capex = None
    return {
        "recovery": overall.get("total_recovery_pct") or overall.get("recovery_pct"),
        "energy": overall.get("energy_kwh_t") or overall.get("total_energy_kwh_t"),
        "capex": capex,
        "opex": overall.get("opex_usd_t") or overall.get("opex_per_t"),
        "score": overall.get("overall_score") or data.get("overall_score"),
    }


@router.post("/simulation-v2/compare", status_code=201, response_model=CompareSetResponse)
def create_compare_set(pid: str, body: CompareSetCreateRequest, user=Depends(project_user)):
    """Create a comparison set from 2..5 existing simulation_runs_v2 rows.

    All runs must belong to this project. Returns the new set_id.
    """
    # Validate all runs exist and belong to this project
    rows = qall(
        "SELECT id::text AS id FROM simulation_runs_v2 WHERE id::text = ANY(%s) AND project_id = %s",
        (body.run_ids, pid),
    )
    found_ids = {r["id"] for r in rows}
    missing = [rid for rid in body.run_ids if rid not in found_ids]
    if missing:
        raise HTTPException(404, f"Runs not found in project: {missing}")

    set_id = str(uuid.uuid4())
    execute(
        "INSERT INTO simulation_comparison_sets (id, project_id, name, run_ids) "
        "VALUES (%s, %s, %s, %s::uuid[])",
        (set_id, pid, body.name, body.run_ids),
    )
    return {
        "set_id": set_id,
        "name": body.name,
        "run_ids": body.run_ids,
        "created_at": None,
    }


@router.get("/simulation-v2/compare/{set_id}", response_model=CompareMatrixResponse)
def get_compare_matrix(pid: str, set_id: str, user=Depends(project_user)):
    """Normalized KPI matrix for the runs in a comparison set."""
    cset = qone(
        "SELECT id::text, name, run_ids FROM simulation_comparison_sets "
        "WHERE id = %s AND project_id = %s",
        (set_id, pid),
    )
    if not cset:
        raise HTTPException(404, "Comparison set not found")

    run_ids_str = _normalize_run_ids(cset["run_ids"])

    rows = qall(
        "SELECT id::text AS run_id, label, results FROM simulation_runs_v2 "
        "WHERE id::text = ANY(%s) AND project_id = %s",
        (run_ids_str, pid),
    )
    by_id = {r["run_id"]: r for r in rows}

    runs_out = []
    for rid in run_ids_str:
        row = by_id.get(rid)
        if row is None:
            runs_out.append({"run_id": rid, "label": None, "kpis": {
                "recovery": None, "energy": None, "capex": None, "opex": None, "score": None,
            }})
            continue
        kpis = _extract_kpis(row.get("results"))
        runs_out.append({
            "run_id": rid,
            "label": row.get("label"),
            "kpis": kpis,
        })

    return {
        "set_id": set_id,
        "name": cset["name"],
        "runs": runs_out,
    }


@router.get("/simulation-v2/compare/{set_id}/diff", response_model=CompareDiffResponse)
def get_compare_diff(pid: str, set_id: str, user=Depends(project_user)):
    """Return pairwise diffs of ops_simulated across all runs in the set."""
    cset = qone(
        "SELECT id::text, run_ids FROM simulation_comparison_sets "
        "WHERE id = %s AND project_id = %s",
        (set_id, pid),
    )
    if not cset:
        raise HTTPException(404, "Comparison set not found")

    run_ids_str = _normalize_run_ids(cset["run_ids"])
    rows = qall(
        "SELECT id::text AS run_id, ops_simulated FROM simulation_runs_v2 "
        "WHERE id::text = ANY(%s) AND project_id = %s",
        (run_ids_str, pid),
    )
    ops_by_id: dict[str, list[str]] = {}
    for r in rows:
        ops_raw = r.get("ops_simulated")
        ops = _coerce_json_list(ops_raw)
        ops_by_id[r["run_id"]] = [str(o) for o in ops if o]

    added_pairs = []
    removed_pairs = []
    for i, from_id in enumerate(run_ids_str):
        for to_id in run_ids_str[i + 1:]:
            from_ops = set(ops_by_id.get(from_id, []))
            to_ops = set(ops_by_id.get(to_id, []))
            added_pairs.append({
                "from_run": from_id,
                "to_run": to_id,
                "ops_added": sorted(to_ops - from_ops),
                "ops_removed": [],
            })
            removed_pairs.append({
                "from_run": from_id,
                "to_run": to_id,
                "ops_added": [],
                "ops_removed": sorted(from_ops - to_ops),
            })

    return {
        "set_id": set_id,
        "ops_added_per_pair": added_pairs,
        "ops_removed_per_pair": removed_pairs,
    }


# =============================================================================
# Custom circuit scenarios (Plan 2) — three entry points + suggestion fork +
# "Mes scénarios" listing.
# =============================================================================

# In-code template library. Kept minimal (3 presets) as a stand-in for a real
# ``circuit_catalog_presets`` table — deferred until Plan 4.
_CUSTOM_TEMPLATE_LIBRARY: dict[str, dict] = {
    "sag_ball": {
        "label": "SAG + Ball mill",
        "blocks": [
            {"id": "feed", "op_code": "FEED", "enabled": True},
            {"id": "sag", "op_code": "SAG_MILL", "enabled": True},
            {"id": "bm", "op_code": "BALL_MILL", "enabled": True},
            {"id": "cil", "op_code": "CIL", "enabled": True},
            {"id": "prod", "op_code": "PRODUCT", "enabled": True},
        ],
        "connections": [
            {"from": "feed", "to": "sag"},
            {"from": "sag", "to": "bm"},
            {"from": "bm", "to": "cil"},
            {"from": "cil", "to": "prod"},
        ],
    },
    "hpgr_ball": {
        "label": "HPGR + Ball mill",
        "blocks": [
            {"id": "feed", "op_code": "FEED", "enabled": True},
            {"id": "hpgr", "op_code": "HPGR", "enabled": True},
            {"id": "bm", "op_code": "BALL_MILL", "enabled": True},
            {"id": "cil", "op_code": "CIL", "enabled": True},
            {"id": "prod", "op_code": "PRODUCT", "enabled": True},
        ],
        "connections": [
            {"from": "feed", "to": "hpgr"},
            {"from": "hpgr", "to": "bm"},
            {"from": "bm", "to": "cil"},
            {"from": "cil", "to": "prod"},
        ],
    },
    "heap_leach": {
        "label": "Heap leach (low-grade / high-tonnage)",
        "blocks": [
            {"id": "feed", "op_code": "FEED", "enabled": True},
            {"id": "crush", "op_code": "GIRATOIRE", "enabled": True},
            {"id": "heap", "op_code": "HEAP_LEACH", "enabled": True},
            {"id": "adr", "op_code": "CIP", "enabled": True},
            {"id": "prod", "op_code": "PRODUCT", "enabled": True},
        ],
        "connections": [
            {"from": "feed", "to": "crush"},
            {"from": "crush", "to": "heap"},
            {"from": "heap", "to": "adr"},
            {"from": "adr", "to": "prod"},
        ],
    },
}


def _create_scenario_row(pid: str, name: str, scenario_type: str, user) -> str:
    """Insert a project_scenarios row and return its UUID as string."""
    sid = str(uuid.uuid4())
    created_by = user.get("id") if isinstance(user, dict) else None
    execute(
        "INSERT INTO project_scenarios (id, project_id, scenario_name, scenario_type, created_by) "
        "VALUES (%s, %s, %s, %s, %s)",
        (sid, pid, name, scenario_type, created_by),
    )
    return sid


def _insert_scenario_flowsheet(
    scenario_id: str,
    blocks: list,
    connections: list,
    source_flowsheet_id: str | None,
) -> str:
    """Insert a scenario_flowsheets row and return its UUID as string."""
    sf_id = str(uuid.uuid4())
    execute(
        "INSERT INTO scenario_flowsheets (id, scenario_id, blocks, connections, source_flowsheet_id) "
        "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s)",
        (sf_id, scenario_id, json.dumps(blocks), json.dumps(connections),
         source_flowsheet_id),
    )
    return sf_id


def _fallback_name(prefix: str) -> str:
    """Generate a scenario name with a short suffix when none was provided."""
    return f"{prefix} — {uuid.uuid4().hex[:6]}"


@router.post(
    "/simulation-v2/custom/from-flowsheet",
    status_code=201,
    response_model=CustomScenarioResponse,
)
def custom_from_flowsheet(
    pid: str,
    body: CustomFromFlowsheetRequest,
    user=Depends(project_user),
):
    """Create a new scenario_flowsheet by copying the project's active flowsheet.

    The returned ``scenario_flowsheet_id`` is editable via the existing
    flowsheet-edit endpoints (``PUT /api/v1/projects/{pid}/scenarios/{sid}/flowsheet``
    or equivalent).
    """
    fs = qone(
        "SELECT id::text AS id, blocks, connections FROM flowsheets "
        "WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
        (pid,),
    )
    if not fs:
        raise HTTPException(404, "No flowsheet exists for this project")

    blocks = _coerce_json_list(fs.get("blocks"))
    connections = _coerce_json_list(fs.get("connections"))
    name = body.name or _fallback_name("Copie flowsheet projet")

    scenario_id = _create_scenario_row(pid, name, "flowsheet", user)
    sf_id = _insert_scenario_flowsheet(scenario_id, blocks, connections, fs["id"])
    return CustomScenarioResponse(
        scenario_flowsheet_id=sf_id,
        scenario_id=scenario_id,
        name=name,
    )


@router.post(
    "/simulation-v2/custom/from-template",
    status_code=201,
    response_model=CustomScenarioResponse,
)
def custom_from_template(
    pid: str,
    body: CustomFromTemplateRequest,
    user=Depends(project_user),
):
    """Create a new scenario_flowsheet from a preset template."""
    tpl = _CUSTOM_TEMPLATE_LIBRARY.get(body.template_name)
    if tpl is None:
        raise HTTPException(
            400,
            f"Unknown template '{body.template_name}'. Available: "
            f"{sorted(_CUSTOM_TEMPLATE_LIBRARY.keys())}",
        )
    # Ensure project exists (prevent orphan scenario rows).
    proj = qone("SELECT id FROM projects WHERE id = %s", (pid,))
    if not proj:
        raise HTTPException(404, "Project not found")

    name = body.name or _fallback_name(f"Template {tpl['label']}")
    scenario_id = _create_scenario_row(pid, name, "flowsheet", user)
    sf_id = _insert_scenario_flowsheet(
        scenario_id, tpl["blocks"], tpl["connections"], None,
    )
    return CustomScenarioResponse(
        scenario_flowsheet_id=sf_id,
        scenario_id=scenario_id,
        name=name,
    )


@router.post(
    "/simulation-v2/custom/blank",
    status_code=201,
    response_model=CustomScenarioResponse,
)
def custom_blank(
    pid: str,
    body: CustomBlankRequest,
    user=Depends(project_user),
):
    """Create an empty scenario_flowsheet (canvas blanc)."""
    proj = qone("SELECT id FROM projects WHERE id = %s", (pid,))
    if not proj:
        raise HTTPException(404, "Project not found")
    name = body.name or _fallback_name("Canvas blanc")
    scenario_id = _create_scenario_row(pid, name, "flowsheet", user)
    sf_id = _insert_scenario_flowsheet(scenario_id, [], [], None)
    return CustomScenarioResponse(
        scenario_flowsheet_id=sf_id,
        scenario_id=scenario_id,
        name=name,
    )


def _fork_blocks_with_ops_delta(
    base_blocks: list,
    base_connections: list,
    ops_to_add: list[str],
    ops_to_remove: list[str],
) -> tuple[list, list]:
    """Apply a suggestion's ops_to_add / ops_to_remove to a flowsheet copy.

    Strategy is intentionally conservative: we remove blocks whose ``op_code``
    matches an entry in ``ops_to_remove`` and append new blocks for each
    ``ops_to_add`` entry (disconnected — the user will wire them in the
    editor). Connections pointing to/from removed blocks are dropped.
    """
    remove_set = set(ops_to_remove or [])
    kept_ids: set[str] = set()
    new_blocks: list = []
    for b in base_blocks:
        if not isinstance(b, dict):
            continue
        if b.get("op_code") in remove_set:
            continue
        kept_ids.add(str(b.get("id")))
        new_blocks.append(dict(b))

    new_connections: list = []
    for c in base_connections:
        if not isinstance(c, dict):
            continue
        if str(c.get("from")) in kept_ids and str(c.get("to")) in kept_ids:
            new_connections.append(dict(c))

    for op in (ops_to_add or []):
        new_blocks.append({
            "id": f"added-{uuid.uuid4().hex[:8]}",
            "op_code": op,
            "enabled": True,
        })

    return new_blocks, new_connections


@router.post(
    "/simulation-v2/suggestions/{suggestion_id}/fork",
    status_code=201,
    response_model=ForkSuggestionResponse,
)
def fork_suggestion(
    pid: str,
    suggestion_id: str,
    user=Depends(project_user),
):
    """Fork a scenario suggestion into an editable scenario_flowsheet.

    The new flowsheet starts from the project's current flowsheet and applies
    the suggestion's ``ops_to_add`` / ``ops_to_remove`` delta. The user can
    refine it in the flowsheet editor.
    """
    sug = qone(
        "SELECT id::text, suggestion_id, title, ops_to_add, ops_to_remove "
        "FROM scenario_suggestions_log "
        "WHERE project_id = %s AND (id::text = %s OR suggestion_id = %s) "
        "ORDER BY created_at DESC LIMIT 1",
        (pid, suggestion_id, suggestion_id),
    )
    if not sug:
        raise HTTPException(404, f"Suggestion '{suggestion_id}' not found for this project")

    ops_to_add = list(sug.get("ops_to_add") or [])
    ops_to_remove = list(sug.get("ops_to_remove") or [])

    fs = qone(
        "SELECT id::text AS id, blocks, connections FROM flowsheets "
        "WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
        (pid,),
    )
    if not fs:
        # No current flowsheet — fall back to a blank scenario with the added ops only
        base_blocks: list = []
        base_connections: list = []
        source_fs_id: str | None = None
    else:
        base_blocks = _coerce_json_list(fs.get("blocks"))
        base_connections = _coerce_json_list(fs.get("connections"))
        source_fs_id = fs["id"]

    new_blocks, new_connections = _fork_blocks_with_ops_delta(
        base_blocks, base_connections, ops_to_add, ops_to_remove,
    )

    name = f"Fork: {sug.get('title') or suggestion_id}"
    scenario_id = _create_scenario_row(pid, name, "flowsheet", user)
    sf_id = _insert_scenario_flowsheet(
        scenario_id, new_blocks, new_connections, source_fs_id,
    )
    return ForkSuggestionResponse(
        scenario_flowsheet_id=sf_id,
        scenario_id=scenario_id,
        name=name,
        ops_added=ops_to_add,
        ops_removed=ops_to_remove,
    )


# =============================================================================
# Scenario listing (Plan 2) — "Mes scénarios" tab
# =============================================================================

@router.delete("/scenarios/flowsheets/{sf_id}")
def delete_scenario_flowsheet(pid: str, sf_id: str, user=Depends(project_user)):
    """Delete a scenario flowsheet and its parent scenario."""
    row = qone(
        "SELECT sf.id, sf.scenario_id "
        "FROM scenario_flowsheets sf "
        "JOIN project_scenarios ps ON ps.id = sf.scenario_id "
        "WHERE sf.id = %s AND ps.project_id = %s",
        (sf_id, pid),
    )
    if not row:
        raise HTTPException(404, "Scénario non trouvé")
    c = conn()
    try:
        cur = c.cursor()
        cur.execute("DELETE FROM scenario_flowsheets WHERE id = %s", (sf_id,))
        cur.execute("DELETE FROM project_scenarios WHERE id = %s", (row["scenario_id"],))
        c.commit()
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    finally:
        release(c)
    return {"ok": True}


@router.get(
    "/scenarios/flowsheets",
    response_model=ScenarioFlowsheetListResponse,
)
def list_scenario_flowsheets(pid: str, user=Depends(project_user)):
    """List scenario_flowsheets for the 'Mes scénarios' tab.

    Empty list when the project has no scenarios yet — no error.
    """
    rows = qall(
        "SELECT sf.id::text AS scenario_flowsheet_id, "
        "       sf.scenario_id::text AS scenario_id, "
        "       ps.scenario_name AS name, "
        "       sf.source_flowsheet_id::text AS source_flowsheet_id, "
        "       sf.blocks, sf.connections, "
        "       sf.created_at "
        "FROM scenario_flowsheets sf "
        "JOIN project_scenarios ps ON ps.id = sf.scenario_id "
        "WHERE ps.project_id = %s "
        "ORDER BY sf.created_at DESC",
        (pid,),
    )
    items = []
    for r in rows or []:
        blocks = _coerce_json_list(r.get("blocks"))
        connections = _coerce_json_list(r.get("connections"))
        created_at = r.get("created_at")
        items.append(ScenarioFlowsheetSummary(
            scenario_flowsheet_id=r["scenario_flowsheet_id"],
            scenario_id=r["scenario_id"],
            name=r.get("name") or "Scénario",
            source_flowsheet_id=r.get("source_flowsheet_id"),
            n_blocks=len(blocks),
            n_connections=len(connections),
            created_at=created_at.isoformat() if created_at is not None and hasattr(created_at, "isoformat") else (str(created_at) if created_at else None),
        ))
    return ScenarioFlowsheetListResponse(items=items)
