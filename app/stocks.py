import time
from tracemalloc import start
from typing import Dict, Any, List
import yfinance as yf

def fetch_snapshot(tickers: List[str]) -> Dict[str, Any]:
    """
    returns a bounded snapshot:
    {
        "asof_ts": <unix>,
        "tickers":{
            "NVDA": {"price":213.45, "change_pct":0.27}}
            ...
    }
    """
    start = time.time()
    #app yfin can fetch multiple tickers at once
    data = yf.download(
        tickers=tickers,
        period="1d",
        interval="1m",
        group_by='ticker',
        auto_adjust=True,
        threads=True,
        progress=False
    )
    print("DATA EMPTY:", data.empty)
    print("COLUMNS:", data.columns)
    print("TAIL:", data.tail(2))

    ticker_out : Dict[str, Dict[str, float]] = {}

    for t in tickers:
        try:
            #when multiple tickers, columns are multiindexed: (field, ticker) or (ticker, field)
            #yfin can be inconsistent need to be able to handle both patterns
            if hasattr(data.columns, 'levels') and len(data.columns.levels) == 2:
                if ("Close", t) in data.columns:
                    close_series = data[("Close", t)].dropna()
                else:
                    #try (ticker, field) pattern
                    close_series = data[(t, "Close")].dropna()

            else:
                #single ticker case (flat columns)
                close_series = data["Close"].dropna()

            if len(close_series) < 2:
                continue

            last = float(close_series.iloc[-1])
            prev = float(close_series.iloc[-2])
            change_pct = (last - prev) * 100 if prev != 0 else 0.0

            ticker_out[t] = {
                "price": last,
                "change_pct": change_pct
            }
        except Exception:
            continue

    latency = time.time() - start

    return {
        "asof_ts": int(time.time()),
        "fetch_latency_sec": latency,
        "tickers": ticker_out
    }