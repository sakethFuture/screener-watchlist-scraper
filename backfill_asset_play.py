"""One-off backfill: reclassify Financial Services stocks currently stuck
on "Asset Play" from before the sector-exemption fix (growth.py's
_is_asset_play) landed. The scraper only re-runs classify()/recommend() for
a stock when its result_date changes, so a logic fix alone doesn't
retroactively correct already-recorded quarters - this script re-runs the
classification for just the affected slice using the real classify()/
recommend() functions (not a re-implementation) against whatever is
already stored in output.json.

Deliberately NOT an approximation: fields this can't know (yoy_sales_growth,
yoy_eps_growth, and full quarterly eps/sales history) are passed through as
missing (empty lists / None), which the real scoring functions already
treat as "that leg of the score doesn't fire" - the same honest handling
they use for any stock with incomplete data, not a guessed number. Turnaround
and Cyclical gates are safe to re-derive this way for this specific slice:
the stored classification of "Asset Play" already tells us Turnaround was
False when this was first computed (elif chain), and Cyclical never applies
to Financial Services regardless of eps history.

Does not touch state.json - the underlying quarter (result_date) hasn't
changed, only the code's interpretation of the same quarter's data has.

Usage: python backfill_asset_play.py [--apply]
  Without --apply: prints the before/after table and exits without writing.
  With --apply: writes the corrected classification/recommendation back to
  output.json.
"""
from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

from growth import classify, recommend
from sector_map import sector_of
from state_store import load_json, save_json


def _round(value):
    return round(value, 2) if value is not None else None


def build_proxy_stock(rec: dict) -> SimpleNamespace:
    """A stand-in StockData built entirely from what's already persisted in
    output.json - no live scrape, no fabricated history."""
    return SimpleNamespace(
        sales=[], eps=[], net_profit=[], opm=[],
        qoq_sales_growth=rec.get("qoq_sales_growth"),
        market_cap=None, current_price=None, stock_pe=rec.get("pe_ratio"),
        book_value=None, pb_ratio=rec.get("pb_ratio"),
        investments=None, cash_equivalents=None,
        cash_plus_investments_pct_mcap=rec.get("cash_plus_investments_pct_mcap"),
        sales_cagr_3yr=rec.get("sales_cagr_3yr"), sales_cagr_5yr=rec.get("sales_cagr_5yr"),
        profit_cagr_3yr=rec.get("eps_cagr_3yr"), profit_cagr_5yr=rec.get("eps_cagr_5yr"),
        annual_sales_avg_3yr=None, annual_net_profit_avg_3yr=None, annual_opm_avg_3yr=None,
        pe_5yr_avg=rec.get("pe_ratio_5yr_avg"),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write corrections to output.json")
    parser.add_argument("--output-path", default="data/output.json")
    parser.add_argument("--state-path", default="data/state.json")
    args = parser.parse_args()

    output = load_json(args.output_path)
    state = load_json(args.state_path)

    affected = [
        (slug, rec) for slug, rec in output.items()
        if rec.get("sector") == "Financial Services" and rec.get("classification") == "Asset Play"
    ]

    print(f"Found {len(affected)} Financial Services stock(s) currently classified Asset Play.\n")
    print(f"{'Ticker':<14}{'Name':<26}{'Before Cat':<14}{'Before Rec':<12}  ->  {'After Cat':<14}{'After Rec':<12} Scores(F/St/Sl)  Confidence")
    print("-" * 155)

    updates = {}
    for slug, rec in affected:
        stock = build_proxy_stock(rec)
        sector = rec.get("sector") or sector_of(rec["name"])
        cls = classify(stock, sector)

        prev = state.get(slug, {})
        prev_qoq = prev.get("last_qoq_sales_growth")
        prev_yoy = prev.get("last_yoy_sales_growth")
        new_rec = recommend(cls.classification, stock, prev_qoq, prev_yoy)

        before_cat, before_rec = rec["classification"], rec["recommendation"]
        after_cat, after_rec = cls.classification, new_rec.recommendation

        if after_cat == "Asset Play":
            confidence = "unchanged - pb_ratio<1 still legitimately Asset Play (that check isn't exempted)"
        elif cls.category_tie:
            confidence = "TIE-BREAK DEFAULT - all scores 0, no real signal (missing yoy_sales_growth blocks the 0.7-weight legs)"
        else:
            confidence = "real signal (unique nonzero score)"

        print(
            f"{slug:<14}{rec['name']:<26}{before_cat:<14}{before_rec:<12}  ->  "
            f"{after_cat:<14}{after_rec:<12} "
            f"{cls.fast_grower_score:.2f}/{cls.stalwart_score:.2f}/{cls.slow_grower_score:.2f}  {confidence}"
        )

        updates[slug] = {
            "classification": after_cat,
            "fast_grower_score": cls.fast_grower_score,
            "stalwart_score": cls.stalwart_score,
            "slow_grower_score": cls.slow_grower_score,
            "category_tie": cls.category_tie,
            "secondary_category": cls.secondary_category,
            "recommendation": after_rec,
            "recommendation_metric": new_rec.recommendation_metric,
            "qoq_swing": _round(new_rec.qoq_swing),
            "yoy_sales_growth": _round(new_rec.yoy_sales_growth),
            "yoy_swing": _round(new_rec.yoy_swing),
            "backfilled_from": "Asset Play (stale, pre-sector-exemption-fix)",
        }

    if not args.apply:
        print("\nDry run only - no files written. Re-run with --apply to write these corrections.")
        return

    for slug, patch in updates.items():
        # In-place update, not a replace: output[slug] keeps every field
        # this script doesn't know about (name, pb_ratio, updated_at, ...).
        output[slug].update(patch)
    save_json(args.output_path, output)
    print(f"\nWrote {len(updates)} correction(s) to {args.output_path}.")


if __name__ == "__main__":
    main()
