from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import Optional

from playwright.sync_api import sync_playwright

import auth
from config import Config
from growth import evaluate_quarter
from logging_setup import configure_logging
from scraper import fetch_with_retry
from state_store import load_json, merge_output, save_json
from watchlist import load_watchlist

log = logging.getLogger(__name__)


def run_scrape_job(config: Optional[Config] = None) -> dict:
    """Run one full pass over the watchlist. Safe to call repeatedly (e.g. from
    a scheduler) - never calls sys.exit, always returns a summary dict."""
    config = config or Config.from_env()
    configure_logging(config.log_level)

    log.info("Starting scrape run")
    stocks = load_watchlist(config.watchlist_path)
    log.info("Loaded %d stocks from %s", len(stocks), config.watchlist_path)

    state = load_json(config.state_path)
    output = load_json(config.output_path)

    counts = {"unchanged": 0, "new_classified": 0, "new_skipped": 0, "errors": 0}
    errors: list[tuple[str, str]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.headless)
        try:
            context = browser.new_context()
            page = context.new_page()
            auth.perform_login(page, config)

            for stock in stocks:
                try:
                    qd = fetch_with_retry(page, stock.slug, config)
                    if qd is None:
                        counts["errors"] += 1
                        errors.append((stock.slug, "no quarterly results table found"))
                        log.error("%s (%s): no quarterly results table found", stock.name, stock.slug)
                        continue

                    prev = state.get(stock.slug, {})
                    prev_date = prev.get("last_result_date")

                    if qd.latest_date == prev_date:
                        counts["unchanged"] += 1
                        log.info("%s (%s): unchanged, still %s", stock.name, stock.slug, prev_date)
                        continue

                    state[stock.slug] = {"name": stock.name, "last_result_date": qd.latest_date}
                    save_json(config.state_path, state)

                    result = evaluate_quarter(qd.dates, qd.sales, qd.net_profit)
                    if result is None:
                        counts["new_skipped"] += 1
                        log.warning(
                            "%s (%s): new result %s but not enough history/data for YoY growth",
                            stock.name, stock.slug, qd.latest_date,
                        )
                        continue

                    merge_output(output, stock.slug, {
                        "name": stock.name,
                        "result_date": qd.latest_date,
                        "sales_growth_pct": round(result.sales_growth, 2),
                        "net_profit_growth_pct": round(result.net_profit_growth, 2),
                        "avg_growth_pct": round(result.avg_growth, 2),
                        "classification": result.classification,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })
                    save_json(config.output_path, output)
                    counts["new_classified"] += 1
                    log.info(
                        "%s (%s): NEW result %s -> %s (avg growth %.1f%%)",
                        stock.name, stock.slug, qd.latest_date, result.classification, result.avg_growth,
                    )
                except Exception:
                    counts["errors"] += 1
                    errors.append((stock.slug, "unexpected error - see traceback above"))
                    log.exception("%s (%s): error", stock.name, stock.slug)
                finally:
                    # Must be in `finally`, not after the try/except: several
                    # branches above `continue` (unchanged, no-table-found,
                    # not-enough-history), which would otherwise skip this
                    # entirely and hammer screener.in with zero delay between
                    # the majority of requests - almost certainly the real
                    # cause of the connection timeouts we were seeing, not IP
                    # reputation.
                    time.sleep(random.uniform(config.min_delay_seconds, config.max_delay_seconds))
        finally:
            browser.close()

    log.info(
        "RUN SUMMARY: total=%d unchanged=%d new_classified=%d new_skipped=%d errors=%d",
        len(stocks), counts["unchanged"], counts["new_classified"], counts["new_skipped"], counts["errors"],
    )
    for slug, msg in errors:
        log.info("  error detail: %s: %s", slug, msg)

    return counts


if __name__ == "__main__":
    run_scrape_job()
