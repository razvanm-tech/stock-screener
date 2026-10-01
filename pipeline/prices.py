"""Daily closing prices, cached between runs.

Provider order: FMP (your free key, capped at FMP_DAILY_BUDGET calls per run),
then two keyless fallbacks (Yahoo chart endpoint, Stooq CSV).
"""
import bisect
import csv
import datetime as dt
import io
import json
import math
import os
import statistics

from .http import pace, session

HISTORY_START = "2016-01-01"   # ~10 years for the "10Y" chart range


class Budget:
    def __init__(self, calls):
        self.left = calls


def _fmp(sym, start, key, budget):
    if not key or budget.left <= 0:
        raise RuntimeError("no FMP budget")
    budget.left -= 1
    pace("fmp", 0.25)
    r = session().get("https://financialmodelingprep.com/stable/historical-price-eod/light",
                      params={"symbol": sym.replace(".", "-"), "from": start, "apikey": key},
                      timeout=30)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"FMP: {str(data)[:120]}")
    return [(row["date"][:10], float(row["price"])) for row in data if row.get("price")]


def _yahoo(sym, start):
    pace("yahoo", 0.6)
    p1 = int(dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc).timestamp())
    r = session().get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym.replace('.', '-')}",
                      params={"period1": p1, "period2": int(dt.datetime.now().timestamp()),
                              "interval": "1d"},
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    closes = res["indicators"]["quote"][0]["close"]  # split-adjusted, like FMP
    out = []
    for ts, c in zip(res["timestamp"], closes):
        if c is not None:
            out.append((dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat(), float(c)))
    if not out:
        raise RuntimeError("Yahoo: empty")
    return out


def _stooq(sym, start):
    pace("stooq", 0.6)
    r = session().get("https://stooq.com/q/d/l/",
                      params={"s": sym.replace(".", "-").lower() + ".us", "i": "d",
                              "d1": start.replace("-", "")},
                      timeout=30)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    out = [(row["Date"], float(row["Close"])) for row in rows if row.get("Close")]
    if not out:
        raise RuntimeError("Stooq: empty")
    return out


def _fetch(sym, start, key, budget, prefer=None):
    providers = {"fmp": lambda s: _fmp(sym, s, key, budget),
                 "yahoo": lambda s: _yahoo(sym, s),
                 "stooq": lambda s: _stooq(sym, s)}
    order = ["fmp", "yahoo", "stooq"]
    if prefer in order:
        order.remove(prefer)
        order.insert(0, prefer)
    errors = []
    for name in order:
        try:
            return name, providers[name](start)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise RuntimeError("; ".join(errors))


def update(sym, cache_dir, today, key, budget):
    """Return sorted [(date, close)] for `sym`, extending the cache if needed."""
    path = os.path.join(cache_dir, f"{sym}.json")
    cached = None
    if os.path.exists(path):
        with open(path) as fh:
            cached = json.load(fh)
        if cached.get("updated") == today.isoformat():
            return [tuple(x) for x in cached["c"]]
    if cached and cached["c"]:
        last = dt.date.fromisoformat(cached["c"][-1][0])
        start = (last - dt.timedelta(days=10)).isoformat()
    else:
        start = HISTORY_START
    prefer = cached.get("src") if cached else None
    try:
        src, rows = _fetch(sym, start, key, budget, prefer)
        if cached and src != prefer:
            # Never splice two providers into one series: refetch it all.
            rows = dict(_fetch(sym, HISTORY_START, key, budget, src)[1])
            cached = None
            rows = list(rows.items())
    except Exception as exc:
        print(f"  prices {sym}: {exc}")
        return [tuple(x) for x in cached["c"]] if cached else []
    merged = {d: c for d, c in (cached["c"] if cached else [])}
    merged.update(dict(rows))
    series = sorted(merged.items())
    os.makedirs(cache_dir, exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"updated": today.isoformat(), "src": src, "c": series}, fh)
    return series


def close_on(series, day):
    """Last close on or before `day` (ISO string)."""
    dates = [d for d, _ in series]
    i = bisect.bisect_right(dates, day) - 1
    return series[i][1] if i >= 0 else None


def rel_return(series, bench, day, days=182):
    """Return of `series` minus return of `bench` over `days` ending on `day`, or None."""
    start = (dt.date.fromisoformat(day) - dt.timedelta(days=days)).isoformat()
    s0, s1, b0, b1 = (close_on(x, d) for x in (series, bench) for d in (start, day))
    if not (s0 and s1 and b0 and b1):
        return None
    return s1 / s0 - b1 / b0


def drawdown(series):
    """How far the last close sits below the highest close in `series` (0 at a new high)."""
    return series[-1][1] / max(c for _, c in series) - 1


def volatility(series, days=90):
    """Annualised volatility of daily returns over the last `days` closes.

    Uses a 365-day year, since crypto trades every day.
    """
    closes = [c for _, c in series[-(days + 1):]]
    rets = [b / a - 1 for a, b in zip(closes, closes[1:])]
    return statistics.stdev(rets) * math.sqrt(365) if len(rets) >= 30 else None


def sma_on(series, day, window=200):
    dates = [d for d, _ in series]
    i = bisect.bisect_right(dates, day)
    if i < window:
        return None
    window_rows = series[i - window:i]
    return sum(c for _, c in window_rows) / window
