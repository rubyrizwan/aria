from __future__ import annotations

from datetime import datetime, timezone
import json

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    endpoint_url: Mapped[str] = mapped_column(String(2048))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str] = mapped_column(String(8), default="GET")
    auth_type: Mapped[str] = mapped_column(String(20), default="bearer")
    auth_header: Mapped[str] = mapped_column(String(100), default="Authorization")
    api_key_label: Mapped[str] = mapped_column(String(120), default="Default")
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    extra_headers: Mapped[str] = mapped_column(Text, default="{}")
    request_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_status: Mapped[int] = mapped_column(Integer, default=200)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=10)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    provider_type: Mapped[str] = mapped_column(String(20), default="unknown")
    models_json: Mapped[str] = mapped_column(Text, default="[]")
    models_endpoint: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), default="pending")
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_inference_latency_ms: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    last_inference_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    checks: Mapped[list["CheckResult"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    inference_results: Mapped[list["ModelInferenceResult"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    inference_history: Mapped[list["ModelInferenceHistory"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )

    @property
    def model_details(self) -> list[dict]:
        try:
            value = json.loads(self.models_json or "[]")
        except json.JSONDecodeError:
            return []
        if not isinstance(value, list):
            return []
        details = []
        for item in value:
            if isinstance(item, str):
                details.append(
                    {
                        "id": item,
                        "display_name": item,
                        "owned_by": "",
                        "created": None,
                        "capabilities": {},
                    }
                )
            elif isinstance(item, dict) and item.get("id"):
                details.append(
                    {
                        "id": str(item["id"]),
                        "display_name": str(item.get("display_name") or item["id"]),
                        "owned_by": str(item.get("owned_by") or ""),
                        "created": item.get("created"),
                        "capabilities": item.get("capabilities")
                        if isinstance(item.get("capabilities"), dict)
                        else {},
                    }
                )
        return sorted(details, key=lambda model: model["id"].casefold())

    @property
    def models(self) -> list[str]:
        return [model["id"] for model in self.model_details]


class CheckResult(Base):
    __tablename__ = "check_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    status: Mapped[str] = mapped_column(String(20), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_type: Mapped[str] = mapped_column(String(20), default="unknown")
    model_count: Mapped[int] = mapped_column(Integer, default=0)

    account: Mapped[Account] = relationship(back_populates="checks")


class ModelInferenceResult(Base):
    __tablename__ = "model_inference_results"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "model_id",
            name="uq_model_inference_account_model",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    model_id: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    account: Mapped[Account] = relationship(back_populates="inference_results")


class ModelInferenceHistory(Base):
    __tablename__ = "model_inference_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    model_id: Mapped[str] = mapped_column(String(255), index=True)
    api_key_label: Mapped[str] = mapped_column(String(120), default="Default")
    status: Mapped[str] = mapped_column(String(30), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    account: Mapped[Account] = relationship(back_populates="inference_history")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
