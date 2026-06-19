"""Verify docker-compose.yml declares all required services and images."""
from pathlib import Path

import yaml

# Tests run with cwd=backend/; compose lives at repository root.
_COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def load_compose():
    with open(_COMPOSE_FILE) as f:
        return yaml.safe_load(f)

def test_has_five_services():
    cfg = load_compose()
    svc = cfg["services"]
    required = {"postgres", "redis", "api", "worker", "frontend"}
    assert required <= set(svc.keys()), \
        f"Missing services: {required - set(svc.keys())}"

def test_postgres_uses_timescaledb():
    cfg = load_compose()
    img = cfg["services"]["postgres"]["image"]
    assert "timescaledb" in img, f"Expected timescaledb image, got: {img}"

def test_redis_service_defined():
    cfg = load_compose()
    redis_svc = cfg["services"]["redis"]
    assert "image" in redis_svc

def test_worker_uses_celery_command():
    cfg = load_compose()
    cmd = cfg["services"]["worker"].get("command", "")
    assert "celery" in str(cmd)

def test_frontend_service_defined():
    cfg = load_compose()
    assert "build" in cfg["services"]["frontend"] or "image" in cfg["services"]["frontend"]
