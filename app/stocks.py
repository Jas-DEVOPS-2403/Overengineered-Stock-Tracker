import os
import time
from typing import Any, Dict, List

import requests


SYMBOL_MAP = {
    "BRK-B": "BRK.B",
}


def _to_provider_symbol(ticker: str) -> str:
    return SYMBOL_MAP.get(ticker, ticker)


def fetch_snapshot(tickers: List[str]) -> Dict[str, Any]:
    """
    Snapshot schema:
    {
      "asof_ts": <unix>,
      "fetch_latency_seconds": <float>,
      "tickers": {
        "AAPL": {"price": 123.4, "change_pct": 1.23},
      }
    }
    """
    token = os.getenv("FINNHUB_API_KEY")
    if not token:
        raise RuntimeError("FINNHUB_API_KEY is not set")

    started = time.time()
    out: Dict[str, Any] = {
        "asof_ts": int(time.time()),
        "fetch_latency_seconds": 0.0,
        "tickers": {},
    }

    session = requests.Session()
    session.headers.update({"User-Agent": "Overengineered-Stock-Tracker/1.0"})

    for ticker in tickers:
        provider_symbol = _to_provider_symbol(ticker)
        try:
            resp = session.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": provider_symbol, "token": token},
                timeout=10,
            )
            resp.raise_for_status()
            quote = resp.json()

            price = float(quote.get("c") or 0.0)
            prev_close = float(quote.get("pc") or 0.0)
            if price <= 0:
                continue

            change_pct = ((price - prev_close) / prev_close * 100.0) if prev_close else 0.0
            out["tickers"][ticker] = {"price": price, "change_pct": change_pct}
        except Exception:
            continue

    out["fetch_latency_seconds"] = time.time() - started
    return out
