# CLAUDE.md — Petra

End-of-day market watchlist. A daily GitHub Actions job writes five small JSON
files and publishes them with a static page to GitHub Pages. No server, no
database, no key in the browser.

## Architecture

```
GitHub Actions (0 23 * * *)
  weekdays  -> Alpha Vantage TIME_SERIES_DAILY        x21 symbols
            -> SEC EDGAR filings                      x18 tickers
            -> latest.json, daily.json, filings.json
  weekends  -> Alpha Vantage TIME_SERIES_WEEKLY_ADJ   x~11 symbols
            -> SEC EDGAR companyfacts                 x~9 tickers
            -> weekly.json, meta.json

  -> data/ pushed to orphan `data` branch (single commit, force-pushed)
  -> _site = web/ + data/ uploaded as a Pages artifact
  -> GitHub Pages serves the page, which fetches its own JSON
```

`fetch_snapshot.py` decides weekday vs weekend from the date in US Eastern
terms. `PETRA_FORCE_MODE=weekday|weekend` overrides it.

## Key files

| File | Purpose |
|---|---|
| `fetch_snapshot.py` | Orchestrator: mode choice, state merge, all five outputs |
| `app/alphavantage.py` | Daily/weekly series, request budget, quota detection |
| `app/edgar.py` | CIK resolution, filings, XBRL shares and EPS |
| `app/config.py` | Tracked symbols, names, sector map, limits |
| `web/index.html` | Entire front end; React UMD from cdnjs, no build step |
| `.github/workflows/publish.yml` | Fetch, push data branch, deploy Pages |

## Things that will bite you

**Alpha Vantage signals quota exhaustion with HTTP 200.** The body carries a
`Note` or `Information` key instead of data. `_payload` raises `QuotaExhausted`
on that; never "fix" it by only checking status codes, or the job will publish
empty files and call it success.

**The free tier is 25 requests/day and end-of-day only.** Weekday and weekend
work are split precisely so they never contend for the same budget. Adding
symbols past ~21 breaks that arithmetic unless the open-source/educational
allowance has been granted. `config.AV_DAILY_BUDGET` is the hard guard.

**`outputsize=full`, `TIME_SERIES_DAILY_ADJUSTED`, `OVERVIEW`, `NEWS_SENTIMENT`
and every `INDEX_DATA` endpoint are premium.** Do not drift onto them.

**SEC EDGAR requires a declared User-Agent** with a contact address, and caps
at 10 requests/second. `SEC_CONTACT_EMAIL` supplies it; the address is not
committed because this repo is public.

**Use `companyfacts`, not `companyconcept`.** The per-concept endpoint returns
200 with an empty unit list for some filers — Coca-Cola among them — while the
same facts are present in `companyfacts`. One request instead of three, and it
actually works.

**Multi-class filers have no usable share count.** EDGAR reports shares
outstanding per class with no usable class dimension, so Berkshire returns its
Class A count (~941k) against a Class B price. `config.MULTI_CLASS` lists the
affected tickers and they publish `mcap: null`. P/E is unaffected.

**Old XBRL tags linger.** Companies migrate between tags and the abandoned one
keeps returning its last values — Berkshire's 2013 EPS of 3035.0 would
otherwise be divided into a $500 price. `_latest_annual` rejects facts older
than two fiscal years.

**Market cap must be re-derived every run.** It is shares x price, and the
price changes daily while the share count refreshes weekly. `derive_valuations`
runs on both paths; deriving only at the weekend would print a week-old cap
beside a current price.

**Scheduled workflows in public repos auto-disable after 60 days of repository
inactivity**, and workflow runs do not count. The daily push to the `data`
branch is what keeps the timer alive. This repo has already been bitten once.

**Pages serves `cache-control: max-age=600` and it is not configurable.** The
page cache-busts its fetches with an hour-granularity stamp.

## Front end notes

Ported from a design handoff (`Stock tracker dashboard design.zip`). The
handoff's markup used a design-canvas template DSL (`sc-for`, `sc-if`,
`style-hover`) compiled by its own `support.js`, which it states is **not part
of the implementation** — so the markup was translated to `React.createElement`
and `support.js` dropped. `renderVals()` kept its shape and name, and the pure
helpers (`fmt`, `sp`, `cap`, `pts`, `ago`) carried over unchanged.

Adaptations forced by end-of-day data:

- The prototype's 2-second random walk is gone. Nothing polls.
- The tile sparkline is ~25 **daily** closes, not intraday, plus an O/H/L/C bar
  for the session the sparkline cannot show.
- Range tabs are 5D/1M/3M (daily) and 1Y/5Y (weekly adjusted), not 1D–1Y.
- "Live · h:mm:ss ET" became "Close of {date}".
- The flash animation fires once on load for prices that changed since this
  visitor's last look, rather than on a synthetic tick.
- The news sidebar is SEC filings; form type is the category.

Theme lives on `document.documentElement` (set before paint by an inline
script) so `body` can follow `--bg` and light mode has no dark gutter.

## Local run

```bash
pip install -r requirements.txt

ALPHAVANTAGE_API_KEY=... SEC_CONTACT_EMAIL=you@example.com python fetch_snapshot.py
mkdir -p web/data && cp data/*.json web/data/
python -m http.server -d web 8137
```

`data/` and `web/data/` are gitignored — published data lives on the `data`
branch, and `main` holds source only.
