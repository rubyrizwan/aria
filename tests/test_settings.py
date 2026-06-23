import json
import sqlite3
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.checker import InferenceResult
from app.database import Base
from app.main import (
    app,
    database_page,
    export_database,
    export_sqlite_database,
    legacy_export_database,
    import_sqlite_database,
    sqlite_database_path,
    new_account,
    pagination,
    settings_page,
)
from app.models import Account, ModelInferenceHistory, ModelInferenceResult
from app.security import encrypt_secret
from app.services import (
    clear_inference_results,
    get_app_preferences,
    save_app_preferences,
    save_inference_result,
)


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


def preferences(**overrides) -> dict:
    values = {
        "auto_monitoring": True,
        "auto_inference": False,
        "auto_inference_interval_hours": 168,
        "history_days": 30,
        "concurrent_checks": 5,
        "concurrent_inference": 5,
        "default_timeout_seconds": 10,
        "default_interval_minutes": 60,
        "default_monitoring": True,
        "table_page_size": 30,
    }
    values.update(overrides)
    return values


def test_application_preferences_are_persistent():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        save_app_preferences(
            session,
            preferences(
                auto_monitoring=False,
                auto_inference=True,
                auto_inference_interval_hours=72,
                history_days=90,
                concurrent_checks=3,
                concurrent_inference=10,
                default_timeout_seconds=25,
                default_interval_minutes=15,
                default_monitoring=False,
                table_page_size=50,
            ),
        )
        stored = get_app_preferences(session)

    assert stored == {
        "auto_monitoring": False,
        "auto_inference": True,
        "auto_inference_interval_hours": 72,
        "history_days": 90,
        "concurrent_checks": 3,
        "concurrent_inference": 10,
        "default_timeout_seconds": 25,
        "default_interval_minutes": 15,
        "default_monitoring": False,
        "table_page_size": 50,
    }


def test_pagination_supports_configured_page_size():
    pager = pagination(2, 120, "/models", page_size=50)

    assert pager["offset"] == 50
    assert pager["page_size"] == 50
    assert pager["total_pages"] == 3


def test_new_provider_form_uses_configured_defaults():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        save_app_preferences(
            session,
            preferences(
                default_timeout_seconds=25,
                default_interval_minutes=15,
                default_monitoring=False,
            ),
        )
        response = new_account(request_for("/accounts/new"), session)

    assert b'name="timeout_seconds" value="25"' in response.body
    assert b'<option value="15" selected>15 minutes</option>' in response.body
    assert b'name="enabled" checked' not in response.body


def test_clear_inference_results_resets_provider_latency():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        account = Account(
            name="Provider",
            endpoint_url="https://example.com",
            encrypted_api_key=encrypt_secret("key"),
            models_json=json.dumps(["model-a"]),
            last_inference_latency_ms=42,
        )
        session.add(account)
        session.commit()
        save_inference_result(
            session,
            account,
            "model-a",
            InferenceResult("available", 200, 42),
        )
        session.commit()

        assert clear_inference_results(session) == 2

        assert session.query(ModelInferenceResult).count() == 0
        assert session.query(ModelInferenceHistory).count() == 0
        assert account.last_inference_latency_ms is None
        assert account.last_inference_at is None


def test_settings_page_shows_runtime_controls_and_counts():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        response = settings_page(request_for("/settings"), session)

    assert b"Concurrent provider checks" in response.body
    assert b"Concurrent inference tests" in response.body
    assert b"Scheduled inference retest" in response.body
    assert b"New provider defaults" in response.body
    assert b"Rows per table" in response.body
    assert b"Data maintenance" in response.body
    assert b"Clear inference results" in response.body
    assert b"Database transfer" not in response.body



def test_database_page_shows_transfer_controls():
    response = database_page(request_for("/database"))

    assert b"Database transfer" in response.body
    assert b"Export database" in response.body
    assert b"Import database" in response.body
    assert b"data-download-form" in response.body
    assert b"database-download-frame" in response.body
    assert b"/database/export" in response.body
    assert b"/database/import" in response.body

def test_sqlite_database_export_creates_consistent_copy(tmp_path):
    source = tmp_path / "source.db"
    exported = tmp_path / "exported.db"
    with sqlite3.connect(source) as database:
        database.execute("create table sample (value text)")
        database.execute("insert into sample values ('stored')")

    export_sqlite_database(source, exported)

    with sqlite3.connect(exported) as database:
        assert database.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert database.execute("select value from sample").fetchone()[0] == "stored"


def test_sqlite_database_import_replaces_target_after_integrity_check(tmp_path):
    uploaded = tmp_path / "uploaded.db"
    target = tmp_path / "target.db"
    with sqlite3.connect(uploaded) as database:
        database.execute("create table sample (value text)")
        database.execute("insert into sample values ('imported')")
    with sqlite3.connect(target) as database:
        database.execute("create table sample (value text)")
        database.execute("insert into sample values ('old')")

    import_sqlite_database(uploaded, target)

    with sqlite3.connect(target) as database:
        assert database.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert database.execute("select value from sample").fetchone()[0] == "imported"


def test_sqlite_database_import_rejects_invalid_upload(tmp_path):
    uploaded = tmp_path / "uploaded.db"
    target = tmp_path / "target.db"
    uploaded.write_bytes(b"not sqlite")

    try:
        import_sqlite_database(uploaded, target)
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("invalid upload should be rejected")
    assert not target.exists()


def test_sqlite_database_path_resolves_relative_urls_from_repository_root():
    root = Path(__file__).resolve().parent.parent

    assert sqlite_database_path("sqlite:///./data/apichecker.db") == (
        root / "data/apichecker.db"
    )



def test_legacy_settings_export_url_redirects_to_database_page():
    response = legacy_export_database()

    assert response.status_code == 303
    assert response.headers["location"] == "/database"

def test_database_export_route_returns_attachment(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as database:
        database.execute("create table sample (value text)")
        database.execute("insert into sample values ('stored')")
    monkeypatch.setattr("app.main.sqlite_database_path", lambda: source)

    response = export_database()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.sqlite3")
    assert "attachment" in response.headers["content-disposition"]
    exported = Path(response.path)
    try:
        with sqlite3.connect(exported) as database:
            assert database.execute("pragma integrity_check").fetchone()[0] == "ok"
            assert database.execute("select value from sample").fetchone()[0] == "stored"
    finally:
        exported.unlink(missing_ok=True)
