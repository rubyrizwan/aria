from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.checker import ProbeResult
from app.database import Base
from app.models import Account
from app.security import encrypt_secret
from app.services import aware_utcnow, save_probe_result, uptime_percent
from app.services import is_auto_monitoring_enabled, set_auto_monitoring


def test_save_result_updates_account_and_schedule():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        account = Account(
            name="Service",
            endpoint_url="https://example.com",
            method="GET",
            auth_type="bearer",
            auth_header="Authorization",
            encrypted_api_key=encrypt_secret("key"),
            extra_headers="{}",
            expected_status=200,
            timeout_seconds=5,
            interval_minutes=15,
            enabled=True,
        )
        session.add(account)
        session.commit()
        before = aware_utcnow()
        check = save_probe_result(
            session,
            account,
            ProbeResult(
                "healthy",
                200,
                42.5,
                provider_type="openai",
                models=[
                    {"id": "model-b", "display_name": "Model B", "capabilities": {}},
                    {
                        "id": "model-a",
                        "display_name": "Model A",
                        "capabilities": {"vision": "provider"},
                    },
                ],
                models_endpoint="https://example.com/v1/models",
            ),
        )
        assert account.last_status == "healthy"
        assert account.provider_type == "openai"
        assert account.models == ["model-a", "model-b"]
        assert check.model_count == 2
        assert check.account_id == account.id
        next_check = account.next_check_at
        if next_check.tzinfo is None:
            next_check = next_check.replace(tzinfo=before.tzinfo)
        assert next_check >= before + timedelta(minutes=14)


def test_uptime_percent():
    assert uptime_percent(9, 10) == 90.0
    assert uptime_percent(0, 0) is None


def test_failed_result_clears_stale_models():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        account = Account(
            name="Stale",
            endpoint_url="https://example.com",
            encrypted_api_key=encrypt_secret("key"),
            models_json='["old-model"]',
            models_endpoint="https://example.com/v1/models",
            interval_minutes=5,
            enabled=True,
        )
        session.add(account)
        session.commit()
        save_probe_result(
            session,
            account,
            ProbeResult("down", 401, 10, "API key ditolak", provider_type="openai"),
        )
        assert account.models == []
        assert account.models_endpoint is None


def test_legacy_model_strings_are_normalized_and_sorted():
    account = Account(models_json='["z-model", "a-model"]')
    assert account.models == ["a-model", "z-model"]
    assert account.model_details[0]["capabilities"] == {}


def test_global_auto_monitoring_setting_is_persistent():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert is_auto_monitoring_enabled(session) is True
        set_auto_monitoring(session, False)
        assert is_auto_monitoring_enabled(session) is False
        set_auto_monitoring(session, True)
        assert is_auto_monitoring_enabled(session) is True
