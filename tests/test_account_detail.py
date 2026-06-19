import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.database import Base
from app.main import (
    INFERENCE_JOBS,
    account_api_key,
    account_detail,
    app,
    delete_account,
    inference_job_payload,
    inference_job_status,
    test_account_models as start_account_model_tests,
)
from app.models import Account, ModelInferenceResult
from app.security import encrypt_secret


def request_for(
    path: str,
    method: str = "GET",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    return Request(
        {
            "type": "http",
            "app": app,
            "method": method,
            "path": path,
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": headers or [],
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 1),
        }
    )


def test_account_detail_shows_notes_and_header_delete():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        account = Account(
            name="Provider",
            endpoint_url="https://example.com",
            notes="Internal provider note",
            encrypted_api_key=encrypt_secret("key"),
        )
        session.add(account)
        session.commit()

        response = account_detail(
            request_for(f"/accounts/{account.id}"), account.id, session
        )

    assert b"Internal provider note" in response.body
    assert b'data-confirm-title="Delete Provider?"' in response.body
    assert b"Type Provider" not in response.body
    assert b'data-reveal-account-key="/accounts/' in response.body
    assert b'type="password"' in response.body
    assert b"Display name" not in response.body
    assert b"<th>Owner</th>" not in response.body
    assert b"data-inference-overlay" in response.body
    assert b"Load models" in response.body
    assert b"Check now" not in response.body
    assert b"Monitoring" in response.body
    assert b"Active" in response.body
    assert b"Every 60 minutes" in response.body
    assert b"Provider check" in response.body
    assert b'<button class="button orange" disabled' in response.body
    template = (
        Path(__file__).resolve().parents[1] / "app/templates/account_detail.html"
    ).read_text()
    models_section = template.split('<section class="section">', 2)[1]
    assert "<th>Display name</th>" not in models_section
    assert "<th>Owner</th>" not in models_section
    assert "<th>Latency</th>" not in models_section


def test_account_api_key_reveals_secret_without_cache():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        account = Account(
            name="Provider",
            endpoint_url="https://example.com",
            encrypted_api_key=encrypt_secret("secret-key"),
        )
        session.add(account)
        session.commit()

        response = account_api_key(account.id, session)

    assert json.loads(response.body) == {"api_key": "secret-key"}
    assert response.headers["cache-control"] == "no-store"


def test_delete_account_does_not_require_name_confirmation():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        account = Account(
            name="Provider",
            endpoint_url="https://example.com",
            encrypted_api_key=encrypt_secret("key"),
        )
        session.add(account)
        session.commit()
        account_id = account.id

        response = delete_account(account_id, session)

        assert response.status_code == 303
        assert session.get(Account, account_id) is None


def test_inference_job_payload_hides_internal_task():
    payload = inference_job_payload(
        {
            "id": "job",
            "status": "running",
            "total": 2,
            "task": object(),
        }
    )
    assert payload == {"id": "job", "status": "running", "total": 2}


def test_inference_job_status_is_scoped_to_provider():
    INFERENCE_JOBS["job-test"] = {
        "id": "job-test",
        "account_id": 4,
        "status": "completed",
        "total": 1,
        "completed": 1,
        "models": ["model-a"],
        "summary": {"available": 1},
        "logs": [],
        "error": None,
    }
    try:
        response = inference_job_status(4, "job-test")
        assert json.loads(response.body)["summary"] == {"available": 1}
    finally:
        INFERENCE_JOBS.pop("job-test", None)


@pytest.mark.asyncio
async def test_start_model_tests_returns_background_job(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        account = Account(
            name="Provider",
            endpoint_url="https://example.com",
            encrypted_api_key=encrypt_secret("key"),
            models_json='["model-a", "model-b"]',
        )
        session.add(account)
        session.commit()

        def fake_create_task(coro, **_kwargs):
            coro.close()
            return object()

        monkeypatch.setattr("app.main.asyncio.create_task", fake_create_task)
        response = await start_account_model_tests(
            request_for(
                f"/accounts/{account.id}/test-models",
                method="POST",
                headers=[(b"accept", b"application/json")],
            ),
            account.id,
            session,
        )
        payload = json.loads(response.body)

    try:
        assert response.status_code == 202
        assert payload["job_id"]
        assert payload["progress_url"].endswith(payload["job_id"])
        assert INFERENCE_JOBS[payload["job_id"]]["total"] == 2
    finally:
        INFERENCE_JOBS.pop(payload["job_id"], None)


@pytest.mark.asyncio
async def test_native_model_test_submit_redirects_instead_of_showing_json(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        account = Account(
            name="Provider",
            endpoint_url="https://example.com",
            encrypted_api_key=encrypt_secret("key"),
            models_json='["model-a"]',
        )
        session.add(account)
        session.commit()

        def fake_create_task(coro, **_kwargs):
            coro.close()
            return object()

        monkeypatch.setattr("app.main.asyncio.create_task", fake_create_task)
        response = await start_account_model_tests(
            request_for(
                f"/accounts/{account.id}/test-models",
                method="POST",
                headers=[(b"accept", b"text/html")],
            ),
            account.id,
            session,
        )

    job_id = next(
        job_id
        for job_id, job in INFERENCE_JOBS.items()
        if job["account_id"] == account.id
    )
    try:
        assert response.status_code == 303
        assert response.headers["location"] == f"/accounts/{account.id}"
    finally:
        INFERENCE_JOBS.pop(job_id, None)


def test_model_filters_and_inference_summary():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        account = Account(
            name="Provider",
            endpoint_url="https://example.com",
            encrypted_api_key=encrypt_secret("key"),
            models_json=json.dumps(
                [
                    {
                        "id": "vision-model",
                        "display_name": "Vision",
                        "capabilities": {"vision": "provider"},
                    },
                    {
                        "id": "reasoning-model",
                        "display_name": "Reasoning",
                        "capabilities": {"reasoning": "provider"},
                    },
                    {"id": "plain-model", "display_name": "Plain"},
                ]
            ),
        )
        session.add(account)
        session.commit()
        session.add_all(
            [
                ModelInferenceResult(
                    account_id=account.id,
                    model_id="vision-model",
                    status="available",
                ),
                ModelInferenceResult(
                    account_id=account.id,
                    model_id="reasoning-model",
                    status="forbidden",
                ),
            ]
        )
        session.commit()

        response = account_detail(
            request_for(f"/accounts/{account.id}"),
            account.id,
            session,
            capability="vision",
            inference_access="available",
        )

    assert b"vision-model" in response.body
    assert b"reasoning-model" not in response.body
    assert b"plain-model" not in response.body
    assert b"Available <strong>1</strong>" in response.body
    assert b"Forbidden <strong>1</strong>" in response.body
    assert b"Not tested <strong>1</strong>" in response.body
    assert b"button orange" in response.body
    assert b'<button class="button orange" disabled' not in response.body
