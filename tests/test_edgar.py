"""SEC EDGAR: filing titles, XBRL fact selection, and the multi-class guard."""

import datetime as dt

from app import edgar

# ---------------------------------------------------------------------------
# Filing titles
# ---------------------------------------------------------------------------

def test_blank_description_falls_back_to_a_readable_label():
    assert edgar._title("", "8-K") == "Material event"


def test_description_that_merely_repeats_the_form_is_replaced():
    # Apple and NVIDIA both return primaryDocDescription == "10-Q", which
    # rendered rows reading "10-Q | 10-Q" and told a reader nothing.
    assert edgar._title("10-Q", "10-Q") == "Quarterly report"
    assert edgar._title("8-k", "8-K") == "Material event"


def test_a_real_description_is_kept():
    assert edgar._title("Material definitive agreement", "8-K") == \
        "Material definitive agreement"


# ---------------------------------------------------------------------------
# XBRL fact selection
# ---------------------------------------------------------------------------

def _fact(fy, val, end, form="10-K", fp="FY"):
    return {"fy": fy, "val": val, "end": end, "form": form, "fp": fp}


def test_annual_fact_prefers_the_most_recent_filing():
    year = dt.date.today().year
    units = {"USD/shares": [
        _fact(year - 1, 5.0, f"{year - 1}-12-31"),
        _fact(year, 7.0, f"{year}-12-31"),
    ]}
    assert edgar._latest_annual(units)["val"] == 7.0


def test_abandoned_tags_returning_ancient_values_are_rejected():
    # Berkshire's EarningsPerShareDiluted still returns its 2013 Class A
    # figure of 3035.0. Divided into a ~$500 Class B price that yields a P/E
    # of 0.16, which is worse than showing nothing.
    units = {"USD/shares": [_fact(2013, 3035.0, "2013-12-31")]}
    assert edgar._latest_annual(units) is None


def test_quarterly_and_non_10k_facts_are_ignored():
    year = dt.date.today().year
    units = {"USD/shares": [
        _fact(year, 1.1, f"{year}-03-31", form="10-Q", fp="Q1"),
        _fact(year, 2.2, f"{year}-06-30", form="8-K", fp="FY"),
    ]}
    assert edgar._latest_annual(units) is None


def test_zero_values_are_not_treated_as_data():
    year = dt.date.today().year
    units = {"USD/shares": [_fact(year, 0, f"{year}-12-31")]}
    assert edgar._latest_annual(units) is None


def test_latest_any_takes_the_newest_regardless_of_form():
    # Shares outstanding is a cover-page fact and usually arrives on a 10-Q.
    units = {"shares": [
        {"end": "2026-01-31", "val": 100, "form": "10-K"},
        {"end": "2026-04-30", "val": 110, "form": "10-Q"},
    ]}
    assert edgar._latest_any(units)["val"] == 110


# ---------------------------------------------------------------------------
# Ticker to CIK
# ---------------------------------------------------------------------------

class _CikSession:
    headers: dict = {}

    def get(self, url, timeout=None):
        from tests.conftest import FakeResponse
        return FakeResponse({
            "0": {"cik_str": 320193, "ticker": "AAPL"},
            "1": {"cik_str": 1067983, "ticker": "BRK.B"},
        })


def test_cik_lookup_zero_pads_to_ten_digits():
    out = edgar.resolve_ciks(["AAPL"], _CikSession())
    assert out["AAPL"] == "0000320193"


def test_class_shares_match_despite_the_dot_dash_mismatch():
    # We write BRK-B; EDGAR writes BRK.B.
    out = edgar.resolve_ciks(["BRK-B"], _CikSession())
    assert out["BRK-B"] == "0001067983"


def test_unknown_symbols_are_absent_rather_than_wrong():
    out = edgar.resolve_ciks(["NOPE"], _CikSession())
    assert "NOPE" not in out
