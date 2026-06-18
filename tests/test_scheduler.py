from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Account
from app.scheduler import CheckScheduler
from app.security import encrypt_secret
from app.services import set_auto_monitoring


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
