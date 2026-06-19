from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.checker import InferenceResult, ProbeResult
from app.config import settings
from app.models import Account, AppSetting, CheckResult, ModelInferenceResult

AUTO_MONITORING_KEY = "auto_monitoring_enabled"


def aware_utcnow() -> datetime:
    return datetime.now(timezone.utc)


def schedule_next(account: Account, now: datetime | None = None) -> None:
    base = now or aware_utcnow()
    account.next_check_at = base + timedelta(minutes=account.interval_minutes)


def save_probe_result(
    session: Session, account: Account, result: ProbeResult
) -> CheckResult:
    now = aware_utcnow()
    check = CheckResult(
        account_id=account.id,
        checked_at=now,
        status=result.status,
        http_status=result.http_status,
        latency_ms=result.latency_ms,
        error_message=result.error_message,
        provider_type=result.provider_type,
        model_count=len(result.models),
    )
    account.last_status = result.status
    account.last_checked_at = now
    account.provider_type = result.provider_type
    if result.status == "healthy":
        account.models_json = json.dumps(result.models)
        account.models_endpoint = result.models_endpoint
    else:
        account.models_json = "[]"
        account.models_endpoint = None
    schedule_next(account, now)
    session.add(check)
    session.commit()
    session.refresh(check)
    return check


def save_inference_result(
    session: Session,
    account: Account,
    model_id: str,
    result: InferenceResult,
) -> ModelInferenceResult:
    row = session.scalar(
        select(ModelInferenceResult).where(
            ModelInferenceResult.account_id == account.id,
            ModelInferenceResult.model_id == model_id,
        )
    )
    if not row:
        row = ModelInferenceResult(
            account_id=account.id,
            model_id=model_id,
            status=result.status,
        )
        session.add(row)
    row.status = result.status
    row.http_status = result.http_status
    row.latency_ms = result.latency_ms
    row.error_message = result.error_message
    row.checked_at = aware_utcnow()
    return row


def update_inference_latency(
    account: Account,
    results: list[InferenceResult],
) -> None:
    latencies = [
        result.latency_ms for result in results if result.latency_ms is not None
    ]
    account.last_inference_latency_ms = (
        round(sum(latencies) / len(latencies), 2) if latencies else None
    )
    account.last_inference_at = aware_utcnow()


def prune_old_results(session: Session) -> int:
    cutoff = aware_utcnow() - timedelta(days=settings.history_days)
    result = session.execute(delete(CheckResult).where(CheckResult.checked_at < cutoff))
    session.commit()
    return result.rowcount or 0


def account_metrics(session: Session, account_id: int, hours: int) -> tuple[int, int]:
    cutoff = aware_utcnow() - timedelta(hours=hours)
    total = session.scalar(
        select(func.count(CheckResult.id)).where(
            CheckResult.account_id == account_id,
            CheckResult.checked_at >= cutoff,
        )
    ) or 0
    healthy = session.scalar(
        select(func.count(CheckResult.id)).where(
            CheckResult.account_id == account_id,
            CheckResult.checked_at >= cutoff,
            CheckResult.status == "healthy",
        )
    ) or 0
    return healthy, total


def uptime_percent(healthy: int, total: int) -> float | None:
    return round((healthy / total) * 100, 1) if total else None


def is_auto_monitoring_enabled(session: Session) -> bool:
    setting = session.get(AppSetting, AUTO_MONITORING_KEY)
    return setting is None or setting.value.lower() == "true"


def set_auto_monitoring(session: Session, enabled: bool) -> None:
    setting = session.get(AppSetting, AUTO_MONITORING_KEY)
    value = "true" if enabled else "false"
    if setting:
        setting.value = value
    else:
        session.add(AppSetting(key=AUTO_MONITORING_KEY, value=value))
    session.commit()
