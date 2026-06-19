from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.checker import InferenceResult
from app.database import Base
from app.models import Account, ModelInferenceHistory, ModelInferenceResult
from app.security import encrypt_secret
from app.services import save_inference_result, update_inference_latency


def test_inference_result_is_updated_per_model():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        account = Account(
            name="Provider",
            endpoint_url="https://example.com",
            encrypted_api_key=encrypt_secret("key"),
        )
        session.add(account)
        session.commit()

        save_inference_result(
            session,
            account,
            "model-a",
            InferenceResult("available", 200, 10),
        )
        session.commit()
        save_inference_result(
            session,
            account,
            "model-a",
            InferenceResult("quota_exceeded", 429, 12, "Quota"),
        )
        session.commit()

        rows = session.query(ModelInferenceResult).all()
        assert len(rows) == 1
        assert rows[0].status == "quota_exceeded"
        assert rows[0].http_status == 429
        history = session.query(ModelInferenceHistory).all()
        assert len(history) == 2
        assert [row.status for row in history] == ["available", "quota_exceeded"]
        assert history[0].api_key_label == "Default"


def test_inference_latency_uses_only_requests_with_latency():
    account = Account(
        name="Provider",
        endpoint_url="https://example.com",
        encrypted_api_key=encrypt_secret("key"),
    )

    update_inference_latency(
        account,
        [
            InferenceResult("available", 200, 100),
            InferenceResult("failed", 500, 200),
            InferenceResult("unsupported", None, None),
        ],
    )

    assert account.last_inference_latency_ms == 150
    assert account.last_inference_at is not None
