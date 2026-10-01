"""Offline tests: python -m unittest discover tests"""
import datetime as dt
import json
import os
import unittest
from unittest import mock

from pipeline import edgar
from pipeline.edgar import parse_form4
from pipeline.fundamentals import metrics, revenue_drawdown, ttm_pair
from pipeline.prices import close_on, drawdown, rel_return, sma_on, volatility
from pipeline.screen import evaluate


def f(start, end, val, filed, form="10-Q"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


# Calendar-year company: FY2024 = 400, 9M 2025 = 360 vs 9M 2024 = 290.
REV = [
    f("2023-01-01", "2023-12-31", 330, "2024-02-10", "10-K"),
    f("2023-01-01", "2023-09-30", 240, "2023-11-01"),
    f("2024-01-01", "2024-12-31", 400, "2025-02-10", "10-K"),
    f("2024-01-01", "2024-09-30", 290, "2024-11-01"),
    f("2024-01-01", "2024-09-30", 290, "2025-11-01"),   # comparative in the 2025 10-Q
    f("2025-01-01", "2025-09-30", 360, "2025-11-01"),
    f("2025-01-01", "2026-12-31", 999, "2027-02-10", "10-K"),  # filed in future: ignored
]


class TTM(unittest.TestCase):
    def test_ytd_method(self):
        end, cur, prior = ttm_pair(REV, "2025-12-31")
        self.assertEqual(end, "2025-09-30")
        self.assertEqual(cur, 400 + 360 - 290)        # 470
        self.assertEqual(prior, 330 + 290 - 240)      # 380

    def test_point_in_time(self):
        # On March 1, 2025 only the FY2024 10-K is public; 2025 data is not.
        end, cur, prior = ttm_pair(REV, "2025-03-01")
        self.assertEqual((end, cur, prior), ("2024-12-31", 400, 330))

    def test_discrete_quarters(self):
        qs = [f("2024-10-01", "2024-12-31", 10, "2025-01-20"),
              f("2025-01-01", "2025-03-31", 11, "2025-04-20"),
              f("2025-04-01", "2025-06-30", 12, "2025-07-20"),
              f("2025-07-01", "2025-09-30", 13, "2025-10-20")]
        end, cur, prior = ttm_pair(qs, "2025-12-31")
        self.assertEqual((end, cur, prior), ("2025-09-30", 46, None))

    def test_stale_company_ignored(self):
        old = [f("2020-01-01", "2020-12-31", 5, "2021-02-01", "10-K")]
        self.assertIsNone(ttm_pair(old, "2025-12-31"))


class Signals(unittest.TestCase):
    def test_growth_speeding_up(self):
        # Discrete quarters Q3 2023 .. Q3 2025. TTM growth to Sep 2025 is 46/41 - 1;
        # one quarter earlier (to Jun 2025) it was 44/40 - 1.
        ends = ["2023-09-30", "2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30",
                "2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30"]
        vals = [10, 10, 10, 10, 11, 11, 11, 11, 13]
        qs = []
        for end, val in zip(ends, vals):
            start = (dt.date.fromisoformat(end) - dt.timedelta(days=88)).replace(day=1).isoformat()
            qs.append(f(start, end, val, (dt.date.fromisoformat(end) + dt.timedelta(days=30)).isoformat()))
        m = metrics({"Revenues": qs}, "2025-12-31")
        self.assertAlmostEqual(m["revenue"] / m["revenue_prior"] - 1, 46 / 41 - 1)
        self.assertAlmostEqual(m["revenue_growth_prev"], 44 / 40 - 1)

    def test_revenue_drawdown(self):
        years = [("2019", 100), ("2020", 120), ("2021", 60), ("2022", 90)]
        facts = [f(f"{y}-01-01", f"{y}-12-31", v, f"{int(y) + 1}-02-15", "10-K") for y, v in years]
        self.assertEqual(revenue_drawdown({"Revenues": facts}, "2025-06-30"),
                         {"drop": 0.5, "trough": "2021-12-31"})
        self.assertIsNone(revenue_drawdown({"Revenues": facts[:2]}, "2025-06-30"))

    def test_rel_return(self):
        stock = [("2025-01-01", 100.0), ("2025-07-02", 150.0)]
        index = [("2025-01-01", 100.0), ("2025-07-02", 110.0)]
        self.assertAlmostEqual(rel_return(stock, index, "2025-07-02"), 0.40)
        self.assertIsNone(rel_return(stock[1:], index, "2025-07-02"))  # no history that far back


class Screen(unittest.TestCase):
    def base(self, **kw):
        m = {"revenue": 470, "revenue_prior": 380, "eps": 2.4, "eps_prior": 2.0,
             "opinc": 80, "ocf": 90, "capex": 30, "da": 20, "debt": 100, "cash": 40}
        m.update(kw)
        return m

    def test_all_pass(self):
        res = evaluate(self.base(), price=110, sma200=100)
        self.assertTrue(res["pass"])
        self.assertEqual(res["n_ok"], 6)

    def test_each_rule_can_fail(self):
        cases = {"rev": dict(revenue_prior=460), "eps": dict(eps=2.1),
                 "opm": dict(opinc=30), "fcf": dict(capex=120),
                 "lev": dict(debt=400, cash=0)}
        for rule, kw in cases.items():
            res = evaluate(self.base(**kw), 110, 100)
            self.assertFalse(res["rules"][rule]["ok"], rule)
            self.assertFalse(res["pass"], rule)
        self.assertFalse(evaluate(self.base(), 90, 100)["rules"]["trend"]["ok"])

    def test_missing_data_does_not_pass(self):
        res = evaluate(self.base(opinc=None), 110, 100)
        self.assertIsNone(res["rules"]["opm"]["ok"])
        self.assertFalse(res["pass"])

    def test_turnaround_and_net_cash(self):
        res = evaluate(self.base(eps_prior=-0.5, debt=10, cash=50), 110, 100)
        self.assertEqual(res["rules"]["eps"]["v"], "turnaround")
        self.assertEqual(res["rules"]["lev"]["v"], "net cash")
        self.assertTrue(res["pass"])

    def test_metrics_from_facts(self):
        cf = {"Revenues": REV}
        m = metrics(cf, "2025-12-31")
        self.assertEqual(m["revenue"], 470)
        self.assertIsNone(m["eps"] if "eps" in m else None)


class Prices(unittest.TestCase):
    def test_close_and_sma(self):
        series = [(f"2025-01-{d:02d}", float(d)) for d in range(1, 31)]
        self.assertEqual(close_on(series, "2025-01-15"), 15.0)
        self.assertEqual(close_on(series, "2024-12-31"), None)
        self.assertEqual(sma_on(series, "2025-01-10", window=10), 5.5)
        self.assertIsNone(sma_on(series, "2025-01-05", window=10))

    def test_drawdown_and_volatility(self):
        self.assertAlmostEqual(drawdown([("d1", 50.0), ("d2", 200.0), ("d3", 150.0)]), -0.25)
        self.assertEqual(drawdown([("d1", 50.0), ("d2", 80.0)]), 0)
        steady = [(f"d{i}", 100 * 1.01 ** i) for i in range(120)]   # same return every day
        self.assertAlmostEqual(volatility(steady), 0, places=9)
        self.assertIsNone(volatility(steady[:20]))


class Form4(unittest.TestCase):
    def test_parse(self):
        xml = """<ownershipDocument><nonDerivativeTable>
          <nonDerivativeTransaction><transactionCoding><transactionCode>P</transactionCode></transactionCoding>
            <transactionAmounts><transactionShares><value>100</value></transactionShares>
            <transactionPricePerShare><value>50.5</value></transactionPricePerShare></transactionAmounts>
          </nonDerivativeTransaction>
          <nonDerivativeTransaction><transactionCoding><transactionCode>S</transactionCode></transactionCoding>
            <transactionAmounts><transactionShares><value>10</value></transactionShares>
            <transactionPricePerShare><value>60</value></transactionPricePerShare></transactionAmounts>
          </nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""
        self.assertEqual(parse_form4(xml), [("P", 100.0, 50.5), ("S", 10.0, 60.0)])

    def test_failed_filing_is_an_error_not_zero_trades(self):
        recent = {"form": ["4"], "filingDate": ["2026-09-01"],
                  "accessionNumber": ["0000000001-26-000001"], "primaryDocument": ["x.xml"]}
        resp = mock.Mock(**{"json.return_value": {"filings": {"recent": recent}}})
        with mock.patch.object(edgar, "session") as s, mock.patch.object(edgar, "pace"), \
                mock.patch.object(edgar, "sec_headers", return_value={}), \
                mock.patch.object(edgar, "_form4_xml", side_effect=RuntimeError("SEC 403")):
            s.return_value.get.return_value = resp
            with self.assertRaises(RuntimeError):
                edgar.insider_summary(1, dt.date(2026, 10, 1))


def _load_root(name):
    with open(os.path.join(os.path.dirname(__file__), "..", name), encoding="utf-8") as fh:
        return json.load(fh)


class HandWrittenFiles(unittest.TestCase):
    """buzz.json and upcoming.json are edited by hand; check they match what the page expects."""
    def check_buzz(self, b):
        self.assertIn(b["buzz"], ("high", "medium", "low"))
        self.assertIn(b["sentiment"], ("positive", "mixed", "negative"))
        self.assertTrue(b["outlook_2027"].strip())
        self.assertLessEqual(len(b["catalysts"]), 3)
        self.assertLessEqual(len(b["risks"]), 2)
        dt.date.fromisoformat(b["fetched"])
        for s in b["sources"]:   # rendered as links on the page
            self.assertTrue(s["url"].startswith("https://"), s["url"])
            self.assertTrue(s["title"])

    def test_buzz(self):
        for sym, b in _load_root("buzz.json").items():
            with self.subTest(sym):
                self.check_buzz(b)

    def test_upcoming(self):
        items = _load_root("upcoming.json")
        self.assertEqual(len({u["id"] for u in items}), len(items), "ids must be unique")
        for u in items:
            with self.subTest(u.get("id")):
                self.check_buzz(u)
                for k in ("name", "ticker", "category", "expected"):
                    self.assertTrue(u[k].strip(), k)
                # Must match STAGES in site/index.html.
                self.assertIn(u["status"], ("date set", "window set", "confirmed, no date",
                                            "exploring", "no plans yet"))


if __name__ == "__main__":
    unittest.main()
