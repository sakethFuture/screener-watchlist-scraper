from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass

SLUG_ALIASES = {"screener_slug", "slug", "symbol", "code", "ticker"}
NAME_ALIASES = {"name", "stock_name", "company", "company_name"}


@dataclass(frozen=True)
class Stock:
    name: str
    slug: str


class WatchlistError(ValueError):
    pass


def _find_key(row: dict, aliases: set[str]) -> str | None:
    for key in row:
        if key.strip().lower() in aliases:
            return key
    return None


def _rows_to_stocks(rows: list[dict]) -> list[Stock]:
    if not rows:
        raise WatchlistError("Watchlist file is empty.")

    slug_key = _find_key(rows[0], SLUG_ALIASES)
    if slug_key is None:
        raise WatchlistError(
            "Could not find a slug column in the watchlist. Expected one of: "
            f"{sorted(SLUG_ALIASES)}. Found columns: {sorted(rows[0].keys())}"
        )
    name_key = _find_key(rows[0], NAME_ALIASES)

    stocks: list[Stock] = []
    missing_slug_rows: list[int] = []
    seen_slugs: dict[str, int] = {}
    duplicate_rows: list[tuple[int, str, int]] = []

    for i, row in enumerate(rows, start=1):
        raw_slug = (row.get(slug_key) or "").strip()
        if not raw_slug:
            missing_slug_rows.append(i)
            continue

        slug = raw_slug.upper()
        name = (row.get(name_key) or slug).strip() if name_key else slug

        if slug in seen_slugs:
            duplicate_rows.append((i, slug, seen_slugs[slug]))
            continue

        seen_slugs[slug] = i
        stocks.append(Stock(name=name, slug=slug))

    if missing_slug_rows:
        raise WatchlistError(
            f"Watchlist rows missing a slug value (1-indexed): {missing_slug_rows}. "
            "Every row needs a screener slug (the ticker used in the screener.in URL, "
            "e.g. TCS, RELIANCE) - it can't be reliably derived from the company name."
        )

    if duplicate_rows:
        details = ", ".join(
            f"row {i} ('{slug}') duplicates row {first}" for i, slug, first in duplicate_rows
        )
        raise WatchlistError(f"Duplicate slugs in watchlist: {details}")

    return stocks


def load_watchlist(path: str) -> list[Stock]:
    if not os.path.exists(path):
        raise WatchlistError(f"Watchlist file not found: {path}")

    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise WatchlistError("Watchlist JSON must be a list of objects.")
        rows = data
    elif ext == ".csv":
        with open(path, "r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    else:
        raise WatchlistError(f"Unsupported watchlist file extension: {ext} (use .json or .csv)")

    return _rows_to_stocks(rows)
