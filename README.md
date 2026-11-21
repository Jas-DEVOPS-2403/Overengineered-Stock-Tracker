# Petra

An end-of-day market watchlist. A GitHub Actions job fetches prices once a day,
writes a handful of small JSON files to a `data` branch, and a static page
reads them. No server, no database, no API key in the browser, nothing that
can generate a bill.

Named for the Nabataean caravan city that taxed the incense routes — a trading
post cut into bare rock.

## Status

The data pipeline works. The daily job fetches and publishes to the
[`data` branch](../../tree/data), which holds the current snapshot.

The page is a personal dashboard, run locally against that branch (see
[Local run](#local-run)), and is deliberately not hosted. Hosting it would
make every visitor a third-party viewer of Alpha Vantage and TradingView
data, and neither set of terms clearly covers that: Alpha Vantage's do not
address redistribution, and TradingView limits its widgets to "personal or
internal business purposes".

Petra is a market data platform and a simulation harness. Data is end-of-day,
any execution modelled on top of it is simulated, and the scheduler is
best-effort; the specifics are in
[Known limitations](#known-limitations-by-design). It is not a trading system
and not investment advice.

## What it does

- Tracks 13 US tickers plus three index-proxy ETFs.
- Publishes close, open, day high/low, volume, previous close and change.
- Charts 5D / 1M / 3M from daily bars and 1Y / 5Y from adjusted weekly closes.
- Derives market cap and P/E from SEC XBRL filings.
- Shows recent SEC filings per company as the news rail.
- Diffs everything against your last visit, entirely in the browser.
- Shows a delayed intraday strip above the dashboard, embedded from
  TradingView: US stocks delayed by the exchanges, crypto in real time. None of
  it is Petra's data, and none of it feeds Petra's figures.

## Architecture

```text
GitHub Actions (once daily, 23:00 UTC)
  -> Alpha Vantage   TIME_SERIES_DAILY (weekdays), WEEKLY_ADJUSTED (weekends)
  -> SEC EDGAR       filings, shares outstanding, EPS
  -> data/*.json     pushed to an orphan `data` branch (one commit, force-pushed)

Local checkout
  -> web/data/       extracted from the `data` branch
  -> web/index.html  static page fetches its own JSON
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
  gone. No source in the stack forbids republication: EDGAR is public domain,
  and Alpha Vantage's terms do not address it.
- Free `GLOBAL_QUOTE`/`TIME_SERIES_DAILY` data is end-of-day only, because
  realtime US data is governed by exchange and FINRA rules. So Petra is an
  end-of-day dashboard and says so, rather than implying a feed it does not
  have.
- One cron a day instead of `*/15`, no accumulated intraday state, no request
  pacing, and no dropped-run problem: each call returns 100 days of history,
  so a missed run heals itself on the next one.
- Runtime dependencies went from six packages to one (`requests`).

### Intraday: an embed, not an API

Re-checked in November 2026 for a free source of near-real-time US quotes.
They exist, and every one is licensed for internal use only:

- **Tiingo**'s free tier carries a consolidated real-time feed; showing it
  publicly needs a paid redistribution licence.
- **Twelve Data**'s free real-time US data is non-display.
- **Alpaca** streams IEX in real time, but its service is for US residents and
  its terms forbid public display.
- **Massive** (formerly Polygon.io) is end-of-day on its free tier, with display
  rights only on paid business plans.

The reason is the same each time: the exchanges charge for showing live
quotes to the public, and a free tier cannot pass that licence on.

TradingView's embeddable widgets are the exception, because TradingView shows
its own data under its own exchange agreements. The page embeds a ticker tape
and never receives, stores or republishes a price. The terms of that:

- **US stocks are delayed.** TradingView's widget FAQ: *"Exchanges want each
  website to pay fees for real-time data. As per their requirements we are only
  allowed to stream delayed data."* The widget marks delayed quotes with a
  "D". A paid TradingView plan does not change this.
- **Crypto and forex are real-time**, so the crypto lines are the ones that
  move in the evening and at the weekend.
- **Display only.** Widget data cannot be read back, so it cannot be rebased,
  guarded or checked for staleness. It sits in its own labelled band and stays
  out of every figure Petra computes.
- **Attribution stays as designed.** TradingView's terms require it, so the
  logo and the "Track all markets on TradingView" line are left alone.
- **Licence scope.** TradingView's terms limit use to "personal or internal
  business purposes". A personal, non-commercial project is the most
  reasonable reading of that, but the terms do not say so expressly.

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

1. **Loading the page cannot cause API calls.** Petra's own data is only ever
   static JSON. Upstream APIs are called exclusively by the cron, at a
   frequency fixed by the schedule, so reloading the page any number of times
   triggers zero upstream requests. A browser-held key or a serverless proxy
   would both break that, and would matter the day the page is hosted. The
   intraday strip does not change this: it is TradingView's iframe, served by
   TradingView, with no key of ours and no quota of ours behind it.
2. **Nothing in the stack has a payment method.** Alpha Vantage and EDGAR are
   free with no card attached, and Actions minutes are unlimited on public
   repositories.

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

- **The scheduler is best-effort.** Measured on this repo's own history under
  its previous `*/15` schedule: 1,437 snapshot commits across 39.1 days, about
  37 runs a day against 96 nominal slots, so roughly 62% of scheduled runs
  never fired. The daily schedule is much less exposed and a missed run heals
  itself, since each call returns 100 days. Anything built on top of this that
  acts on a schedule has to treat a missed decision as ordinary rather than
  exceptional.
- **The intraday strip is a third-party embed.** It depends on TradingView
  being reachable, an ad blocker may hide it, and TradingView records the
  embedding page, the symbols shown and the viewer's IP address. The page
  says so in its footer. If the strip fails to load, the band is left empty
  and nothing else on the page is affected.
- **No crypto price is *the* price.** Crypto has no consolidated tape and no
  NBBO, so quoted prices differ between venues. The stored crypto series is one
  source's daily close; a cross-venue average is an average, not a price anyone
  can trade at. Whichever is shown, the page names it.

## Direction

The intended end point is a strategy runner on data worth trusting. Each step
depends on the previous one being correct, so they are built in order rather
than together.

1. **Market data platform.** Licence-clean sources, a fixed request budget, a
   daily publish. Done.
2. **Backtest engine.** A small set of strategies over stored daily bars,
   measured against buy-and-hold, with fees and slippage charged and
   risk-adjusted results reported: CAGR, Sharpe, max drawdown, turnover, and
   fees as a share of gross return. A signal computed on bar *t* executes at
   *t+1*'s open, never at *t*'s close. Not started.
3. **Paper execution.** A scheduled job that computes a signal, simulates a
   fill, logs it, and reports the gap between what the backtest predicted and
   what the scheduled run produced. Not started.
4. **Near-live data.** A delayed *display* strip exists already (see
   [Intraday: an embed, not an API](#intraday-an-embed-not-an-api)). Near-live
   data that Petra *computes with* is crypto only, and only once 2 and 3
   produce figures worth reading. US equity real-time data is licensed through the exchanges and
   stays out of reach. Crypto is less gated but not ungated: no major venue
   licenses a third party to display its market data, so there is no exchange
   websocket to stream. What is possible is a keyless, attributed aggregator
   fetched by the browser on a slow poll -- no key to leak, no proxy to pay
   for, and the cost-safety property above survives intact.

Execution is simulated throughout. No real orders and no real money at any
step.

## Tests and CI

```bash
pip install -r requirements-dev.txt
ruff check .
pytest
```

54 tests, all offline — both providers are faked, so the suite costs none of
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

Against the data the daily job published, which costs no API quota:

```bash
git fetch origin data
mkdir -p web/data
git archive -o data.tar FETCH_HEAD && tar -xf data.tar -C web/data && rm data.tar
python -m http.server -d web 8137      # http://localhost:8137
```

`git archive` rather than `git checkout FETCH_HEAD -- .`, which would also
stage the data files into `main`'s index. The same commands work in
PowerShell, with `Remove-Item data.tar` for the last step.

To build the data yourself instead, which spends the day's Alpha Vantage
quota:

```bash
pip install -r requirements.txt
ALPHAVANTAGE_API_KEY=... SEC_CONTACT_EMAIL=you@example.com python fetch_snapshot.py
mkdir -p web/data && cp data/*.json web/data/
```

`PETRA_FORCE_MODE=weekend` exercises the weekly/fundamentals path on a weekday.

## The daily job

Add repository secrets `ALPHAVANTAGE_API_KEY` and `SEC_CONTACT_EMAIL`. The
workflow runs daily and on `workflow_dispatch`, which takes an optional
`mode` input to force the weekday or weekend path. Its only output is the
`data` branch; there is no deploy step.

## Project layout

- `fetch_snapshot.py` — orchestrator; decides weekday vs weekend, merges prior
  state, writes the five files
- `app/alphavantage.py` — price and history client, with the request budget
- `app/edgar.py` — filings and XBRL fundamentals
- `app/config.py` — tracked symbols, display names, sector map
- `web/index.html` — the entire front end; React from a CDN, no build step
- `.github/workflows/publish.yml` — fetch, push the data branch, alert on failure

## Credits

Price data by [Alpha Vantage](https://www.alphavantage.co). Filings and
fundamentals from [SEC EDGAR](https://www.sec.gov/edgar), public domain.
Not investment advice; end-of-day data, not for trading.
