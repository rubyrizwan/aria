from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "APICHECKER_DATABASE_URL", "sqlite:///./data/apichecker.db"
    )
    master_key: str | None = os.getenv("APICHECKER_MASTER_KEY")
    history_days: int = int(os.getenv("APICHECKER_HISTORY_DAYS", "30"))
    max_concurrent_checks: int = int(
        os.getenv("APICHECKER_MAX_CONCURRENT_CHECKS", "5")
    )
    scheduler_poll_seconds: int = int(
        os.getenv("APICHECKER_SCHEDULER_POLL_SECONDS", "10")
    )
    host: str = os.getenv("APICHECKER_HOST", "127.0.0.1").strip()
    port: int = int(os.getenv("APICHECKER_PORT", "8000"))
    service_manager: str = os.getenv(
        "APICHECKER_SERVICE_MANAGER", "launcher"
    ).strip().lower()


settings = Settings()
