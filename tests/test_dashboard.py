from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.main import provider_ordering
from app.models import Account
from app.security import encrypt_secret


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
