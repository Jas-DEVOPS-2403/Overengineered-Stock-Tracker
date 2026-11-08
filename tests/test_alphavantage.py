"""Alpha Vantage client.

Each test here corresponds to a failure that actually happened while
building this, not to a coverage target.
"""

import pytest

from app import alphavantage as av
from tests.conftest import FakeSession


def budget(n=10):
    return av.Budget(n)


# ---------------------------------------------------------------------------
# The budget guard
# ---------------------------------------------------------------------------

def test_budget_refuses_past_its_limit():
    b = av.Budget(2)
    b.take()
    b.take()
    with pytest.raises(av.QuotaExhausted):
        b.take()
    assert b.spent == 2


# ---------------------------------------------------------------------------
# Quota and throttle detection
#
# Alpha Vantage answers BOTH the per-second throttle and the exhausted daily
# cap with HTTP 200 and a Note/Information key instead of data. A client that
# only checks status codes publishes empty files and reports success.
# ---------------------------------------------------------------------------

def test_quota_message_on_http_200_is_not_mistaken_for_data():
    session = FakeSession([{"Information": "the 25 requests per day limit"}] * 3)
    with pytest.raises(av.QuotaExhausted):
        av._payload(session, {"function": "X"}, budget())


def test_throttle_is_retried_before_being_called_exhausted():
    # Refused once, then succeeds. A run must survive this: treating the
    # per-second throttle as "the day is over" aborted the whole 21-symbol
    # loop after the first symbol.
    session = FakeSession([
        {"Note": "please spread out your free API requests (1 per second)"},
        {"Time Series (Daily)": {"2026-01-02": {"1. open": "1", "2. high": "1",
                                                "3. low": "1", "4. close": "1",
                                                "5. volume": "1"}}},
    ])
    b = budget()
    body = av._payload(session, {"function": "TIME_SERIES_DAILY"}, b)
    assert "Time Series (Daily)" in body
    # Both attempts were real HTTP requests, so both must count against quota.
    assert b.spent == 2


def test_retries_are_bounded_and_then_raise():
    session = FakeSession([{"Note": "throttled"}] * 5)
    b = budget()
    with pytest.raises(av.QuotaExhausted):
        av._payload(session, {"function": "X"}, b)
    assert b.spent == len(av._RETRY_BACKOFF) + 1


def test_bad_symbol_is_not_retried():
    # An Error Message means the request was understood and rejected.
    session = FakeSession([{"Error Message": "Invalid API call"}] * 3)
    b = budget()
    with pytest.raises(RuntimeError) as exc:
        av._payload(session, {"function": "X"}, b)
    assert not isinstance(exc.value, av.QuotaExhausted)
    assert b.spent == 1


# ---------------------------------------------------------------------------
# The four response shapes
# ---------------------------------------------------------------------------

def test_daily_bars_are_oldest_last_with_prices_parsed():
    session = FakeSession([{"Time Series (Daily)": {
        "2026-01-03": {"1. open": "11", "2. high": "12", "3. low": "10",
                       "4. close": "11.5", "5. volume": "2000"},
        "2026-01-02": {"1. open": "10", "2. high": "11", "3. low": "9",
                       "4. close": "10.5", "5. volume": "1000"},
    }}])
    out = av.fetch_daily("TEST", session, budget())
    dates = [d for d, _ in out["bars"]]
    assert dates == ["2026-01-02", "2026-01-03"]
    assert out["bars"][-1][1]["c"] == 11.5
    assert out["bars"][-1][1]["v"] == 2000


def test_crypto_shape_is_date_keyed():
    session = FakeSession([{"Time Series (Digital Currency Daily)": {
        "2026-01-02": {"4. close": "40000.5"},
        "2026-01-01": {"4. close": "39000.0"},
    }}])
    points = av.fetch_cross_asset("BTC", session, budget())
    assert points == [("2026-01-01", 39000.0), ("2026-01-02", 40000.5)]


def test_gold_shape_is_a_newest_first_list_of_price():
    session = FakeSession([{"data": [
        {"date": "2026-01-02", "price": "2100.5"},
        {"date": "2026-01-01", "price": "2090.25"},
    ]}])
    points = av.fetch_cross_asset("GOLD", session, budget())
    assert points == [("2026-01-01", 2090.25), ("2026-01-02", 2100.5)]


def test_weekly_adjusted_uses_the_adjusted_close():
    # Not "4. close". Using the raw close here would silently turn the
    # benchmark into a price-return series and understate it by the
    # dividends -- roughly 9-11% over five years.
    session = FakeSession([{"Weekly Adjusted Time Series": {
        "2026-01-09": {"4. close": "100.0", "5. adjusted close": "104.0"},
    }}])
    points = av.fetch_cross_asset("SPY", session, budget())
    assert points == [("2026-01-09", 104.0)]


# ---------------------------------------------------------------------------
# EIA quirks
# ---------------------------------------------------------------------------

def test_missing_eia_observations_are_skipped_not_crashed():
    # FRED writes "." for a missing observation, which float() rejects.
    session = FakeSession([{"data": [
        {"date": "2026-01-03", "value": "75.0"},
        {"date": "2026-01-02", "value": "."},
        {"date": "2026-01-01", "value": "74.0"},
    ]}])
    points = av.fetch_cross_asset("WTI", session, budget())
    assert [d for d, _ in points] == ["2026-01-01", "2026-01-03"]


def test_negative_oil_prices_survive_parsing():
    # WTI settled at -$37.63 on 2020-04-20. Parsing must keep it; it is the
    # chart's job to refuse to rebase across it, not the parser's job to
    # quietly drop an inconvenient real observation.
    session = FakeSession([{"data": [
        {"date": "2020-04-21", "value": "10.01"},
        {"date": "2020-04-20", "value": "-37.63"},
    ]}])
    points = av.fetch_cross_asset("WTI", session, budget())
    assert ("2020-04-20", -37.63) in points


def test_empty_series_raises_rather_than_publishing_nothing():
    session = FakeSession([{"data": []}])
    with pytest.raises(RuntimeError):
        av.fetch_cross_asset("WTI", session, budget())
