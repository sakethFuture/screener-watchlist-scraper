from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

import auth
from config import Config

log = logging.getLogger(__name__)

_EXTRACT_QUARTERS_JS = """
() => {
  const section = document.querySelector('#quarters');
  if (!section) return null;
  const table = section.querySelector('[data-result-table] table');
  if (!table) return null;
  const headerCells = Array.from(table.querySelectorAll('thead th[data-date-key]'));
  const dates = headerCells.map(th => th.getAttribute('data-date-key'));
  if (dates.length === 0) return null;
  const rows = Array.from(table.querySelectorAll('tbody tr'));
  const labeled = {};
  for (const row of rows) {
    const labelCell = row.querySelector('td.text');
    if (!labelCell) continue;
    const label = labelCell.innerText.replace(/\\u00a0/g, ' ').replace(/\\+/g, '').trim();
    const valueCells = Array.from(row.querySelectorAll('td')).slice(1);
    labeled[label] = valueCells.map(td => td.innerText.trim());
  }
  return { dates, rows: labeled };
}
"""


class SessionExpiredError(Exception):
    pass


@dataclass(frozen=True)
class QuarterData:
    latest_date: str
    dates: list[str]
    sales: list[Optional[float]]
    net_profit: list[Optional[float]]


def parse_number(text: str) -> Optional[float]:
    text = text.strip()
    if not text or text == "-":
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "").replace("%", "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def _company_urls(slug: str) -> tuple[str, str]:
    base = f"https://www.screener.in/company/{slug}"
    return f"{base}/consolidated/", f"{base}/"


def _extract_from(page: Page, url: str) -> Optional[dict]:
    response = page.goto(url, wait_until="domcontentloaded")
    if "/login/" in page.url:
        raise SessionExpiredError(f"Redirected to login while fetching {url}")
    if response is not None and response.status >= 400:
        return None
    return page.evaluate(_EXTRACT_QUARTERS_JS)


def fetch_latest_quarter(page: Page, slug: str) -> Optional[QuarterData]:
    consolidated_url, standalone_url = _company_urls(slug)

    raw = _extract_from(page, consolidated_url)
    if raw is None:
        raw = _extract_from(page, standalone_url)
    if raw is None:
        return None

    dates: list[str] = raw["dates"]
    rows: dict[str, list[str]] = raw["rows"]

    if "Sales" not in rows or "Net Profit" not in rows:
        log.warning("%s: quarters table missing Sales/Net Profit row(s), found: %s", slug, list(rows.keys()))
        return None

    sales = [parse_number(v) for v in rows["Sales"]]
    net_profit = [parse_number(v) for v in rows["Net Profit"]]

    return QuarterData(latest_date=dates[-1], dates=dates, sales=sales, net_profit=net_profit)


def fetch_with_retry(context, page: Page, slug: str, config: Config) -> Optional[QuarterData]:
    attempts = config.max_retries_per_stock + 1
    last_error: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        try:
            return fetch_latest_quarter(page, slug)
        except SessionExpiredError:
            log.warning("%s: session expired mid-run, re-logging in", slug)
            auth.perform_login(context, page, config)
        except PlaywrightTimeoutError as e:
            last_error = e
            log.warning("%s: timeout on attempt %d/%d: %s", slug, attempt, attempts, e)
        except Exception as e:
            last_error = e
            log.warning("%s: error on attempt %d/%d: %s", slug, attempt, attempts, e)

        if attempt < attempts:
            time.sleep(2 * attempt)

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"{slug}: failed after {attempts} attempts (session kept expiring)")
