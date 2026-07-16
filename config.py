from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Config:
    screener_email: str
    screener_password: str
    data_dir: str
    watchlist_path: str
    state_path: str
    output_path: str
    storage_state_path: str
    min_delay_seconds: float
    max_delay_seconds: float
    max_retries_per_stock: int
    headless: bool
    scrape_cron: str
    allowed_origins: list[str] = field(default_factory=list)
    run_now_token: str = ""
    log_level: str = "INFO"

    @staticmethod
    def from_env() -> "Config":
        data_dir = _env("DATA_DIR", "./data")

        def derived(name: str, filename: str) -> str:
            return _env(name, os.path.join(data_dir, filename))

        allowed_origins_raw = _env("ALLOWED_ORIGINS", "*")
        allowed_origins = [o.strip() for o in allowed_origins_raw.split(",") if o.strip()]

        return Config(
            screener_email=_env("SCREENER_EMAIL", required=True),
            screener_password=_env("SCREENER_PASSWORD", required=True),
            data_dir=data_dir,
            watchlist_path=derived("WATCHLIST_PATH", "watchlist.json"),
            state_path=derived("STATE_PATH", "state.json"),
            output_path=derived("OUTPUT_PATH", "output.json"),
            storage_state_path=derived("STORAGE_STATE_PATH", "storage_state.json"),
            min_delay_seconds=float(_env("MIN_DELAY_SECONDS", "3")),
            max_delay_seconds=float(_env("MAX_DELAY_SECONDS", "7")),
            max_retries_per_stock=int(_env("MAX_RETRIES_PER_STOCK", "2")),
            headless=_env("HEADLESS", "true").strip().lower() != "false",
            scrape_cron=_env("SCRAPE_CRON", "0 3 * * *"),
            allowed_origins=allowed_origins,
            run_now_token=_env("RUN_NOW_TOKEN", ""),
            log_level=_env("LOG_LEVEL", "INFO"),
        )
