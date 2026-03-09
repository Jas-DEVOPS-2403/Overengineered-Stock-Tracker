# Overengineered Stock Tracker

A stock metrics pipeline that fetches market data, converts it to Prometheus metrics, and visualizes it in Grafana.

This project started as a direct scrape model and then pivoted to a scheduled snapshot architecture for stability and lower API usage.

## What It Does

- Fetches stock quotes for a configured ticker set in `app/config.py`.
- Writes a normalized snapshot to `snapshot.json`.
- Serves Prometheus metrics from FastAPI at `/metrics`.
- Exposes health check at `/healthz`.
- Uses Grafana Alloy to scrape metrics (pull) and forward to Grafana Cloud (push).

## Current Architecture

```text
GitHub Actions (every 5 min)
  -> Finnhub API
  -> snapshot.json (committed to repo)
  -> Render FastAPI service serves /metrics
  -> Grafana Alloy scrapes /metrics (pull)
  -> Alloy remote_write to Grafana Cloud (push)
```

## Why the pivot

### Pivot 1: Live API fetch on `/metrics` -> Scheduled snapshot

Initial model:

```text
Grafana -> Render /metrics -> market API
```

Problems:

- Scrape frequency multiplied API calls.
- Data endpoint reliability was tied directly to dashboard polling.
- Burst scraping risked throttling/rate limits.

Updated model:

```text
Scheduler -> API -> snapshot storage -> /metrics serving
```

Result:

- Predictable API usage.
- More reliable metrics endpoint.
- Cleaner separation: ingestion vs serving.

### Pivot 2: `yfinance` stack -> Finnhub API

Problems observed:

- Dependency/build friction (`pandas` + newer Python runtime mismatch).
- Inconsistent behavior from finance wrappers under hosted environments.
- Signals of datacenter/IP-based restrictions that can look like bot traffic and trigger degraded responses.

Result:

- Moved quote ingestion logic to `Finnhub` with explicit API key auth (`FINNHUB_API_KEY`).
- Added ticker symbol mapping where provider symbol formats differ (example: `BRK-B` -> `BRK.B`).

### Pivot 3: Direct Grafana expectations -> Alloy pull then push

Key operational model:

- Prometheus-style scraping is pull-based.
- Grafana Cloud ingestion uses remote_write push.
- Alloy bridges both: it pulls `/metrics`, then pushes upstream.

This "pull then push" pattern resolved visibility gaps in Grafana.

## Learning Points

- Treat metrics generation and data ingestion as separate concerns.
- Do not let dashboard scrape cadence control external API usage.
- Pin runtime/tooling versions early; hosted builders can drift.
- External finance APIs may behave differently from cloud/datacenter IP ranges.
- Keep secrets out of code and use environment variables in Alloy/workflows.
- Validate end-to-end path, not only app health:
  - `snapshot.json` freshness
  - `/metrics` response
  - Alloy scrape success
  - remote_write success in Grafana

## Project Layout

- `app/server.py`: FastAPI endpoints (`/metrics`, `/healthz`)
- `app/stocks.py`: Finnhub ingestion logic
- `app/exporters.py`: Snapshot -> Prometheus formatter
- `app/config.py`: ticker list and app config
- `fetch_snapshot.py`: scheduled snapshot generator
- `.github/workflows/snapshot.yml`: cron workflow to refresh snapshot
- `alloy/config.alloy`: scrape + remote_write pipeline
- `alloy/Dockerfile`: Alloy container

## Local Run

```bash
source .venv/bin/activate
pip install -r requirements.txt
export FINNHUB_API_KEY="your_key"
python fetch_snapshot.py
python main.py
```

Check:

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/metrics
```

## Environment Variables

- `FINNHUB_API_KEY`: required for snapshot generation
- `GRAFANA_CLOUD_PROM_USER`: Grafana Cloud Prometheus user ID (Alloy)
- `GRAFANA_CLOUD_API_KEY`: Grafana Cloud API token with metrics write scope (Alloy)
