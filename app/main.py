from __future__ import annotations

import asyncio
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

from app.database import Base, SessionLocal, engine, get_db
from app.checker import InferenceResult, probe_account, test_model_inference
from app.models import Account, CheckResult, ModelInferenceResult
from app.scheduler import scheduler
from app.security import (
    SecretConfigurationError,
    decrypt_secret,
    encrypt_secret,
    get_fernet,
)
from app.services import (
    account_metrics,
    save_inference_result,
    update_inference_latency,
    uptime_percent,
)
from app.services import is_auto_monitoring_enabled, set_auto_monitoring
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
INFERENCE_JOBS: dict[str, dict] = {}


def provider_ordering():
    return func.lower(Account.name), Account.name


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
    semaphore = asyncio.Semaphore(5)
    try:
        with SessionLocal() as session:
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
            .order_by(*provider_ordering())
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
                "openai": openai,
                "anthropic": anthropic,
                "model_total": model_total,
                "auto_monitoring": auto_monitoring,
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
    provider_total = db.scalar(select(func.count(Account.id))) or 0
    query = select(Account).order_by(*provider_ordering())
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
            provider_total=provider_total,
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
                "interval_minutes": 60,
                "enabled": True,
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
        capability=capability,
        inference_access=inference_access,
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
    all_inference_rows = list(
        db.scalars(
            select(ModelInferenceResult).where(
                ModelInferenceResult.account_id == account_id
            )
        )
    )
    all_inference_results = {row.model_id: row for row in all_inference_rows}
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
        page=page,
        model_q=model_q,
        capability=capability_filter,
        inference_access=inference_filter,
    )
    models = filtered_models[
        model_pager["offset"] : model_pager["offset"] + PAGE_SIZE
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
    return JSONResponse(
        {
            "name": account.name,
            "base_url": account.endpoint_url,
            "api_key_label": account.api_key_label,
            "api_key": decrypt_secret(account.encrypted_api_key),
            "notes": account.notes or "",
            "model_count": len(account.models),
            "compatibility": account.provider_type,
            "status": "disabled" if not account.enabled else account.last_status,
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
    if len(INFERENCE_JOBS) > 100:
        completed = [
            job_id
            for job_id, job in INFERENCE_JOBS.items()
            if job["status"] in {"completed", "failed"}
        ]
        for old_job_id in completed[:50]:
            INFERENCE_JOBS.pop(old_job_id, None)

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
