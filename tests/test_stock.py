import random
import unittest
from datetime import date, timedelta

from relief_stock import core, create_app
from relief_stock.core import Move, StockError, balance, batches, dispatch, expiring, loss_report, pick, reconcile, reorder, stock_card

T = date(2026, 11, 2)


def rx(item, batch, qty, days=None, day=T):
    return Move(day, item, batch, qty, "receipt", day + timedelta(days=days) if days is not None else None)


class TestLedger(unittest.TestCase):
    def test_balance(self):
        m = [rx("Rice", "A", 100), rx("Rice", "B", 50), Move(T, "Rice", "A", -30, "dispatch"), rx("Oil", "X", 9)]
        self.assertEqual(balance(m, "Rice"), 120)
        self.assertEqual(batches(m, "Rice")["A"][0], 70)

    def test_stock_card_runs(self):
        m = [rx("Rice", "A", 100), Move(T, "Rice", "A", -30, "dispatch")]
        self.assertEqual([b for _, b in stock_card(m, "Rice")], [100, 70])


class TestPicking(unittest.TestCase):
    def test_soonest_expiry_first(self):
        m = [rx("Rice", "late", 100, 300), rx("Rice", "soon", 100, 30)]
        self.assertEqual(pick(m, "Rice", 150, T), [("soon", 100), ("late", 50)])

    def test_no_expiry_goes_last(self):
        m = [rx("Soap", "forever", 100), rx("Soap", "dated", 10, 400)]
        self.assertEqual(pick(m, "Soap", 20, T)[0], ("dated", 10))

    def test_expired_is_never_picked(self):
        m = [rx("Rice", "old", 100, -1), rx("Rice", "ok", 10, 50)]
        with self.assertRaises(StockError) as c:
            pick(m, "Rice", 50, T)
        self.assertIn("100 more is expired", str(c.exception))

    def test_expiring_today_can_still_go(self):
        self.assertEqual(pick([rx("Rice", "a", 5, 0)], "Rice", 5, T), [("a", 5)])

    def test_never_more_than_held(self):
        with self.assertRaises(StockError):
            pick([rx("Rice", "a", 5)], "Rice", 6, T)
        with self.assertRaises(StockError):
            pick([rx("Rice", "a", 5)], "Rice", 0, T)

    def test_random_dispatches_keep_every_batch_at_or_above_zero(self):
        rng = random.Random(1)
        m = [rx("Rice", f"b{i}", rng.randint(10, 100), rng.randint(5, 300)) for i in range(10)]
        start = balance(m, "Rice")
        out = 0
        for _ in range(200):
            q = rng.randint(1, 40)
            try:
                m += dispatch(m, "Rice", q, T)
                out += q
            except StockError:
                pass
            self.assertTrue(all(v[0] >= -1e-9 for v in batches(m, "Rice").values()))
        self.assertAlmostEqual(balance(m, "Rice"), start - out)

    def test_order_follows_expiry_across_many_dispatches(self):
        m = [rx("Rice", "a", 30, 10), rx("Rice", "b", 30, 20), rx("Rice", "c", 30, 30)]
        m += dispatch(m, "Rice", 40, T)
        self.assertEqual(batches(m, "Rice")["a"][0], 0)
        self.assertEqual(batches(m, "Rice")["b"][0], 20)


class TestPlanning(unittest.TestCase):
    def test_expiring(self):
        rows = expiring([rx("A", "x", 5, 10), rx("A", "y", 5, 500), rx("B", "z", 5, -3)], T)
        self.assertEqual([r[1] for r in rows], ["z", "x"])

    def test_empty_batch_is_not_listed(self):
        m = [rx("A", "x", 5, 10), Move(T, "A", "x", -5, "dispatch")]
        self.assertEqual(expiring(m, T), [])

    def test_reorder(self):
        m = [rx("Rice", "a", 1000, day=T - timedelta(days=70))]
        m += [Move(T - timedelta(days=i), "Rice", "a", -10, "dispatch") for i in range(60)]
        r = reorder(m, "Rice", T)
        self.assertEqual(r["daily_use"], 10)
        self.assertEqual(r["days_of_cover"], 40)
        self.assertFalse(r["order_now"])
        m += [Move(T, "Rice", "a", -220, "dispatch")]
        r = reorder(m, "Rice", T)
        self.assertTrue(r["order_now"])

    def test_expired_stock_does_not_count_as_cover(self):
        old = T - timedelta(days=70)
        m = [rx("Rice", "a", 5000, 60, old), rx("Rice", "b", 700, 400, old)] + [Move(T - timedelta(days=i), "Rice", "b", -10, "dispatch") for i in range(60)]
        r = reorder(m, "Rice", T)
        self.assertEqual(r["usable"], 100)
        self.assertTrue(r["order_now"])

    def test_no_use_means_no_cover_figure(self):
        self.assertIsNone(reorder([rx("A", "x", 5)], "A", T)["days_of_cover"])

    def test_reconcile(self):
        m = [rx("Rice", "a", 100)]
        out = reconcile(m, {("Rice", "a"): 97}, T)
        self.assertEqual((out[0].qty, out[0].kind), (-3, "count"))
        self.assertEqual(balance(m + out, "Rice"), 97)
        self.assertEqual(reconcile(m, {("Rice", "a"): 100}, T), [])

    def test_losses(self):
        m = [rx("Rice", "a", 100), Move(T, "Rice", "a", -5, "loss", note="damaged"), Move(T, "Rice", "a", -5, "count")]
        self.assertEqual(loss_report(m)["Rice"], (10, 0.1))


class TestApp(unittest.TestCase):
    def setUp(self):
        self.a = create_app(":memory:", lambda: T)
        self.a.post("/receive", item="Rice", batch="A", qty=100, expiry="2027-01-01")
        self.a.post("/receive", item="Rice", batch="B", qty=100, expiry="2026-12-01")

    def text(self, url):
        return self.a.get(url).body.decode()

    def test_dispatch_gives_a_picking_list(self):
        page = self.a.post("/dispatch", item="Rice", qty=150).body.decode()
        self.assertLess(page.index("Batch B"), page.index("Batch A"))
        self.assertIn("50", self.text("/"))

    def test_over_issue_is_refused_and_saves_nothing(self):
        self.assertEqual(self.a.post("/dispatch", item="Rice", qty=500).status, 400)
        self.assertEqual(self.a.one("SELECT COUNT(*) FROM moves"), 2)

    def test_expiring_shows(self):
        self.assertIn("Expiring soon", self.text("/"))
        self.assertIn(">B<", self.text("/").split("Expiring soon")[1])

    def test_loss(self):
        self.assertEqual(self.a.post("/loss", item="Rice", batch="A", qty=5, reason="damaged").status, 303)
        self.assertEqual(self.a.post("/loss", item="Rice", batch="A", qty=500, reason="damaged").status, 400)
        self.assertEqual(self.a.post("/loss", item="Rice", batch="A", qty=1, reason="aliens").status, 400)
        self.assertIn("2.5%", self.text("/loss"))

    def test_count(self):
        self.assertEqual(self.a.post("/count", c0=90, c1=100).status, 200)
        self.assertEqual(self.a.one("SELECT SUM(qty) FROM moves"), 190)

    def test_stock_card_and_export(self):
        self.assertIn("Stock card", self.text("/item/Rice"))
        self.assertEqual(self.a.get("/item/None").status, 404)
        self.assertIn("Rice,A", self.a.get("/ledger.csv").body.decode())


if __name__ == "__main__":
    unittest.main()
