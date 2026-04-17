#!/usr/bin/env python3
"""
Push stock metrics to Grafana Cloud via Prometheus remote_write.

Reads snapshot.json and encodes it as a Prometheus WriteRequest (protobuf +
raw snappy), then POSTs to the Grafana Cloud remote_write endpoint.

This eliminates the need for a separately-deployed Grafana Alloy instance.
Required env vars:
  GRAFANA_REMOTE_WRITE_URL  e.g. https://prometheus-prod-XX.grafana.net/api/prom/push
  GRAFANA_CLOUD_PROM_USER   numeric user ID shown in Grafana Cloud data source settings
  GRAFANA_CLOUD_API_KEY     Grafana Cloud API token (MetricsPublisher role)
"""

import json
import os
import struct
import sys

import requests
import snappy


# ── Minimal Prometheus remote_write protobuf encoder ─────────────────────────
# Schema mirrors prometheus/prompb WriteRequest / TimeSeries / Label / Sample.

def _varint(n: int) -> bytes:
    buf = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            buf.append(b | 0x80)
        else:
            buf.append(b)
            break
    return bytes(buf)


def _field_len(num: int, data: bytes) -> bytes:
    """Wire type 2 (length-delimited)."""
    return _varint((num << 3) | 2) + _varint(len(data)) + data


def _field_varint(num: int, n: int) -> bytes:
    """Wire type 0 (varint)."""
    return _varint((num << 3) | 0) + _varint(n)


def _field_double(num: int, v: float) -> bytes:
    """Wire type 1 (64-bit)."""
    return _varint((num << 3) | 1) + struct.pack("<d", v)


def _encode_label(name: str, value: str) -> bytes:
    return _field_len(1, name.encode()) + _field_len(2, value.encode())


def _encode_sample(value: float, ts_ms: int) -> bytes:
    return _field_double(1, value) + _field_varint(2, ts_ms)


def _encode_timeseries(labels: dict, value: float, ts_ms: int) -> bytes:
    msg = b"".join(
        _field_len(1, _encode_label(k, v)) for k, v in sorted(labels.items())
    )
    msg += _field_len(2, _encode_sample(value, ts_ms))
    return msg


def build_write_request(metrics: list) -> bytes:
    """
    Build a Prometheus WriteRequest protobuf payload.

    metrics: list of (labels_dict, float_value, timestamp_ms)
             Each labels_dict must include '__name__'.
    """
    return b"".join(
        _field_len(1, _encode_timeseries(labels, value, ts_ms))
        for labels, value, ts_ms in metrics
    )


# ── Snapshot → metrics ────────────────────────────────────────────────────────

def snapshot_to_metrics(snapshot: dict) -> list:
    ts_ms = int(snapshot["asof_ts"] * 1000)
    metrics = []

    for ticker, data in snapshot.get("tickers", {}).items():
        if (price := data.get("price")) and price > 0:
            metrics.append(({"__name__": "stock_price", "ticker": ticker}, float(price), ts_ms))
        if (chg := data.get("change_pct")) is not None:
            metrics.append(({"__name__": "stock_change_pct", "ticker": ticker}, float(chg), ts_ms))
        metrics.append(({"__name__": "stock_fetch_success", "ticker": ticker}, 1.0, ts_ms))

    if (lat := snapshot.get("fetch_latency_seconds")) is not None:
        metrics.append(({"__name__": "stock_fetch_latency_seconds"}, float(lat), ts_ms))

    metrics.append(({"__name__": "stock_data_asof_timestamp"}, float(snapshot["asof_ts"]), ts_ms))
    return metrics


# ── Push ──────────────────────────────────────────────────────────────────────

def push(metrics: list, url: str, user: str, token: str) -> None:
    proto = build_write_request(metrics)
    # snappy.compress() produces raw block format, matching Go's snappy.Encode used by remote_write.
    compressed = snappy.compress(proto)

    resp = requests.post(
        url,
        data=compressed,
        headers={
            "Content-Type": "application/x-protobuf",
            "Content-Encoding": "snappy",
            "X-Prometheus-Remote-Write-Version": "0.1.0",
        },
        auth=(user, token),
        timeout=30,
    )

    if not resp.ok:
        print(f"ERROR: HTTP {resp.status_code} — {resp.text[:500]}", file=sys.stderr)
        resp.raise_for_status()

    print(f"Pushed {len(metrics)} metrics → HTTP {resp.status_code}")


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    missing = [v for v in ("GRAFANA_REMOTE_WRITE_URL", "GRAFANA_CLOUD_PROM_USER", "GRAFANA_CLOUD_API_KEY") if not os.environ.get(v)]
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    url   = os.environ["GRAFANA_REMOTE_WRITE_URL"]
    user  = os.environ["GRAFANA_CLOUD_PROM_USER"]
    token = os.environ["GRAFANA_CLOUD_API_KEY"]

    with open("snapshot.json") as f:
        snapshot = json.load(f)

    metrics = snapshot_to_metrics(snapshot)
    push(metrics, url, user, token)
    tickers = snapshot.get("tickers", {})
    print(f"Done — {len(tickers)} tickers, {len(metrics)} series pushed.")
