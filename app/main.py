from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from math import ceil
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import Base, engine, get_db
from app.models import Account, CheckResult
from app.scheduler import scheduler
from app.security import SecretConfigurationError, encrypt_secret, get_fernet
from app.services import account_metrics, schedule_next, uptime_percent
from app.services import is_auto_monitoring_enabled, set_auto_monitoring
from app.validation import (
    ALLOWED_INTERVALS,
    validate_endpoint_url,
)
from app.version import __release_date__, __version__

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


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


app = FastAPI(title="API Checker", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

DbSession = Annotated[Session, Depends(get_db)]
PAGE_SIZE = 30
APP_VERSION = __version__
RELEASE_DATE = datetime.strptime(__release_date__, "%Y-%m-%d").strftime("%d %B %Y")


def template_context(request: Request, **values):
    return {"request": request, "path": request.url.path, **values}


def pagination(
    requested_page: int,
    total: int,
    path: str,
    page_param: str = "page",
    **params,
) -> dict:
    total_pages = max(1, ceil(total / PAGE_SIZE))
    current_page = min(max(requested_page, 1), total_pages)

    def page_url(target: int) -> str:
        return f"{path}?{urlencode({**params, page_param: target})}"

    start = max(1, current_page - 2)
    end = min(total_pages, current_page + 2)
    return {
        "page": current_page,
        "total": total,
        "total_pages": total_pages,
        "offset": (current_page - 1) * PAGE_SIZE,
        "pages": [(number, page_url(number)) for number in range(start, end + 1)],
        "previous_url": page_url(current_page - 1) if current_page > 1 else None,
        "next_url": page_url(current_page + 1) if current_page < total_pages else None,
    }


def account_form_values(form) -> dict:
    return {
        "name": str(form.get("name", "")).strip(),
        "endpoint_url": str(form.get("endpoint_url", "")).strip(),
        "api_key": str(form.get("api_key", "")),
        "remove_api_key": form.get("remove_api_key") == "on",
        "timeout_seconds": str(form.get("timeout_seconds", "10")),
        "interval_minutes": str(form.get("interval_minutes", "5")),
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
        interval_minutes = 5
    return {
        "errors": errors,
        "clean": {
            **values,
            "endpoint_url": endpoint_url,
            "timeout_seconds": timeout_seconds,
            "interval_minutes": interval_minutes,
        },
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: DbSession, saved: bool = False):
    return templates.TemplateResponse(
        request,
        "settings.html",
        template_context(
            request,
            auto_monitoring=is_auto_monitoring_enabled(db),
            saved=saved,
        ),
    )


@app.post("/settings")
async def update_settings(request: Request, db: DbSession):
    form = await request.form()
    set_auto_monitoring(db, form.get("auto_monitoring") == "on")
    return RedirectResponse("/settings?saved=true", status_code=303)


@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request):
    return templates.TemplateResponse(
        request,
        "about.html",
        template_context(
            request,
            app_version=APP_VERSION,
            release_date=RELEASE_DATE,
        ),
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: DbSession, page: int = 1):
    total = db.scalar(select(func.count(Account.id))) or 0
    pager = pagination(page, total, "/")
    accounts = list(
        db.scalars(
            select(Account)
            .order_by(Account.name)
            .offset(pager["offset"])
            .limit(PAGE_SIZE)
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
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        template_context(
            request,
            accounts=accounts,
            pager=pager,
            latest=latest,
            stats={
                "total": total,
                "healthy": healthy,
                "down": down,
                "disabled": disabled,
                "uptime": uptime_percent(check_healthy, check_total),
            },
        ),
    )


@app.get("/accounts", response_class=HTMLResponse)
def accounts_page(
    request: Request,
    db: DbSession,
    q: str = "",
    status: str = "all",
    page: int = 1,
):
    query = select(Account).order_by(Account.name)
    if q:
        query = query.where(Account.name.ilike(f"%{q.strip()}%"))
    if status == "disabled":
        query = query.where(Account.enabled.is_(False))
    elif status in {"healthy", "down", "pending"}:
        query = query.where(Account.enabled.is_(True), Account.last_status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    pager = pagination(page, total, "/accounts", q=q, status=status)
    accounts = list(db.scalars(query.offset(pager["offset"]).limit(PAGE_SIZE)))
    return templates.TemplateResponse(
        request,
        "accounts.html",
        template_context(
            request,
            accounts=accounts,
            query=q,
            status=status,
            pager=pager,
        ),
    )


@app.get("/accounts/new", response_class=HTMLResponse)
def new_account(request: Request):
    return templates.TemplateResponse(
        request,
        "account_form.html",
        template_context(
            request,
            account=None,
            values={
                "timeout_seconds": 10,
                "interval_minutes": 5,
                "enabled": True,
            },
            errors=[],
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
                request, account=None, values=values, errors=result["errors"]
            ),
            status_code=422,
        )
    clean = result["clean"]
    account = Account(
        name=clean["name"],
        endpoint_url=clean["endpoint_url"],
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
):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404)
    check_query = select(CheckResult).where(CheckResult.account_id == account_id)
    check_total = db.scalar(
        select(func.count()).select_from(check_query.subquery())
    ) or 0
    pager = pagination(
        page,
        check_total,
        f"/accounts/{account_id}",
        model_page=model_page,
        model_q=model_q,
    )
    checks = list(
        db.scalars(
            check_query.order_by(CheckResult.checked_at.desc())
            .offset(pager["offset"])
            .limit(PAGE_SIZE)
        )
    )
    latest_check = db.scalar(
        check_query.order_by(CheckResult.checked_at.desc()).limit(1)
    )
    healthy_24h, total_24h = account_metrics(db, account_id, 24)
    healthy_7d, total_7d = account_metrics(db, account_id, 24 * 7)
    model_query = model_q.strip().casefold()
    filtered_models = [
        model
        for model in account.model_details
        if not model_query
        or model_query in model["id"].casefold()
        or model_query in model["display_name"].casefold()
    ]
    model_pager = pagination(
        model_page,
        len(filtered_models),
        f"/accounts/{account_id}",
        page_param="model_page",
        page=page,
        model_q=model_q,
    )
    models = filtered_models[
        model_pager["offset"] : model_pager["offset"] + PAGE_SIZE
    ]
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
            uptime_24h=uptime_percent(healthy_24h, total_24h),
            uptime_7d=uptime_percent(healthy_7d, total_7d),
        ),
    )


@app.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
def edit_account(request: Request, account_id: int, db: DbSession):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404)
    values = {
        "name": account.name,
        "endpoint_url": account.endpoint_url,
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
                request, account=account, values=values, errors=result["errors"]
            ),
            status_code=422,
        )
    clean = result["clean"]
    for field in (
        "name",
        "endpoint_url",
        "timeout_seconds",
        "interval_minutes",
        "enabled",
    ):
        setattr(account, field, clean[field])
    if clean["remove_api_key"]:
        account.encrypted_api_key = encrypt_secret("")
    elif clean["api_key"]:
        account.encrypted_api_key = encrypt_secret(clean["api_key"])
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


@app.post("/accounts/{account_id}/check")
async def check_now(account_id: int, db: DbSession):
    if not db.get(Account, account_id):
        raise HTTPException(404)
    await scheduler.check_account(account_id)
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@app.post("/accounts/{account_id}/delete")
def delete_account(
    account_id: int,
    db: DbSession,
    confirmation: Annotated[str, Form()] = "",
):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404)
    if confirmation != account.name:
        raise HTTPException(400, "Account name confirmation did not match.")
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
