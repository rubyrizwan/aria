import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.database import Base
from app.main import (
    app,
    available_model_detail,
    available_model_groups,
    available_model_stats,
    available_models_page,
    INFERENCE_JOBS,
    test_model_across_providers as start_model_provider_test,
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


def add_provider(
    session: Session,
    name: str,
    model_id: str,
    *,
    latency_ms: float,
    status: str = "available",
    capabilities: dict | None = None,
    provider_type: str = "openai",
    checked_at: datetime | None = None,
) -> Account:
    account = Account(
        name=name,
        endpoint_url=f"https://{name.casefold()}.example.com/v1",
        api_key_label="Paid",
        encrypted_api_key=encrypt_secret(f"{name}-secret"),
        provider_type=provider_type,
        models_json=json.dumps(
            [
                {
                    "id": model_id,
                    "capabilities": capabilities or {},
                }
            ]
        ),
        last_status="healthy",
    )
    session.add(account)
    session.flush()
    session.add(
        ModelInferenceResult(
            account_id=account.id,
            model_id=model_id,
            status=status,
            http_status=200,
            latency_ms=latency_ms,
            checked_at=checked_at or datetime.now(timezone.utc),
        )
    )
    return account


def test_available_models_are_grouped_sorted_and_ignore_stale_results():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        add_provider(session, "Zulu", "GPT-B", latency_ms=200)
        add_provider(session, "Alpha", "gpt-a", latency_ms=100)
        add_provider(session, "Beta", "GPT-A", latency_ms=300)
        stale = add_provider(session, "Stale", "removed-model", latency_ms=10)
        stale.models_json = "[]"
        add_provider(
            session,
            "Unavailable",
            "failed-model",
            latency_ms=20,
            status="forbidden",
        )
        session.commit()

        groups = available_model_groups(session)

    assert [group["model_id"].casefold() for group in groups] == ["gpt-a", "gpt-b"]
    assert groups[0]["provider_count"] == 2
    assert groups[0]["providers"] == ["Alpha", "Beta"]
    assert groups[0]["average_latency_ms"] == 200
    assert groups[0]["best_latency_ms"] == 100
    assert groups[0]["worst_latency_ms"] == 300
    assert groups[0]["fastest_provider"] == "Alpha"


def test_available_models_page_supports_search_and_detail_modal():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        account = add_provider(session, "Provider", "vision-model", latency_ms=125)
        account.notes = "Primary key"
        session.commit()

        response = available_models_page(
            request_for("/models"),
            session,
            q="VISION",
        )
        detail = available_model_detail("vision-model", session)

    assert b"vision-model" in response.body
    assert b'data-model-detail-url="/models/detail?model_id=vision-model"' in response.body
    assert b"1 total" in response.body
    payload = json.loads(detail.body)
    assert payload["model_id"] == "vision-model"
    assert payload["providers"][0]["provider"] == "Provider"
    assert payload["providers"][0]["api_key"] == "Provider-secret"
    assert payload["providers"][0]["latency_ms"] == 125
    assert payload["providers"][0]["notes"] == "Primary key"
    assert payload["openai_config"]["model"] == "vision-model"
    assert detail.headers["cache-control"] == "no-store"


def test_available_models_navigation_is_present():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        response = available_models_page(request_for("/models"), session)
    assert b"Available Models" in response.body


def test_available_model_filters_sorting_stats_and_capabilities():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine, expire_on_commit=False) as session:
        alpha = add_provider(
            session,
            "Alpha",
            "vision-model",
            latency_ms=700,
            capabilities={"vision": "provider"},
            checked_at=now,
        )
        add_provider(
            session,
            "Beta",
            "vision-model",
            latency_ms=300,
            capabilities={"tools": "provider"},
            checked_at=now,
        )
        add_provider(
            session,
            "Old",
            "old-model",
            latency_ms=1200,
            provider_type="anthropic",
            checked_at=now - timedelta(days=10),
        )
        session.commit()

        filtered = available_model_groups(
            session,
            provider_id=alpha.id,
            capability="vision",
            availability="multiple",
            latency="under_500",
            freshness="fresh",
            sort="latency_asc",
        )
        stats = available_model_stats(available_model_groups(session))

    assert [group["model_id"] for group in filtered] == ["vision-model"]
    assert filtered[0]["capabilities"] == {
        "vision": "provider",
        "tools": "provider",
    }
    assert filtered[0]["freshness"] == "fresh"
    assert stats["models"] == 2
    assert stats["providers"] == 3
    assert stats["multi_provider"] == 1


@pytest.mark.asyncio
async def test_model_retest_starts_background_job(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        add_provider(session, "Provider", "model-a", latency_ms=100)
        session.commit()

        def fake_create_task(coro, **_kwargs):
            coro.close()
            return object()

        monkeypatch.setattr("app.main.asyncio.create_task", fake_create_task)
        response = await start_model_provider_test(
            request_for(
                "/models/test",
                method="POST",
                headers=[(b"accept", b"application/json")],
            ),
            session,
            "model-a",
        )
        payload = json.loads(response.body)

    try:
        assert response.status_code == 202
        assert payload["progress_url"].startswith("/models/test/")
        assert INFERENCE_JOBS[payload["job_id"]]["total"] == 1
    finally:
        INFERENCE_JOBS.pop(payload["job_id"], None)
