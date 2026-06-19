from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Account
from app.checker import InferenceResult
from app.scheduler import CheckScheduler
from app.security import encrypt_secret
from app.services import get_app_preferences, save_app_preferences, set_auto_monitoring


async def test_scheduler_skips_due_accounts_when_globally_disabled(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with Session(engine) as session:
        session.add(
            Account(
                name="Disabled globally",
                endpoint_url="https://example.com",
                encrypted_api_key=encrypt_secret(""),
                interval_minutes=5,
                enabled=True,
            )
        )
        session.commit()
        set_auto_monitoring(session, False)

    scheduler = CheckScheduler()
    called = []

    async def fake_check(account_id):
        called.append(account_id)

    monkeypatch.setattr("app.scheduler.SessionLocal", sessions)
    monkeypatch.setattr(scheduler, "check_account", fake_check)
    await scheduler.run_due_checks()

    assert called == []


async def test_scheduler_uses_configured_concurrency(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with Session(engine) as session:
        session.add(
            Account(
                name="Due",
                endpoint_url="https://example.com",
                encrypted_api_key=encrypt_secret(""),
                interval_minutes=5,
                enabled=True,
            )
        )
        session.commit()
        values = get_app_preferences(session)
        values["concurrent_checks"] = 3
        save_app_preferences(session, values)

    scheduler = CheckScheduler()

    async def fake_check(_account_id):
        return None

    monkeypatch.setattr("app.scheduler.SessionLocal", sessions)
    monkeypatch.setattr(scheduler, "check_account", fake_check)
    await scheduler.run_due_checks()

    assert scheduler._semaphore._value == 3


async def test_scheduler_skips_automatic_inference_by_default(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with Session(engine) as session:
        session.add(
            Account(
                name="Provider",
                endpoint_url="https://example.com",
                encrypted_api_key=encrypt_secret(""),
                models_json='[{"id":"model-a"}]',
                enabled=True,
            )
        )
        session.commit()

    scheduler = CheckScheduler()
    called = []

    async def fake_test(account_id, concurrency):
        called.append((account_id, concurrency))

    monkeypatch.setattr("app.scheduler.SessionLocal", sessions)
    monkeypatch.setattr(scheduler, "test_account_models", fake_test)
    await scheduler.run_due_inference()

    assert called == []


async def test_scheduler_runs_due_automatic_inference(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with Session(engine) as session:
        session.add(
            Account(
                name="Provider",
                endpoint_url="https://example.com",
                encrypted_api_key=encrypt_secret(""),
                models_json='[{"id":"model-a"}]',
                enabled=True,
            )
        )
        session.commit()
        values = get_app_preferences(session)
        values["auto_inference"] = True
        values["auto_inference_interval_hours"] = 24
        values["concurrent_inference"] = 3
        save_app_preferences(session, values)

    scheduler = CheckScheduler()
    called = []

    async def fake_test(account_id, concurrency):
        called.append((account_id, concurrency))
        return [InferenceResult("available", 200, 10)]

    monkeypatch.setattr("app.scheduler.SessionLocal", sessions)
    monkeypatch.setattr(scheduler, "test_account_models", fake_test)
    await scheduler.run_due_inference()

    assert len(called) == 1
    assert called[0][1] == 3
