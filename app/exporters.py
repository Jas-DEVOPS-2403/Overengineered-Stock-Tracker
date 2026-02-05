from typing import Dict, Any, List, Tuple
from prometheus_client import CollectorRegistry, Gauge, generate_latest, CONTENT_TYPE_LATEST


def snapshot_to_prometheus(snapshot: Dict[str, Any], expected_tickers: List[str]) -> Tuple[bytes, str]:
    """
    Converts a stock snapshot to Prometheus exposition format.
    """
    registry = CollectorRegistry()

    g_price = Gauge('stock_price', 'Last price', ['ticker'], registry=registry)
    g_change = Gauge('stock_change_pct', 'Percentage change vs previous minute', ['ticker'], registry=registry)
    g_success = Gauge('stock_fetch_success', '1 if ticker present in snapshot, else 0', ['ticker'], registry=registry)
    g_latency = Gauge('stock_fetch_latency_seconds', 'Latency of stock data fetch in seconds', registry=registry)
    g_asof = Gauge('stock_data_asof_timestamp', 'Snapshot timestamp in unix', registry=registry)

    g_latency.set(float(snapshot.get("fetch_latency_seconds", 0.0)))
    g_asof.set(float(snapshot.get("asof_ts", 0.0)))

    tickers_data = snapshot.get("tickers", {}) or {}

    for t in expected_tickers:
        if t in tickers_data:
            g_success.labels(ticker=t).set(1)
            g_price.labels(ticker=t).set(float(tickers_data[t]["price"]))
            g_change.labels(ticker=t).set(float(tickers_data[t]["change_pct"]))
        else:
            g_success.labels(ticker=t).set(0)

    body = generate_latest(registry)
    return body, CONTENT_TYPE_LATEST
