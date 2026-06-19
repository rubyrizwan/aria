from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.checker import InferenceResult, ProbeResult
from app.config import settings
from app.models import (
    Account,
    AppSetting,
    CheckResult,
    ModelInferenceHistory,
    ModelInferenceResult,
)

AUTO_MONITORING_KEY = "auto_monitoring_enabled"
AUTO_INFERENCE_KEY = "auto_inference_enabled"
AUTO_INFERENCE_INTERVAL_KEY = "auto_inference_interval_hours"
HISTORY_DAYS_KEY = "history_days"
CONCURRENT_CHECKS_KEY = "concurrent_checks"
CONCURRENT_INFERENCE_KEY = "concurrent_inference"
DEFAULT_TIMEOUT_KEY = "default_timeout_seconds"
DEFAULT_INTERVAL_KEY = "default_interval_minutes"
DEFAULT_MONITORING_KEY = "default_monitoring_enabled"
TABLE_PAGE_SIZE_KEY = "table_page_size"

APP_SETTING_DEFAULTS = {
    AUTO_INFERENCE_KEY: False,
    AUTO_INFERENCE_INTERVAL_KEY: 168,
    HISTORY_DAYS_KEY: settings.history_days,
    CONCURRENT_CHECKS_KEY: settings.max_concurrent_checks,
    CONCURRENT_INFERENCE_KEY: 5,
    DEFAULT_TIMEOUT_KEY: 10,
    DEFAULT_INTERVAL_KEY: 60,
    DEFAULT_MONITORING_KEY: True,
    TABLE_PAGE_SIZE_KEY: 30,
}


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
    now = aware_utcnow()
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
    row.checked_at = now
    session.add(
        ModelInferenceHistory(
            account_id=account.id,
            model_id=model_id,
            api_key_label=account.api_key_label or "Default",
            status=result.status,
            http_status=result.http_status,
            latency_ms=result.latency_ms,
            error_message=result.error_message,
            checked_at=now,
        )
    )
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


def prune_old_results(session: Session, history_days: int | None = None) -> int:
    retention = history_days or get_int_setting(
        session, HISTORY_DAYS_KEY, settings.history_days
    )
    cutoff = aware_utcnow() - timedelta(days=retention)
    check_result = session.execute(
        delete(CheckResult).where(CheckResult.checked_at < cutoff)
    )
    inference_result = session.execute(
        delete(ModelInferenceHistory).where(
            ModelInferenceHistory.checked_at < cutoff
        )
    )
    session.commit()
    return (check_result.rowcount or 0) + (inference_result.rowcount or 0)


def clear_inference_results(session: Session) -> int:
    snapshot_result = session.execute(delete(ModelInferenceResult))
    history_result = session.execute(delete(ModelInferenceHistory))
    for account in session.scalars(select(Account)):
        account.last_inference_latency_ms = None
        account.last_inference_at = None
    session.commit()
    return (snapshot_result.rowcount or 0) + (history_result.rowcount or 0)


def stored_result_counts(session: Session) -> dict[str, int]:
    return {
        "checks": session.scalar(select(func.count(CheckResult.id))) or 0,
        "inference": session.scalar(select(func.count(ModelInferenceHistory.id))) or 0,
    }


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
    return get_bool_setting(session, AUTO_MONITORING_KEY, True)


def set_auto_monitoring(session: Session, enabled: bool) -> None:
    set_app_setting(session, AUTO_MONITORING_KEY, enabled)
    session.commit()


def get_app_setting(session: Session, key: str, default):
    setting = session.get(AppSetting, key)
    return default if setting is None else setting.value


def get_int_setting(session: Session, key: str, default: int) -> int:
    try:
        return int(get_app_setting(session, key, default))
    except (TypeError, ValueError):
        return default


def get_bool_setting(session: Session, key: str, default: bool) -> bool:
    value = get_app_setting(session, key, default)
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


def set_app_setting(session: Session, key: str, value) -> None:
    setting = session.get(AppSetting, key)
    stored_value = str(value).lower() if isinstance(value, bool) else str(value)
    if setting:
        setting.value = stored_value
    else:
        session.add(AppSetting(key=key, value=stored_value))


def get_app_preferences(session: Session) -> dict:
    return {
        "auto_monitoring": is_auto_monitoring_enabled(session),
        "auto_inference": get_bool_setting(
            session, AUTO_INFERENCE_KEY, APP_SETTING_DEFAULTS[AUTO_INFERENCE_KEY]
        ),
        "auto_inference_interval_hours": get_int_setting(
            session,
            AUTO_INFERENCE_INTERVAL_KEY,
            APP_SETTING_DEFAULTS[AUTO_INFERENCE_INTERVAL_KEY],
        ),
        "history_days": get_int_setting(
            session, HISTORY_DAYS_KEY, APP_SETTING_DEFAULTS[HISTORY_DAYS_KEY]
        ),
        "concurrent_checks": get_int_setting(
            session,
            CONCURRENT_CHECKS_KEY,
            APP_SETTING_DEFAULTS[CONCURRENT_CHECKS_KEY],
        ),
        "concurrent_inference": get_int_setting(
            session,
            CONCURRENT_INFERENCE_KEY,
            APP_SETTING_DEFAULTS[CONCURRENT_INFERENCE_KEY],
        ),
        "default_timeout_seconds": get_int_setting(
            session, DEFAULT_TIMEOUT_KEY, APP_SETTING_DEFAULTS[DEFAULT_TIMEOUT_KEY]
        ),
        "default_interval_minutes": get_int_setting(
            session, DEFAULT_INTERVAL_KEY, APP_SETTING_DEFAULTS[DEFAULT_INTERVAL_KEY]
        ),
        "default_monitoring": get_bool_setting(
            session,
            DEFAULT_MONITORING_KEY,
            APP_SETTING_DEFAULTS[DEFAULT_MONITORING_KEY],
        ),
        "table_page_size": get_int_setting(
            session, TABLE_PAGE_SIZE_KEY, APP_SETTING_DEFAULTS[TABLE_PAGE_SIZE_KEY]
        ),
    }


def save_app_preferences(session: Session, values: dict) -> None:
    set_app_setting(session, AUTO_MONITORING_KEY, values["auto_monitoring"])
    set_app_setting(session, AUTO_INFERENCE_KEY, values["auto_inference"])
    set_app_setting(
        session,
        AUTO_INFERENCE_INTERVAL_KEY,
        values["auto_inference_interval_hours"],
    )
    set_app_setting(session, HISTORY_DAYS_KEY, values["history_days"])
    set_app_setting(session, CONCURRENT_CHECKS_KEY, values["concurrent_checks"])
    set_app_setting(
        session, CONCURRENT_INFERENCE_KEY, values["concurrent_inference"]
    )
    set_app_setting(
        session, DEFAULT_TIMEOUT_KEY, values["default_timeout_seconds"]
    )
    set_app_setting(
        session, DEFAULT_INTERVAL_KEY, values["default_interval_minutes"]
    )
    set_app_setting(
        session, DEFAULT_MONITORING_KEY, values["default_monitoring"]
    )
    set_app_setting(session, TABLE_PAGE_SIZE_KEY, values["table_page_size"])
    session.commit()
