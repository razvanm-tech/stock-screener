"""S&P 500 constituents (current membership)."""
import csv
import io

from .http import session

CONSTITUENTS_URL = ("https://raw.githubusercontent.com/datasets/"
                    "s-and-p-500-companies/main/data/constituents.csv")


def load_constituents():
    r = session().get(CONSTITUENTS_URL, timeout=30)
    r.raise_for_status()
    rows = []
    for row in csv.DictReader(io.StringIO(r.text)):
        cik = (row.get("CIK") or "").strip()
        rows.append({
            "sym": row["Symbol"].strip(),
            "name": row["Security"].strip(),
            "sector": row["GICS Sector"].strip(),
            "cik": int(cik) if cik.isdigit() else None,
        })
    return rows
