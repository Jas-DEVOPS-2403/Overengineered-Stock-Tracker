"""Tracked symbols and their display metadata.

Everything here is deliberately static. No free API gives GICS sectors
(Alpha Vantage's OVERVIEW endpoint is premium, Finnhub only returns a single
industry string), so the sector map is hardcoded. For a fixed watchlist that
is one edit a year, which is cheaper and more reliable than a request.
"""

import os

# Watchlist: ticker -> (display name, sector)
#
# The page's search/add picks from this set, because a visitor can only be
# shown data the scraper actually fetched. Widening this list is the one-line
# change that follows Alpha Vantage granting the open-source request quota.
TICKERS = {
    "AAPL":  ("Apple",                 "Technology"),
    "MSFT":  ("Microsoft",             "Technology"),
    "NVDA":  ("NVIDIA",                "Semiconductors"),
    "AMD":   ("AMD",                   "Semiconductors"),
    "AMZN":  ("Amazon",                "Consumer Discretionary"),
    "TSLA":  ("Tesla",                 "Consumer Discretionary"),
    "NKE":   ("Nike",                  "Consumer Discretionary"),
    "SBUX":  ("Starbucks",             "Consumer Discretionary"),
    "GOOGL": ("Alphabet",              "Communication Services"),
    "META":  ("Meta Platforms",        "Communication Services"),
    "NFLX":  ("Netflix",               "Communication Services"),
    "DIS":   ("Disney",                "Communication Services"),
    "BRK-B": ("Berkshire Hathaway B",  "Financials"),
    "JPM":   ("JPMorgan Chase",        "Financials"),
    "V":     ("Visa",                  "Financials"),
    "BLK":   ("BlackRock",             "Financials"),
    "COST":  ("Costco",                "Consumer Staples"),
    "KO":    ("Coca-Cola",             "Consumer Staples"),
}

# Index proxies.
#
# No free tier anywhere sells index levels -- Alpha Vantage's INDEX_DATA
# endpoints for SPX/DJI/IXIC are all premium -- so these ETFs stand in.
# The UI must show PERCENT CHANGE ONLY and never the price level, because
# SPY is roughly SPX/10 and DIA roughly DJI/100. They are also labelled as
# the ETFs rather than the indices: QQQ tracks the Nasdaq-100 (~100 names),
# not the Nasdaq Composite (~3,000), and their daily moves genuinely differ.
PROXIES = {
    "SPY": "S&P 500 (SPY)",
    "QQQ": "Nasdaq 100 (QQQ)",
    "DIA": "Dow 30 (DIA)",
}

# Symbols where Alpha Vantage's spelling differs from ours. Kept as a map
# rather than inlined because providers disagree about class-share notation
# (Finnhub wanted BRK.B where Alpha Vantage takes BRK-B).
SYMBOL_MAP: dict[str, str] = {}

# Every symbol the scraper fetches, watchlist plus proxies.
ALL_SYMBOLS = list(TICKERS) + list(PROXIES)

# Multi-class filers, where market cap cannot be derived correctly.
#
# EDGAR reports EntityCommonStockSharesOutstanding per share class without a
# usable class dimension, so a multi-class filer yields one class's count with
# no way to tell which. Measured against these 18 tickers: Berkshire returns
# ~941k (the Class A count, against a ~$500 Class B price -- three orders of
# magnitude out), Visa ~469M against an actual ~1.95B, Nike ~855M against
# ~1.48B, and Alphabet and Meta return nothing usable at all.
#
# EPS is unaffected, being per-share already, so P/E is still derived for any
# of these that report it. Only market cap renders as "--". There is no free
# fix, and a confident wrong number is worse than a dash.
#
# Every other ticker checked out, including NFLX at 4.1B shares -- which looks
# wrong until you remember Netflix's 10-for-1 split in November 2025.
MULTI_CLASS = {"GOOGL", "META", "NKE", "BRK-B", "V"}

# Alpha Vantage's free tier is 25 requests/day, hard. The orchestrator splits
# work across the calendar so the two call types never contend: daily bars on
# weekdays (21 calls), weekly history at the weekend (21 split over two days).
# The guard exists so a bug cannot silently burn the day's budget.
AV_DAILY_BUDGET = 25

# Trading days of daily history to publish. TIME_SERIES_DAILY&outputsize=compact
# returns 100 bars per call, which covers the 5D and 1M views outright.
DAILY_POINTS = 100

# Weeks of weekly history to publish. TIME_SERIES_WEEKLY_ADJUSTED is free and
# carries 25+ years; 1Y and 5Y are slices of this one array.
WEEKLY_POINTS = 260

# SEC EDGAR requires a declared User-Agent naming the project and a contact
# address; undeclared clients get blocked. The address comes from the
# environment rather than this file, because this repo is public and a
# committed address is a spam magnet.
SEC_CONTACT = os.getenv("SEC_CONTACT_EMAIL", "")


def sec_user_agent() -> str:
    if not SEC_CONTACT:
        raise RuntimeError(
            "SEC_CONTACT_EMAIL is not set. SEC EDGAR requires a contact "
            "address in the User-Agent header; see .env.example."
        )
    return f"Petra market dashboard ({SEC_CONTACT})"


def provider_symbol(ticker: str) -> str:
    """Our ticker spelling -> Alpha Vantage's."""
    return SYMBOL_MAP.get(ticker, ticker)


def display_name(symbol: str) -> str:
    if symbol in TICKERS:
        return TICKERS[symbol][0]
    return PROXIES.get(symbol, symbol)


def sector(symbol: str) -> str:
    if symbol in TICKERS:
        return TICKERS[symbol][1]
    return "Index proxy"
