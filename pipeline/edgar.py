"""SEC EDGAR: XBRL company facts and Form 4 insider transactions.

Free, no API key. SEC asks for a descriptive User-Agent and at most
10 requests per second; we stay well under that.
"""
import datetime as dt
import gzip
import json
import os
import re
import xml.etree.ElementTree as ET

from .http import pace, sec_headers, session

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

# Only these tags are kept in the cache (company facts files can be tens of MB).
TAGS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "RevenuesNetOfInterestExpense",
    ],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted",
            "EarningsPerShareBasic"],
    "opinc": ["OperatingIncomeLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
           "DepreciationAmortizationAndAccretionNet", "Depreciation"],
    "ltdebt": ["LongTermDebt"],
    "ltdebt_nc": ["LongTermDebtNoncurrent"],
    "ltdebt_c": ["LongTermDebtCurrent", "DebtCurrent"],
    "stdebt": ["ShortTermBorrowings"],
    "cp": ["CommercialPaper"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "sti": ["ShortTermInvestments", "MarketableSecuritiesCurrent"],
}
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F", "10-KT"}
FACTS_MAX_AGE_DAYS = 7


def _reduce(raw):
    """Keep only the tags we use, as {tag: [facts]} in one unit."""
    gaap = raw.get("facts", {}).get("us-gaap", {})
    out = {}
    for tags in TAGS.values():
        for tag in tags:
            node = gaap.get(tag)
            if not node:
                continue
            units = node.get("units", {})
            facts = units.get("USD") or units.get("USD/shares") or []
            out[tag] = [
                {k: f[k] for k in ("start", "end", "val", "form", "filed") if k in f}
                for f in facts if f.get("form") in FORMS
            ]
    return out


def company_facts(cik, cache_dir, today):
    """Reduced company facts, refreshed when the cached copy is a week old."""
    path = os.path.join(cache_dir, f"{cik}.json.gz")
    if os.path.exists(path):
        with gzip.open(path, "rt") as fh:
            cached = json.load(fh)
        age = (today - dt.date.fromisoformat(cached["fetched"])).days
        if age < FACTS_MAX_AGE_DAYS:
            return cached["facts"]
    else:
        cached = None
    try:
        pace("sec", 0.15)
        r = session().get(FACTS_URL.format(cik=cik), headers=sec_headers(), timeout=60)
        if r.status_code == 404:
            return cached["facts"] if cached else {}
        r.raise_for_status()
        facts = _reduce(r.json())
    except Exception as exc:  # keep stale data rather than nothing
        print(f"  facts CIK {cik}: {exc}")
        return cached["facts"] if cached else {}
    os.makedirs(cache_dir, exist_ok=True)
    with gzip.open(path, "wt") as fh:
        json.dump({"fetched": today.isoformat(), "facts": facts}, fh)
    return facts


def _form4_xml(cik, acc, doc):
    # primaryDocument often points at the rendered view "xslF345X05/x.xml";
    # the raw XML sits at the same name without the xsl folder.
    doc = doc.split("/")[-1]
    pace("sec", 0.15)
    r = session().get(ARCHIVE_URL.format(cik=cik, acc=acc.replace("-", ""), doc=doc),
                      headers=sec_headers(), timeout=30)
    if r.status_code == 403:  # SEC names the reason for a block in the page title
        title = re.search(r"<title>(.*?)</title>", r.text, re.S | re.I)
        raise RuntimeError(f"SEC 403: {title.group(1).strip() if title else r.text[:120]}")
    r.raise_for_status()
    return r.text


def parse_form4(xml_text):
    """Return list of (code, shares, price) for non-derivative transactions."""
    root = ET.fromstring(xml_text)
    out = []
    for tx in root.iter("nonDerivativeTransaction"):
        code = tx.findtext("transactionCoding/transactionCode", "").strip()
        shares = tx.findtext("transactionAmounts/transactionShares/value", "0")
        price = tx.findtext("transactionAmounts/transactionPricePerShare/value", "0")
        try:
            out.append((code, float(shares or 0), float(price or 0)))
        except ValueError:
            continue
    return out


def insider_summary(cik, today, window_days=90, max_filings=40):
    """Open-market insider buys (P) and sells (S) from public Form 4 filings."""
    pace("sec", 0.15)
    r = session().get(SUBMISSIONS_URL.format(cik=cik), headers=sec_headers(), timeout=30)
    r.raise_for_status()
    recent = r.json().get("filings", {}).get("recent", {})
    since = (today - dt.timedelta(days=window_days)).isoformat()
    buys = sells = 0.0
    n_buys = n_sells = 0
    seen = 0
    for form, filed, acc, doc in zip(recent.get("form", []), recent.get("filingDate", []),
                                     recent.get("accessionNumber", []),
                                     recent.get("primaryDocument", [])):
        if form != "4" or filed < since:
            continue
        if seen >= max_filings:
            break
        seen += 1
        # No per-filing catch: a skipped filing would understate trades, so any
        # failure fails the whole summary and the caller keeps its older data.
        for code, shares, price in parse_form4(_form4_xml(cik, acc, doc)):
            if code == "P":
                buys += shares * price
                n_buys += 1
            elif code == "S":
                sells += shares * price
                n_sells += 1
    return {"window_days": window_days, "buys_usd": round(buys), "sells_usd": round(sells),
            "n_buys": n_buys, "n_sells": n_sells, "filings_read": seen}
