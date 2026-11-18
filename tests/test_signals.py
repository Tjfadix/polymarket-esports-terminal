import unittest

from server import signals


class TestBookHelpers(unittest.TestCase):
    def test_best_prices_unsorted_levels(self):
        bids = [{"price": "0.40", "size": "10"}, {"price": 0.42, "size": 5}]
        asks = [{"price": 0.46, "size": 7}, {"price": 0.44, "size": 9}]
        self.assertEqual(signals.best_bid_price(bids), 0.42)
        self.assertEqual(signals.best_ask_price(asks), 0.44)

    def test_empty_book(self):
        self.assertIsNone(signals.best_bid_price([]))
        self.assertIsNone(signals.best_ask_price([]))

    def test_merge_yes_book_minting_parity(self):
        yes = {"bids": [{"price": 0.40, "size": 10}], "asks": [{"price": 0.42, "size": 10}]}
        no = {"bids": [{"price": 0.61, "size": 8}], "asks": [{"price": 0.63, "size": 6}]}
        merged = signals.merge_yes_book(yes, no)
        bids = {p: s for p, s in merged["bids"]}
        asks = {p: s for p, s in merged["asks"]}
        self.assertEqual(asks.get(0.39), 8)   # NO bid 0.61 -> YES ask 0.39
        self.assertEqual(bids.get(0.37), 6)   # NO ask 0.63 -> YES bid 0.37
        best_ask = merged["asks"][0][0]
        self.assertEqual(best_ask, 0.39)      # parity ask beats native 0.42

    def test_depth_within(self):
        bids = [{"price": 0.50, "size": 100}, {"price": 0.49, "size": 50}, {"price": 0.40, "size": 999}]
        self.assertEqual(signals.depth_within(bids, 0.03, is_bid=True), 150.0)

    def test_walk_book_slippage(self):
        asks = [{"price": 0.50, "size": 100}, {"price": 0.52, "size": 100}]
        vwap, leftover = signals.walk_book(asks, 150, is_bid=False)
        self.assertAlmostEqual(vwap, (100 * 0.50 + 50 * 0.52) / 150)
        self.assertEqual(leftover, 0)
        vwap, leftover = signals.walk_book(asks, 250, is_bid=False)
        self.assertEqual(leftover, 50)


class TestFlowAndFair(unittest.TestCase):
    def setUp(self):
        self.now = 10_000_000

    def test_flow_skew_buy_pressure(self):
        trades = [{"side": "BUY", "size": 100, "price": 0.6, "timestamp": self.now - 10}]
        self.assertGreater(signals.flow_skew(trades, self.now), 0.99)

    def test_flow_skew_empty(self):
        self.assertEqual(signals.flow_skew([], self.now), 0.0)

    def test_fair_value_weighted_median(self):
        trades = [
            {"price": 0.10, "size": 1, "timestamp": self.now},
            {"price": 0.50, "size": 1, "timestamp": self.now},
            {"price": 0.90, "size": 1, "timestamp": self.now},
        ]
        self.assertEqual(signals.fair_value(trades, self.now), 0.5)

    def test_fair_value_recency_wins(self):
        trades = [
            {"price": 0.20, "size": 1, "timestamp": self.now},
            {"price": 0.20, "size": 1, "timestamp": self.now},
            {"price": 0.80, "size": 5, "timestamp": self.now - 6 * 3600},
        ]
        self.assertEqual(signals.fair_value(trades, self.now), 0.2)


class TestMomentum(unittest.TestCase):
    def test_uptrend_positive(self):
        now = 10_000_000
        hist = [{"t": now - (120 - i) * 60, "p": 0.40 + i * 0.003} for i in range(120)]
        self.assertGreater(signals.momentum_score(hist, now), 0.3)

    def test_uptrend_negative(self):
        now = 10_000_000
        hist = [{"t": now - (120 - i) * 60, "p": 0.70 - i * 0.003} for i in range(120)]
        self.assertLess(signals.momentum_score(hist, now), -0.3)

    def test_flat_zero(self):
        now = 10_000_000
        hist = [{"t": now - (100 - i) * 60, "p": 0.5} for i in range(100)]
        self.assertAlmostEqual(abs(signals.momentum_score(hist, now)), 0.0, places=6)

    def test_short_series_zero(self):
        self.assertEqual(signals.momentum_score([{"t": 1, "p": 0.5}], 10), 0.0)


class TestEdgeAndSizes(unittest.TestCase):
    def test_fee_model(self):
        self.assertAlmostEqual(signals.fee_per_share(0.4, 0.03), 0.012)
        self.assertAlmostEqual(signals.fee_per_share(0.98, 0.03), 0.0006)

    def test_edge_positive_and_negative(self):
        self.assertAlmostEqual(signals.edge(0.60, 0.55, 0.0), 0.05)
        self.assertLess(signals.edge(0.50, 0.55, 0.03), 0.0)

    def test_edge_needs_both_sides(self):
        self.assertIsNone(signals.edge(None, 0.5))
        self.assertIsNone(signals.edge(0.5, 1.0))

    def test_kelly_caps(self):
        self.assertAlmostEqual(signals.kelly_fraction(0.6, 0.5), (0.6 - 0.5) / 0.5 / 2)
        self.assertEqual(signals.kelly_fraction(0.99, 0.05), 0.25)
        self.assertEqual(signals.kelly_fraction(0.3, 0.5), 0.0)

    def test_volume_shock(self):
        self.assertAlmostEqual(signals.volume_shock(10_000, 70_000), 1.0)
        self.assertAlmostEqual(signals.volume_shock(7_000, 70_000), 0.7)
        self.assertEqual(signals.volume_shock(0, 0), 1.0)
        self.assertEqual(signals.volume_shock(999_000, 7_000), 10.0)


class TestLiquidityScore(unittest.TestCase):
    def test_tight_deep_active(self):
        self.assertEqual(signals.liquidity_score(0.001, 0.001, 5000, 5000, 50_000), 100)

    def test_wide_thin(self):
        self.assertLess(signals.liquidity_score(0.09, 0.001, 10, 10, 100), 25)

    def test_no_spread(self):
        self.assertEqual(signals.liquidity_score(None, 0.001, 100, 100, 1000), 0)


class TestComposite(unittest.TestCase):
    def _args(self, **kw):
        now = 10_000_000
        base = {
            "mid": 0.5, "best_bid": 0.495, "best_ask": 0.505, "tick": 0.001,
            "yes_book": {"bids": [{"price": 0.495, "size": 2000}],
                         "asks": [{"price": 0.505, "size": 200}]},
            "no_book": {"bids": [{"price": 0.48, "size": 200}],
                        "asks": [{"price": 0.50, "size": 300}]},
            "trades": [{"side": "BUY", "size": 500, "price": 0.60, "timestamp": now - 60}],
            "history": [{"t": now - (100 - i) * 60, "p": 0.5} for i in range(100)],
            "volume24": 200_000, "volume1wk": 700_000,
            "fee_rate": 0.03, "now_ts": now,
        }
        base.update(kw)
        return base

    def test_strong_buy_on_yes(self):
        sig = signals.build_signal(**self._args())
        self.assertEqual(sig["side"], "YES")
        self.assertIn(sig["action"], (signals.ACTION_STRONG, signals.ACTION_BUY))
        self.assertGreater(sig["conviction"], 60)

    def test_sell_pressure_flips_to_no(self):
        sig = signals.build_signal(**self._args(
            yes_book={"bids": [{"price": 0.495, "size": 100}],
                      "asks": [{"price": 0.505, "size": 3000}]},
            no_book={"bids": [{"price": 0.48, "size": 4000}],
                     "asks": [{"price": 0.51, "size": 100}]},
            trades=[{"side": "SELL", "size": 800, "price": 0.45, "timestamp": 10_000_000 - 60}],
        ))
        self.assertEqual(sig["side"], "NO")
        self.assertGreaterEqual(sig["conviction"], 45)

    def test_thin_book_gated(self):
        sig = signals.build_signal(**self._args(
            volume24=50, volume1wk=0,
            yes_book={"bids": [{"price": 0.40, "size": 3}], "asks": [{"price": 0.60, "size": 3}]},
            no_book={"bids": [], "asks": []},
        ))
        self.assertEqual(sig["action"], signals.ACTION_NONE)
        self.assertIsNone(sig["side"])
        self.assertLess(sig["liq"], 40)


if __name__ == "__main__":
    unittest.main()
