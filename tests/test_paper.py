import os
import tempfile
import unittest

from server.paper import PaperEngine


def make_book(mid=0.50, spread=0.01, depth=500):
    return {
        "bids": [{"price": round(mid - spread / 2, 4), "size": depth}],
        "asks": [{"price": round(mid + spread / 2, 4), "size": depth}],
    }


class TestPaper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "acct.json")
        self.eng = PaperEngine(self.path, 10_000)

    def test_buy_fills_at_ask(self):
        res = self.eng.execute("tok", "BUY", 100, make_book(mid=0.50, spread=0.01),
                               {"label": "NAVI", "feeRate": 0.0})
        self.assertEqual(res["price"], 0.505)
        acc = self.eng.account(lambda t: 0.505)
        self.assertEqual(acc["positions"][0]["shares"], 100)
        self.assertAlmostEqual(acc["cash"], 10_000 - 50.5, places=2)

    def test_sell_requires_position(self):
        with self.assertRaises(ValueError):
            self.eng.execute("tok", "SELL", 100, make_book(), {"feeRate": 0.0})

    def test_round_trip_pnl(self):
        book_bid0 = make_book(mid=0.50, spread=0.01)
        self.eng.execute("tok", "BUY", 100, book_bid0, {"label": "X", "feeRate": 0.0})
        book_bid1 = make_book(mid=0.60, spread=0.01)
        res = self.eng.execute("tok", "SELL", 100, book_bid1, {"feeRate": 0.0})
        self.assertEqual(res["price"], 0.595)  # bids side
        acc = self.eng.account(lambda t: None)
        self.assertAlmostEqual(acc["realized"], (0.595 - 0.505) * 100, places=2)
        self.assertEqual(acc["positions"], [])

    def test_no_liquidity_rejected(self):
        with self.assertRaises(ValueError):
            self.eng.execute("tok", "BUY", 100, {"bids": [], "asks": []}, {"feeRate": 0.0})

    def test_insufficient_cash(self):
        eng = PaperEngine(os.path.join(self.tmp, "acct2.json"), 10)
        with self.assertRaises(ValueError):
            eng.execute("tok", "BUY", 10000, make_book(mid=0.5), {"feeRate": 0.0})

    def test_partial_walk_then_close(self):
        book = {"asks": [{"price": 0.50, "size": 50}, {"price": 0.52, "size": 300}],
                "bids": [{"price": 0.49, "size": 1000}]}
        self.eng.execute("tok", "BUY", 150, book, {"feeRate": 0.0})
        acc = self.eng.account(lambda t: 0.50)
        self.assertAlmostEqual(acc["positions"][0]["avgCost"],
                               (50 * 0.50 + 100 * 0.52) / 150, places=4)

    def test_persistence_and_reset(self):
        self.eng.execute("tok", "BUY", 10, make_book(), {"label": "L", "feeRate": 0.0})
        again = PaperEngine(self.path, 10_000)
        self.assertEqual(len(again.state["positions"]), 1)
        again.reset()
        self.assertEqual(again.account(lambda t: None)["cash"], 10_000)


if __name__ == "__main__":
    unittest.main()
