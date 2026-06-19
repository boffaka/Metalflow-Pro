"""
Shared pytest fixtures for MetalFlow Pro integration tests.
Uses Kokoya Gold Mine (Liberia PFS) as the realistic test project.

Requires TEST_DATABASE_URL env var. If absent, integration tests are skipped.
"""
from __future__ import annotations

import os
import pytest

TEST_DB_URL = os.getenv("TEST_DATABASE_URL")


def pytest_configure(config):
    """Register custom markers so tests can opt out of the global DB skip."""
    config.addinivalue_line(
        "markers",
        "no_db: pure unit test that does not need TEST_DATABASE_URL — runs even when "
        "the integration suite is skipped",
    )


def pytest_collection_modifyitems(config, items):
    """Skip integration tests cleanly when no test DB is configured.

    Tests marked `@pytest.mark.no_db` (or carrying a module-level
    `pytestmark = pytest.mark.no_db`) are spared because they mock all DB
    boundaries and need no live database.
    """
    if TEST_DB_URL:
        return
    skip_marker = pytest.mark.skip(reason="TEST_DATABASE_URL not set; skipping integration tests")
    for item in items:
        if "no_db" in item.keywords:
            continue
        item.add_marker(skip_marker)

# Set test environment before any app imports (from audit/security fixtures)
os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-production")
os.environ.setdefault("DATABASE_URL", os.getenv("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/mpdpms_test"))
os.environ.setdefault("ADMIN_EMAIL", "admin@test.dev")
os.environ.setdefault("ADMIN_PASSWORD", "TestPassword123!")

if TEST_DB_URL:
    os.environ.setdefault("DATABASE_URL", TEST_DB_URL)
    os.environ.setdefault("JWT_SECRET", "kokoya_test_secret_that_is_definitely_long_enough_32chars")
    os.environ.setdefault("ADMIN_PASSWORD", "KokoyaTest1!")
    os.environ.setdefault("AUTO_MIGRATE", "1")

    try:
        from fastapi.testclient import TestClient
        try:
            from backend.main import app
        except ImportError:
            import sys
            sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
            from main import app

        @pytest.fixture(scope="session")
        def client():
            with TestClient(app) as c:
                yield c

        @pytest.fixture(scope="session")
        def auth_token(client):
            r = client.post("/api/v1/auth/login", json={
                "email": os.getenv("ADMIN_EMAIL", "admin@example.com"),
                "password": os.getenv("ADMIN_PASSWORD", "KokoyaTest1!"),
            })
            assert r.status_code == 200, f"Login failed: {r.text}"
            return r.json()["access_token"]

        @pytest.fixture(scope="session")
        def auth_headers(auth_token):
            return {"Authorization": f"Bearer {auth_token}"}

        # Alias so tests can use either name
        pm_headers = auth_headers

        @pytest.fixture(scope="session")
        def admin_token(client):
            """Get a valid JWT for the admin user."""
            resp = client.post("/api/v1/auth/login", json={
                "email": os.environ["ADMIN_EMAIL"],
                "password": os.environ["ADMIN_PASSWORD"],
            })
            assert resp.status_code == 200, f"Admin login failed: {resp.text}"
            return resp.json()["access_token"]

        @pytest.fixture(scope="session")
        def admin_headers(admin_token):
            return {"Authorization": f"Bearer {admin_token}"}

        @pytest.fixture(autouse=True)
        def reset_rate_limiter():
            """Clear the in-memory rate limiter between tests."""
            yield
            try:
                from main import _login_attempts
                _login_attempts.clear()
            except ImportError:
                pass

        @pytest.fixture(scope="session")
        def test_project_id(client, auth_headers):
            """Create Kokoya Gold Mine project as the integration test fixture."""
            r = client.post("/api/v1/projects", json={
                "project_name": "Kokoya Gold Mine",
                "project_code": "KGM-PFS-001",
                "target_tph": 1517,
                "gold_grade_g_t": 1.5,
                "status": "PFS",
                "location": "Liberia",
                "commodity": "Gold",
                "process_options": "Crushing+HPGR+BallMill+Flotation+IsaMill+CIP",
                "capacity_mtpa": round(1517 * 22.08 * 0.92 * 365 / 1e6, 2),
                "operating_hours_day": 22.08,
                "availability_pct": 92,
            }, headers=auth_headers)
            assert r.status_code == 201, f"Project creation failed: {r.text}"
            pid = r.json()["id"]
            yield pid
            client.delete(f"/api/v1/projects/{pid}", headers=auth_headers)

        @pytest.fixture(scope="module")
        def test_equipment_id(client, auth_headers, test_project_id):
            resp = client.post(
                f"/api/v1/projects/{test_project_id}/equipment",
                json={"equipment_tag": "EQ-TEST-001", "equipment_type": "Crusher"},
                headers=auth_headers,
            )
            assert resp.status_code in (200, 201)
            return resp.json()["id"]

        @pytest.fixture(scope="session")
        def _create_role_user(client, auth_headers):
            """Factory for creating test users with specific roles."""
            created = []
            def _factory(role: str, email: str):
                r = client.post("/api/v1/admin/users", json={
                    "email": email,
                    "password": "KokoyaTest1!",
                    "role": role,
                    "full_name": f"Test {role}",
                }, headers=auth_headers)
                if r.status_code == 201:
                    created.append(r.json()["id"])
                return r.json()
            yield _factory
            for uid in created:
                client.delete(f"/api/v1/admin/users/{uid}", headers=auth_headers)

        @pytest.fixture(scope="session")
        def readonly_headers(client, _create_role_user):
            _create_role_user("Read-only", "readonly@kokoya.test")
            r = client.post("/api/v1/auth/login", json={
                "email": "readonly@kokoya.test", "password": "KokoyaTest1!"
            })
            return {"Authorization": f"Bearer {r.json()['access_token']}"}

        @pytest.fixture(scope="session")
        def metallurgist_headers(client, _create_role_user):
            _create_role_user("Metallurgist", "metallurgist@kokoya.test")
            r = client.post("/api/v1/auth/login", json={
                "email": "metallurgist@kokoya.test", "password": "KokoyaTest1!"
            })
            return {"Authorization": f"Bearer {r.json()['access_token']}"}

        # ------------------------------------------------------------------
        # Simulation v3 shared fixtures (Chunk 2 Task 6bis)
        # ------------------------------------------------------------------
        import json
        import uuid
        from db import execute, qall


        @pytest.fixture
        def db_setup():
            """Placeholder dependency — exists so tests can declare 'db_setup' and get
            a clean lifecycle guarantee (no-op for now; real cleanup below in created
            fixtures via try/finally or autouse cleanup if needed)."""
            yield


        @pytest.fixture
        def seeded_simple_project(db_setup):
            """Create a project + one active circuit_template + DC v2 + linear flowsheet.

            Yields dict(project_id, flowsheet_id, template_id). Cleans up via
            ON DELETE CASCADE (projects FK cascades everywhere).
            """
            pid = str(uuid.uuid4())
            tpl_id = str(uuid.uuid4())
            fs_id = str(uuid.uuid4())
            try:
                execute(
                    "INSERT INTO projects (id, project_name, project_code) VALUES (%s, %s, %s)",
                    (pid, f"Test-{pid[:6]}", f"TST-{pid[:6]}"),
                )
                # Ensure unit_operations_catalog rows
                for op_code, cat in (("FEED", "feed"), ("HPGR", "comminution"),
                                      ("BALL_MILL", "comminution"), ("CIL", "leaching"),
                                      ("PRODUCT", "product")):
                    execute(
                        "INSERT INTO unit_operations_catalog (op_code, category, label) "
                        "VALUES (%s, %s, %s) ON CONFLICT (op_code) DO NOTHING",
                        (op_code, cat, op_code.replace("_", " ").title()),
                    )
                execute(
                    "INSERT INTO circuit_templates (id, project_id, name, is_active) VALUES (%s, %s, %s, TRUE)",
                    (tpl_id, pid, "main-template"),
                )
                for op in ("HPGR", "BALL_MILL", "CIL"):
                    execute(
                        "INSERT INTO circuit_operations (template_id, op_code, enabled) VALUES (%s, %s, TRUE) "
                        "ON CONFLICT (template_id, op_code) DO NOTHING",
                        (tpl_id, op),
                    )
                execute(
                    """INSERT INTO design_criteria_v2
                       (project_id, template_id, op_code, ref_number, item, design_value)
                       VALUES (%s, %s, 'HPGR', 'DC-001', 'Specific energy', 2.5)""",
                    (pid, tpl_id),
                )
                blocks = [
                    {"id": "b1", "op_code": "HPGR", "enabled": True},
                    {"id": "b2", "op_code": "BALL_MILL", "enabled": True},
                    {"id": "b3", "op_code": "CIL", "enabled": True},
                ]
                connections = [{"from": "b1", "to": "b2"}, {"from": "b2", "to": "b3"}]
                execute(
                    "INSERT INTO flowsheets (id, project_id, blocks, connections) "
                    "VALUES (%s, %s, %s::jsonb, %s::jsonb)",
                    (fs_id, pid, json.dumps(blocks), json.dumps(connections)),
                )
                yield {"project_id": pid, "flowsheet_id": fs_id, "template_id": tpl_id}
            finally:
                execute("DELETE FROM projects WHERE id = %s", (pid,))


        @pytest.fixture
        def seeded_project_with_branch(db_setup):
            """Same pattern but with a gravity side-loop (branch) in the flowsheet."""
            pid = str(uuid.uuid4())
            tpl_id = str(uuid.uuid4())
            fs_id = str(uuid.uuid4())
            try:
                execute(
                    "INSERT INTO projects (id, project_name, project_code) VALUES (%s, %s, %s)",
                    (pid, f"Test-{pid[:6]}", f"TST-{pid[:6]}"),
                )
                for op_code, cat in (("FEED", "feed"), ("BALL_MILL", "comminution"),
                                      ("GRAVITY_CONCENTRATOR", "gravity"), ("REGRIND_MILL", "comminution"),
                                      ("CIL", "leaching"), ("PRODUCT", "product")):
                    execute(
                        "INSERT INTO unit_operations_catalog (op_code, category, label) "
                        "VALUES (%s, %s, %s) ON CONFLICT (op_code) DO NOTHING",
                        (op_code, cat, op_code.replace("_", " ").title()),
                    )
                execute(
                    "INSERT INTO circuit_templates (id, project_id, name, is_active) VALUES (%s, %s, %s, TRUE)",
                    (tpl_id, pid, "main-template"),
                )
                for op in ("BALL_MILL", "GRAVITY_CONCENTRATOR", "REGRIND_MILL", "CIL"):
                    execute(
                        "INSERT INTO circuit_operations (template_id, op_code, enabled) VALUES (%s, %s, TRUE) "
                        "ON CONFLICT (template_id, op_code) DO NOTHING",
                        (tpl_id, op),
                    )
                blocks = [
                    {"id": "feed", "op_code": "FEED", "enabled": True},
                    {"id": "bm", "op_code": "BALL_MILL", "enabled": True},
                    {"id": "grav", "op_code": "GRAVITY_CONCENTRATOR", "enabled": True, "branch_label": "gravity-primary"},
                    {"id": "regrind", "op_code": "REGRIND_MILL", "enabled": True, "branch_label": "gravity-primary"},
                    {"id": "cil", "op_code": "CIL", "enabled": True},
                    {"id": "prod", "op_code": "PRODUCT", "enabled": True},
                ]
                connections = [
                    {"from": "feed", "to": "bm"},
                    {"from": "bm", "to": "cil"},
                    {"from": "bm", "to": "grav"},
                    {"from": "grav", "to": "regrind"},
                    {"from": "regrind", "to": "cil"},
                    {"from": "cil", "to": "prod"},
                ]
                execute(
                    "INSERT INTO flowsheets (id, project_id, blocks, connections) "
                    "VALUES (%s, %s, %s::jsonb, %s::jsonb)",
                    (fs_id, pid, json.dumps(blocks), json.dumps(connections)),
                )
                yield {"project_id": pid, "flowsheet_id": fs_id, "template_id": tpl_id}
            finally:
                execute("DELETE FROM projects WHERE id = %s", (pid,))

        # ------------------------------------------------------------------
        # Simulation v4 shared fixtures (Chunk 1 Task 1.3)
        # ------------------------------------------------------------------

        @pytest.fixture
        def seeded_project(test_project_id, auth_headers):
            """Wrap the existing test_project_id as a dict for v4 tests.

            Carries auth_headers so dependent fixtures can authenticate
            without each test having to declare auth_headers explicitly.
            """
            return {"id": test_project_id, "_headers": auth_headers}

        @pytest.fixture
        def seeded_node(client, seeded_project):
            # Ensure a flowsheet exists, then add a single root node
            h = seeded_project["_headers"]
            client.post(f"/api/v1/projects/{seeded_project['id']}/flowsheet", headers=h)
            res = client.post(
                f"/api/v1/projects/{seeded_project['id']}/flowsheet/operations",
                json={"op_code": "FEED", "node_label": "Test feed", "throughput_tph": 100.0},
                headers=h,
            )
            assert res.status_code == 201, res.text
            return res.json()


        @pytest.fixture
        def seeded_bullion_node(client, seeded_project, seeded_node):
            """A second node, child of seeded_node, marked as bullion. Used by tests
            that need plant-level metrics written under the bullion leaf op."""
            h = seeded_project["_headers"]
            res = client.post(
                f"/api/v1/projects/{seeded_project['id']}/flowsheet/operations",
                json={"op_code": "BULLION", "parent_op_id": seeded_node["id"],
                      "product_kind": "bullion", "node_label": "Test bullion"},
                headers=h,
            )
            assert res.status_code == 201, res.text
            return res.json()


        @pytest.fixture
        def seeded_run(seeded_project):
            """Insert a minimal simulation_runs_v2 row directly via the helper.

            Note: simulation_runs_v2 has no `status` column in the current
            schema; run_type defaults to 'rigorous' and is sufficient here.
            """
            row = execute(
                "INSERT INTO simulation_runs_v2 (project_id, params) "
                "VALUES (%s, %s::jsonb) RETURNING id",
                (seeded_project["id"], "{}"),
            )
            return row

    except Exception:
        pass  # No DB — all integration tests will be skipped
