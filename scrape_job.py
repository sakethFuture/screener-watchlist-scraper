from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import Optional

from playwright.sync_api import sync_playwright

import auth
from config import Config
from growth import classify, recommend
from logging_setup import configure_logging
from scraper import fetch_with_retry
from sector_map import sector_of
from state_store import load_json, merge_output, save_json
from watchlist import load_watchlist

log = logging.getLogger(__name__)


def _round(value: Optional[float]) -> Optional[float]:
    return round(value, 2) if value is not None else None


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

    counts = {"unchanged": 0, "new_classified": 0, "errors": 0}
    errors: list[tuple[str, str]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.headless)
        try:
            context = browser.new_context()
            page = context.new_page()
            auth.perform_login(page, config)

            for stock in stocks:
                try:
                    sd = fetch_with_retry(page, stock.slug, config)
                    if sd is None:
                        counts["errors"] += 1
                        errors.append((stock.slug, "no quarterly results table found"))
                        log.error("%s (%s): no quarterly results table found", stock.name, stock.slug)
                        continue

                    prev = state.get(stock.slug, {})
                    prev_date = prev.get("last_result_date")
                    prev_qoq_sales_growth = prev.get("last_qoq_sales_growth")

                    if sd.latest_date == prev_date:
                        counts["unchanged"] += 1
                        log.info("%s (%s): unchanged, still %s", stock.name, stock.slug, prev_date)
                        continue

                    state[stock.slug] = {
                        "name": stock.name,
                        "last_result_date": sd.latest_date,
                        "last_qoq_sales_growth": sd.qoq_sales_growth,
                    }
                    save_json(config.state_path, state)

                    sector = sector_of(stock.name)
                    classification = classify(sd, sector).classification
                    rec = recommend(classification, sd, prev_qoq_sales_growth)
                    negative_eps_quarters = sum(1 for e in sd.eps if e is not None and e < 0)

                    merge_output(output, stock.slug, {
                        "name": stock.name,
                        "sector": sector,
                        "result_date": sd.latest_date,
                        "classification": classification,
                        "recommendation": rec.recommendation,
                        "cyclical_flag": rec.cyclical_flag,
                        "cyclical_note": rec.note,
                        "pb_ratio": _round(sd.pb_ratio),
                        "pe_ratio": _round(sd.stock_pe),
                        "pe_ratio_5yr_avg": _round(sd.pe_5yr_avg),
                        "cash_plus_investments_pct_mcap": _round(sd.cash_plus_investments_pct_mcap),
                        "eps_cagr_3yr": _round(sd.profit_cagr_3yr),
                        "eps_cagr_5yr": _round(sd.profit_cagr_5yr),
                        "sales_cagr_3yr": _round(sd.sales_cagr_3yr),
                        "sales_cagr_5yr": _round(sd.sales_cagr_5yr),
                        "qoq_sales_growth": _round(sd.qoq_sales_growth),
                        "qoq_swing": _round(rec.qoq_swing),
                        "negative_eps_quarters_of_8": negative_eps_quarters,
                        "opm_current": _round(sd.opm[-1]) if sd.opm else None,
                        "opm_3yr_avg": _round(sd.annual_opm_avg_3yr),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })
                    save_json(config.output_path, output)
                    counts["new_classified"] += 1
                    log.info(
                        "%s (%s): NEW result %s -> %s / %s%s",
                        stock.name, stock.slug, sd.latest_date, classification, rec.recommendation,
                        f" [{rec.cyclical_flag}]" if rec.cyclical_flag else "",
                    )
                except Exception:
                    counts["errors"] += 1
                    errors.append((stock.slug, "unexpected error - see traceback above"))
                    log.exception("%s (%s): error", stock.name, stock.slug)
                finally:
                    # Must be in `finally`, not after the try/except: several
                    # branches above `continue` (unchanged, no-table-found),
                    # which would otherwise skip this entirely and hammer
                    # screener.in with zero delay between the majority of
                    # requests.
                    time.sleep(random.uniform(config.min_delay_seconds, config.max_delay_seconds))
        finally:
            browser.close()

    log.info(
        "RUN SUMMARY: total=%d unchanged=%d new_classified=%d errors=%d",
        len(stocks), counts["unchanged"], counts["new_classified"], counts["errors"],
    )
    for slug, msg in errors:
        log.info("  error detail: %s: %s", slug, msg)

    return counts


if __name__ == "__main__":
    run_scrape_job()
