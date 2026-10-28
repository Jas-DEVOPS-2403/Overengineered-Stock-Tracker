# Petra

An end-of-day market watchlist. A GitHub Actions job fetches prices once a day,
writes a handful of small JSON files, and publishes them with a static page to
GitHub Pages. No server, no database, no API key in the browser, nothing that
can generate a bill.

Named for the Nabataean caravan city that taxed the incense routes — a trading
post cut into bare rock.

## What it does

- Tracks 18 US tickers plus three index-proxy ETFs.
- Publishes close, open, day high/low, volume, previous close and change.
- Charts 5D / 1M / 3M from daily bars and 1Y / 5Y from adjusted weekly closes.
- Derives market cap and P/E from SEC XBRL filings.
- Shows recent SEC filings per company as the news rail.
- Diffs everything against your last visit, entirely in the browser.

## Architecture

```text
GitHub Actions (once daily, 23:00 UTC)
  -> Alpha Vantage   TIME_SERIES_DAILY (weekdays), WEEKLY_ADJUSTED (weekends)
  -> SEC EDGAR       filings, shares outstanding, EPS
  -> data/*.json     pushed to an orphan `data` branch (one commit, force-pushed)
  -> _site           web/ + data/ uploaded as a Pages artifact
  -> GitHub Pages    static page fetches its own JSON
```

Five published files, each refreshed only as often as it changes:

| File | Refresh | Contents |
|---|---|---|
| `latest.json` | daily | per-symbol OHLCV, previous close, change, status |
| `daily.json` | daily | 100 daily closes per symbol |
| `weekly.json` | weekly | 260 adjusted weekly closes per symbol |
| `filings.json` | daily | recent SEC filings per ticker |
| `meta.json` | weekly | names, sectors, shares, EPS, market cap, P/E |

About 70 KB in total, and roughly 15 KB gzipped on a cold visit.

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

### Pivot 4: Observability stack -> the page it should have been

The stack had no consumer. It generated Prometheus metrics, pushed them to
Grafana Cloud, and ran a Render web service to serve `/metrics` — and nobody
looked at any of it. It had also been dead for seven months: the workflow was
disabled, its last runs had failed, and the committed snapshot was from March.

Problems:

- No front end existed. The whole pipeline produced numbers no person read.
- `python-snappy` needs a C library the runner does not install, which is the
  likeliest reason the scheduled job started failing.
- Scheduled workflows in public repos are **disabled automatically after 60
  days with no repository activity**, and workflow runs do not count as
  activity.

The provider choice was then decided by licensing rather than features:

- **Finnhub** forbids it on its face — *"strictly for personal use"*, *"not
  redistribute or share access to data or derived results"* — with no academic
  programme and no documented way to ask.
- **FMP**, **EODHD** and **marketdata.app** prohibit public display explicitly.
- **Twelve Data** and **Tiingo** free tiers are *"internal non-display usage
  only"*.
- **Stooq** now answers scripted clients with a JavaScript proof-of-work
  challenge.
- **Alpha Vantage's** terms contain no redistribution or display restriction at
  all, and §2 affirmatively grants the right to display.
- **SEC EDGAR** is public domain outright.

Result:

- Alpha Vantage for prices, SEC EDGAR for filings and fundamentals, Finnhub
  gone. Every source in the stack permits republication.
- Free `GLOBAL_QUOTE`/`TIME_SERIES_DAILY` data is end-of-day only, because
  realtime US data is governed by exchange and FINRA rules. So Petra is an
  end-of-day dashboard and says so, rather than implying a feed it does not
  have.
- One cron a day instead of `*/15`, no accumulated intraday state, no request
  pacing, and no dropped-run problem: each call returns 100 days of history,
  so a missed run heals itself on the next one.
- Runtime dependencies went from six packages to one (`requests`).

## Cost safety

Worth stating plainly, because it is a property of the design rather than a
setting someone remembered to switch on:

1. **Visitor traffic cannot cause API calls.** The page only ever fetches
   static JSON from Pages. Upstream APIs are called exclusively by the cron, at
   a frequency fixed by the schedule. A bot hitting the page a million times
   triggers zero upstream requests. A browser-held key or a serverless proxy
   would both have that hole.
2. **Nothing in the stack has a payment method.** Pages, Alpha Vantage and
   EDGAR are all free with no card attached. Pages' 100 GB/month is a soft
   limit — GitHub throttles or emails, it does not invoice. Actions minutes are
   unlimited on public repositories.
3. The only consumable is Pages bandwidth, and at ~15 KB gzipped that is
   roughly six million pageviews to reach a soft limit that carries no charge.

**Adding a serverless proxy would reintroduce the risk.** That is the one
change this design should not accept without rethinking the above.

## Known limitations, by design

- **End-of-day only.** Free Alpha Vantage data updates after the close.
  There is no intraday series and no live tick; the page shows "Close of
  {date}" rather than pretending otherwise.
- **Index tiles are ETFs.** No free tier sells index levels, so SPY, QQQ and
  DIA stand in, showing **percent change only** — SPY is about SPX/10 and DIA
  about DJI/100, so printing the ETF price under an index name would be wrong.
  QQQ also tracks the Nasdaq-100, not the Composite.
- **No market cap for multi-class filers.** EDGAR reports shares outstanding
  per class without a usable class dimension, so Berkshire returns the Class A
  count against a Class B price. GOOGL, META, NKE, BRK-B and V therefore show
  `--` instead of a confident wrong number. Their P/E still works, EPS being
  per-share already.
- **P/E is fiscal-year, not TTM.** It uses the latest 10-K EPS, and is labelled
  `P/E (FY25)` accordingly. A true trailing figure means summing four
  potentially restated quarters.
- **Daily bars are as-traded.** Alpha Vantage's adjusted daily series is
  premium, so a split inside the 100-day window leaves a step in the chart.
  The weekly series is adjusted and does not.
- **The watchlist is per-browser.** It lives in `localStorage`; there are no
  accounts and no server to hold one.

## Local run

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in ALPHAVANTAGE_API_KEY, SEC_CONTACT_EMAIL

ALPHAVANTAGE_API_KEY=... SEC_CONTACT_EMAIL=you@example.com python fetch_snapshot.py

mkdir -p web/data && cp data/*.json web/data/
python -m http.server -d web 8137      # http://localhost:8137
```

`PETRA_FORCE_MODE=weekend` exercises the weekly/fundamentals path on a weekday.

## Deploying

Push to `main`. Then, once:

- Settings -> Pages -> Source: **GitHub Actions** (the workflow also sets this
  itself via `configure-pages`).
- Add repository secrets `ALPHAVANTAGE_API_KEY` and `SEC_CONTACT_EMAIL`.

The workflow runs daily and on `workflow_dispatch`, which takes an optional
`mode` input to force the weekday or weekend path.

## Project layout

- `fetch_snapshot.py` — orchestrator; decides weekday vs weekend, merges prior
  state, writes the five files
- `app/alphavantage.py` — price and history client, with the request budget
- `app/edgar.py` — filings and XBRL fundamentals
- `app/config.py` — tracked symbols, display names, sector map
- `web/index.html` — the entire front end; React from a CDN, no build step
- `.github/workflows/publish.yml` — fetch, push the data branch, deploy Pages

## Credits

Price data by [Alpha Vantage](https://www.alphavantage.co). Filings and
fundamentals from [SEC EDGAR](https://www.sec.gov/edgar), public domain.
Not investment advice; end-of-day data, not for trading.
