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
  all. They do not *grant* public display either — §2 is a standard EULA clause
  granting display of the software on devices you own or control, for personal
  use. So public display is unaddressed rather than authorised, which is a
  materially better position than an express prohibition, but it is not a
  licence. Stated plainly here rather than overclaimed.
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

## Across asset classes

A comparison panel rebases equities, commodities and crypto to 100 from a
common start, on a log scale, with total return, annualised volatility and
max drawdown per series.

Most charts like this are quietly wrong. The specific traps, and what is done
about each:

- **Price return vs total return.** Dividends are roughly 9–11% cumulative for
  a broad equity index over five years. Plotting price-return equities against
  crypto understates equities by exactly that, every time. The equity lines
  here come from the **weekly adjusted** series, so they are total return.
- **Trading calendars.** Crypto runs 365 days a year, equities about 252. The
  usual fix — forward-filling equities across weekends — inserts zero-return
  days, which deflates measured equity volatility by about 17% and corrupts
  every correlation. Everything is **downsampled to one weekly grid** instead,
  which never invents a point. Volatility is annualised at √52 throughout;
  mixing √252 and √365 is a ~20% error.
- **Start date.** It dominates the conclusion. Bitcoin from November 2021 is
  +20% with a 74% drawdown; from November 2025 it is −25%. Both are true, which
  is why the window is **selectable** rather than fixed.
- **Log scale.** Not cosmetic. On a linear axis a 20x crypto move flattens a 2x
  equity move into a straight line.
- **Spot is not an investable return.** Gold, silver and the oil/gas series are
  spot prices — no carry, no yield, nobody earned them. They are labelled as
  such on the chart rather than being silently mixed with total-return lines.
- **Prices that reach zero.** WTI settled at **−$37.63** on 2020-04-20. Rebasing
  across that is undefined, so any series whose window touches zero or below is
  **excluded and named**, not plotted as nonsense.

Deliberately excluded: Alpha Vantage's IMF-backed commodity endpoints (copper,
wheat, coffee and the rest). They are monthly *period averages*, which cannot
join a weekly grid and whose averaging destroys realised volatility — and the
underlying FRED series carry an IMF copyright notice that requires permission
for anything beyond personal use.

The EIA-sourced series (WTI, Brent, natural gas) come through FRED, so the page
carries the required notice: *"This product uses the FRED® API but is not
endorsed or certified by the Federal Reserve Bank of St. Louis."*

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
- **Daily bars are as-traded; weekly bars are adjusted.** Alpha Vantage's
  adjusted *daily* series is premium, so a split inside the 100-day window
  leaves a step in the chart. The weekly series is split- and
  dividend-adjusted and does not. The consequence worth knowing: the 5D/1M/3M
  tabs are **price return** while 1Y/5Y are **total return**, so the longer
  windows include dividends and the shorter ones do not. Over five years that
  gap is roughly 9-11% for a broad index — which is why any cross-asset
  comparison built on this data must use the weekly series throughout rather
  than mixing the two.
- **The watchlist is per-browser.** It lives in `localStorage`; there are no
  accounts and no server to hold one.

## Tests and CI

```bash
pip install -r requirements-dev.txt
ruff check .
pytest
```

44 tests, all offline — both providers are faked, so the suite costs none of
the 25-request daily budget and cannot be broken by a provider outage. CI runs
`ruff` and `pytest` on every push and pull request.

The suite is deliberately built around failures that actually happened rather
than around coverage. Each of these is a test because it was once a bug:

- Alpha Vantage answers both its per-second throttle and its exhausted daily
  cap with **HTTP 200** and a `Note`/`Information` key. One test proves a
  refusal is never mistaken for data; another proves a throttle is retried
  rather than aborting the whole run.
- Berkshire's abandoned XBRL tag still returns its **2013** Class A EPS of
  3035.0, which against a Class B price gives a P/E of 0.16.
- Multi-class filers report shares outstanding with no usable class
  dimension, so they publish no market cap — but keep their P/E, EPS being
  per-share already.
- A symbol missing from one fetch must be **padded with null**, not closed up,
  or index *i* stops meaning the same date for every series.
- The EIA series write `"."` for a missing observation, and WTI genuinely went
  negative in April 2020. Both must survive parsing.
- Real environment variables must win over a local `.env`, or CI secrets get
  silently overridden.

There is also an arithmetic test asserting the daily request budget still
fits, so adding a ticker without doing the maths fails CI rather than failing
quietly at 23:00 UTC.

**If the daily publish fails, it opens a GitHub issue** (one rolling issue,
commented on rather than duplicated) with the run link and the likely causes.
And once data falls more than four days behind, the page itself shows a
banner saying so. This project spent seven months serving March data behind a
workflow that had quietly disabled itself; both of those exist so that cannot
happen silently again.

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
