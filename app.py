from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from config import Config
from logging_setup import configure_logging
from scrape_job import run_scrape_job
from state_store import load_json

SCRAPE_JOB_ID = "scrape"

config = Config.from_env()
configure_logging(config.log_level)
log = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(
        run_scrape_job,
        trigger=CronTrigger.from_crontab(config.scrape_cron),
        id=SCRAPE_JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    log.info("Scheduler started, scrape cron: %s", config.scrape_cron)
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Screener Watchlist Scraper", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/output")
def get_output():
    return load_json(config.output_path)


@app.post("/run-now")
def run_now(x_run_token: str = Header(default="")):
    if not config.run_now_token or x_run_token != config.run_now_token:
        raise HTTPException(status_code=401, detail="missing or invalid X-Run-Token header")

    job = scheduler.get_job(SCRAPE_JOB_ID)
    if job is None:
        raise HTTPException(status_code=503, detail="scheduler not ready yet")

    job.modify(next_run_time=datetime.now())
    return {"status": "triggered"}
