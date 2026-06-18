from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import or_, select

from app.checker import probe_account
from app.config import settings
from app.database import SessionLocal
from app.models import Account
from app.services import (
    is_auto_monitoring_enabled,
    prune_old_results,
    save_probe_result,
)

logger = logging.getLogger(__name__)


class CheckScheduler:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_checks)
        self._last_prune_date = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="api-check-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_due_checks()
                today = datetime.now(timezone.utc).date()
                if self._last_prune_date != today:
                    with SessionLocal() as session:
                        prune_old_results(session)
                    self._last_prune_date = today
            except Exception:
                logger.exception("Scheduler cycle failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=settings.scheduler_poll_seconds
                )
            except TimeoutError:
                pass

    async def run_due_checks(self) -> None:
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            if not is_auto_monitoring_enabled(session):
                return
            ids = list(
                session.scalars(
                    select(Account.id).where(
                        Account.enabled.is_(True),
                        or_(Account.next_check_at.is_(None), Account.next_check_at <= now),
                    )
                )
            )
        await asyncio.gather(*(self.check_account(account_id) for account_id in ids))

    async def check_account(self, account_id: int):
        async with self._semaphore:
            with SessionLocal() as session:
                account = session.get(Account, account_id)
                if not account:
                    return None
                result = await probe_account(account)
                return save_probe_result(session, account, result)


scheduler = CheckScheduler()
