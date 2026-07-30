from __future__ import annotations

import logging
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

import auth
from config import Config

log = logging.getLogger(__name__)

_EXTRACT_PAGE_DATA_JS = """
() => {
  function parseTable(sectionSelector) {
    const section = document.querySelector(sectionSelector);
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

  const quarters = parseTable('#quarters');
  const annual = parseTable('#profit-loss');
  const balanceSheet = parseTable('#balance-sheet');

  const ratios = {};
  document.querySelectorAll('#top-ratios li').forEach(li => {
    const name = li.querySelector('.name');
    const number = li.querySelector('.value .number');
    if (name && number) {
      ratios[name.innerText.trim()] = number.innerText.trim();
    }
  });

  const ranges = {};
  document.querySelectorAll('#profit-loss table.ranges-table').forEach(t => {
    const title = t.querySelector('th');
    if (!title) return;
    const key = title.innerText.trim();
    const periods = {};
    Array.from(t.querySelectorAll('tr')).slice(1).forEach(tr => {
      const cells = tr.querySelectorAll('td');
      if (cells.length === 2) {
        periods[cells[0].innerText.replace(':', '').trim()] = cells[1].innerText.trim();
      }
    });
    ranges[key] = periods;
  });

  const idEl = document.querySelector('[data-company-id]');
  const companyId = idEl ? idEl.getAttribute('data-company-id') : null;

  return { quarters, annual, balanceSheet, ratios, ranges, companyId };
}
"""


class SessionExpiredError(Exception):
    pass


@dataclass(frozen=True)
class StockData:
    latest_date: str
    dates: list[str]
    sales: list[Optional[float]]
    net_profit: list[Optional[float]]
    eps: list[Optional[float]]
    opm: list[Optional[float]]
    qoq_sales_growth: Optional[float]
    market_cap: Optional[float]
    current_price: Optional[float]
    stock_pe: Optional[float]
    book_value: Optional[float]
    pb_ratio: Optional[float]
    investments: Optional[float]
    cash_equivalents: Optional[float]
    cash_plus_investments_pct_mcap: Optional[float]
    sales_cagr_3yr: Optional[float]
    sales_cagr_5yr: Optional[float]
    profit_cagr_3yr: Optional[float]
    profit_cagr_5yr: Optional[float]
    annual_sales_avg_3yr: Optional[float]
    annual_net_profit_avg_3yr: Optional[float]
    annual_opm_avg_3yr: Optional[float]
    pe_5yr_avg: Optional[float]


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


def _last_non_ttm_columns(dates: list[str], n: int) -> list[int]:
    """Index of the last n annual columns, excluding a trailing 'TTM' column."""
    indices = [i for i, d in enumerate(dates) if d != "TTM"]
    return indices[-n:] if len(indices) >= n else []


def _annual_row_average(rows: dict[str, list[str]], dates: list[str], label: str, years: int = 3) -> Optional[float]:
    if label not in rows:
        return None
    values = [parse_number(v) for v in rows[label]]
    idx = _last_non_ttm_columns(dates, years)
    if not idx:
        return None
    picked = [values[i] for i in idx if values[i] is not None]
    if len(picked) < years:
        return None
    return sum(picked) / len(picked)


def _range_period(ranges: dict, table_key: str, period_label: str) -> Optional[float]:
    table = ranges.get(table_key)
    if not table:
        return None
    return parse_number(table.get(period_label, ""))


def _extract_from(page: Page, url: str) -> Optional[dict]:
    response = page.goto(url, wait_until="domcontentloaded")
    if "/login/" in page.url:
        raise SessionExpiredError(f"Redirected to login while fetching {url}")
    if response is not None and response.status >= 400:
        return None
    return page.evaluate(_EXTRACT_PAGE_DATA_JS)


def _fetch_pe_5yr_avg(page: Page, company_id: str, consolidated: bool) -> Optional[float]:
    query = urllib.parse.quote("Price to Earning")
    url = (
        f"https://www.screener.in/api/company/{company_id}/chart/"
        f"?q={query}&days=1825&consolidated={'true' if consolidated else 'false'}"
    )
    try:
        response = page.request.get(url)
    except Exception as e:
        log.warning("company %s: PE history fetch failed: %s", company_id, e)
        return None
    if not response.ok:
        if response.status in (401, 403):
            raise SessionExpiredError(f"PE history fetch got HTTP {response.status}")
        return None
    try:
        data = response.json()
    except Exception:
        return None
    for dataset in data.get("datasets", []):
        if dataset.get("metric") == "Price to Earning":
            values = [v[1] for v in dataset.get("values", []) if isinstance(v[1], (int, float))]
            if values:
                return sum(values) / len(values)
    return None


def _fetch_cash_equivalents(page: Page, company_id: str, consolidated: bool) -> Optional[float]:
    query = urllib.parse.quote("Other Assets")
    url = (
        f"https://www.screener.in/api/company/{company_id}/schedules/"
        f"?parent={query}&section=balance-sheet&consolidated={'true' if consolidated else 'false'}"
    )
    try:
        response = page.request.get(url)
    except Exception as e:
        log.warning("company %s: Cash Equivalents fetch failed: %s", company_id, e)
        return None
    if not response.ok:
        if response.status in (401, 403):
            raise SessionExpiredError(f"schedules fetch got HTTP {response.status}")
        return None
    try:
        data = response.json()
    except Exception:
        return None
    series = data.get("Cash Equivalents")
    if not series:
        return None
    values = list(series.values())
    return parse_number(values[-1]) if values else None


def fetch_stock_data(page: Page, slug: str) -> Optional[StockData]:
    consolidated_url, standalone_url = _company_urls(slug)

    raw = _extract_from(page, consolidated_url)
    is_consolidated = True
    if raw is None or raw.get("quarters") is None:
        raw = _extract_from(page, standalone_url)
        is_consolidated = False
    if raw is None or raw.get("quarters") is None:
        return None

    quarters = raw["quarters"]
    dates: list[str] = quarters["dates"]
    rows: dict[str, list[str]] = quarters["rows"]

    sales_label = "Sales" if "Sales" in rows else ("Revenue" if "Revenue" in rows else None)
    if sales_label is None or "Net Profit" not in rows:
        log.warning("%s: quarters table missing Sales/Revenue/Net Profit row(s), found: %s", slug, list(rows.keys()))
        return None

    sales = [parse_number(v) for v in rows[sales_label]]
    net_profit = [parse_number(v) for v in rows["Net Profit"]]
    eps = [parse_number(v) for v in rows["EPS in Rs"]] if "EPS in Rs" in rows else [None] * len(dates)
    # Banks/NBFCs/insurers show "Financing Margin %" instead of "OPM %".
    opm_label = "OPM %" if "OPM %" in rows else ("Financing Margin %" if "Financing Margin %" in rows else None)
    opm = [parse_number(v) for v in rows[opm_label]] if opm_label else [None] * len(dates)

    # screener.in shows an "Upcoming result date" badge for an announced-but-
    # unreported board meeting (a SEBI-mandated disclosure, not a result).
    # Confirmed live that this badge sits outside the table entirely, so it
    # can't leak into `rows` above - but as a hard guarantee regardless of
    # page-structure changes, never trust a column as a genuine reported
    # quarter unless it actually has both Sales/Revenue and Net Profit
    # figures. Trim any trailing column(s) that lack real data rather than
    # trusting the date header alone.
    last_reported_idx = None
    for i in range(len(dates) - 1, -1, -1):
        if sales[i] is not None and net_profit[i] is not None:
            last_reported_idx = i
            break

    if last_reported_idx is None:
        log.warning("%s: no quarter with both Sales/Revenue and Net Profit actually reported", slug)
        return None

    dates = dates[: last_reported_idx + 1]
    sales = sales[: last_reported_idx + 1]
    net_profit = net_profit[: last_reported_idx + 1]
    eps = eps[: last_reported_idx + 1]
    opm = opm[: last_reported_idx + 1]

    # Keep at most the trailing 8 quarters (spec's stated window) for the
    # fields that feed the classification rules.
    dates = dates[-8:]
    sales = sales[-8:]
    net_profit = net_profit[-8:]
    eps = eps[-8:]
    opm = opm[-8:]

    qoq_sales_growth = None
    if len(sales) >= 2 and sales[-2] not in (None, 0) and sales[-1] is not None:
        qoq_sales_growth = (sales[-1] - sales[-2]) / abs(sales[-2]) * 100

    ratios = raw.get("ratios") or {}
    market_cap = parse_number(ratios.get("Market Cap", ""))
    current_price = parse_number(ratios.get("Current Price", ""))
    stock_pe = parse_number(ratios.get("Stock P/E", ""))
    book_value = parse_number(ratios.get("Book Value", ""))
    pb_ratio = (current_price / book_value) if (current_price is not None and book_value not in (None, 0)) else None

    balance_sheet = raw.get("balanceSheet")
    investments = None
    if balance_sheet and "Investments" in balance_sheet["rows"]:
        bs_values = [parse_number(v) for v in balance_sheet["rows"]["Investments"]]
        non_null = [v for v in bs_values if v is not None]
        investments = non_null[-1] if non_null else None

    company_id = raw.get("companyId")
    cash_equivalents = _fetch_cash_equivalents(page, company_id, is_consolidated) if company_id else None

    cash_plus_investments_pct_mcap = None
    if market_cap not in (None, 0) and (investments is not None or cash_equivalents is not None):
        cash_plus_investments = (investments or 0) + (cash_equivalents or 0)
        cash_plus_investments_pct_mcap = cash_plus_investments / market_cap * 100

    ranges = raw.get("ranges") or {}
    sales_cagr_3yr = _range_period(ranges, "Compounded Sales Growth", "3 Years")
    sales_cagr_5yr = _range_period(ranges, "Compounded Sales Growth", "5 Years")
    profit_cagr_3yr = _range_period(ranges, "Compounded Profit Growth", "3 Years")
    profit_cagr_5yr = _range_period(ranges, "Compounded Profit Growth", "5 Years")

    annual = raw.get("annual")
    annual_sales_avg_3yr = annual_net_profit_avg_3yr = annual_opm_avg_3yr = None
    if annual:
        annual_dates = annual["dates"]
        annual_rows = annual["rows"]
        annual_sales_label = "Sales" if "Sales" in annual_rows else ("Revenue" if "Revenue" in annual_rows else None)
        if annual_sales_label:
            annual_sales_avg_3yr = _annual_row_average(annual_rows, annual_dates, annual_sales_label)
        annual_net_profit_avg_3yr = _annual_row_average(annual_rows, annual_dates, "Net Profit")
        annual_opm_label = "OPM %" if "OPM %" in annual_rows else ("Financing Margin %" if "Financing Margin %" in annual_rows else None)
        if annual_opm_label:
            annual_opm_avg_3yr = _annual_row_average(annual_rows, annual_dates, annual_opm_label)

    pe_5yr_avg = _fetch_pe_5yr_avg(page, company_id, is_consolidated) if company_id else None

    return StockData(
        latest_date=dates[-1],
        dates=dates,
        sales=sales,
        net_profit=net_profit,
        eps=eps,
        opm=opm,
        qoq_sales_growth=qoq_sales_growth,
        market_cap=market_cap,
        current_price=current_price,
        stock_pe=stock_pe,
        book_value=book_value,
        pb_ratio=pb_ratio,
        investments=investments,
        cash_equivalents=cash_equivalents,
        cash_plus_investments_pct_mcap=cash_plus_investments_pct_mcap,
        sales_cagr_3yr=sales_cagr_3yr,
        sales_cagr_5yr=sales_cagr_5yr,
        profit_cagr_3yr=profit_cagr_3yr,
        profit_cagr_5yr=profit_cagr_5yr,
        annual_sales_avg_3yr=annual_sales_avg_3yr,
        annual_net_profit_avg_3yr=annual_net_profit_avg_3yr,
        annual_opm_avg_3yr=annual_opm_avg_3yr,
        pe_5yr_avg=pe_5yr_avg,
    )


def fetch_with_retry(page: Page, slug: str, config: Config) -> Optional[StockData]:
    attempts = config.max_retries_per_stock + 1
    last_error: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        try:
            return fetch_stock_data(page, slug)
        except SessionExpiredError:
            log.warning("%s: session expired mid-run, re-logging in", slug)
            auth.perform_login(page, config)
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
