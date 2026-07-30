from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from sector_map import CYCLICAL_SECTORS

if TYPE_CHECKING:
    # Only for type hints - growth.py stays dependency-free at runtime
    # (scraper.py transitively needs Playwright via auth.py; this module
    # shouldn't require a browser to import/test).
    from scraper import StockData

# Number of quarterly columns needed to look back exactly one year (4 quarters).
YOY_LOOKBACK = 4

# Thresholds not given numeric values in the spec ("strongly positive" /
# "weak or negative" current EPS growth for the Cyclical P/E override).
# Reusing the Fast Grower/Stalwart bands already in the spec for consistency;
# adjustable like the 0.7x/1.3x P/E multiples.
STRONG_EPS_GROWTH = 20.0
WEAK_EPS_GROWTH = 5.0


@dataclass(frozen=True)
class ClassificationResult:
    classification: str


@dataclass(frozen=True)
class RecommendationResult:
    recommendation: str
    cyclical_flag: Optional[str]  # None | "peak_warning" | "trough_setup"
    note: Optional[str]
    qoq_swing: Optional[float]


def yoy_growth(latest: Optional[float], year_ago: Optional[float]) -> Optional[float]:
    if latest is None or year_ago is None or year_ago == 0:
        return None
    return (latest - year_ago) / abs(year_ago) * 100


def mean(values: list[float]) -> Optional[float]:
    values = [v for v in values if v is not None]
    if not values:
        return None
    return sum(values) / len(values)


def stddev(values: list[float]) -> Optional[float]:
    """Population standard deviation (trailing window is treated as the
    full set of interest, not a sample of a larger population)."""
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    variance = sum((v - m) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def _qoq_series(values: list[Optional[float]]) -> list[float]:
    """QoQ % growth for each consecutive pair in a trailing quarterly series."""
    out = []
    for i in range(1, len(values)):
        prev, cur = values[i - 1], values[i]
        if prev is None or cur is None or prev == 0:
            continue
        out.append((cur - prev) / abs(prev) * 100)
    return out


def _bad_eps_quarter_count(eps: list[Optional[float]]) -> int:
    """Count of quarters that are negative, or declining vs the prior
    quarter. The oldest quarter in the window has no prior to compare
    against, so it only counts if negative."""
    count = 0
    for i, value in enumerate(eps):
        if value is None:
            continue
        if value < 0:
            count += 1
        elif i > 0 and eps[i - 1] is not None and value < eps[i - 1]:
            count += 1
    return count


def _is_turnaround(stock: "StockData") -> bool:
    bad_quarters = _bad_eps_quarter_count(stock.eps)
    if bad_quarters < 6:
        return False

    if stock.eps and stock.eps[-1] is not None and stock.eps[-1] > 0:
        return True

    if (
        len(stock.net_profit) >= 2 and len(stock.sales) >= 2
        and stock.net_profit[-1] is not None and stock.sales[-1] not in (None, 0)
        and stock.net_profit[-2] is not None and stock.sales[-2] not in (None, 0)
    ):
        current_npm = stock.net_profit[-1] / stock.sales[-1] * 100
        prev_npm = stock.net_profit[-2] / stock.sales[-2] * 100
        if current_npm > prev_npm:
            return True

    if (
        stock.sales and stock.net_profit and stock.opm
        and stock.sales[-1] is not None and stock.annual_sales_avg_3yr is not None
        and stock.net_profit[-1] is not None and stock.annual_net_profit_avg_3yr is not None
        and stock.opm[-1] is not None and stock.annual_opm_avg_3yr is not None
        and stock.sales[-1] > stock.annual_sales_avg_3yr
        and stock.net_profit[-1] > stock.annual_net_profit_avg_3yr
        and stock.opm[-1] > stock.annual_opm_avg_3yr
    ):
        return True

    return False


def _is_asset_play(stock: "StockData") -> bool:
    if stock.pb_ratio is not None and stock.pb_ratio < 1:
        return True
    if stock.cash_plus_investments_pct_mcap is not None and stock.cash_plus_investments_pct_mcap > 40:
        return True
    return False


def _is_cyclical(stock: "StockData", sector: str) -> bool:
    if sector not in CYCLICAL_SECTORS:
        return False
    qoq_eps = _qoq_series(stock.eps)
    sd = stddev(qoq_eps)
    return sd is not None and sd > 35


def _is_fast_grower(stock: "StockData", yoy_eps_growth: Optional[float]) -> bool:
    if (
        stock.qoq_sales_growth is not None and stock.qoq_sales_growth > 20
        and stock.sales_cagr_3yr is not None and stock.sales_cagr_3yr > 20
        and stock.sales_cagr_5yr is not None and stock.sales_cagr_5yr > 20
    ):
        return True
    if yoy_eps_growth is not None and yoy_eps_growth > 20:
        return True
    return False


def _is_stalwart(stock: "StockData", yoy_sales_growth: Optional[float]) -> bool:
    if (
        stock.profit_cagr_3yr is not None and 10 <= stock.profit_cagr_3yr <= 20
        and stock.profit_cagr_5yr is not None and 10 <= stock.profit_cagr_5yr <= 20
    ):
        return True
    if (
        yoy_sales_growth is not None and 10 <= yoy_sales_growth <= 15
        and stock.sales_cagr_3yr is not None and 10 <= stock.sales_cagr_3yr <= 15
        and stock.sales_cagr_5yr is not None and 10 <= stock.sales_cagr_5yr <= 15
    ):
        return True
    return False


def classify(stock: "StockData", sector: str) -> ClassificationResult:
    """Priority-ordered classification - first matching rule wins.
    Turnaround > Asset Play > Cyclical > Fast Grower > Stalwart > Slow Grower.
    """
    if _is_turnaround(stock):
        return ClassificationResult("Turnaround")
    if _is_asset_play(stock):
        return ClassificationResult("Asset Play")
    if _is_cyclical(stock, sector):
        return ClassificationResult("Cyclical")

    yoy_eps_growth = yoy_growth(stock.eps[-1], stock.eps[-1 - YOY_LOOKBACK]) if len(stock.eps) > YOY_LOOKBACK else None
    yoy_sales_growth = yoy_growth(stock.sales[-1], stock.sales[-1 - YOY_LOOKBACK]) if len(stock.sales) > YOY_LOOKBACK else None

    if _is_fast_grower(stock, yoy_eps_growth):
        return ClassificationResult("Fast Grower")
    if _is_stalwart(stock, yoy_sales_growth):
        return ClassificationResult("Stalwart")

    return ClassificationResult("Slow Grower")


def recommend(
    classification: str,
    stock: "StockData",
    prev_qoq_sales_growth: Optional[float],
) -> RecommendationResult:
    qoq_swing = None
    if stock.qoq_sales_growth is not None and prev_qoq_sales_growth is not None:
        qoq_swing = stock.qoq_sales_growth - prev_qoq_sales_growth

    if classification == "Cyclical":
        # Peter Lynch: normal "strong growth = buy" logic is actively
        # misleading for cyclicals - a low P/E with strong current earnings
        # often means the cycle has peaked, not that the stock is cheap.
        yoy_eps_growth = (
            yoy_growth(stock.eps[-1], stock.eps[-1 - YOY_LOOKBACK]) if len(stock.eps) > YOY_LOOKBACK else None
        )
        if stock.stock_pe is not None and stock.pe_5yr_avg not in (None, 0):
            if stock.stock_pe < 0.7 * stock.pe_5yr_avg and yoy_eps_growth is not None and yoy_eps_growth > STRONG_EPS_GROWTH:
                return RecommendationResult(
                    "Hold", "peak_warning",
                    "⚠ possible cycle peak - low PE + strong earnings is a Lynch warning sign, not a buy signal",
                    qoq_swing,
                )
            if stock.stock_pe > 1.3 * stock.pe_5yr_avg and (yoy_eps_growth is None or yoy_eps_growth < WEAK_EPS_GROWTH):
                return RecommendationResult(
                    "Hold", "trough_setup",
                    "possible cycle trough - may be a recovery setup, worth a closer look",
                    qoq_swing,
                )
        return RecommendationResult("Hold", None, None, qoq_swing)

    yoy_sales_growth = yoy_growth(stock.sales[-1], stock.sales[-1 - YOY_LOOKBACK]) if len(stock.sales) > YOY_LOOKBACK else None
    qoq_sales_growth = stock.qoq_sales_growth

    swing_buy = qoq_swing is not None and qoq_swing > 12
    if (yoy_sales_growth is not None and yoy_sales_growth > 12) or (qoq_sales_growth is not None and qoq_sales_growth > 12) or swing_buy:
        return RecommendationResult("Buy", None, None, qoq_swing)

    negative = (yoy_sales_growth is not None and yoy_sales_growth < 0) or (qoq_sales_growth is not None and qoq_sales_growth < 0)
    if negative and not swing_buy:
        return RecommendationResult("Sell", None, None, qoq_swing)

    return RecommendationResult("Hold", None, None, qoq_swing)
