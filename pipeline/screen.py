"""The six fixed screen rules. A stock passes only if every rule passes.

Each rule returns {"v": value shown in the table, "ok": True | False | None};
None means the data was missing, which counts as not passing.
"""

RULES = {
    "rev": "Revenue growth > 10% (trailing 12 months, year over year)",
    "eps": "EPS positive and up > 10% (trailing 12 months)",
    "opm": "Operating margin > 10%",
    "fcf": "Free cash flow positive (operating cash flow minus capex)",
    "lev": "Net debt / EBITDA < 3 (net cash passes)",
    "trend": "Price above its 200-day moving average",
}


def _growth(cur, prior):
    if cur is None or prior is None or prior <= 0:
        return None
    return cur / prior - 1


def evaluate(m, price, sma200):
    r = {}

    g = _growth(m.get("revenue"), m.get("revenue_prior"))
    r["rev"] = {"v": g, "ok": None if g is None else g > 0.10}

    eps, eps_prior = m.get("eps"), m.get("eps_prior")
    if eps is None or eps_prior is None:
        r["eps"] = {"v": None, "ok": None}
    elif eps_prior <= 0:
        # From a loss to a profit counts as "up"; growth % is not meaningful.
        r["eps"] = {"v": "turnaround" if eps > 0 else None, "ok": eps > 0}
    else:
        eg = eps / eps_prior - 1
        r["eps"] = {"v": eg, "ok": eps > 0 and eg > 0.10}

    rev, op = m.get("revenue"), m.get("opinc")
    opm = op / rev if rev and op is not None and rev > 0 else None
    r["opm"] = {"v": opm, "ok": None if opm is None else opm > 0.10}

    ocf = m.get("ocf")
    if ocf is None:
        r["fcf"] = {"v": None, "ok": None}
    else:
        fcf = ocf - (m.get("capex") or 0)
        r["fcf"] = {"v": fcf, "ok": fcf > 0}

    debt = m.get("debt") or 0          # no debt tags at all is read as no debt
    cash = m.get("cash") or 0
    net_debt = debt - cash
    ebitda = None if op is None else op + (m.get("da") or 0)
    if net_debt <= 0:
        r["lev"] = {"v": "net cash", "ok": True}
    elif ebitda is None:
        r["lev"] = {"v": None, "ok": None}
    elif ebitda <= 0:
        r["lev"] = {"v": None, "ok": False}
    else:
        x = net_debt / ebitda
        r["lev"] = {"v": x, "ok": x < 3}

    if price is None or sma200 is None:
        r["trend"] = {"v": None, "ok": None}
    else:
        gap = price / sma200 - 1
        r["trend"] = {"v": gap, "ok": gap > 0}

    passed = all(rule["ok"] is True for rule in r.values())
    rg = r["rev"]["v"] if isinstance(r["rev"]["v"], float) else 0
    eg = r["eps"]["v"]
    eg = 1.0 if eg == "turnaround" else (eg if isinstance(eg, float) else 0)
    score = round(100 * (rg + max(-1.0, min(eg, 2.0))), 1)
    return {"pass": passed, "score": score,
            "n_ok": sum(1 for rule in r.values() if rule["ok"] is True),
            "rules": r}
