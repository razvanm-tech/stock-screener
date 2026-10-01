"""Point-in-time fundamentals from reduced XBRL company facts.

Every function takes `asof` (ISO date string): only facts whose `filed`
date is on or before it are used, so a January 1 screen sees exactly what
was public on January 1.
"""
import datetime as dt

from .edgar import TAGS

STALE_DAYS = 220  # ignore a company whose latest period ended long ago


def _d(s):
    return dt.date.fromisoformat(s)


def _days(f):
    return (_d(f["end"]) - _d(f["start"])).days


def duration_facts(facts, asof):
    """Duration facts filed by `asof`, one per (start, end): latest filing wins."""
    best = {}
    for f in facts:
        if "start" not in f or f.get("filed", "9999") > asof:
            continue
        key = (f["start"], f["end"])
        if key not in best or f["filed"] > best[key]["filed"]:
            best[key] = f
    return list(best.values())


def instant_facts(facts, asof):
    best = {}
    for f in facts:
        if "start" in f or f.get("filed", "9999") > asof or f["end"] > asof:
            continue
        if f["end"] not in best or f["filed"] > best[f["end"]]["filed"]:
            best[f["end"]] = f
    return best


def ttm_ending(facts, end):
    """Trailing-12-month value for the period ending `end`, or None."""
    e = _d(end)
    # (a) a full fiscal year ending there
    for f in facts:
        if f["end"] == end and 340 <= _days(f) <= 380:
            return f["val"]
    # (b) fiscal year + year-to-date - prior year-to-date
    ytds = sorted((f for f in facts if f["end"] == end and 80 <= _days(f) < 340),
                  key=_days, reverse=True)
    for y in ytds:
        fy_end = _d(y["start"]) - dt.timedelta(days=1)
        annual = [f for f in facts if 340 <= _days(f) <= 380
                  and abs((_d(f["end"]) - fy_end).days) <= 7]
        prior_end = e - dt.timedelta(days=365)
        prior = [f for f in facts if abs((_d(f["end"]) - prior_end).days) <= 10
                 and abs(_days(f) - _days(y)) <= 15]
        if annual and prior:
            return annual[0]["val"] + y["val"] - prior[0]["val"]
    # (c) four consecutive discrete quarters
    quarters = [f for f in facts if 80 <= _days(f) <= 100]
    total, cursor = 0.0, e
    for _ in range(4):
        match = [q for q in quarters if abs((_d(q["end"]) - cursor).days) <= 5]
        if not match:
            return None
        total += match[0]["val"]
        cursor = _d(match[0]["start"]) - dt.timedelta(days=1)
    return total


def ttm_pair(facts, asof):
    """(period_end, ttm_now, ttm_one_year_earlier) for the latest usable period."""
    facts = duration_facts(facts, asof)
    if not facts:
        return None
    ends = sorted({f["end"] for f in facts}, reverse=True)
    for end in ends[:6]:
        if (_d(asof) - _d(end)).days > STALE_DAYS:
            return None
        cur = ttm_ending(facts, end)
        if cur is None:
            continue
        target = _d(end) - dt.timedelta(days=365)
        prior_end = next((x for x in ends if abs((_d(x) - target).days) <= 10), None)
        prior = ttm_ending(facts, prior_end) if prior_end else None
        return end, cur, prior
    return None


def best_ttm(cf, key, asof):
    """Try each tag for a concept; keep the one with the most recent period."""
    best = None
    for rank, tag in enumerate(TAGS[key]):
        res = ttm_pair(cf.get(tag, []), asof)
        if res and (best is None or res[0] > best[0][0]):
            best = (res, tag)
    return best


def latest_instant(cf, key, asof):
    """(end, value) of the most recent balance-sheet value for a concept."""
    best = None
    for tag in TAGS[key]:
        inst = instant_facts(cf.get(tag, []), asof)
        if inst:
            end = max(inst)
            if (_d(asof) - _d(end)).days <= STALE_DAYS and (best is None or end > best[0]):
                best = (end, inst[end]["val"])
    return best


def metrics(cf, asof):
    """All screen inputs as of `asof`. Missing values are None."""
    out = {"period_end": None}
    rev = best_ttm(cf, "revenue", asof)
    if rev:
        (end, cur, prior), _tag = rev
        out.update(period_end=end, revenue=cur, revenue_prior=prior)
    eps = best_ttm(cf, "eps", asof)
    if eps:
        (_e, cur, prior), _tag = eps
        out.update(eps=cur, eps_prior=prior)
    for key in ("opinc", "ocf", "capex", "da"):
        res = best_ttm(cf, key, asof)
        out[key] = res[0][1] if res else None

    def inst(key):
        res = latest_instant(cf, key, asof)
        return res[1] if res else None

    lt = inst("ltdebt")
    if lt is None:
        nc, cur_part = inst("ltdebt_nc"), inst("ltdebt_c")
        lt = None if nc is None and cur_part is None else (nc or 0) + (cur_part or 0)
    debt_parts = [lt, inst("stdebt"), inst("cp")]
    out["debt"] = None if all(p is None for p in debt_parts) else sum(p or 0 for p in debt_parts)
    cash_parts = [inst("cash"), inst("sti")]
    out["cash"] = None if all(p is None for p in cash_parts) else sum(p or 0 for p in cash_parts)
    return out
