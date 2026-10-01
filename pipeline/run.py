"""Nightly build: screen every S&P 500 stock on Jan 1 and today.

Usage: python -m pipeline.run
Env:   SEC_USER_AGENT (required), FMP_API_KEY (optional)
The 2027 buzz column comes from buzz.json, which is written by hand on request.
"""
import datetime as dt
import json
import os
import statistics

from . import edgar, prices
from .http import sec_headers
from .fundamentals import metrics
from .screen import RULES, evaluate
from .universe import load_constituents

SCREEN_START = os.environ.get("SCREEN_START", "2025-12-31")   # Jan 1 screen = data public by Dec 31
FMP_BUDGET = int(os.environ.get("FMP_DAILY_BUDGET", "240"))    # free plan allows 250/day
INSIDER_TOP_N = int(os.environ.get("INSIDER_TOP_N", "25"))
INSIDER_MAX_AGE = int(os.environ.get("INSIDER_MAX_AGE_DAYS", "7"))
BENCHMARK = "SPY"

CACHE = "cache"
OUT = os.path.join("site", "data")
BUZZ_FILE = "buzz.json"


def _clean(x):
    if isinstance(x, float):
        return round(x, 4)
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x


def _write_chart(sym, series):
    os.makedirs(os.path.join(OUT, "prices"), exist_ok=True)
    with open(os.path.join(OUT, "prices", f"{sym}.json"), "w") as fh:
        json.dump({"d": [d for d, _ in series], "c": [round(c, 2) for _, c in series]},
                  fh, separators=(",", ":"))


def _load_buzz():
    if not os.path.exists(BUZZ_FILE):
        return {}
    with open(BUZZ_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def _insider(sym, cik, today):
    """Open-market insider trades, refreshed every INSIDER_MAX_AGE days."""
    path = os.path.join(CACHE, "insider2", f"{sym}.json")  # "insider" cached false zeros
    cached = None
    if os.path.exists(path):
        with open(path) as fh:
            cached = json.load(fh)
        if (today - dt.date.fromisoformat(cached["fetched"])).days < INSIDER_MAX_AGE:
            return cached["insider"]
    try:
        data = {"fetched": today.isoformat(), "insider": edgar.insider_summary(cik, today)}
    except Exception as exc:  # keep stale data rather than nothing
        print(f"  insider {sym}: {exc}")
        return cached["insider"] if cached else None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh)
    return data["insider"]


def main():
    sec_headers()  # fail fast if SEC_USER_AGENT is missing
    today = dt.datetime.now(dt.timezone.utc).date()
    asof_now = today.isoformat()
    budget = prices.Budget(FMP_BUDGET)
    fmp_key = os.environ.get("FMP_API_KEY", "").strip()

    buzz = _load_buzz()
    universe = load_constituents()
    print(f"{len(universe)} constituents; screen start {SCREEN_START}; today {asof_now}")

    # Least recently updated symbols first, so FMP's budget rotates fairly.
    def age(stock):
        p = os.path.join(CACHE, "prices", f"{stock['sym']}.json")
        return os.path.getmtime(p) if os.path.exists(p) else 0
    universe.sort(key=age)

    spy = prices.update(BENCHMARK, os.path.join(CACHE, "prices"), today, fmp_key, budget)
    _write_chart(BENCHMARK, spy)
    spy_start, spy_now = prices.close_on(spy, SCREEN_START), (spy[-1][1] if spy else None)

    rows = []
    for i, s in enumerate(universe, 1):
        sym = s["sym"]
        if i % 50 == 0:
            print(f"  {i}/{len(universe)}  FMP calls left: {budget.left}")
        series = prices.update(sym, os.path.join(CACHE, "prices"), today, fmp_key, budget)
        if series:
            _write_chart(sym, series)
        cf = edgar.company_facts(s["cik"], os.path.join(CACHE, "facts"), today) if s["cik"] else {}

        p_start = prices.close_on(series, SCREEN_START) if series else None
        p_now = series[-1][1] if series else None
        last_day = series[-1][0] if series else asof_now

        m_jan = metrics(cf, SCREEN_START)
        m_now = metrics(cf, asof_now)
        jan = evaluate(m_jan, p_start, prices.sma_on(series, SCREEN_START) if series else None)
        now = evaluate(m_now, p_now, prices.sma_on(series, last_day) if series else None)
        jan["period_end"], now["period_end"] = m_jan["period_end"], m_now["period_end"]

        rows.append({
            "sym": sym, "name": s["name"], "sector": s["sector"], "cik": s["cik"],
            "price": p_now, "price_date": series[-1][0] if series else None,
            "ytd": (p_now / p_start - 1) if p_now and p_start else None,
            "jan": jan, "now": now, "buzz": buzz.get(sym), "insider": None,
        })

    # Insider activity for today's top passers.
    passers = sorted((r for r in rows if r["now"]["pass"]), key=lambda r: -r["now"]["score"])
    for r in passers[:INSIDER_TOP_N]:
        if r["cik"]:
            r["insider"] = _insider(r["sym"], r["cik"], today)

    jan_pass = [r for r in rows if r["jan"]["pass"] and r["ytd"] is not None]
    all_ytd = [r["ytd"] for r in rows if r["ytd"] is not None]
    summary = {
        "jan_passed": len(jan_pass),
        "jan_basket_return": statistics.mean(r["ytd"] for r in jan_pass) if jan_pass else None,
        "benchmark": BENCHMARK,
        "benchmark_return": (spy_now / spy_start - 1) if spy_now and spy_start else None,
        "median_stock_return": statistics.median(all_ytd) if all_ytd else None,
        "now_passed": len(passers),
    }
    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "screen_start": SCREEN_START, "today": asof_now, "rules": RULES,
        "summary": summary, "stocks": sorted(rows, key=lambda r: r["sym"]),
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "screen.json"), "w") as fh:
        json.dump(_clean(payload), fh, separators=(",", ":"))
    print(f"done: {summary}")


if __name__ == "__main__":
    main()
