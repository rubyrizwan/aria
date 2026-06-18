from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
import json

from app.main import account_api_key, account_detail, app, delete_account
from app.models import Account
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
