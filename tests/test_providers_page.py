from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.database import Base
from app.main import (
    accounts_page,
    app,
    bulk_account_action,
    provider_catalog_rows,
    provider_catalog_stats,
)
from app.models import Account, ModelInferenceResult
from app.security import encrypt_secret


def request_for(
    path: str,
    method: str = "GET",
    form: list[tuple[str, str]] | None = None,
) -> Request:
    body = urlencode(form or []).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "app": app,
            "method": method,
            "path": path,
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
                (b"content-length", str(len(body)).encode()),
            ],
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 1),
        },
        receive,
    )


def add_provider(
    session: Session,
    name: str,
    *,
    status: str = "healthy",
    enabled: bool = True,
    models: list[str] | None = None,
    available: int = 0,
    latency: float | None = None,
    checked_at: datetime | None = None,
) -> Account:
    account = Account(
        name=name,
        endpoint_url=f"https://{name.casefold()}.example.com/v1",
        encrypted_api_key=encrypt_secret("key"),
        provider_type="openai",
        models_json=str(models or []).replace("'", '"'),
        last_status=status,
        enabled=enabled,
        last_inference_latency_ms=latency,
        last_checked_at=checked_at,
    )
    session.add(account)
    session.flush()
    for model_id in (models or [])[:available]:
        session.add(
            ModelInferenceResult(
                account_id=account.id,
                model_id=model_id,
                status="available",
                latency_ms=latency,
            )
        )
    return account


def test_provider_catalog_aggregates_stats_and_freshness():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine, expire_on_commit=False) as session:
        add_provider(
            session,
            "Healthy",
            models=["a", "b"],
            available=1,
            latency=100,
            checked_at=now,
        )
        add_provider(session, "Down", status="down", checked_at=now - timedelta(days=2))
        add_provider(session, "Disabled", enabled=False)
        session.commit()

        rows = provider_catalog_rows(session)
        stats = provider_catalog_stats(rows)

    assert stats == {
        "total": 3,
        "healthy": 1,
        "down": 1,
        "pending": 0,
        "disabled": 1,
        "without_models": 2,
    }
    healthy = next(row for row in rows if row["account"].name == "Healthy")
    disabled = next(row for row in rows if row["account"].name == "Disabled")
    assert healthy["available_count"] == 1
    assert healthy["freshness"] == "fresh"
    assert disabled["freshness"] == "never"


def test_provider_page_filters_and_renders_bulk_controls():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        add_provider(session, "Alpha", models=["a"], available=1, latency=100)
        add_provider(session, "Beta", models=[])
        session.commit()

        response = accounts_page(
            request_for("/accounts"),
            session,
            models="with",
            inference="available",
            sort="latency_asc",
        )

    assert b"Alpha" in response.body
    assert b"Beta" not in response.body
    assert b"Bulk action" in response.body
    assert b"Most available" in response.body
    assert b"data-select-all" in response.body
    assert b"data-provider-actions" in response.body
    assert b'data-provider-model-count="1"' in response.body


@pytest.mark.asyncio
async def test_bulk_enable_and_delete_providers():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        first = add_provider(session, "First", enabled=False)
        second = add_provider(session, "Second", enabled=False)
        session.commit()

        response = await bulk_account_action(
            request_for(
                "/accounts/bulk",
                method="POST",
                form=[
                    ("bulk_action", "enable"),
                    ("account_ids", str(first.id)),
                    ("account_ids", str(second.id)),
                ],
            ),
            session,
        )
        assert response.status_code == 303
        assert first.enabled is True
        assert second.enabled is True

        await bulk_account_action(
            request_for(
                "/accounts/bulk",
                method="POST",
                form=[
                    ("bulk_action", "delete"),
                    ("account_ids", str(first.id)),
                ],
            ),
            session,
        )
        assert session.get(Account, first.id) is None
        assert session.get(Account, second.id) is not None
