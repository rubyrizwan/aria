from pathlib import Path
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.database import Base
from app.main import (
    app,
    dashboard,
    dashboard_attention,
    dashboard_chart_data,
    inference_chart_data,
    provider_catalog_rows,
    provider_ordering,
)
from app.models import (
    Account,
    CheckResult,
    ModelInferenceHistory,
    ModelInferenceResult,
)
from app.security import encrypt_secret


def request_for(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "app": app,
            "method": "GET",
            "path": path,
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": [],
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 1),
        }
    )


def test_dashboard_provider_order_is_case_insensitive_ascending():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for name in ("zeta", "Beta", "alpha"):
            session.add(
                Account(
                    name=name,
                    endpoint_url="https://example.com",
                    encrypted_api_key=encrypt_secret(""),
                    enabled=True,
                )
            )
        session.commit()
        names = list(
            session.scalars(select(Account.name).order_by(*provider_ordering()))
        )

    assert names == ["alpha", "Beta", "zeta"]


def test_dashboard_template_has_no_add_button_or_endpoint_text():
    template = (Path(__file__).resolve().parents[1] / "app/templates/dashboard.html").read_text()
    assert "Add provider" not in template
    assert "account.endpoint_url" not in template
    assert 'http-equiv="refresh"' not in template
    assert "auto refresh every 30 seconds" not in template


def test_dashboard_chart_data_builds_success_latency_and_distribution():
    now = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)
    checks = [
        CheckResult(
            checked_at=now - timedelta(hours=2),
            status="healthy",
            latency_ms=100,
        ),
        CheckResult(
            checked_at=now - timedelta(hours=2),
            status="down",
            latency_ms=300,
            error_message="Request timeout",
        ),
        CheckResult(
            checked_at=now - timedelta(hours=1),
            status="down",
            http_status=401,
            latency_ms=200,
            error_message="API key rejected",
        ),
    ]

    chart = dashboard_chart_data(checks, "24h", now)

    assert chart["total_checks"] == 3
    assert chart["success_rate"] == 33.3
    assert chart["average_latency"] == 200
    assert chart["success_segments"]
    assert chart["latency_average_segments"]
    distribution = {item["status"]: item["count"] for item in chart["distribution"]}
    assert distribution == {"healthy": 1, "down": 0, "timeout": 1, "auth": 1}


def test_dashboard_attention_prioritizes_provider_issues():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine, expire_on_commit=False) as session:
        down = Account(
            name="Down",
            endpoint_url="https://down.example.com",
            encrypted_api_key=encrypt_secret(""),
            enabled=True,
            last_status="down",
            last_checked_at=now,
        )
        overdue = Account(
            name="Overdue",
            endpoint_url="https://overdue.example.com",
            encrypted_api_key=encrypt_secret(""),
            enabled=True,
            last_status="healthy",
            last_checked_at=now,
            next_check_at=now - timedelta(hours=1),
            models_json='["model-a"]',
        )
        session.add_all([down, overdue])
        session.commit()

        attention = dashboard_attention(provider_catalog_rows(session), now)

    assert [item["title"] for item in attention[:2]] == ["Down", "Overdue"]
    assert attention[0]["severity"] == "bad"


def test_inference_chart_data_tracks_real_model_access_history():
    now = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)
    history = [
        ModelInferenceHistory(
            checked_at=now - timedelta(hours=2),
            model_id="model-a",
            api_key_label="Paid",
            status="available",
            latency_ms=100,
        ),
        ModelInferenceHistory(
            checked_at=now - timedelta(hours=2),
            model_id="model-b",
            api_key_label="Paid",
            status="quota_exceeded",
            latency_ms=300,
        ),
        ModelInferenceHistory(
            checked_at=now - timedelta(hours=1),
            model_id="model-c",
            api_key_label="Paid",
            status="forbidden",
            latency_ms=200,
        ),
    ]

    chart = inference_chart_data(history, "24h", now)

    assert chart["total_checks"] == 3
    assert chart["success_rate"] == 33.3
    assert chart["average_latency"] == 200
    distribution = {item["status"]: item["count"] for item in chart["distribution"]}
    assert distribution["available"] == 1
    assert distribution["quota_exceeded"] == 1
    assert distribution["forbidden"] == 1


def test_dashboard_renders_charts_indicators_and_attention():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine, expire_on_commit=False) as session:
        account = Account(
            name="Provider",
            endpoint_url="https://example.com",
            encrypted_api_key=encrypt_secret("key"),
            provider_type="openai",
            models_json='["model-a"]',
            last_status="healthy",
            enabled=True,
            last_checked_at=now,
            next_check_at=now + timedelta(hours=1),
            last_inference_latency_ms=150,
        )
        session.add(account)
        session.flush()
        session.add(
            CheckResult(
                account_id=account.id,
                checked_at=now,
                status="healthy",
                latency_ms=120,
                provider_type="openai",
                model_count=1,
            )
        )
        session.add(
            ModelInferenceResult(
                account_id=account.id,
                model_id="model-a",
                status="available",
                latency_ms=150,
            )
        )
        session.add(
            ModelInferenceHistory(
                account_id=account.id,
                model_id="model-a",
                api_key_label="Default",
                status="available",
                latency_ms=150,
            )
        )
        session.commit()

        response = dashboard(request_for("/"), session, period="7d")

    assert b"Inference success trend" in response.body
    assert b"Inference latency trend" in response.body
    assert b"Inference distribution" in response.body
    assert b"Recent inference" in response.body
    assert b"Needs attention" in response.body
    assert b"Inference available" in response.body
    assert b'class="active" href="/?period=7d"' in response.body
