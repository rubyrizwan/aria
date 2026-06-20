from __future__ import annotations

import asyncio
import hashlib
import hmac
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from math import ceil
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, SessionLocal, engine, get_db
from app.checker import InferenceResult, probe_account, test_model_inference
from app.models import (
    Account,
    CheckResult,
    ModelInferenceHistory,
    ModelInferenceResult,
)
from app.scheduler import scheduler
from app.security import (
    SecretConfigurationError,
    decrypt_secret,
    encrypt_secret,
    get_fernet,
)
from app.services import (
    account_metrics,
    clear_inference_results,
    get_app_preferences,
    prune_old_results,
    save_inference_result,
    save_app_preferences,
    stored_result_counts,
    update_inference_latency,
    uptime_percent,
)
from app.services import is_auto_monitoring_enabled
from app.validation import (
    ALLOWED_INTERVALS,
    validate_endpoint_url,
)
from app.version import __release_date__, __version__

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")
ASSET_VERSION = max(
    (BASE_DIR / "static/app.css").stat().st_mtime_ns,
    (BASE_DIR / "static/app.js").stat().st_mtime_ns,
)
templates.env.globals["asset_version"] = ASSET_VERSION
templates.env.globals["server_host"] = settings.host
templates.env.globals["server_port"] = settings.port
templates.env.globals["service_manager"] = settings.service_manager
templates.env.globals["restart_supported"] = settings.service_manager in {
    "launcher",
    "systemd-user",
}
templates.env.globals["restart_token"] = hmac.new(
    (settings.master_key or "").encode(),
    b"apichecker:restart",
    hashlib.sha256,
).hexdigest()


def format_datetime(value: datetime | None) -> str:
    if not value:
        return "Never"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%d %b %Y, %H:%M")


templates.env.filters["datetime"] = format_datetime


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_fernet()
    Base.metadata.create_all(bind=engine)
    scheduler.start()
    yield
    await scheduler.stop()


app = FastAPI(
    title="ARIA",
    description="API Reliability & Inference Analyzer",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

DbSession = Annotated[Session, Depends(get_db)]
PAGE_SIZE = 30
HISTORY_DAY_OPTIONS = {7, 14, 30, 60, 90}
CONCURRENCY_OPTIONS = {1, 3, 5, 10}
TABLE_PAGE_SIZE_OPTIONS = {15, 30, 50, 100}
AUTO_INFERENCE_INTERVAL_OPTIONS = {24, 72, 168}
APP_VERSION = __version__
RELEASE_DATE = datetime.strptime(__release_date__, "%Y-%m-%d").strftime("%d %B %Y")
RELEASE_HISTORY = (
    {
        "version": "1.0.1",
        "date": "20 June 2026",
        "summary": "Launcher process detection now remains reliable after moving the repository directory.",
    },
    {
        "version": "1.0.0",
        "date": "20 June 2026",
        "summary": "First stable ARIA release for private AI provider and inference access monitoring.",
    },
    {
        "version": "0.4.3",
        "date": "19 June 2026",
        "summary": "User-facing branding changed to ARIA: API Reliability & Inference Analyzer.",
    },
    {
        "version": "0.4.2",
        "date": "19 June 2026",
        "summary": "Unified model catalog, inference history, operational dashboard, scheduled retests, backups, and service controls.",
    },
    {
        "version": "0.4.1",
        "date": "19 June 2026",
        "summary": "Model access testing, inference progress, filters, monitoring state, and latency.",
    },
    {
        "version": "0.4.0",
        "date": "19 June 2026",
        "summary": "Provider details, API key labels, monitoring intervals, and dashboard improvements.",
    },
    {
        "version": "0.3.1",
        "date": "18 June 2026",
        "summary": "Database migration compatibility for multiple API keys.",
    },
)
INFERENCE_JOBS: dict[str, dict] = {}


def provider_ordering():
    return func.lower(Account.name), Account.name


def model_freshness(value: datetime | None) -> str:
    if not value:
        return "stale"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - value
    if age <= timedelta(days=1):
        return "fresh"
    if age <= timedelta(days=7):
        return "aging"
    return "stale"


def available_model_groups(
    db: Session,
    query: str = "",
    provider_id: int | None = None,
    compatibility: str = "all",
    capability: str = "all",
    availability: str = "all",
    latency: str = "all",
    freshness: str = "all",
    sort: str = "name_asc",
) -> list[dict]:
    grouped: dict[str, dict] = {}
    rows = db.execute(
        select(ModelInferenceResult, Account)
        .join(Account, Account.id == ModelInferenceResult.account_id)
        .where(ModelInferenceResult.status == "available")
        .order_by(
            func.lower(ModelInferenceResult.model_id),
            ModelInferenceResult.model_id,
            func.lower(Account.name),
        )
    )
    search = query.strip().casefold()
    for result, account in rows:
        if result.model_id not in account.models:
            continue
        key = result.model_id.casefold()
        if search and search not in key:
            continue
        group = grouped.setdefault(
            key,
            {
                "model_id": result.model_id,
                "providers": [],
                "latencies": [],
                "last_tested": result.checked_at,
            },
        )
        model_detail = next(
            (
                detail
                for detail in account.model_details
                if detail["id"] == result.model_id
            ),
            {"capabilities": {}},
        )
        group.setdefault("provider_ids", []).append(account.id)
        group.setdefault("provider_rows", []).append(
            {
                "id": account.id,
                "name": account.name,
                "latency_ms": result.latency_ms,
                "compatibility": account.provider_type,
            }
        )
        group.setdefault("compatibilities", set()).add(account.provider_type)
        group.setdefault("capabilities", {}).update(model_detail["capabilities"])
        group["providers"].append(account.name)
        if result.latency_ms is not None:
            group["latencies"].append((result.latency_ms, account.name))
        if result.checked_at and (
            not group["last_tested"] or result.checked_at > group["last_tested"]
        ):
            group["last_tested"] = result.checked_at

    groups = []
    for group in grouped.values():
        latencies = group.pop("latencies")
        group["providers"].sort(key=str.casefold)
        group["provider_rows"].sort(
            key=lambda item: (
                item["latency_ms"] is None,
                item["latency_ms"] or 0,
                item["name"].casefold(),
            )
        )
        group["provider_count"] = len(group["providers"])
        group["average_latency_ms"] = (
            round(sum(value for value, _ in latencies) / len(latencies), 2)
            if latencies
            else None
        )
        group["best_latency_ms"] = min(
            (value for value, _ in latencies), default=None
        )
        group["worst_latency_ms"] = max(
            (value for value, _ in latencies), default=None
        )
        group["fastest_provider"] = (
            min(latencies, key=lambda item: item[0])[1] if latencies else None
        )
        group["freshness"] = model_freshness(group["last_tested"])
        groups.append(group)

    if provider_id:
        groups = [
            group for group in groups if provider_id in group["provider_ids"]
        ]
    if compatibility in {"openai", "anthropic"}:
        groups = [
            group
            for group in groups
            if compatibility in group["compatibilities"]
        ]
    if capability in {"vision", "reasoning", "audio", "tools"}:
        groups = [
            group for group in groups if capability in group["capabilities"]
        ]
    if availability == "multiple":
        groups = [group for group in groups if group["provider_count"] > 1]
    elif availability == "single":
        groups = [group for group in groups if group["provider_count"] == 1]
    latency_limits = {"under_500": 500, "under_1000": 1000, "under_3000": 3000}
    if latency in latency_limits:
        groups = [
            group
            for group in groups
            if group["best_latency_ms"] is not None
            and group["best_latency_ms"] < latency_limits[latency]
        ]
    if freshness in {"fresh", "aging", "stale"}:
        groups = [group for group in groups if group["freshness"] == freshness]

    sorters = {
        "name_asc": lambda item: item["model_id"].casefold(),
        "name_desc": lambda item: item["model_id"].casefold(),
        "providers_desc": lambda item: (
            -item["provider_count"],
            item["model_id"].casefold(),
        ),
        "latency_asc": lambda item: (
            item["best_latency_ms"] is None,
            item["best_latency_ms"] or 0,
            item["model_id"].casefold(),
        ),
        "tested_desc": lambda item: (
            -(item["last_tested"].timestamp() if item["last_tested"] else 0),
            item["model_id"].casefold(),
        ),
    }
    groups.sort(
        key=sorters.get(sort, sorters["name_asc"]),
        reverse=sort == "name_desc",
    )
    return groups


def available_model_stats(groups: list[dict]) -> dict:
    provider_ids = {
        provider_id for group in groups for provider_id in group["provider_ids"]
    }
    latencies = [
        group["best_latency_ms"]
        for group in groups
        if group["best_latency_ms"] is not None
    ]
    latest_tested = max(
        (group["last_tested"] for group in groups if group["last_tested"]),
        default=None,
    )
    return {
        "models": len(groups),
        "providers": len(provider_ids),
        "multi_provider": sum(group["provider_count"] > 1 for group in groups),
        "average_best_latency": (
            round(sum(latencies) / len(latencies), 2) if latencies else None
        ),
        "latest_tested": latest_tested,
    }


def provider_catalog_rows(db: Session) -> list[dict]:
    accounts = list(db.scalars(select(Account)))
    inference_rows = list(db.scalars(select(ModelInferenceResult)))
    inference_by_account: dict[int, list[ModelInferenceResult]] = {}
    for result in inference_rows:
        inference_by_account.setdefault(result.account_id, []).append(result)

    rows = []
    for account in accounts:
        current_models = set(account.models)
        results = [
            result
            for result in inference_by_account.get(account.id, [])
            if result.model_id in current_models
        ]
        inference_summary: dict[str, int] = {}
        for result in results:
            inference_summary[result.status] = (
                inference_summary.get(result.status, 0) + 1
            )
        rows.append(
            {
                "account": account,
                "model_count": len(current_models),
                "available_count": inference_summary.get("available", 0),
                "tested_count": len(results),
                "inference_summary": inference_summary,
                "latency_ms": account.last_inference_latency_ms,
                "freshness": (
                    model_freshness(account.last_checked_at)
                    if account.last_checked_at
                    else "never"
                ),
            }
        )
    return rows


def provider_catalog_stats(rows: list[dict]) -> dict:
    return {
        "total": len(rows),
        "healthy": sum(
            row["account"].enabled and row["account"].last_status == "healthy"
            for row in rows
        ),
        "down": sum(
            row["account"].enabled and row["account"].last_status == "down"
            for row in rows
        ),
        "pending": sum(
            row["account"].enabled and row["account"].last_status == "pending"
            for row in rows
        ),
        "disabled": sum(not row["account"].enabled for row in rows),
        "without_models": sum(row["model_count"] == 0 for row in rows),
    }


def aware_datetime(value: datetime | None) -> datetime | None:
    if value and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def check_failure_category(check: CheckResult) -> str:
    if check.status == "healthy":
        return "healthy"
    message = (check.error_message or "").casefold()
    if check.http_status in {401, 403} or any(
        marker in message for marker in ("unauthorized", "forbidden", "api key")
    ):
        return "auth"
    if "timeout" in message:
        return "timeout"
    return "down"


def chart_points(values: list[float | None], maximum: float = 100) -> list[str]:
    if not values:
        return []
    width = 560
    height = 150
    step = width / max(len(values) - 1, 1)
    points = []
    segment = []
    for index, value in enumerate(values):
        if value is None:
            if segment:
                points.append(" ".join(segment))
                segment = []
            continue
        x = round(index * step, 2)
        y = round(height - (min(max(value, 0), maximum) / maximum * height), 2)
        segment.append(f"{x},{y}")
    if segment:
        points.append(" ".join(segment))
    return points


def dashboard_chart_data(
    checks: list[CheckResult],
    period: str,
    now: datetime | None = None,
) -> dict:
    config = {
        "24h": (timedelta(hours=24), 12, "%H:%M"),
        "7d": (timedelta(days=7), 14, "%d %b"),
        "30d": (timedelta(days=30), 15, "%d %b"),
    }
    selected = period if period in config else "24h"
    duration, bucket_count, label_format = config[selected]
    current = now or datetime.now(timezone.utc)
    start = current - duration
    bucket_seconds = duration.total_seconds() / bucket_count
    buckets = [
        {"checks": [], "label": (start + timedelta(seconds=bucket_seconds * index)).strftime(label_format)}
        for index in range(bucket_count)
    ]
    recent_checks = []
    for check in checks:
        checked_at = aware_datetime(check.checked_at)
        if not checked_at or checked_at < start or checked_at > current:
            continue
        index = min(
            int((checked_at - start).total_seconds() / bucket_seconds),
            bucket_count - 1,
        )
        buckets[index]["checks"].append(check)
        recent_checks.append(check)

    success_values: list[float | None] = []
    latency_average: list[float | None] = []
    latency_maximum: list[float | None] = []
    for bucket in buckets:
        bucket_checks = bucket["checks"]
        if not bucket_checks:
            success_values.append(None)
            latency_average.append(None)
            latency_maximum.append(None)
            continue
        healthy = sum(check.status == "healthy" for check in bucket_checks)
        success_values.append(round(healthy / len(bucket_checks) * 100, 1))
        latencies = [
            check.latency_ms
            for check in bucket_checks
            if check.latency_ms is not None
        ]
        latency_average.append(
            round(sum(latencies) / len(latencies), 2) if latencies else None
        )
        latency_maximum.append(max(latencies) if latencies else None)

    max_latency = max(
        [value for value in latency_maximum if value is not None] or [1]
    )
    distribution = {"healthy": 0, "down": 0, "timeout": 0, "auth": 0}
    for check in recent_checks:
        category = check_failure_category(check)
        distribution[category] = distribution.get(category, 0) + 1
    total = len(recent_checks)
    all_latencies = [
        check.latency_ms
        for check in recent_checks
        if check.latency_ms is not None
    ]
    distribution_rows = [
        {
            "status": status,
            "count": count,
            "percent": round(count / total * 100, 1) if total else 0,
        }
        for status, count in distribution.items()
    ]
    return {
        "period": selected,
        "labels": [bucket["label"] for bucket in buckets],
        "success_values": success_values,
        "success_segments": chart_points(success_values),
        "latency_average": latency_average,
        "latency_maximum": latency_maximum,
        "latency_average_segments": chart_points(latency_average, max_latency),
        "latency_max_segments": chart_points(latency_maximum, max_latency),
        "max_latency": max_latency,
        "distribution": distribution_rows,
        "total_checks": total,
        "success_rate": (
            round(
                sum(check.status == "healthy" for check in recent_checks)
                / total
                * 100,
                1,
            )
            if total
            else None
        ),
        "average_latency": (
            round(sum(all_latencies) / len(all_latencies), 2)
            if all_latencies
            else None
        ),
        "latest_check": max(
            (aware_datetime(check.checked_at) for check in recent_checks),
            default=None,
        ),
    }


def inference_chart_data(
    history: list[ModelInferenceHistory],
    period: str,
    now: datetime | None = None,
) -> dict:
    config = {
        "24h": (timedelta(hours=24), 12, "%H:%M"),
        "7d": (timedelta(days=7), 14, "%d %b"),
        "30d": (timedelta(days=30), 15, "%d %b"),
    }
    selected = period if period in config else "24h"
    duration, bucket_count, label_format = config[selected]
    current = now or datetime.now(timezone.utc)
    start = current - duration
    bucket_seconds = duration.total_seconds() / bucket_count
    buckets = [
        {
            "rows": [],
            "label": (
                start + timedelta(seconds=bucket_seconds * index)
            ).strftime(label_format),
        }
        for index in range(bucket_count)
    ]
    recent = []
    for row in history:
        checked_at = aware_datetime(row.checked_at)
        if not checked_at or checked_at < start or checked_at > current:
            continue
        index = min(
            int((checked_at - start).total_seconds() / bucket_seconds),
            bucket_count - 1,
        )
        buckets[index]["rows"].append(row)
        recent.append(row)

    success_values = []
    latency_average = []
    latency_maximum = []
    for bucket in buckets:
        rows = bucket["rows"]
        if not rows:
            success_values.append(None)
            latency_average.append(None)
            latency_maximum.append(None)
            continue
        success_values.append(
            round(sum(row.status == "available" for row in rows) / len(rows) * 100, 1)
        )
        latencies = [row.latency_ms for row in rows if row.latency_ms is not None]
        latency_average.append(
            round(sum(latencies) / len(latencies), 2) if latencies else None
        )
        latency_maximum.append(max(latencies) if latencies else None)

    max_latency = max(
        [value for value in latency_maximum if value is not None] or [1]
    )
    status_order = (
        "available",
        "forbidden",
        "unauthorized",
        "quota_exceeded",
        "timeout",
        "failed",
        "unavailable",
        "unsupported",
    )
    status_counts = {
        status: sum(row.status == status for row in recent)
        for status in status_order
    }
    total = len(recent)
    latencies = [row.latency_ms for row in recent if row.latency_ms is not None]
    return {
        "period": selected,
        "labels": [bucket["label"] for bucket in buckets],
        "success_segments": chart_points(success_values),
        "latency_average_segments": chart_points(latency_average, max_latency),
        "latency_max_segments": chart_points(latency_maximum, max_latency),
        "max_latency": max_latency,
        "distribution": [
            {
                "status": status,
                "count": count,
                "percent": round(count / total * 100, 1) if total else 0,
            }
            for status, count in status_counts.items()
            if count or status in {"available", "failed", "quota_exceeded"}
        ],
        "total_checks": total,
        "success_rate": (
            round(status_counts["available"] / total * 100, 1) if total else None
        ),
        "average_latency": (
            round(sum(latencies) / len(latencies), 2) if latencies else None
        ),
        "latest_check": max(
            (aware_datetime(row.checked_at) for row in recent),
            default=None,
        ),
    }


def dashboard_attention(rows: list[dict], now: datetime | None = None) -> list[dict]:
    current = now or datetime.now(timezone.utc)
    items = []
    for row in rows:
        account = row["account"]
        if account.enabled and account.last_status == "down":
            items.append(
                {
                    "severity": "bad",
                    "icon": "server-crash",
                    "title": account.name,
                    "message": "Provider is currently unavailable.",
                    "url": f"/accounts/{account.id}",
                }
            )
        elif not account.last_checked_at:
            items.append(
                {
                    "severity": "neutral",
                    "icon": "circle-help",
                    "title": account.name,
                    "message": "Provider has never been checked.",
                    "url": f"/accounts/{account.id}",
                }
            )
        elif account.enabled and aware_datetime(account.next_check_at) and aware_datetime(account.next_check_at) < current:
            items.append(
                {
                    "severity": "warning",
                    "icon": "clock-alert",
                    "title": account.name,
                    "message": "Scheduled check is overdue.",
                    "url": f"/accounts/{account.id}",
                }
            )
        elif row["inference_summary"].get("unauthorized", 0) or row[
            "inference_summary"
        ].get("forbidden", 0):
            items.append(
                {
                    "severity": "bad",
                    "icon": "key-round",
                    "title": account.name,
                    "message": "One or more model access checks were rejected.",
                    "url": f"/accounts/{account.id}",
                }
            )
        elif row["inference_summary"].get("quota_exceeded", 0):
            items.append(
                {
                    "severity": "warning",
                    "icon": "badge-dollar-sign",
                    "title": account.name,
                    "message": "One or more models reported quota exceeded.",
                    "url": f"/accounts/{account.id}",
                }
            )
        elif model_freshness(account.last_checked_at) == "stale":
            items.append(
                {
                    "severity": "warning",
                    "icon": "history",
                    "title": account.name,
                    "message": "Provider check data is older than seven days.",
                    "url": f"/accounts/{account.id}",
                }
            )
        elif row["model_count"] == 0:
            items.append(
                {
                    "severity": "warning",
                    "icon": "package-open",
                    "title": account.name,
                    "message": "No models are currently indexed.",
                    "url": f"/accounts/{account.id}",
                }
            )
    priority = {"bad": 0, "warning": 1, "neutral": 2}
    return sorted(
        items,
        key=lambda item: (priority[item["severity"]], item["title"].casefold()),
    )[:8]


def template_context(request: Request, **values):
    return {"request": request, "path": request.url.path, **values}


def inference_job_payload(job: dict) -> dict:
    return {
        key: value
        for key, value in job.items()
        if key not in {"task"}
    }


async def run_inference_job(job_id: str, account_id: int) -> None:
    job = INFERENCE_JOBS[job_id]
    try:
        with SessionLocal() as session:
            preferences = get_app_preferences(session)
            semaphore = asyncio.Semaphore(preferences["concurrent_inference"])
            account = session.get(Account, account_id)
            if not account:
                raise RuntimeError("Provider no longer exists.")
            completed_results: list[InferenceResult] = []

            async def run(model_id: str):
                async with semaphore:
                    job["logs"].append(
                        {"model": model_id, "status": "running", "message": "Testing"}
                    )
                    try:
                        result = await test_model_inference(account, model_id)
                    except Exception as exc:
                        result = InferenceResult(
                            "failed",
                            None,
                            None,
                            f"Inference test failed: {exc}"[:500],
                        )
                    return model_id, result

            tasks = [
                asyncio.create_task(run(model_id)) for model_id in job["models"]
            ]
            for task in asyncio.as_completed(tasks):
                model_id, result = await task
                save_inference_result(session, account, model_id, result)
                completed_results.append(result)
                session.commit()
                job["completed"] += 1
                job["summary"][result.status] = (
                    job["summary"].get(result.status, 0) + 1
                )
                job["logs"].append(
                    {
                        "model": model_id,
                        "status": result.status,
                        "message": result.error_message
                        or "Inference completed successfully.",
                    }
                )
            update_inference_latency(account, completed_results)
            session.commit()
        job["status"] = "completed"
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
        job["logs"].append(
            {"model": "Job", "status": "failed", "message": str(exc)}
        )


async def run_model_provider_job(
    job_id: str,
    model_id: str,
    account_ids: list[int],
) -> None:
    job = INFERENCE_JOBS[job_id]
    try:
        with SessionLocal() as session:
            preferences = get_app_preferences(session)
            semaphore = asyncio.Semaphore(preferences["concurrent_inference"])

            async def run(account_id: int):
                async with semaphore:
                    account = session.get(Account, account_id)
                    if not account:
                        return account_id, "Deleted provider", InferenceResult(
                            "failed", None, None, "Provider no longer exists."
                        )
                    job["logs"].append(
                        {
                            "model": account.name,
                            "status": "running",
                            "message": f"Testing {model_id}",
                        }
                    )
                    try:
                        result = await test_model_inference(account, model_id)
                    except Exception as exc:
                        result = InferenceResult(
                            "failed",
                            None,
                            None,
                            f"Inference test failed: {exc}"[:500],
                        )
                    return account_id, account.name, result

            tasks = [
                asyncio.create_task(run(account_id)) for account_id in account_ids
            ]
            for task in asyncio.as_completed(tasks):
                account_id, account_name, result = await task
                account = session.get(Account, account_id)
                if account:
                    save_inference_result(session, account, model_id, result)
                    update_inference_latency(account, [result])
                    session.commit()
                job["completed"] += 1
                job["summary"][result.status] = (
                    job["summary"].get(result.status, 0) + 1
                )
                job["logs"].append(
                    {
                        "model": account_name,
                        "status": result.status,
                        "message": result.error_message
                        or "Inference completed successfully.",
                    }
                )
        job["status"] = "completed"
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
        job["logs"].append(
            {"model": "Job", "status": "failed", "message": str(exc)}
        )


def prune_inference_jobs() -> None:
    if len(INFERENCE_JOBS) <= 100:
        return
    completed = [
        job_id
        for job_id, job in INFERENCE_JOBS.items()
        if job["status"] in {"completed", "failed"}
    ]
    for old_job_id in completed[:50]:
        INFERENCE_JOBS.pop(old_job_id, None)


def pagination(
    requested_page: int,
    total: int,
    path: str,
    page_param: str = "page",
    page_size: int = PAGE_SIZE,
    **params,
) -> dict:
    total_pages = max(1, ceil(total / page_size))
    current_page = min(max(requested_page, 1), total_pages)

    def page_url(target: int) -> str:
        return f"{path}?{urlencode({**params, page_param: target})}"

    start = max(1, current_page - 2)
    end = min(total_pages, current_page + 2)
    return {
        "page": current_page,
        "total": total,
        "total_pages": total_pages,
        "offset": (current_page - 1) * page_size,
        "page_size": page_size,
        "pages": [(number, page_url(number)) for number in range(start, end + 1)],
        "previous_url": page_url(current_page - 1) if current_page > 1 else None,
        "next_url": page_url(current_page + 1) if current_page < total_pages else None,
    }


def account_form_values(form) -> dict:
    return {
        "name": str(form.get("name", "")).strip(),
        "endpoint_url": str(form.get("endpoint_url", "")).strip(),
        "notes": str(form.get("notes", "")).strip(),
        "api_key_label": str(form.get("api_key_label", "")).strip(),
        "api_key": str(form.get("api_key", "")).strip(),
        "remove_api_key": form.get("remove_api_key") == "on",
        "timeout_seconds": str(form.get("timeout_seconds", "10")),
        "interval_minutes": str(form.get("interval_minutes", "60")),
        "enabled": form.get("enabled") == "on",
    }


def validate_account_form(values: dict) -> dict:
    errors: list[str] = []
    try:
        endpoint_url = validate_endpoint_url(values["endpoint_url"])
    except ValueError as exc:
        errors.append(str(exc))
        endpoint_url = values["endpoint_url"]
    if not values["name"]:
        errors.append("Provider name is required.")
    if not values["api_key_label"]:
        errors.append("API key label is required.")
    try:
        timeout_seconds = int(values["timeout_seconds"])
        if not 1 <= timeout_seconds <= 60:
            raise ValueError
    except ValueError:
        errors.append("Timeout must be between 1 and 60 seconds.")
        timeout_seconds = 10
    try:
        interval_minutes = int(values["interval_minutes"])
        if interval_minutes not in ALLOWED_INTERVALS:
            raise ValueError
    except ValueError:
        errors.append("Select a supported check interval.")
        interval_minutes = 60
    return {
        "errors": errors,
        "clean": {
            **values,
            "endpoint_url": endpoint_url,
            "timeout_seconds": timeout_seconds,
            "interval_minutes": interval_minutes,
        },
    }


def verification_candidate(clean: dict, existing_secret: str | None = None) -> Account:
    if clean["remove_api_key"]:
        encrypted_api_key = encrypt_secret("")
    elif clean["api_key"]:
        encrypted_api_key = encrypt_secret(clean["api_key"])
    elif existing_secret:
        encrypted_api_key = existing_secret
    else:
        encrypted_api_key = encrypt_secret("")

    return Account(
        name=clean["name"],
        endpoint_url=clean["endpoint_url"],
        notes=clean.get("notes") or None,
        encrypted_api_key=encrypted_api_key,
        timeout_seconds=clean["timeout_seconds"],
        interval_minutes=clean["interval_minutes"],
        enabled=clean["enabled"],
    )


def account_connection_changed(account: Account, clean: dict) -> bool:
    if account.endpoint_url != clean["endpoint_url"]:
        return True
    current_secret = decrypt_secret(account.encrypted_api_key)
    next_secret = "" if clean["remove_api_key"] else clean["api_key"]
    return next_secret != current_secret


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/system/restart")
async def restart_application(request: Request):
    form = await request.form()
    expected_token = templates.env.globals["restart_token"]
    supplied_token = str(form.get("restart_token", ""))
    if not supplied_token or not hmac.compare_digest(supplied_token, expected_token):
        raise HTTPException(403, "Invalid restart token.")
    if settings.service_manager not in {"launcher", "systemd-user"}:
        raise HTTPException(503, "Unsupported application service manager.")
    restart_helper = BASE_DIR.parent / "scripts" / "restart-service"
    if not restart_helper.is_file():
        raise HTTPException(503, "Application restart helper is unavailable.")
    try:
        subprocess.Popen(
            [str(restart_helper), settings.service_manager],
            cwd=BASE_DIR.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise HTTPException(503, f"Application restart could not be scheduled: {exc}")
    return JSONResponse(
        {"status": "restarting"},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    db: DbSession,
    saved: bool = False,
    maintenance: str = "",
):
    preferences = get_app_preferences(db)
    maintenance_message = ""
    if maintenance.startswith("pruned-"):
        maintenance_message = (
            f"History cleanup completed. {maintenance.removeprefix('pruned-')} "
            "expired provider check and inference records were deleted."
        )
    elif maintenance.startswith("inference-cleared-"):
        maintenance_message = (
            f"Inference results cleared. "
            f"{maintenance.removeprefix('inference-cleared-')} rows were deleted."
        )
    return templates.TemplateResponse(
        request,
        "settings.html",
        template_context(
            request,
            preferences=preferences,
            counts=stored_result_counts(db),
            saved=saved,
            maintenance_message=maintenance_message,
            errors=[],
        ),
    )


@app.post("/settings")
async def update_settings(request: Request, db: DbSession):
    form = await request.form()
    values = {
        "auto_monitoring": form.get("auto_monitoring") == "on",
        "auto_inference": form.get("auto_inference") == "on",
        "default_monitoring": form.get("default_monitoring") == "on",
    }
    errors = []
    numeric_fields = {
        "history_days": HISTORY_DAY_OPTIONS,
        "concurrent_checks": CONCURRENCY_OPTIONS,
        "concurrent_inference": CONCURRENCY_OPTIONS,
        "auto_inference_interval_hours": AUTO_INFERENCE_INTERVAL_OPTIONS,
        "default_interval_minutes": ALLOWED_INTERVALS,
        "table_page_size": TABLE_PAGE_SIZE_OPTIONS,
    }
    for field, options in numeric_fields.items():
        try:
            value = int(str(form.get(field, "")))
            if value not in options:
                raise ValueError
            values[field] = value
        except ValueError:
            errors.append(f"Invalid value for {field.replace('_', ' ')}.")
    try:
        timeout = int(str(form.get("default_timeout_seconds", "")))
        if not 1 <= timeout <= 60:
            raise ValueError
        values["default_timeout_seconds"] = timeout
    except ValueError:
        errors.append("Default timeout must be between 1 and 60 seconds.")

    if errors:
        fallback = get_app_preferences(db)
        fallback.update(values)
        return templates.TemplateResponse(
            request,
            "settings.html",
            template_context(
                request,
                preferences=fallback,
                counts=stored_result_counts(db),
                saved=False,
                maintenance_message="",
                errors=errors,
            ),
            status_code=422,
        )
    save_app_preferences(db, values)
    return RedirectResponse("/settings?saved=true", status_code=303)


@app.post("/settings/prune-history")
def prune_history_now(db: DbSession):
    preferences = get_app_preferences(db)
    deleted = prune_old_results(db, preferences["history_days"])
    return RedirectResponse(
        f"/settings?maintenance=pruned-{deleted}",
        status_code=303,
    )


@app.post("/settings/clear-inference")
def clear_inference_now(db: DbSession):
    deleted = clear_inference_results(db)
    return RedirectResponse(
        f"/settings?maintenance=inference-cleared-{deleted}",
        status_code=303,
    )


@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request):
    return templates.TemplateResponse(
        request,
        "about.html",
        template_context(
            request,
            app_version=APP_VERSION,
            release_date=RELEASE_DATE,
            release_history=RELEASE_HISTORY,
        ),
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: DbSession,
    page: int = 1,
    period: str = "24h",
):
    page_size = get_app_preferences(db)["table_page_size"]
    provider_rows = provider_catalog_rows(db)
    total = db.scalar(select(func.count(Account.id))) or 0
    pager = pagination(
        page, total, "/", page_size=page_size, period=period
    )
    accounts = list(
        db.scalars(
            select(Account)
            .order_by(*provider_ordering())
            .offset(pager["offset"])
            .limit(page_size)
        )
    )
    healthy = db.scalar(
        select(func.count(Account.id)).where(Account.last_status == "healthy")
    ) or 0
    down = db.scalar(
        select(func.count(Account.id)).where(Account.last_status == "down")
    ) or 0
    disabled = db.scalar(
        select(func.count(Account.id)).where(Account.enabled.is_(False))
    ) or 0
    openai = db.scalar(
        select(func.count(Account.id)).where(Account.provider_type == "openai")
    ) or 0
    anthropic = db.scalar(
        select(func.count(Account.id)).where(Account.provider_type == "anthropic")
    ) or 0
    model_total = sum(
        len(account.models) for account in db.scalars(select(Account))
    )
    auto_monitoring = is_auto_monitoring_enabled(db)
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    check_total = db.scalar(
        select(func.count(CheckResult.id)).where(CheckResult.checked_at >= since)
    ) or 0
    check_healthy = db.scalar(
        select(func.count(CheckResult.id)).where(
            CheckResult.checked_at >= since, CheckResult.status == "healthy"
        )
    ) or 0
    latest = list(
        db.scalars(
            select(CheckResult)
            .order_by(CheckResult.checked_at.desc())
            .limit(12)
        )
    )
    chart_since = datetime.now(timezone.utc) - timedelta(days=30)
    inference_history = list(
        db.scalars(
            select(ModelInferenceHistory)
            .where(ModelInferenceHistory.checked_at >= chart_since)
            .order_by(ModelInferenceHistory.checked_at)
        )
    )
    chart = inference_chart_data(inference_history, period)
    recent_inference = list(
        db.scalars(
            select(ModelInferenceHistory)
            .order_by(ModelInferenceHistory.checked_at.desc())
            .limit(12)
        )
    )
    inference_snapshot: dict[str, int] = {}
    for row in provider_rows:
        for status, count in row["inference_summary"].items():
            inference_snapshot[status] = inference_snapshot.get(status, 0) + count
    overdue = sum(
        row["account"].enabled
        and aware_datetime(row["account"].next_check_at)
        and aware_datetime(row["account"].next_check_at)
        < datetime.now(timezone.utc)
        for row in provider_rows
    )
    never_checked = sum(
        not row["account"].last_checked_at for row in provider_rows
    )
    stale = sum(
        row["account"].last_checked_at
        and model_freshness(row["account"].last_checked_at) == "stale"
        for row in provider_rows
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        template_context(
            request,
            accounts=accounts,
            pager=pager,
            latest=latest,
            recent_inference=recent_inference,
            chart=chart,
            attention=dashboard_attention(provider_rows),
            stats={
                "total": total,
                "healthy": healthy,
                "down": down,
                "disabled": disabled,
                "openai": openai,
                "anthropic": anthropic,
                "model_total": model_total,
                "auto_monitoring": auto_monitoring,
                "uptime": uptime_percent(check_healthy, check_total),
                "overdue": overdue,
                "never_checked": never_checked,
                "stale": stale,
                "average_latency": chart["average_latency"],
                "inference_available": inference_snapshot.get("available", 0),
                "inference_failed": sum(
                    inference_snapshot.get(status, 0)
                    for status in (
                        "failed",
                        "forbidden",
                        "unauthorized",
                        "unavailable",
                    )
                ),
                "quota_exceeded": inference_snapshot.get("quota_exceeded", 0),
            },
        ),
    )


@app.get("/accounts", response_class=HTMLResponse)
def accounts_page(
    request: Request,
    db: DbSession,
    q: str = "",
    status: str = "all",
    compatibility: str = "all",
    monitoring: str = "all",
    models: str = "all",
    inference: str = "all",
    freshness: str = "all",
    sort: str = "name_asc",
    page: int = 1,
):
    page_size = get_app_preferences(db)["table_page_size"]
    all_rows = provider_catalog_rows(db)
    rows = all_rows
    search = q.strip().casefold()
    if search:
        rows = [
            row
            for row in rows
            if search in row["account"].name.casefold()
            or search in row["account"].endpoint_url.casefold()
            or search in (row["account"].api_key_label or "").casefold()
        ]
    if status == "disabled":
        rows = [row for row in rows if not row["account"].enabled]
    elif status in {"healthy", "down", "pending"}:
        rows = [
            row
            for row in rows
            if row["account"].enabled and row["account"].last_status == status
        ]
    if compatibility in {"openai", "anthropic", "unknown"}:
        rows = [
            row
            for row in rows
            if row["account"].provider_type == compatibility
        ]
    if monitoring == "enabled":
        rows = [row for row in rows if row["account"].enabled]
    elif monitoring == "disabled":
        rows = [row for row in rows if not row["account"].enabled]
    if models == "with":
        rows = [row for row in rows if row["model_count"] > 0]
    elif models == "without":
        rows = [row for row in rows if row["model_count"] == 0]
    if inference == "tested":
        rows = [row for row in rows if row["tested_count"] > 0]
    elif inference == "available":
        rows = [row for row in rows if row["available_count"] > 0]
    elif inference == "untested":
        rows = [row for row in rows if row["tested_count"] == 0]
    if freshness in {"fresh", "aging"}:
        rows = [row for row in rows if row["freshness"] == freshness]
    elif freshness == "stale":
        rows = [
            row for row in rows if row["freshness"] in {"stale", "never"}
        ]

    sorters = {
        "name_asc": lambda row: row["account"].name.casefold(),
        "name_desc": lambda row: row["account"].name.casefold(),
        "status": lambda row: (
            not row["account"].enabled,
            row["account"].last_status,
            row["account"].name.casefold(),
        ),
        "models_desc": lambda row: (
            -row["model_count"],
            row["account"].name.casefold(),
        ),
        "available_desc": lambda row: (
            -row["available_count"],
            row["account"].name.casefold(),
        ),
        "latency_asc": lambda row: (
            row["latency_ms"] is None,
            row["latency_ms"] or 0,
            row["account"].name.casefold(),
        ),
        "checked_desc": lambda row: (
            -(
                row["account"].last_checked_at.timestamp()
                if row["account"].last_checked_at
                else 0
            ),
            row["account"].name.casefold(),
        ),
    }
    rows.sort(
        key=sorters.get(sort, sorters["name_asc"]),
        reverse=sort == "name_desc",
    )
    total = len(rows)
    filter_params = {
        "q": q,
        "status": status,
        "compatibility": compatibility,
        "monitoring": monitoring,
        "models": models,
        "inference": inference,
        "freshness": freshness,
        "sort": sort,
    }
    pager = pagination(
        page, total, "/accounts", page_size=page_size, **filter_params
    )
    rows = rows[pager["offset"] : pager["offset"] + page_size]
    return templates.TemplateResponse(
        request,
        "accounts.html",
        template_context(
            request,
            provider_rows=rows,
            query=q,
            filters=filter_params,
            pager=pager,
            provider_total=len(all_rows),
            stats=provider_catalog_stats(all_rows),
        ),
    )


@app.get("/models", response_class=HTMLResponse)
def available_models_page(
    request: Request,
    db: DbSession,
    q: str = "",
    provider: int = 0,
    compatibility: str = "all",
    capability: str = "all",
    availability: str = "all",
    latency: str = "all",
    freshness: str = "all",
    sort: str = "name_asc",
    page: int = 1,
):
    page_size = get_app_preferences(db)["table_page_size"]
    all_groups = available_model_groups(db)
    groups = available_model_groups(
        db,
        q,
        provider_id=provider or None,
        compatibility=compatibility,
        capability=capability,
        availability=availability,
        latency=latency,
        freshness=freshness,
        sort=sort,
    )
    filter_params = {
        "q": q,
        "provider": provider,
        "compatibility": compatibility,
        "capability": capability,
        "availability": availability,
        "latency": latency,
        "freshness": freshness,
        "sort": sort,
    }
    pager = pagination(
        page,
        len(groups),
        "/models",
        page_size=page_size,
        **filter_params,
    )
    models = groups[pager["offset"] : pager["offset"] + page_size]
    provider_ids = {
        provider_id for group in all_groups for provider_id in group["provider_ids"]
    }
    providers = list(
        db.scalars(
            select(Account)
            .where(Account.id.in_(provider_ids))
            .order_by(*provider_ordering())
        )
    ) if provider_ids else []
    return templates.TemplateResponse(
        request,
        "models.html",
        template_context(
            request,
            models=models,
            query=q,
            pager=pager,
            model_total=len(all_groups),
            filtered_total=len(groups),
            stats=available_model_stats(all_groups),
            providers=providers,
            filters=filter_params,
        ),
    )


@app.get("/models/detail")
def available_model_detail(model_id: str, db: DbSession):
    rows = db.execute(
        select(ModelInferenceResult, Account)
        .join(Account, Account.id == ModelInferenceResult.account_id)
        .where(
            ModelInferenceResult.status == "available",
            func.lower(ModelInferenceResult.model_id) == model_id.casefold(),
        )
    )
    providers = []
    display_model_id = model_id
    for result, account in rows:
        if result.model_id not in account.models:
            continue
        display_model_id = result.model_id
        model_detail = next(
            (
                detail
                for detail in account.model_details
                if detail["id"] == result.model_id
            ),
            {"capabilities": {}},
        )
        providers.append(
            {
                "provider": account.name,
                "provider_id": account.id,
                "base_url": account.endpoint_url,
                "api_key_label": account.api_key_label,
                "api_key": decrypt_secret(account.encrypted_api_key),
                "latency_ms": result.latency_ms,
                "http_status": result.http_status,
                "last_tested": format_datetime(result.checked_at),
                "compatibility": account.provider_type,
                "provider_status": (
                    "disabled" if not account.enabled else account.last_status
                ),
                "notes": account.notes or "",
                "capabilities": model_detail["capabilities"],
            }
        )
    if not providers:
        raise HTTPException(404, "Available model could not be found.")
    providers.sort(
        key=lambda item: (
            item["latency_ms"] is None,
            item["latency_ms"] or 0,
            item["provider"].casefold(),
        )
    )
    return JSONResponse(
        {
            "model_id": display_model_id,
            "providers": providers,
            "openai_config": {
                "model": display_model_id,
                "base_url": providers[0]["base_url"],
                "api_key": providers[0]["api_key"],
            },
        },
        headers={"Cache-Control": "no-store"},
    )


@app.post("/models/test")
async def test_model_across_providers(
    request: Request,
    db: DbSession,
    model_id: str,
):
    accounts = [
        account
        for account in db.scalars(select(Account).order_by(*provider_ordering()))
        if any(model.casefold() == model_id.casefold() for model in account.models)
    ]
    if not accounts:
        raise HTTPException(404, "No provider currently exposes this model.")
    prune_inference_jobs()
    job_id = uuid4().hex
    INFERENCE_JOBS[job_id] = {
        "id": job_id,
        "model_id": model_id,
        "status": "running",
        "total": len(accounts),
        "completed": 0,
        "models": [model_id],
        "summary": {},
        "logs": [],
        "error": None,
    }
    task = asyncio.create_task(
        run_model_provider_job(
            job_id,
            model_id,
            [account.id for account in accounts],
        ),
        name=f"model-provider-test-{job_id}",
    )
    INFERENCE_JOBS[job_id]["task"] = task
    payload = {
        "job_id": job_id,
        "progress_url": f"/models/test/{job_id}",
    }
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(payload, status_code=202)
    return RedirectResponse("/models", status_code=303)


@app.get("/models/test/{job_id}")
def model_inference_job_status(job_id: str):
    job = INFERENCE_JOBS.get(job_id)
    if not job or "model_id" not in job:
        raise HTTPException(404)
    return JSONResponse(inference_job_payload(job), headers={"Cache-Control": "no-store"})


@app.get("/accounts/new", response_class=HTMLResponse)
def new_account(request: Request, db: DbSession):
    preferences = get_app_preferences(db)
    return templates.TemplateResponse(
        request,
        "account_form.html",
        template_context(
            request,
            account=None,
            values={
                "timeout_seconds": preferences["default_timeout_seconds"],
                "interval_minutes": preferences["default_interval_minutes"],
                "enabled": preferences["default_monitoring"],
                "api_key_label": "Default",
            },
            errors=[],
            verification=None,
        ),
    )


@app.post("/accounts/verify", response_class=HTMLResponse)
async def verify_account(request: Request, db: DbSession):
    form = await request.form()
    values = account_form_values(form)
    result = validate_account_form(values)
    account_id_value = str(form.get("account_id", "")).strip()
    account = None
    existing_secret = None

    if account_id_value:
        try:
            account = db.get(Account, int(account_id_value))
        except ValueError:
            account = None
        if not account:
            raise HTTPException(404)
        existing_secret = account.encrypted_api_key

    if result["errors"]:
        return templates.TemplateResponse(
            request,
            "account_form.html",
            template_context(
                request,
                account=account,
                values=values,
                errors=result["errors"],
                verification=None,
            ),
            status_code=422,
        )

    candidate = verification_candidate(result["clean"], existing_secret)
    verification = await probe_account(candidate)
    return templates.TemplateResponse(
        request,
        "account_form.html",
        template_context(
            request,
            account=account,
            values=values,
            errors=[],
            verification=verification,
        ),
    )


@app.post("/accounts/new", response_class=HTMLResponse)
async def create_account(request: Request, db: DbSession):
    values = account_form_values(await request.form())
    result = validate_account_form(values)
    if db.scalar(select(Account).where(Account.name == values["name"])):
        result["errors"].append("An account with this name already exists.")
    if result["errors"]:
        return templates.TemplateResponse(
            request,
            "account_form.html",
            template_context(
                request,
                account=None,
                values=values,
                errors=result["errors"],
                verification=None,
            ),
            status_code=422,
        )
    clean = result["clean"]
    account = Account(
        name=clean["name"],
        endpoint_url=clean["endpoint_url"],
        notes=clean["notes"] or None,
        api_key_label=clean["api_key_label"],
        encrypted_api_key=encrypt_secret(clean["api_key"]),
        timeout_seconds=clean["timeout_seconds"],
        interval_minutes=clean["interval_minutes"],
        enabled=clean["enabled"],
    )
    account.next_check_at = datetime.now(timezone.utc) if account.enabled else None
    db.add(account)
    db.commit()
    return RedirectResponse("/accounts", status_code=303)


@app.get("/accounts/{account_id}", response_class=HTMLResponse)
def account_detail(
    request: Request,
    account_id: int,
    db: DbSession,
    page: int = 1,
    model_page: int = 1,
    model_q: str = "",
    capability: str = "all",
    inference_access: str = "all",
    view: str = "models",
    inference_page: int = 1,
    inference_q: str = "",
    inference_status: str = "all",
):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404)
    check_query = select(CheckResult).where(CheckResult.account_id == account_id)
    check_total = db.scalar(
        select(func.count()).select_from(check_query.subquery())
    ) or 0
    page_size = get_app_preferences(db)["table_page_size"]
    active_view = view if view in {"models", "inference", "checks"} else "models"
    pager = pagination(
        page,
        check_total,
        f"/accounts/{account_id}",
        page_size=page_size,
        model_page=model_page,
        model_q=model_q,
        capability=capability,
        inference_access=inference_access,
        view="checks",
    )
    checks = list(
        db.scalars(
            check_query.order_by(CheckResult.checked_at.desc())
            .offset(pager["offset"])
            .limit(page_size)
        )
    )
    latest_check = db.scalar(
        check_query.order_by(CheckResult.checked_at.desc()).limit(1)
    )
    healthy_24h, total_24h = account_metrics(db, account_id, 24)
    healthy_7d, total_7d = account_metrics(db, account_id, 24 * 7)
    all_inference_rows = list(
        db.scalars(
            select(ModelInferenceResult).where(
                ModelInferenceResult.account_id == account_id
            )
        )
    )
    all_inference_results = {row.model_id: row for row in all_inference_rows}
    history_query = select(ModelInferenceHistory).where(
        ModelInferenceHistory.account_id == account_id
    )
    history_search = inference_q.strip()
    if history_search:
        history_query = history_query.where(
            ModelInferenceHistory.model_id.ilike(f"%{history_search}%")
        )
    valid_history_statuses = {
        "available",
        "forbidden",
        "unauthorized",
        "unavailable",
        "quota_exceeded",
        "timeout",
        "failed",
        "unsupported",
    }
    history_status_filter = (
        inference_status
        if inference_status in valid_history_statuses
        else "all"
    )
    if history_status_filter != "all":
        history_query = history_query.where(
            ModelInferenceHistory.status == history_status_filter
        )
    history_total = db.scalar(
        select(func.count()).select_from(history_query.subquery())
    ) or 0
    history_pager = pagination(
        inference_page,
        history_total,
        f"/accounts/{account_id}",
        page_param="inference_page",
        page_size=page_size,
        view="inference",
        inference_q=inference_q,
        inference_status=history_status_filter,
    )
    inference_history_rows = list(
        db.scalars(
            history_query.order_by(ModelInferenceHistory.checked_at.desc())
            .offset(history_pager["offset"])
            .limit(page_size)
        )
    )
    inference_history_available = db.scalar(
        select(func.count(ModelInferenceHistory.id)).where(
            ModelInferenceHistory.account_id == account_id,
            ModelInferenceHistory.status == "available",
        )
    ) or 0
    inference_history_total = db.scalar(
        select(func.count(ModelInferenceHistory.id)).where(
            ModelInferenceHistory.account_id == account_id
        )
    ) or 0
    inference_status_order = (
        "available",
        "forbidden",
        "unauthorized",
        "unavailable",
        "quota_exceeded",
        "timeout",
        "failed",
        "unsupported",
        "not_tested",
    )
    inference_summary = {status: 0 for status in inference_status_order}
    for model_id in account.models:
        result = all_inference_results.get(model_id)
        status = result.status if result else "not_tested"
        inference_summary[status] = inference_summary.get(status, 0) + 1

    model_query = model_q.strip().casefold()
    capability_filter = capability if capability in {
        "all",
        "vision",
        "reasoning",
        "audio",
        "tools",
        "none",
    } else "all"
    inference_filter = inference_access if inference_access in {
        "all",
        "available",
        "unauthorized",
        "forbidden",
        "unavailable",
        "quota_exceeded",
        "timeout",
        "failed",
        "unsupported",
        "not_tested",
    } else "all"
    filtered_models = [
        model
        for model in account.model_details
        if not model_query
        or model_query in model["id"].casefold()
        or model_query in model["display_name"].casefold()
    ]
    if capability_filter != "all":
        filtered_models = [
            model
            for model in filtered_models
            if (
                not model["capabilities"]
                if capability_filter == "none"
                else capability_filter in model["capabilities"]
            )
        ]
    if inference_filter != "all":
        filtered_models = [
            model
            for model in filtered_models
            if (
                all_inference_results.get(model["id"]).status
                if all_inference_results.get(model["id"])
                else "not_tested"
            )
            == inference_filter
        ]
    model_pager = pagination(
        model_page,
        len(filtered_models),
        f"/accounts/{account_id}",
        page_param="model_page",
        page_size=page_size,
        page=page,
        model_q=model_q,
        capability=capability_filter,
        inference_access=inference_filter,
        view="models",
    )
    models = filtered_models[
        model_pager["offset"] : model_pager["offset"] + page_size
    ]
    inference_results = {
        model["id"]: all_inference_results[model["id"]]
        for model in models
        if model["id"] in all_inference_results
    }
    return templates.TemplateResponse(
        request,
        "account_detail.html",
        template_context(
            request,
            account=account,
            checks=checks,
            latest_check=latest_check,
            pager=pager,
            models=models,
            model_pager=model_pager,
            model_query=model_q,
            capability_filter=capability_filter,
            inference_filter=inference_filter,
            inference_results=inference_results,
            inference_summary=inference_summary,
            inference_status_order=inference_status_order,
            active_view=active_view,
            inference_history=inference_history_rows,
            inference_history_pager=history_pager,
            inference_history_query=inference_q,
            inference_history_status=history_status_filter,
            inference_history_total=inference_history_total,
            inference_history_success=uptime_percent(
                inference_history_available, inference_history_total
            ),
            auto_monitoring=is_auto_monitoring_enabled(db),
            uptime_24h=uptime_percent(healthy_24h, total_24h),
            uptime_7d=uptime_percent(healthy_7d, total_7d),
        ),
    )


@app.get("/accounts/{account_id}/summary")
def account_summary(account_id: int, db: DbSession):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404)
    current_models = set(account.models)
    inference_rows = [
        row
        for row in db.scalars(
            select(ModelInferenceResult).where(
                ModelInferenceResult.account_id == account_id
            )
        )
        if row.model_id in current_models
    ]
    inference_summary: dict[str, int] = {}
    for row in inference_rows:
        inference_summary[row.status] = inference_summary.get(row.status, 0) + 1
    return JSONResponse(
        {
            "id": account.id,
            "name": account.name,
            "base_url": account.endpoint_url,
            "api_key_label": account.api_key_label,
            "api_key": decrypt_secret(account.encrypted_api_key),
            "notes": account.notes or "",
            "model_count": len(account.models),
            "compatibility": account.provider_type,
            "status": "disabled" if not account.enabled else account.last_status,
            "monitoring": "enabled" if account.enabled else "disabled",
            "interval_minutes": account.interval_minutes,
            "last_checked": format_datetime(account.last_checked_at),
            "last_latency_ms": account.last_inference_latency_ms,
            "inference_summary": inference_summary,
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/accounts/{account_id}/api-key")
def account_api_key(account_id: int, db: DbSession):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404)
    return JSONResponse(
        {"api_key": decrypt_secret(account.encrypted_api_key)},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
def edit_account(request: Request, account_id: int, db: DbSession):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404)
    values = {
        "name": account.name,
        "endpoint_url": account.endpoint_url,
        "notes": account.notes or "",
        "api_key_label": account.api_key_label,
        "api_key": decrypt_secret(account.encrypted_api_key),
        "timeout_seconds": account.timeout_seconds,
        "interval_minutes": account.interval_minutes,
        "enabled": account.enabled,
    }
    return templates.TemplateResponse(
        request,
        "account_form.html",
        template_context(
            request, account=account, values=values, errors=[]
        ),
    )


@app.post("/accounts/{account_id}/edit", response_class=HTMLResponse)
async def update_account(request: Request, account_id: int, db: DbSession):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404)
    values = account_form_values(await request.form())
    result = validate_account_form(values)
    duplicate = db.scalar(
        select(Account).where(Account.name == values["name"], Account.id != account_id)
    )
    if duplicate:
        result["errors"].append("An account with this name already exists.")
    if result["errors"]:
        return templates.TemplateResponse(
            request,
            "account_form.html",
            template_context(
                request,
                account=account,
                values=values,
                errors=result["errors"],
                verification=None,
            ),
            status_code=422,
        )
    clean = result["clean"]
    connection_changed = account_connection_changed(account, clean)
    for field in (
        "name",
        "endpoint_url",
        "notes",
        "api_key_label",
        "timeout_seconds",
        "interval_minutes",
        "enabled",
    ):
        setattr(account, field, clean[field])
    if clean["remove_api_key"]:
        account.encrypted_api_key = encrypt_secret("")
    elif connection_changed:
        account.encrypted_api_key = encrypt_secret(clean["api_key"])
    if connection_changed:
        account.provider_type = "unknown"
        account.models_json = "[]"
        account.models_endpoint = None
        account.last_status = "pending"
    account.next_check_at = datetime.now(timezone.utc) if account.enabled else None
    db.commit()
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@app.post("/accounts/{account_id}/toggle")
def toggle_account(account_id: int, db: DbSession):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404)
    account.enabled = not account.enabled
    account.next_check_at = datetime.now(timezone.utc) if account.enabled else None
    db.commit()
    return RedirectResponse("/accounts", status_code=303)


@app.post("/accounts/bulk")
async def bulk_account_action(request: Request, db: DbSession):
    form = await request.form()
    action = str(form.get("bulk_action", "")).strip()
    account_ids = []
    for value in form.getlist("account_ids"):
        try:
            account_ids.append(int(str(value)))
        except ValueError:
            continue
    accounts = list(
        db.scalars(select(Account).where(Account.id.in_(account_ids)))
    ) if account_ids else []
    if not accounts:
        raise HTTPException(400, "Select at least one provider.")
    if action in {"enable", "disable"}:
        enabled = action == "enable"
        now = datetime.now(timezone.utc)
        for account in accounts:
            account.enabled = enabled
            account.next_check_at = now if enabled else None
        db.commit()
    elif action == "load_models":
        await asyncio.gather(
            *(scheduler.check_account(account.id) for account in accounts)
        )
    elif action == "delete":
        for account in accounts:
            db.delete(account)
        db.commit()
    else:
        raise HTTPException(400, "Select a valid bulk action.")
    return RedirectResponse("/accounts", status_code=303)


@app.post("/accounts/{account_id}/check")
async def check_now(account_id: int, db: DbSession):
    if not db.get(Account, account_id):
        raise HTTPException(404)
    await scheduler.check_account(account_id)
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@app.post("/accounts/{account_id}/test-models")
async def test_account_models(request: Request, account_id: int, db: DbSession):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404)
    if not account.models:
        raise HTTPException(400, "No models are available for inference testing.")
    prune_inference_jobs()

    job_id = uuid4().hex
    INFERENCE_JOBS[job_id] = {
        "id": job_id,
        "account_id": account_id,
        "status": "running",
        "total": len(account.models),
        "completed": 0,
        "models": list(account.models),
        "summary": {},
        "logs": [],
        "error": None,
    }
    task = asyncio.create_task(
        run_inference_job(job_id, account_id),
        name=f"inference-test-{account_id}-{job_id}",
    )
    INFERENCE_JOBS[job_id]["task"] = task
    payload = {
        "job_id": job_id,
        "progress_url": f"/accounts/{account_id}/test-models/{job_id}",
    }
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(payload, status_code=202)
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@app.get("/accounts/{account_id}/test-models/{job_id}")
def inference_job_status(account_id: int, job_id: str):
    job = INFERENCE_JOBS.get(job_id)
    if not job or job["account_id"] != account_id:
        raise HTTPException(404)
    return JSONResponse(inference_job_payload(job), headers={"Cache-Control": "no-store"})


@app.post("/accounts/{account_id}/delete")
def delete_account(account_id: int, db: DbSession):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404)
    db.delete(account)
    db.commit()
    return RedirectResponse("/accounts", status_code=303)


@app.exception_handler(SecretConfigurationError)
def secret_error(request: Request, exc: SecretConfigurationError):
    return templates.TemplateResponse(
        request,
        "error.html",
        template_context(request, title="Configuration error", message=str(exc)),
        status_code=500,
    )
