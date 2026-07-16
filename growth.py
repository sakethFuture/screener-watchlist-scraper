from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Number of quarterly columns needed to look back exactly one year (4 quarters).
YOY_LOOKBACK = 4


@dataclass(frozen=True)
class ClassificationResult:
    sales_growth: float
    net_profit_growth: float
    avg_growth: float
    classification: str


def yoy_growth(latest: Optional[float], year_ago: Optional[float]) -> Optional[float]:
    if latest is None or year_ago is None or year_ago == 0:
        return None
    return (latest - year_ago) / abs(year_ago) * 100


def classify(avg_growth: float) -> str:
    if avg_growth < 8:
        return "Slow Grower"
    if avg_growth < 15:
        return "Stalwart"
    return "Fast Grower"


def evaluate_quarter(dates: list[str], sales: list[Optional[float]], net_profit: list[Optional[float]]) -> Optional[ClassificationResult]:
    """Compute YoY growth + classification for the latest quarter.

    Returns None if there isn't enough history (fewer than 5 quarters, i.e. no
    same-quarter-last-year column) or the needed values are missing/zero.
    """
    if len(dates) <= YOY_LOOKBACK:
        return None

    latest_sales, year_ago_sales = sales[-1], sales[-1 - YOY_LOOKBACK]
    latest_np, year_ago_np = net_profit[-1], net_profit[-1 - YOY_LOOKBACK]

    sales_growth = yoy_growth(latest_sales, year_ago_sales)
    net_profit_growth = yoy_growth(latest_np, year_ago_np)

    if sales_growth is None or net_profit_growth is None:
        return None

    avg_growth = (sales_growth + net_profit_growth) / 2
    return ClassificationResult(
        sales_growth=sales_growth,
        net_profit_growth=net_profit_growth,
        avg_growth=avg_growth,
        classification=classify(avg_growth),
    )
