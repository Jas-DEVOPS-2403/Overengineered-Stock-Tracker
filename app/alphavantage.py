"""Alpha Vantage client.

Two endpoints, both free:

  TIME_SERIES_DAILY&outputsize=compact   100 daily OHLCV bars
  TIME_SERIES_WEEKLY_ADJUSTED            25+ years of adjusted weekly closes

GLOBAL_QUOTE is deliberately not used: it costs the same one request and
returns less than the daily series, whose newest bar *is* the latest quote.

Three things about this API that are easy to get wrong:

1. Free-tier data is end-of-day. Realtime and 15-minute-delayed US data sit
   behind the `entitlement` parameter, which is premium, because exchange and
   FINRA rules govern it. Calling more often than once a day gains nothing.
2. Quota exhaustion arrives as **HTTP 200** with a `Note` or `Information`
   key instead of the data. A client that only checks status codes will treat
   it as success and happily publish nothing. `_payload` raises instead.
3. `outputsize=full` and TIME_SERIES_DAILY_ADJUSTED are premium. The daily
   series here is therefore as-traded, so a split inside the 100-bar window
   leaves a step in the chart. The weekly series is adjusted and does not.
"""

import os
import time
from typing import Any, Dict, List, Tuple

import requests

from app import config

BASE_URL = "https://www.alphavantage.co/query"
TIMEOUT = 20

# Alpha Vantage asks for at most 1 request per second on the free tier, and
# enforces it separately from the 25/day cap. Firing a 21-symbol loop with no
# pacing gets the second request refused outright.
_MIN_INTERVAL = 1.2
_RETRY_BACKOFF = (5, 15)
_last_call = 0.0


class QuotaExhausted(RuntimeError):
    """Alpha Vantage refused the call because the daily budget is gone."""


class Budget:
    """Hard ceiling on requests per run.

    The free tier is 25 requests/day with no way to query remaining quota, so
    the only protection against a loop bug burning the day is to count here
    and refuse to exceed it.
    """

    def __init__(self, limit: int):
        self.limit = limit
        self.spent = 0

    def take(self) -> None:
        if self.spent >= self.limit:
            raise QuotaExhausted(
                f"refusing to exceed the {self.limit}-request budget for today"
            )
        self.spent += 1


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Petra/1.0 (+https://github.com/Jas-DEVOPS-2403/petra)"})
    return s


def _api_key() -> str:
    key = os.getenv("ALPHAVANTAGE_API_KEY")
    if not key:
        raise RuntimeError("ALPHAVANTAGE_API_KEY is not set")
    return key


def _pace() -> None:
    global _last_call
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _payload(session: requests.Session, params: Dict[str, str], budget: Budget) -> Dict[str, Any]:
    """One request, paced and retried.

    Alpha Vantage answers both the per-second throttle and the daily cap with
    **HTTP 200** and a `Note`/`Information` key instead of data, and the two
    messages are not reliably distinguishable -- the throttle message quotes
    the daily limit too. So a refusal is retried with backoff, and only
    treated as the day being over once the retries are also refused. A caller
    that only checked status codes would publish empty files and report
    success.
    """
    params = {**params, "apikey": _api_key()}
    refusal = ""

    for attempt in range(len(_RETRY_BACKOFF) + 1):
        budget.take()
        _pace()
        resp = session.get(BASE_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        body = resp.json()

        if "Error Message" in body:
            # A bad symbol. Retrying will not help.
            raise RuntimeError(str(body["Error Message"])[:300])

        if "Note" not in body and "Information" not in body:
            return body

        refusal = str(body.get("Note") or body.get("Information"))[:300]
        if attempt < len(_RETRY_BACKOFF):
            time.sleep(_RETRY_BACKOFF[attempt])

    raise QuotaExhausted(refusal)


def _series(body: Dict[str, Any], key_fragment: str) -> Dict[str, Dict[str, str]]:
    """Pull the one time-series block out of a response.

    Alpha Vantage names it differently per endpoint ("Time Series (Daily)",
    "Weekly Adjusted Time Series"), so match on a fragment rather than
    hardcoding every spelling.
    """
    for key, value in body.items():
        if key_fragment in key.lower() and isinstance(value, dict):
            return value
    raise RuntimeError(f"no time series in response; keys were {list(body)}")


def fetch_daily(symbol: str, session: requests.Session, budget: Budget) -> Dict[str, Any]:
    """100 daily bars, newest last. The final bar is the latest close."""
    body = _payload(
        session,
        {
            "function": "TIME_SERIES_DAILY",
            "symbol": config.provider_symbol(symbol),
            "outputsize": "compact",
        },
        budget,
    )
    series = _series(body, "time series (daily)")

    dates = sorted(series)[-config.DAILY_POINTS:]
    bars: List[Tuple[str, Dict[str, float]]] = []
    for date in dates:
        row = series[date]
        bars.append(
            (
                date,
                {
                    "o": float(row["1. open"]),
                    "h": float(row["2. high"]),
                    "l": float(row["3. low"]),
                    "c": float(row["4. close"]),
                    "v": int(float(row["5. volume"])),
                },
            )
        )
    if not bars:
        raise RuntimeError(f"{symbol}: daily series was empty")
    return {"symbol": symbol, "bars": bars}


def fetch_weekly(symbol: str, session: requests.Session, budget: Budget) -> Dict[str, Any]:
    """Adjusted weekly closes, newest last, trimmed to WEEKLY_POINTS."""
    body = _payload(
        session,
        {
            "function": "TIME_SERIES_WEEKLY_ADJUSTED",
            "symbol": config.provider_symbol(symbol),
        },
        budget,
    )
    series = _series(body, "weekly adjusted")

    dates = sorted(series)[-config.WEEKLY_POINTS:]
    closes = [float(series[d]["5. adjusted close"]) for d in dates]
    if not closes:
        raise RuntimeError(f"{symbol}: weekly series was empty")
    return {"symbol": symbol, "dates": dates, "closes": closes}
