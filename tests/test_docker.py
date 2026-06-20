import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_image_runs_as_non_root_with_healthcheck_and_migrations():
    dockerfile = (ROOT / "Dockerfile").read_text()
    entrypoint = ROOT / "scripts/docker-entrypoint"
    entrypoint_text = entrypoint.read_text()

    assert "FROM python:3.12-slim" in dockerfile
    assert "USER aria" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert 'ENTRYPOINT ["aria-entrypoint"]' in dockerfile
    assert os.access(entrypoint, os.X_OK)
    assert "alembic upgrade head" in entrypoint_text
    assert 'exec "$@"' in entrypoint_text


def test_compose_keeps_host_port_private_and_data_persistent():
    compose = (ROOT / "compose.yaml").read_text()

    assert '"127.0.0.1:${ARIA_PORT:-8000}:8000"' in compose
    assert "APICHECKER_HOST: 0.0.0.0" in compose
    assert "APICHECKER_SERVICE_MANAGER: disabled" in compose
    assert "sqlite:////app/data/apichecker.db" in compose
    assert "aria-data:/app/data" in compose
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose


def test_dockerignore_excludes_secrets_and_runtime_data():
    ignored = (ROOT / ".dockerignore").read_text().splitlines()

    assert ".env" in ignored
    assert ".venv" in ignored
    assert "data" in ignored
    assert "backups" in ignored
