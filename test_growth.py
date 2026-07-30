"""Unit tests for growth.py's classification/recommendation engine.

Uses a lightweight stand-in for scraper.StockData (a SimpleNamespace with
every field growth.py reads) so this test module has zero dependency on
Playwright - matching growth.py's own dependency-free design.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from growth import classify, recommend, mean, stddev


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


class FastGrowerTest(unittest.TestCase):
    def test_qoq_and_both_cagrs_above_20(self):
        stock = make_stock(
            eps=[1] * 8, sales=[100] * 8, net_profit=[10] * 8,
            qoq_sales_growth=20.01, sales_cagr_3yr=20.01, sales_cagr_5yr=20.01,
        )
        self.assertEqual(classify(stock, "Healthcare").classification, "Fast Grower")

    def test_all_exactly_20_is_not_fast_grower(self):
        stock = make_stock(
            eps=[1] * 8, sales=[100] * 8, net_profit=[10] * 8,
            qoq_sales_growth=20.0, sales_cagr_3yr=20.0, sales_cagr_5yr=20.0,
        )
        self.assertNotEqual(classify(stock, "Healthcare").classification, "Fast Grower")

    def test_yoy_eps_growth_above_20_alone_qualifies(self):
        # eps[-1]=13, eps[-5]=10 -> 30% YoY growth, no CAGR data needed.
        eps = [None, None, None, 10, None, None, None, 13]
        stock = make_stock(eps=eps, sales=[100] * 8, net_profit=[10] * 8)
        self.assertEqual(classify(stock, "Healthcare").classification, "Fast Grower")

    def test_yoy_eps_growth_exactly_20_is_not_enough(self):
        eps = [None, None, None, 10, None, None, None, 12]  # exactly 20%
        stock = make_stock(eps=eps, sales=[100] * 8, net_profit=[10] * 8)
        self.assertNotEqual(classify(stock, "Healthcare").classification, "Fast Grower")


class StalwartTest(unittest.TestCase):
    def test_eps_cagr_in_band(self):
        stock = make_stock(
            eps=[1] * 8, sales=[100] * 8, net_profit=[10] * 8,
            profit_cagr_3yr=10.0, profit_cagr_5yr=20.0,  # inclusive boundaries
        )
        self.assertEqual(classify(stock, "Healthcare").classification, "Stalwart")

    def test_eps_cagr_just_below_band_falls_through(self):
        stock = make_stock(
            eps=[1] * 8, sales=[100] * 8, net_profit=[10] * 8,
            profit_cagr_3yr=9.99, profit_cagr_5yr=20.0,
        )
        self.assertNotEqual(classify(stock, "Healthcare").classification, "Stalwart")

    def test_sales_growth_and_cagr_band(self):
        # yoy sales: eps unrelated here, use sales[-1]/[-5] for YoY sales growth = 10%
        sales = [None, None, None, 100, None, None, None, 110]
        stock = make_stock(
            eps=[1] * 8, sales=sales, net_profit=[10] * 8,
            sales_cagr_3yr=10.0, sales_cagr_5yr=15.0,
        )
        self.assertEqual(classify(stock, "Healthcare").classification, "Stalwart")


class SlowGrowerFallbackTest(unittest.TestCase):
    def test_nothing_matches_falls_back_to_slow_grower(self):
        stock = make_stock(eps=[1] * 8, sales=[100] * 8, net_profit=[10] * 8)
        self.assertEqual(classify(stock, "Healthcare").classification, "Slow Grower")


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
        sales = [None, None, None, 100, None, None, None, 112.01]  # YoY 12.01%
        stock = make_stock(eps=[1] * 8, sales=sales, net_profit=[10] * 8, qoq_sales_growth=0)
        result = recommend("Slow Grower", stock, prev_qoq_sales_growth=0)
        self.assertEqual(result.recommendation, "Buy")

    def test_yoy_sales_growth_exactly_12_is_not_buy(self):
        sales = [None, None, None, 100, None, None, None, 112.0]  # exactly 12%
        stock = make_stock(eps=[1] * 8, sales=sales, net_profit=[10] * 8, qoq_sales_growth=0)
        result = recommend("Slow Grower", stock, prev_qoq_sales_growth=0)
        self.assertNotEqual(result.recommendation, "Buy")

    def test_buy_on_qoq_swing(self):
        stock = make_stock(eps=[1] * 8, sales=[100] * 8, net_profit=[10] * 8, qoq_sales_growth=5)
        # swing = 5 - (-8) = 13 > 12
        result = recommend("Slow Grower", stock, prev_qoq_sales_growth=-8)
        self.assertEqual(result.recommendation, "Buy")
        self.assertAlmostEqual(result.qoq_swing, 13.0, places=6)

    def test_sell_on_negative_growth(self):
        sales = [None, None, None, 100, None, None, None, 95]  # YoY -5%
        stock = make_stock(eps=[1] * 8, sales=sales, net_profit=[10] * 8, qoq_sales_growth=-2)
        result = recommend("Slow Grower", stock, prev_qoq_sales_growth=-2)  # swing = 0, not > 12
        self.assertEqual(result.recommendation, "Sell")

    def test_negative_growth_but_strong_swing_is_buy_not_sell(self):
        # Sell condition explicitly requires NOT(qoq_swing > 12) - a strong
        # swing should win out to Buy even with negative growth.
        sales = [None, None, None, 100, None, None, None, 95]  # YoY -5%
        stock = make_stock(eps=[1] * 8, sales=sales, net_profit=[10] * 8, qoq_sales_growth=5)
        result = recommend("Slow Grower", stock, prev_qoq_sales_growth=-8)  # swing = 13 > 12
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
