# CLAUDE.md — Overengineered Stock Tracker

Personal observability learning project. Fetches stock quotes, exposes
Prometheus metrics, and pushes them to Grafana Cloud. Intentionally
overengineered for the sake of learning the full observability stack.

## Architecture

```
GitHub Actions (*/15 * * * *)
  → fetch_snapshot.py       — calls Finnhub, writes snapshot.json
  → push_metrics.py         — encodes snapshot as Prometheus remote_write,
                               POSTs directly to Grafana Cloud
  → commit snapshot.json    — so Render /metrics stays fresh

Render (FastAPI, free tier)
  → GET /metrics            — serves snapshot.json in Prometheus text format
  → GET /healthz            — health check

Grafana Cloud
  → receives remote_write pushes from GitHub Actions
  → dashboards read from hosted Prometheus
```

Alloy (`alloy/`) is kept as reference but is **not deployed** — the
direct-push approach from GitHub Actions is simpler and does not require
an always-on container.

## Key Files

| File | Purpose |
|---|---|
| `fetch_snapshot.py` | Calls Finnhub API, writes `snapshot.json` |
| `push_metrics.py` | Pushes `snapshot.json` to Grafana Cloud via remote_write |
| `app/server.py` | FastAPI — `/metrics` and `/healthz` |
| `app/exporters.py` | `snapshot.json` → Prometheus text format |
| `app/stocks.py` | Finnhub quote fetching logic |
| `app/config.py` | Ticker list and cache TTL |
| `.github/workflows/snapshot.yml` | Cron: fetch → push → commit |
| `alloy/config.alloy` | Alloy config (reference only, not deployed) |
| `render.yaml` | Render deployment spec for the FastAPI service |

## Environment Variables / Secrets

Set these in **GitHub → Settings → Secrets and variables → Actions**:

| Secret | Description |
|---|---|
| `FINNHUB_API_KEY` | Free API key from finnhub.io |
| `GRAFANA_REMOTE_WRITE_URL` | e.g. `https://prometheus-prod-37-prod-ap-southeast-1.grafana.net/api/prom/push` — visible in Grafana Cloud → Prometheus data source details |
| `GRAFANA_CLOUD_PROM_USER` | Numeric user ID from the same data source page |
| `GRAFANA_CLOUD_API_KEY` | Grafana Cloud API token with **MetricsPublisher** role |

For local testing, copy `.env.example` to `.env` and fill in values.

## Running Locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export FINNHUB_API_KEY="..."
python fetch_snapshot.py          # writes snapshot.json

# Optional: push to Grafana Cloud
export GRAFANA_REMOTE_WRITE_URL="..."
export GRAFANA_CLOUD_PROM_USER="..."
export GRAFANA_CLOUD_API_KEY="..."
python push_metrics.py

# Run the FastAPI server
python main.py
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/metrics
```

## How push_metrics.py Works

`push_metrics.py` implements Prometheus
[remote_write](https://prometheus.io/docs/concepts/remote_write_spec/) from
scratch (no Alloy, no Pushgateway):

1. Reads `snapshot.json`
2. Builds a `WriteRequest` protobuf manually (no generated code — the schema
   is simple enough to encode by hand with varint/length-delimited fields)
3. Compresses with raw snappy (`snappy.raw_compress`, matching Go's
   `snappy.Encode` used by the Prometheus client)
4. POSTs to the Grafana Cloud endpoint with `Content-Type:
   application/x-protobuf` and `Content-Encoding: snappy`

Metrics pushed:
- `stock_price{ticker="..."}` — last trade price
- `stock_change_pct{ticker="..."}` — percentage change
- `stock_fetch_success{ticker="..."}` — 1 if present in snapshot
- `stock_fetch_latency_seconds` — total Finnhub fetch latency
- `stock_data_asof_timestamp` — unix timestamp of snapshot

## Grafana Cloud Setup (one-time)

1. Go to **Grafana Cloud → your stack → Prometheus → Details**.
2. Copy the **Remote Write URL**, **Username** (numeric), and generate an
   **API token** with the `MetricsPublisher` role.
3. Add all three as GitHub Secrets (see table above).
4. In Grafana, add a dashboard or use Explore to query `stock_price{ticker="AAPL"}`.

## Disabling / Killing Compute

To stop all scheduled jobs:
- **GitHub Actions**: go to the repo → Actions → "Update Snapshot" →
  click the `...` menu → **Disable workflow**.
- **Render**: go to your Render dashboard → select the service →
  **Settings** → **Suspend** (or delete it entirely).

Once disabled, no API calls are made and no metrics are pushed.

## Render Deployment

`render.yaml` defines the FastAPI web service. Render auto-deploys on
every push to `main`. The free tier sleeps after 15 min of inactivity —
that is fine because `/metrics` is only for manual inspection; actual
metrics flow through the GitHub Actions push path.

## Common Debug Commands

```bash
# Check what snapshot.json looks like right now
cat snapshot.json | python -m json.tool

# Manually trigger a push (after exporting env vars)
python push_metrics.py

# Hit the live Render endpoint
curl https://overengineered-stock-tracker.onrender.com/healthz
curl https://overengineered-stock-tracker.onrender.com/metrics
```
