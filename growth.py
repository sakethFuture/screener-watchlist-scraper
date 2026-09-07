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

# Step 3 Buy/Sell threshold for non-Cyclical categories. Moved from 12% to
# 15% - the one intentional change to this rule versus the prior version.
BUY_SELL_THRESHOLD = 15.0


@dataclass(frozen=True)
class ClassificationResult:
    classification: str
    fast_grower_score: Optional[float] = None
    stalwart_score: Optional[float] = None
    slow_grower_score: Optional[float] = None
    # Populated only when Step 2 itself decided `classification` and more
    # than one category tied for the top score - a genuine scoring tie.
    category_tie: Optional[list[str]] = None
    # Populated only when a Step 1 gate (Turnaround/Asset Play/Cyclical)
    # decided `classification` - the Fast/Stalwart/Slow Grower label the
    # stock would otherwise carry on growth profile alone. Not a tie: the
    # gate result and this label are two different lenses on the same
    # stock, both worth showing.
    secondary_category: Optional[str] = None


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


def _is_asset_play(stock: "StockData", sector: str) -> bool:
    if stock.pb_ratio is not None and stock.pb_ratio < 1:
        return True
    # Financial Services (banks/NBFCs/insurers) structurally carry huge
    # investment books as core business, not "excess cash" in the Peter
    # Lynch asset-play sense - the cash+investments check would otherwise
    # flag most of them (e.g. HDFC Bank measured at 126% of market cap).
    # P/B < 1 above still applies to financials - that's still a
    # meaningful value/distress signal for a bank, unlike the cash ratio.
    if sector != "Financial Services" and stock.cash_plus_investments_pct_mcap is not None and stock.cash_plus_investments_pct_mcap > 40:
        return True
    return False


def _is_cyclical(stock: "StockData", sector: str) -> bool:
    if sector not in CYCLICAL_SECTORS:
        return False
    qoq_eps = _qoq_series(stock.eps)
    sd = stddev(qoq_eps)
    return sd is not None and sd > 35


# Step 2 scoring weights (spec: weighted 0.7/0.3 combination per category,
# highest score wins, rather than the old OR'd boolean gates).
FAST_GROWER_QOQ_CAGR_WEIGHT = 0.7
FAST_GROWER_YOY_EPS_WEIGHT = 0.3

STALWART_EPS_CAGR_WEIGHT = 0.3
STALWART_SALES_WEIGHT = 0.7

SLOW_GROWER_YOY_SALES_WEIGHT = 0.7
SLOW_GROWER_EPS_CAGR_WEIGHT = 0.3

# "Between 10 and 19" per spec - inclusive both ends, matching this
# codebase's existing convention for CAGR bands.
STALWART_BAND_LOW = 10.0
STALWART_BAND_HIGH = 19.0

# Highest-score-wins tie-break order when Step 2 scores tie (including an
# all-zero 3-way tie) - the ledger's Category field is single-select and
# must always have one value, so ties surface via category_tie instead.
_TIE_BREAK_ORDER = ["Fast Grower", "Stalwart", "Slow Grower"]


def _fast_grower_score(stock: "StockData", yoy_eps_growth: Optional[float]) -> float:
    qoq_and_sales_cagr = (
        stock.qoq_sales_growth is not None and stock.qoq_sales_growth > 20
        and stock.sales_cagr_3yr is not None and stock.sales_cagr_3yr > 20
        and stock.sales_cagr_5yr is not None and stock.sales_cagr_5yr > 20
    )
    yoy_eps = yoy_eps_growth is not None and yoy_eps_growth > 20
    return FAST_GROWER_QOQ_CAGR_WEIGHT * qoq_and_sales_cagr + FAST_GROWER_YOY_EPS_WEIGHT * yoy_eps


def _stalwart_score(stock: "StockData", yoy_sales_growth: Optional[float]) -> float:
    eps_cagr_in_band = (
        stock.profit_cagr_3yr is not None and STALWART_BAND_LOW <= stock.profit_cagr_3yr <= STALWART_BAND_HIGH
        and stock.profit_cagr_5yr is not None and STALWART_BAND_LOW <= stock.profit_cagr_5yr <= STALWART_BAND_HIGH
    )
    sales_in_band = (
        yoy_sales_growth is not None and STALWART_BAND_LOW <= yoy_sales_growth <= STALWART_BAND_HIGH
        and stock.sales_cagr_3yr is not None and STALWART_BAND_LOW <= stock.sales_cagr_3yr <= STALWART_BAND_HIGH
        and stock.sales_cagr_5yr is not None and STALWART_BAND_LOW <= stock.sales_cagr_5yr <= STALWART_BAND_HIGH
    )
    return STALWART_EPS_CAGR_WEIGHT * eps_cagr_in_band + STALWART_SALES_WEIGHT * sales_in_band


def _slow_grower_score(stock: "StockData", yoy_sales_growth: Optional[float]) -> float:
    sales_below_band = yoy_sales_growth is not None and yoy_sales_growth < 10
    eps_cagr_below_band = (
        stock.profit_cagr_3yr is not None and stock.profit_cagr_3yr < 10
        and stock.profit_cagr_5yr is not None and stock.profit_cagr_5yr < 10
    )
    return SLOW_GROWER_YOY_SALES_WEIGHT * sales_below_band + SLOW_GROWER_EPS_CAGR_WEIGHT * eps_cagr_below_band


def classify(stock: "StockData", sector: str) -> ClassificationResult:
    """Step 2's weighted growth-profile score (Fast/Stalwart/Slow Grower) is
    always computed, regardless of whether a Step 1 gate fires - it's the
    stock's growth profile on its own terms, independent of the value/
    cyclicality/turnaround lens the gates apply.

    If a gate (Turnaround/Asset Play/Cyclical, in that priority order)
    fires, it wins the primary classification and the growth-profile
    result is attached as `secondary_category` - a second, complementary
    signal, not a tie. If no gate fires, Step 2's own highest score
    decides the primary, and ties there (including an all-zero 3-way tie)
    are broken by _TIE_BREAK_ORDER and surfaced via `category_tie`.
    """
    yoy_eps_growth = yoy_growth(stock.eps[-1], stock.eps[-1 - YOY_LOOKBACK]) if len(stock.eps) > YOY_LOOKBACK else None
    yoy_sales_growth = yoy_growth(stock.sales[-1], stock.sales[-1 - YOY_LOOKBACK]) if len(stock.sales) > YOY_LOOKBACK else None

    # Rounded before comparison so float noise (e.g. summed 0.7 + 0.3 terms)
    # can't produce a spurious near-miss instead of a genuine tie.
    scores = {
        "Fast Grower": round(_fast_grower_score(stock, yoy_eps_growth), 6),
        "Stalwart": round(_stalwart_score(stock, yoy_sales_growth), 6),
        "Slow Grower": round(_slow_grower_score(stock, yoy_sales_growth), 6),
    }
    best = max(scores.values())
    winners = [name for name in _TIE_BREAK_ORDER if scores[name] == best]
    step2_primary = winners[0]
    step2_tie = winners if len(winners) > 1 else None

    if _is_turnaround(stock):
        gate = "Turnaround"
    elif _is_asset_play(stock, sector):
        gate = "Asset Play"
    elif _is_cyclical(stock, sector):
        gate = "Cyclical"
    else:
        gate = None

    return ClassificationResult(
        gate or step2_primary,
        fast_grower_score=scores["Fast Grower"],
        stalwart_score=scores["Stalwart"],
        slow_grower_score=scores["Slow Grower"],
        category_tie=None if gate else step2_tie,
        secondary_category=step2_primary if gate else None,
    )


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

    swing_buy = qoq_swing is not None and qoq_swing > BUY_SELL_THRESHOLD
    if (
        (yoy_sales_growth is not None and yoy_sales_growth > BUY_SELL_THRESHOLD)
        or (qoq_sales_growth is not None and qoq_sales_growth > BUY_SELL_THRESHOLD)
        or swing_buy
    ):
        return RecommendationResult("Buy", None, None, qoq_swing)

    negative = (yoy_sales_growth is not None and yoy_sales_growth < 0) or (qoq_sales_growth is not None and qoq_sales_growth < 0)
    if negative and not swing_buy:
        return RecommendationResult("Sell", None, None, qoq_swing)

    return RecommendationResult("Hold", None, None, qoq_swing)
