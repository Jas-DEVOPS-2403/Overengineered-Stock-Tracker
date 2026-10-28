"""Build Petra's published data files.

Run once a day. Alpha Vantage's free tier is end-of-day only, so there is
nothing to gain from running more often, and 25 requests/day is the hard
ceiling that shapes the schedule:

    Mon-Fri   TIME_SERIES_DAILY           x21 symbols  -> latest.json, daily.json
              EDGAR filings               x18 tickers  -> filings.json
    Sat-Sun   TIME_SERIES_WEEKLY_ADJUSTED x~11 symbols -> weekly.json
              EDGAR fundamentals          x~9 tickers  -> meta.json

Splitting the two Alpha Vantage call types across weekdays and weekends means
they never contend for the same budget, which removes any need for rotation
logic or staleness windows. EDGAR is keyless and outside that budget.

The previous run's output is the input: `data/` is read at the start and
rewritten at the end, so a symbol whose fetch fails keeps its last known
values and is marked stale rather than vanishing from the page.
"""

import datetime as dt
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

from app import alphavantage as av
from app import config, edgar

DATA_DIR = "data"
SCHEMA = 1


def load_env(path: str = ".env") -> None:
    """Read a local .env into the environment, if one exists.

    Deliberately hand-rolled rather than pulling in python-dotenv: this is
    twelve lines against a dependency, and `requests` staying the only
    runtime requirement is part of the point of this rewrite. Real
    environment variables always win, which is what makes CI unaffected --
    there is no .env in the runner.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def load(name: str) -> Dict[str, Any]:
    path = os.path.join(DATA_DIR, name)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(name: str, body: Dict[str, Any]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, name)
    with open(path, "w", encoding="utf-8") as fh:
        # Compact separators: every visitor downloads these files and
        # pretty-printing roughly doubles the payload for no benefit.
        json.dump(body, fh, separators=(",", ":"), sort_keys=True)
    print(f"  wrote {path} ({os.path.getsize(path):,} bytes)")


def to_pairs(series: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """Published {dates, closes} form -> {symbol: {date: close}} for merging."""
    dates = series.get("dates") or []
    out: Dict[str, Dict[str, float]] = {}
    for symbol, closes in (series.get("closes") or {}).items():
        out[symbol] = {
            date: close for date, close in zip(dates, closes) if close is not None
        }
    return out


def to_series(pairs: Dict[str, Dict[str, float]], keep: int) -> Dict[str, Any]:
    """{symbol: {date: close}} -> a shared date axis with aligned arrays.

    Symbols are aligned onto the union of dates and padded with null where a
    symbol has no point, so an index into any array always means the same
    date. Without that, one failed fetch would silently shift a whole series.
    """
    all_dates = sorted({date for points in pairs.values() for date in points})[-keep:]
    return {
        "schema": SCHEMA,
        "dates": all_dates,
        "closes": {
            symbol: [points.get(date) for date in all_dates]
            for symbol, points in sorted(pairs.items())
        },
    }


# --------------------------------------------------------------------------
# weekday: daily bars + filings
# --------------------------------------------------------------------------

def run_weekday(budget: av.Budget) -> None:
    session = av.new_session()
    prev_latest = load("latest.json")
    prev_symbols = prev_latest.get("symbols", {})
    daily_pairs = to_pairs(load("daily.json"))

    symbols: Dict[str, Any] = {}
    stale: List[str] = []
    newest_date = ""

    for symbol in config.ALL_SYMBOLS:
        try:
            result = av.fetch_daily(symbol, session, budget)
        except av.QuotaExhausted as exc:
            # Budget gone mid-run. Stop cleanly rather than hammering a wall;
            # what was fetched is still written, the rest stays stale.
            print(f"  ! quota exhausted before {symbol}: {exc}")
            break
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not sink the run
            print(f"  ! {symbol}: {type(exc).__name__}: {exc}")
            continue

        bars = result["bars"]
        date, last = bars[-1]
        prev_close = bars[-2][1]["c"] if len(bars) > 1 else last["c"]
        change = last["c"] - prev_close

        symbols[symbol] = {
            "c": round(last["c"], 4),
            "o": round(last["o"], 4),
            "h": round(last["h"], 4),
            "l": round(last["l"], 4),
            "v": last["v"],
            "pc": round(prev_close, 4),
            "d": round(change, 4),
            "dp": round(change / prev_close * 100.0, 4) if prev_close else 0.0,
            "s": "ok",
            "asof": date,
        }
        newest_date = max(newest_date, date)
        daily_pairs.setdefault(symbol, {}).update(
            {bar_date: bar["c"] for bar_date, bar in bars}
        )

    # Carry forward anything we could not refresh, so the page keeps the row.
    for symbol in config.ALL_SYMBOLS:
        if symbol in symbols:
            continue
        carried = dict(prev_symbols.get(symbol) or {})
        if not carried:
            continue
        carried["s"] = "stale"
        symbols[symbol] = carried
        stale.append(symbol)

    if not symbols:
        raise RuntimeError(
            "no symbols resolved and no prior data to fall back on; "
            "refusing to publish an empty dashboard"
        )

    save(
        "latest.json",
        {
            "schema": SCHEMA,
            "asof": newest_date or prev_latest.get("asof", ""),
            "generated": int(time.time()),
            "symbols": symbols,
            "stale": sorted(stale),
        },
    )
    save("daily.json", to_series(daily_pairs, config.DAILY_POINTS))
    # Market cap tracks today's close, so re-derive it now that prices moved.
    derive_valuations()
    run_filings()


def run_filings() -> None:
    """Refresh the filings rail. Keyless, so outside the Alpha Vantage budget."""
    try:
        sec = edgar.session()
    except RuntimeError as exc:
        print(f"  ! skipping filings: {exc}")
        return

    ciks = resolve_ciks_cached(sec)
    items: List[Dict[str, Any]] = []
    for symbol in config.TICKERS:
        cik = ciks.get(symbol)
        if not cik:
            continue
        try:
            items.extend(edgar.fetch_filings(symbol, cik, sec))
        except Exception as exc:  # noqa: BLE001
            print(f"  ! filings {symbol}: {type(exc).__name__}: {exc}")

    items.sort(key=lambda row: row["date"], reverse=True)
    save("filings.json", {"schema": SCHEMA, "updated": int(time.time()), "items": items[:60]})


# --------------------------------------------------------------------------
# weekend: weekly history + fundamentals
# --------------------------------------------------------------------------

def run_weekend(budget: av.Budget, half: int) -> None:
    """Refresh half the weekly series. `half` is 0 on Saturday, 1 on Sunday."""
    session = av.new_session()
    weekly_pairs = to_pairs(load("weekly.json"))

    midpoint = (len(config.ALL_SYMBOLS) + 1) // 2
    chunk = config.ALL_SYMBOLS[:midpoint] if half == 0 else config.ALL_SYMBOLS[midpoint:]

    for symbol in chunk:
        try:
            result = av.fetch_weekly(symbol, session, budget)
        except av.QuotaExhausted as exc:
            print(f"  ! quota exhausted before {symbol}: {exc}")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {symbol}: {type(exc).__name__}: {exc}")
            continue
        weekly_pairs[symbol] = dict(zip(result["dates"], result["closes"]))

    if weekly_pairs:
        save("weekly.json", to_series(weekly_pairs, config.WEEKLY_POINTS))

    run_fundamentals(half)


def run_fundamentals(half: int) -> None:
    """Shares and EPS for half the watchlist, then derive market cap and P/E."""
    try:
        sec = edgar.session()
    except RuntimeError as exc:
        print(f"  ! skipping fundamentals: {exc}")
        return

    ciks = resolve_ciks_cached(sec)
    symbols: Dict[str, Any] = load("meta.json").get("symbols", {})
    prices = {
        symbol: row.get("c")
        for symbol, row in (load("latest.json").get("symbols") or {}).items()
    }

    tickers = list(config.TICKERS)
    midpoint = (len(tickers) + 1) // 2
    chunk = tickers[:midpoint] if half == 0 else tickers[midpoint:]

    for symbol in chunk:
        row = dict(symbols.get(symbol) or {})
        row["name"] = config.display_name(symbol)
        row["sector"] = config.sector(symbol)

        cik = ciks.get(symbol)
        if cik:
            try:
                facts = edgar.fetch_fundamentals(symbol, cik, sec)
                row.update({k: v for k, v in facts.items() if v is not None})
            except Exception as exc:  # noqa: BLE001
                print(f"  ! fundamentals {symbol}: {type(exc).__name__}: {exc}")

        symbols[symbol] = row

    # Proxies get a label and nothing else; an ETF has no EPS to speak of.
    for symbol, label in config.PROXIES.items():
        symbols.setdefault(symbol, {"name": label, "sector": "Index proxy"})

    save(
        "meta.json",
        {
            "schema": SCHEMA,
            "updated": int(time.time()),
            "symbols": symbols,
            "proxies": list(config.PROXIES),
        },
    )
    derive_valuations()


def derive_valuations() -> None:
    """Recompute market cap and P/E from the newest close.

    This runs on *every* run, not just when the XBRL facts are refreshed,
    because market cap is shares x price and the price changes daily. Deriving
    it only at the weekend would print a week-old market cap beside a current
    price. Shares and EPS come from the stored facts, so this costs no
    requests at all.
    """
    meta = load("meta.json")
    symbols = meta.get("symbols")
    if not symbols:
        return
    prices = {
        symbol: row.get("c")
        for symbol, row in (load("latest.json").get("symbols") or {}).items()
    }
    if not prices:
        print("  note: no prices yet, leaving market cap and P/E unset")
        return

    for symbol, row in symbols.items():
        price = prices.get(symbol)
        shares, eps = row.get("shares"), row.get("eps")
        # Multi-class filers report an ambiguous share count, so they get no
        # market cap at all -- but EPS is per-share, so P/E still stands.
        usable = shares if symbol not in config.MULTI_CLASS else None
        row["mcap"] = round(price * usable / 1e9, 2) if price and usable else None
        row["pe"] = round(price / eps, 1) if price and eps and eps > 0 else None

    meta["symbols"] = symbols
    meta["proxies"] = list(config.PROXIES)
    save("meta.json", meta)


_cik_cache: Optional[Dict[str, str]] = None


def resolve_ciks_cached(sec) -> Dict[str, str]:
    """One ticker->CIK lookup per run, shared by filings and fundamentals."""
    global _cik_cache
    if _cik_cache is None:
        try:
            _cik_cache = edgar.resolve_ciks(config.ALL_SYMBOLS, sec)
            missing = sorted(set(config.TICKERS) - set(_cik_cache))
            if missing:
                print(f"  note: no CIK for {', '.join(missing)}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! CIK lookup failed: {type(exc).__name__}: {exc}")
            _cik_cache = {}
    return _cik_cache


# --------------------------------------------------------------------------

def main() -> int:
    load_env()
    # Weekday vs weekend is decided in US Eastern terms, because that is what
    # "a trading day" means; a UTC-evening run is still the same session.
    #
    # zoneinfo needs the IANA database, which Linux has and Windows does not
    # unless `tzdata` is installed. Rather than take a dependency for this,
    # fall back to a fixed -5. That is EST, so it is an hour off during EDT --
    # harmless at this schedule, since 23:00 UTC is 18:00 or 19:00 ET and the
    # calendar day is the same either way. It would matter if the cron moved
    # near a midnight boundary.
    now_utc = dt.datetime.now(dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        now_et = now_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001 - no tzdata available
        now_et = now_utc - dt.timedelta(hours=5)
    weekday = now_et.weekday()  # Mon=0 .. Sun=6
    forced = os.getenv("PETRA_FORCE_MODE", "").strip().lower()

    budget = av.Budget(config.AV_DAILY_BUDGET)
    mode = forced or ("weekend" if weekday >= 5 else "weekday")
    print(f"Petra: {mode} run ({now_et:%Y-%m-%d %H:%M} ET), budget {budget.limit}")

    if mode == "weekend":
        run_weekend(budget, 0 if weekday == 5 else 1)
    else:
        run_weekday(budget)

    print(f"Done. Used {budget.spent} of {budget.limit} Alpha Vantage requests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
