"""Unit tests for growth.py's classification/recommendation engine.

Uses a lightweight stand-in for scraper.StockData (a SimpleNamespace with
every field growth.py reads) so this test module has zero dependency on
Playwright - matching growth.py's own dependency-free design.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from growth import (
    classify,
    recommend,
    mean,
    stddev,
    _fast_grower_score,
    _stalwart_score,
    _slow_grower_score,
)


def make_stock(**overrides) -> SimpleNamespace:
    defaults = dict(
        dates=[f"202{i}-01-01" for i in range(8)],
        sales=[None] * 8,
        net_profit=[None] * 8,
        eps=[None] * 8,
        opm=[None] * 8,
        qoq_sales_growth=None,
        market_cap=None,
        current_price=None,
        stock_pe=None,
        book_value=None,
        pb_ratio=None,
        investments=None,
        cash_equivalents=None,
        cash_plus_investments_pct_mcap=None,
        sales_cagr_3yr=None,
        sales_cagr_5yr=None,
        profit_cagr_3yr=None,
        profit_cagr_5yr=None,
        annual_sales_avg_3yr=None,
        annual_net_profit_avg_3yr=None,
        annual_opm_avg_3yr=None,
        pe_5yr_avg=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class HelpersTest(unittest.TestCase):
    def test_mean(self):
        self.assertEqual(mean([1, 2, 3]), 2)
        self.assertIsNone(mean([]))
        self.assertIsNone(mean([None, None]))

    def test_stddev_population(self):
        # population stddev of [2, 4, 4, 4, 5, 5, 7, 9] is 2.0 (textbook example)
        self.assertAlmostEqual(stddev([2, 4, 4, 4, 5, 5, 7, 9]), 2.0, places=6)
        self.assertIsNone(stddev([5]))  # needs >= 2 points


class TurnaroundTest(unittest.TestCase):
    def test_six_bad_quarters_and_current_eps_positive(self):
        # 6 negative/declining quarters (indices 0-5), then a recovery.
        eps = [-2, -3, -1, -4, -2, -1, 0.5, 1.5]
        stock = make_stock(eps=eps, sales=[100] * 8, net_profit=[10] * 8, opm=[10] * 8)
        self.assertEqual(classify(stock, "Healthcare").classification, "Turnaround")

    def test_five_bad_quarters_is_not_enough(self):
        eps = [1, 2, 3, -1, -2, -1, 2, 3]  # only a handful bad, well under 6
        stock = make_stock(eps=eps, sales=[100] * 8, net_profit=[10] * 8, opm=[10] * 8)
        self.assertNotEqual(classify(stock, "Healthcare").classification, "Turnaround")

    def test_six_bad_quarters_but_no_recovery_signal(self):
        # 6+ bad quarters, current EPS still negative, NPM not improving,
        # and current sales/profit/OPM below 3yr average - none of the
        # recovery ORs are true.
        eps = [-1, -2, -3, -4, -5, -6, -7, -8]
        stock = make_stock(
            eps=eps,
            sales=[100, 100, 100, 100, 100, 100, 100, 50],
            net_profit=[10, 10, 10, 10, 10, 10, 10, -5],
            opm=[10, 10, 10, 10, 10, 10, 10, 5],
            annual_sales_avg_3yr=200,
            annual_net_profit_avg_3yr=20,
            annual_opm_avg_3yr=15,
        )
        self.assertNotEqual(classify(stock, "Healthcare").classification, "Turnaround")

    def test_recovery_via_npm_improvement(self):
        eps = [-2, -3, -1, -4, -2, -1, -3, -2]  # all bad (>=6), still negative EPS
        stock = make_stock(
            eps=eps,
            sales=[100, 100, 100, 100, 100, 100, 100, 100],
            net_profit=[10, 10, 10, 10, 10, 10, 10, 15],  # NPM improved 10%->15%
        )
        self.assertEqual(classify(stock, "Healthcare").classification, "Turnaround")

    def test_recovery_via_above_3yr_averages(self):
        eps = [-2, -3, -1, -4, -2, -1, -3, -2]
        stock = make_stock(
            eps=eps,
            sales=[100] * 7 + [250],
            net_profit=[10] * 7 + [25],
            opm=[10] * 7 + [20],
            annual_sales_avg_3yr=200,
            annual_net_profit_avg_3yr=20,
            annual_opm_avg_3yr=15,
        )
        self.assertEqual(classify(stock, "Healthcare").classification, "Turnaround")


class AssetPlayTest(unittest.TestCase):
    def test_pb_below_one(self):
        stock = make_stock(pb_ratio=0.99, eps=[1] * 8, sales=[100] * 8, net_profit=[10] * 8)
        self.assertEqual(classify(stock, "Healthcare").classification, "Asset Play")

    def test_pb_exactly_one_is_not_asset_play(self):
        stock = make_stock(pb_ratio=1.0, eps=[1] * 8, sales=[100] * 8, net_profit=[10] * 8)
        self.assertNotEqual(classify(stock, "Healthcare").classification, "Asset Play")

    def test_cash_plus_investments_above_40pct(self):
        stock = make_stock(pb_ratio=2.0, cash_plus_investments_pct_mcap=40.01, eps=[1] * 8, sales=[100] * 8, net_profit=[10] * 8)
        self.assertEqual(classify(stock, "Healthcare").classification, "Asset Play")

    def test_cash_plus_investments_exactly_40pct_is_not_asset_play(self):
        stock = make_stock(pb_ratio=2.0, cash_plus_investments_pct_mcap=40.0, eps=[1] * 8, sales=[100] * 8, net_profit=[10] * 8)
        self.assertNotEqual(classify(stock, "Healthcare").classification, "Asset Play")

    def test_financial_services_exempt_from_cash_plus_investments_check(self):
        # Real observed case: a bank's investment book routinely dwarfs its
        # market cap (core business, not "excess cash") - this must not
        # trigger Asset Play for Financial Services.
        stock = make_stock(pb_ratio=2.0, cash_plus_investments_pct_mcap=126.0, eps=[1] * 8, sales=[100] * 8, net_profit=[10] * 8)
        self.assertNotEqual(classify(stock, "Financial Services").classification, "Asset Play")

    def test_financial_services_still_flagged_via_pb_below_one(self):
        # The P/B < 1 path is still a meaningful signal for a bank/NBFC,
        # unlike the cash ratio - only the cash+investments check is exempt.
        stock = make_stock(pb_ratio=0.8, cash_plus_investments_pct_mcap=126.0, eps=[1] * 8, sales=[100] * 8, net_profit=[10] * 8)
        self.assertEqual(classify(stock, "Financial Services").classification, "Asset Play")


class CyclicalTest(unittest.TestCase):
    HIGH_VOLATILITY_EPS = [10, -20, 30, -25, 15, -30, 20, -15]  # deliberately wild QoQ swings

    def test_cyclical_sector_with_high_stddev(self):
        stock = make_stock(eps=self.HIGH_VOLATILITY_EPS, sales=[100] * 8, net_profit=[10] * 8)
        result = classify(stock, "Metals & Mining")
        self.assertEqual(result.classification, "Cyclical")

    def test_non_cyclical_sector_never_classified_cyclical_even_with_high_stddev(self):
        stock = make_stock(eps=self.HIGH_VOLATILITY_EPS, sales=[100] * 8, net_profit=[10] * 8)
        result = classify(stock, "Information Technology")
        self.assertNotEqual(result.classification, "Cyclical")

    def test_cyclical_sector_low_stddev_is_not_cyclical(self):
        stable_eps = [10, 10.5, 11, 10.8, 11.2, 11, 11.5, 11.3]
        stock = make_stock(eps=stable_eps, sales=[100] * 8, net_profit=[10] * 8)
        result = classify(stock, "Metals & Mining")
        self.assertNotEqual(result.classification, "Cyclical")


class ScoringHelpersTest(unittest.TestCase):
    """Direct tests of the Step 2 weighted scoring functions (0.7/0.3 legs)."""

    def test_fast_grower_score_both_legs(self):
        stock = make_stock(qoq_sales_growth=25, sales_cagr_3yr=25, sales_cagr_5yr=25)
        self.assertAlmostEqual(_fast_grower_score(stock, yoy_eps_growth=25), 1.0)

    def test_fast_grower_score_qoq_cagr_leg_only(self):
        stock = make_stock(qoq_sales_growth=25, sales_cagr_3yr=25, sales_cagr_5yr=25)
        self.assertAlmostEqual(_fast_grower_score(stock, yoy_eps_growth=None), 0.7)

    def test_fast_grower_score_yoy_eps_leg_only(self):
        stock = make_stock()
        self.assertAlmostEqual(_fast_grower_score(stock, yoy_eps_growth=25), 0.3)

    def test_fast_grower_score_zero(self):
        stock = make_stock()
        self.assertEqual(_fast_grower_score(stock, yoy_eps_growth=None), 0.0)

    def test_fast_grower_qoq_cagr_leg_needs_all_three_strictly_above_20(self):
        stock = make_stock(qoq_sales_growth=20.0, sales_cagr_3yr=25, sales_cagr_5yr=25)  # qoq exactly 20
        self.assertEqual(_fast_grower_score(stock, yoy_eps_growth=None), 0.0)

    def test_stalwart_score_both_legs(self):
        stock = make_stock(profit_cagr_3yr=15, profit_cagr_5yr=15, sales_cagr_3yr=15, sales_cagr_5yr=15)
        self.assertAlmostEqual(_stalwart_score(stock, yoy_sales_growth=15), 1.0)

    def test_stalwart_score_eps_cagr_leg_only(self):
        stock = make_stock(profit_cagr_3yr=15, profit_cagr_5yr=15)
        self.assertAlmostEqual(_stalwart_score(stock, yoy_sales_growth=None), 0.3)

    def test_stalwart_score_sales_leg_only(self):
        stock = make_stock(sales_cagr_3yr=15, sales_cagr_5yr=15)
        self.assertAlmostEqual(_stalwart_score(stock, yoy_sales_growth=15), 0.7)

    def test_stalwart_band_19_inclusive_20_excluded(self):
        # Spec band is "10 to 19" (moved down from the old 10-20/10-15 bands).
        in_band = make_stock(profit_cagr_3yr=19.0, profit_cagr_5yr=19.0)
        self.assertAlmostEqual(_stalwart_score(in_band, yoy_sales_growth=None), 0.3)
        out_of_band = make_stock(profit_cagr_3yr=20.0, profit_cagr_5yr=19.0)
        self.assertEqual(_stalwart_score(out_of_band, yoy_sales_growth=None), 0.0)

    def test_slow_grower_score_both_legs(self):
        stock = make_stock(profit_cagr_3yr=5, profit_cagr_5yr=5)
        self.assertAlmostEqual(_slow_grower_score(stock, yoy_sales_growth=5), 1.0)

    def test_slow_grower_score_sales_leg_only(self):
        stock = make_stock()
        self.assertAlmostEqual(_slow_grower_score(stock, yoy_sales_growth=5), 0.7)

    def test_slow_grower_score_eps_cagr_leg_only(self):
        stock = make_stock(profit_cagr_3yr=5, profit_cagr_5yr=5)
        self.assertAlmostEqual(_slow_grower_score(stock, yoy_sales_growth=None), 0.3)


class ClassificationScoringTest(unittest.TestCase):
    """classify()'s Step 2 behavior: highest weighted score wins; ties
    (including an all-zero 3-way tie) are broken Fast > Stalwart > Slow
    for the primary field, and surfaced via category_tie."""

    def test_clean_fast_grower_winner(self):
        stock = make_stock(
            eps=[1] * 8, sales=[None] * 8, net_profit=[10] * 8,
            qoq_sales_growth=25, sales_cagr_3yr=25, sales_cagr_5yr=25,
        )
        result = classify(stock, "Healthcare")
        self.assertEqual(result.classification, "Fast Grower")
        self.assertAlmostEqual(result.fast_grower_score, 0.7)
        self.assertEqual(result.stalwart_score, 0.0)
        self.assertEqual(result.slow_grower_score, 0.0)
        self.assertIsNone(result.category_tie)

    def test_clean_stalwart_winner(self):
        stock = make_stock(
            eps=[1] * 8, sales=[None] * 8, net_profit=[10] * 8,
            profit_cagr_3yr=15, profit_cagr_5yr=15,
        )
        result = classify(stock, "Healthcare")
        self.assertEqual(result.classification, "Stalwart")
        self.assertAlmostEqual(result.stalwart_score, 0.3)
        self.assertEqual(result.fast_grower_score, 0.0)
        self.assertEqual(result.slow_grower_score, 0.0)
        self.assertIsNone(result.category_tie)

    def test_clean_slow_grower_winner(self):
        sales = [None, None, None, 100, None, None, None, 105]  # YoY 5% < 10
        stock = make_stock(eps=[1] * 8, sales=sales, net_profit=[10] * 8)
        result = classify(stock, "Healthcare")
        self.assertEqual(result.classification, "Slow Grower")
        self.assertAlmostEqual(result.slow_grower_score, 0.7)
        self.assertEqual(result.fast_grower_score, 0.0)
        self.assertEqual(result.stalwart_score, 0.0)
        self.assertIsNone(result.category_tie)

    def test_two_way_tie_fast_and_slow_picks_fast_by_priority(self):
        # Fast Grower's QoQ+CAGR leg (0.7) ties Slow Grower's sales leg (0.7)
        # because YoY sales growth here is flat (0%, which is also < 10%).
        sales = [None, None, None, 100, None, None, None, 100]
        stock = make_stock(
            eps=[1] * 8, sales=sales, net_profit=[10] * 8,
            qoq_sales_growth=25, sales_cagr_3yr=25, sales_cagr_5yr=25,
        )
        result = classify(stock, "Healthcare")
        self.assertEqual(result.classification, "Fast Grower")
        self.assertEqual(result.category_tie, ["Fast Grower", "Slow Grower"])

    def test_three_way_zero_tie_picks_fast_by_priority(self):
        # No usable data at all -> every score is 0, but the ledger's
        # single-select Category field still needs one resolved value.
        stock = make_stock(eps=[1] * 8, sales=[None] * 8, net_profit=[10] * 8)
        result = classify(stock, "Healthcare")
        self.assertEqual(result.classification, "Fast Grower")
        self.assertEqual(result.category_tie, ["Fast Grower", "Stalwart", "Slow Grower"])
        self.assertEqual(result.fast_grower_score, 0.0)
        self.assertEqual(result.stalwart_score, 0.0)
        self.assertEqual(result.slow_grower_score, 0.0)


class SecondaryCategoryTest(unittest.TestCase):
    """When a Step 1 gate wins, Step 2's growth-profile score is still
    computed and attached as secondary_category (not a tie - a gate result
    and a growth-profile label are two different, complementary lenses)."""

    def test_asset_play_gets_slow_grower_secondary(self):
        sales = [None, None, None, 100, None, None, None, 105]  # YoY 5% -> Slow Grower profile
        stock = make_stock(eps=[1] * 8, sales=sales, net_profit=[10] * 8, pb_ratio=0.5)
        result = classify(stock, "Healthcare")
        self.assertEqual(result.classification, "Asset Play")
        self.assertIsNone(result.category_tie)
        self.assertEqual(result.secondary_category, "Slow Grower")
        self.assertAlmostEqual(result.slow_grower_score, 0.7)

    def test_turnaround_gets_secondary_category(self):
        eps = [-2, -3, -1, -4, -2, -1, 0.5, 1.5]  # 6 bad quarters, current EPS positive
        sales = [None, None, None, 100, None, None, None, 105]  # Slow Grower profile
        stock = make_stock(eps=eps, sales=sales, net_profit=[10] * 8, opm=[10] * 8)
        result = classify(stock, "Healthcare")
        self.assertEqual(result.classification, "Turnaround")
        self.assertIsNone(result.category_tie)
        self.assertEqual(result.secondary_category, "Slow Grower")

    def test_cyclical_gets_secondary_category(self):
        stock = make_stock(
            eps=CyclicalTest.HIGH_VOLATILITY_EPS, sales=[100] * 8, net_profit=[10] * 8,
            qoq_sales_growth=25, sales_cagr_3yr=25, sales_cagr_5yr=25,  # Fast Grower profile
        )
        result = classify(stock, "Metals & Mining")
        self.assertEqual(result.classification, "Cyclical")
        self.assertIsNone(result.category_tie)
        self.assertEqual(result.secondary_category, "Fast Grower")

    def test_no_gate_has_no_secondary_category(self):
        stock = make_stock(
            eps=[1] * 8, sales=[None] * 8, net_profit=[10] * 8,
            qoq_sales_growth=25, sales_cagr_3yr=25, sales_cagr_5yr=25,
        )
        result = classify(stock, "Healthcare")
        self.assertEqual(result.classification, "Fast Grower")
        self.assertIsNone(result.secondary_category)


class PriorityOrderTest(unittest.TestCase):
    def test_turnaround_beats_asset_play(self):
        eps = [-2, -3, -1, -4, -2, -1, -3, -2]
        stock = make_stock(
            eps=eps, sales=[100] * 7 + [250], net_profit=[10] * 7 + [25], opm=[10] * 7 + [20],
            annual_sales_avg_3yr=200, annual_net_profit_avg_3yr=20, annual_opm_avg_3yr=15,
            pb_ratio=0.5,  # would also qualify for Asset Play
        )
        self.assertEqual(classify(stock, "Healthcare").classification, "Turnaround")

    def test_asset_play_beats_cyclical(self):
        stock = make_stock(
            eps=CyclicalTest.HIGH_VOLATILITY_EPS, sales=[100] * 8, net_profit=[10] * 8,
            pb_ratio=0.5,  # would also qualify for Cyclical in a cyclical sector
        )
        self.assertEqual(classify(stock, "Metals & Mining").classification, "Asset Play")

    def test_cyclical_beats_fast_grower(self):
        stock = make_stock(
            eps=CyclicalTest.HIGH_VOLATILITY_EPS, sales=[100] * 8, net_profit=[10] * 8,
            qoq_sales_growth=25, sales_cagr_3yr=25, sales_cagr_5yr=25,  # would also qualify Fast Grower
        )
        self.assertEqual(classify(stock, "Metals & Mining").classification, "Cyclical")

    def test_fast_grower_beats_stalwart(self):
        stock = make_stock(
            eps=[1] * 8, sales=[100] * 8, net_profit=[10] * 8,
            qoq_sales_growth=25, sales_cagr_3yr=25, sales_cagr_5yr=25,  # Fast Grower
            profit_cagr_3yr=15, profit_cagr_5yr=15,  # would also qualify Stalwart
        )
        self.assertEqual(classify(stock, "Healthcare").classification, "Fast Grower")


class CyclicalRecommendationTest(unittest.TestCase):
    def test_peak_warning(self):
        eps = [None, None, None, 10, None, None, None, 13]  # YoY EPS growth 30% (> 20 strong threshold)
        stock = make_stock(eps=eps, sales=[100] * 8, net_profit=[10] * 8, stock_pe=13, pe_5yr_avg=20)
        # 13 < 0.7*20=14 -> peak warning path
        result = recommend("Cyclical", stock, prev_qoq_sales_growth=None)
        self.assertEqual(result.recommendation, "Hold")
        self.assertEqual(result.cyclical_flag, "peak_warning")
        self.assertIsNotNone(result.note)

    def test_trough_setup(self):
        eps = [None, None, None, 10, None, None, None, 9]  # YoY EPS growth -10% (weak)
        stock = make_stock(eps=eps, sales=[100] * 8, net_profit=[10] * 8, stock_pe=27, pe_5yr_avg=20)
        # 27 > 1.3*20=26 -> trough setup path
        result = recommend("Cyclical", stock, prev_qoq_sales_growth=None)
        self.assertEqual(result.recommendation, "Hold")
        self.assertEqual(result.cyclical_flag, "trough_setup")
        self.assertIsNotNone(result.note)

    def test_neutral_cyclical_no_flag(self):
        eps = [None, None, None, 10, None, None, None, 11]  # YoY EPS growth 10% (neither strong nor weak)
        stock = make_stock(eps=eps, sales=[100] * 8, net_profit=[10] * 8, stock_pe=20, pe_5yr_avg=20)
        result = recommend("Cyclical", stock, prev_qoq_sales_growth=None)
        self.assertEqual(result.recommendation, "Hold")
        self.assertIsNone(result.cyclical_flag)
        self.assertIsNone(result.note)

    def test_pe_exactly_at_07x_boundary_is_not_peak_warning(self):
        eps = [None, None, None, 10, None, None, None, 13]  # strong growth
        stock = make_stock(eps=eps, sales=[100] * 8, net_profit=[10] * 8, stock_pe=14.0, pe_5yr_avg=20)  # exactly 0.7x
        result = recommend("Cyclical", stock, prev_qoq_sales_growth=None)
        self.assertIsNone(result.cyclical_flag)

    def test_pe_exactly_at_13x_boundary_is_not_trough_setup(self):
        eps = [None, None, None, 10, None, None, None, 9]  # weak growth
        stock = make_stock(eps=eps, sales=[100] * 8, net_profit=[10] * 8, stock_pe=26.0, pe_5yr_avg=20)  # exactly 1.3x
        result = recommend("Cyclical", stock, prev_qoq_sales_growth=None)
        self.assertIsNone(result.cyclical_flag)


class StandardRecommendationTest(unittest.TestCase):
    def test_buy_on_yoy_sales_growth(self):
        sales = [None, None, None, 100, None, None, None, 115.01]  # YoY 15.01%
        stock = make_stock(eps=[1] * 8, sales=sales, net_profit=[10] * 8, qoq_sales_growth=0)
        result = recommend("Slow Grower", stock, prev_qoq_sales_growth=0)
        self.assertEqual(result.recommendation, "Buy")

    def test_yoy_sales_growth_exactly_15_is_not_buy(self):
        sales = [None, None, None, 100, None, None, None, 115.0]  # exactly 15%
        stock = make_stock(eps=[1] * 8, sales=sales, net_profit=[10] * 8, qoq_sales_growth=0)
        result = recommend("Slow Grower", stock, prev_qoq_sales_growth=0)
        self.assertNotEqual(result.recommendation, "Buy")

    def test_buy_on_qoq_swing(self):
        stock = make_stock(eps=[1] * 8, sales=[100] * 8, net_profit=[10] * 8, qoq_sales_growth=5)
        # swing = 5 - (-11) = 16 > 15
        result = recommend("Slow Grower", stock, prev_qoq_sales_growth=-11)
        self.assertEqual(result.recommendation, "Buy")
        self.assertAlmostEqual(result.qoq_swing, 16.0, places=6)

    def test_sell_on_negative_growth(self):
        sales = [None, None, None, 100, None, None, None, 95]  # YoY -5%
        stock = make_stock(eps=[1] * 8, sales=sales, net_profit=[10] * 8, qoq_sales_growth=-2)
        result = recommend("Slow Grower", stock, prev_qoq_sales_growth=-2)  # swing = 0, not > 15
        self.assertEqual(result.recommendation, "Sell")

    def test_negative_growth_but_strong_swing_is_buy_not_sell(self):
        # Sell condition explicitly requires NOT(qoq_swing > 15) - a strong
        # swing should win out to Buy even with negative growth.
        sales = [None, None, None, 100, None, None, None, 95]  # YoY -5%
        stock = make_stock(eps=[1] * 8, sales=sales, net_profit=[10] * 8, qoq_sales_growth=5)
        result = recommend("Slow Grower", stock, prev_qoq_sales_growth=-11)  # swing = 16 > 15
        self.assertEqual(result.recommendation, "Buy")

    def test_hold_when_nothing_triggers(self):
        sales = [None, None, None, 100, None, None, None, 105]  # YoY 5%, neither buy nor sell
        stock = make_stock(eps=[1] * 8, sales=sales, net_profit=[10] * 8, qoq_sales_growth=2)
        result = recommend("Slow Grower", stock, prev_qoq_sales_growth=1)  # swing = 1
        self.assertEqual(result.recommendation, "Hold")

    def test_missing_prev_qoq_does_not_crash_and_swing_is_none(self):
        stock = make_stock(eps=[1] * 8, sales=[100] * 8, net_profit=[10] * 8, qoq_sales_growth=5)
        result = recommend("Slow Grower", stock, prev_qoq_sales_growth=None)
        self.assertIsNone(result.qoq_swing)
        self.assertEqual(result.recommendation, "Hold")


if __name__ == "__main__":
    unittest.main()
