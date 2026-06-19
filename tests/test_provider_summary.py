import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.main import account_summary
from app.models import Account
from app.security import encrypt_secret


def test_provider_summary_reveals_key_without_cache():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        account = Account(
            name="Provider",
            endpoint_url="https://example.com/v1",
            notes="Paid account",
            api_key_label="Paid",
            encrypted_api_key=encrypt_secret("secret-key"),
            provider_type="openai",
            models_json='["model-a", "model-b"]',
            last_status="healthy",
        )
        session.add(account)
        session.commit()

        response = account_summary(account.id, session)
        payload = json.loads(response.body)

    assert payload == {
        "id": account.id,
        "name": "Provider",
        "base_url": "https://example.com/v1",
        "api_key_label": "Paid",
        "api_key": "secret-key",
        "notes": "Paid account",
        "model_count": 2,
        "compatibility": "openai",
        "status": "healthy",
        "monitoring": "enabled",
        "interval_minutes": 60,
        "last_checked": "Never",
        "last_latency_ms": None,
        "inference_summary": {},
    }
    assert response.headers["cache-control"] == "no-store"


def test_provider_summary_returns_not_found():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        with pytest.raises(HTTPException) as exc:
            account_summary(999, session)
    assert exc.value.status_code == 404
