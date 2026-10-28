"""SEC EDGAR client — filings feed and derived fundamentals.

EDGAR is the only source in this project with an unambiguous licence: SEC
content is public information and "may be copied or further distributed by
users of the web site without the SEC's permission". That is why the news
rail is filings rather than headlines, and why market cap and P/E are
computed here instead of read from Alpha Vantage's premium OVERVIEW endpoint.

Two hard requirements from the SEC, both documented and both enforced:

  * a User-Agent naming the project and a contact address, and
  * at most 10 requests per second.

Endpoints used, all keyless:
  www.sec.gov/files/company_tickers.json          ticker -> CIK
  data.sec.gov/submissions/CIK##########.json     filing history
  data.sec.gov/api/xbrl/companyconcept/...        one XBRL fact's history
"""

import datetime as dt
import time
from typing import Any, Dict, List, Optional

import requests

from app import config

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
TIMEOUT = 20

# The documented ceiling is 10 requests/second. 8/s leaves headroom for the
# fact that wall-clock pacing is approximate.
_MIN_INTERVAL = 0.125
_last_call = 0.0

# Filing forms worth surfacing. The full history is mostly ownership noise
# (Forms 3/4/5 are insider transactions and would drown everything else).
INTERESTING_FORMS = {
    "8-K": "Material event",
    "10-Q": "Quarterly report",
    "10-K": "Annual report",
    "DEF 14A": "Proxy statement",
    "S-1": "Registration",
    "S-3": "Registration",
    "SC 13D": "Ownership stake",
    "SC 13G": "Ownership stake",
}


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": config.sec_user_agent(),
            "Accept-Encoding": "gzip, deflate",
        }
    )
    return s


def _get(s: requests.Session, url: str) -> Optional[Any]:
    """Paced GET. Returns None for a 404 so a missing filer is not fatal."""
    global _last_call
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()

    resp = s.get(url, timeout=TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def resolve_ciks(symbols: List[str], s: requests.Session) -> Dict[str, str]:
    """Map our tickers to zero-padded 10-digit CIKs in one request."""
    body = _get(s, TICKER_MAP_URL) or {}
    # The file is {"0": {"cik_str": 320193, "ticker": "AAPL", ...}, ...}
    by_ticker = {
        row["ticker"].upper(): str(row["cik_str"]).zfill(10)
        for row in body.values()
        if isinstance(row, dict) and row.get("ticker")
    }
    # EDGAR writes class shares with a dot (BRK.B) where we use a dash.
    out = {}
    for symbol in symbols:
        cik = by_ticker.get(symbol.upper()) or by_ticker.get(symbol.replace("-", ".").upper())
        if cik:
            out[symbol] = cik
    return out


def fetch_filings(symbol: str, cik: str, s: requests.Session, limit: int = 6) -> List[Dict[str, Any]]:
    """Recent interesting filings, newest first."""
    body = _get(s, SUBMISSIONS_URL.format(cik=cik))
    if not body:
        return []
    recent = body.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    descs = recent.get("primaryDocDescription", [])
    docs = recent.get("primaryDocument", [])
    accessions = recent.get("accessionNumber", [])

    out: List[Dict[str, Any]] = []
    for i, form in enumerate(forms):
        if form not in INTERESTING_FORMS:
            continue
        accession = accessions[i] if i < len(accessions) else ""
        doc = docs[i] if i < len(docs) else ""
        out.append(
            {
                "t": symbol,
                "form": form,
                "date": dates[i] if i < len(dates) else "",
                # primaryDocDescription is often blank; fall back to a plain
                # English label for the form so no row renders empty.
                # primaryDocDescription is often blank, and often just repeats
                # the form code ("10-Q"), which tells a reader nothing. Use the
                # plain-English label in both cases.
                "title": _title(descs[i] if i < len(descs) else "", form),
                "url": (
                    "https://www.sec.gov/Archives/edgar/data/"
                    f"{int(cik)}/{accession.replace('-', '')}/{doc}"
                    if accession and doc
                    else f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
                ),
            }
        )
        if len(out) >= limit:
            break
    return out


def _title(description: str, form: str) -> str:
    description = (description or "").strip()
    if not description or description.upper() == form.upper():
        return INTERESTING_FORMS.get(form, form)
    return description


def _latest_annual(
    units: Dict[str, List[Dict[str, Any]]], max_age_years: int = 2
) -> Optional[Dict[str, Any]]:
    """Most recent 10-K full-year datapoint from an XBRL concept's units.

    The age guard matters. Companies move between XBRL tags over the years, so
    a tag a filer abandoned in 2013 still returns its old values and would
    otherwise look like current data -- which is how Berkshire's 2013 Class A
    EPS of 3035.0 ends up being divided into a $500 Class B price. Anything
    older than a couple of fiscal years is treated as absent instead.
    """
    cutoff = dt.date.today().year - max_age_years
    best = None
    for facts in units.values():
        for fact in facts:
            if fact.get("form") != "10-K" or fact.get("fp") != "FY":
                continue
            if fact.get("val") in (None, 0):
                continue
            if int(fact.get("fy") or 0) < cutoff:
                continue
            if best is None or str(fact.get("end", "")) > str(best.get("end", "")):
                best = fact
    return best


def _latest_any(units: Dict[str, List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    best = None
    for facts in units.values():
        for fact in facts:
            if fact.get("val") in (None, 0):
                continue
            if best is None or str(fact.get("end", "")) > str(best.get("end", "")):
                best = fact
    return best


def fetch_fundamentals(symbol: str, cik: str, s: requests.Session) -> Dict[str, Any]:
    """Shares outstanding and annual diluted EPS, from one companyfacts call.

    Market cap and P/E are derived from these by the orchestrator, which has
    the price. EPS is the latest 10-K full-year figure, so the resulting ratio
    is a fiscal-year P/E and is labelled "P/E (FY)" in the UI -- summing four
    restated quarters into a true TTM figure is a different and much larger
    job, and overclaiming would be worse than labelling it honestly.

    This reads `companyfacts` rather than three `companyconcept` calls. The
    per-concept endpoint returns 200 with an empty unit list for some filers
    (Coca-Cola among them) while the same facts are present in companyfacts,
    so the bulk endpoint is both fewer requests and more reliable. Responses
    run to several MB, which is why the session asks for gzip; fundamentals
    refresh weekly, so the volume is immaterial.
    """
    out: Dict[str, Any] = {"shares": None, "eps": None, "eps_period": None}

    body = _get(s, COMPANYFACTS_URL.format(cik=cik))
    if not body:
        return out
    facts = body.get("facts", {})

    shares = facts.get("dei", {}).get("EntityCommonStockSharesOutstanding")
    if shares:
        fact = _latest_any(shares.get("units", {}))
        if fact:
            out["shares"] = float(fact["val"])

    gaap = facts.get("us-gaap", {})
    for tag in ("EarningsPerShareDiluted", "EarningsPerShareBasic"):
        concept = gaap.get(tag)
        if not concept:
            continue
        fact = _latest_annual(concept.get("units", {}))
        if fact:
            out["eps"] = float(fact["val"])
            out["eps_period"] = fact.get("fy")
            break

    return out
