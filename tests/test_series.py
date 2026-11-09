"""Series alignment, resampling, valuation guards and .env loading."""

import json
import os

import fetch_snapshot as fs
from app import config

# ---------------------------------------------------------------------------
# Shared-axis alignment
#
# Series are published as one date axis plus per-symbol arrays. If a symbol
# is padded wrongly, index i stops meaning the same date for every symbol and
# one failed fetch silently shifts a whole line on the chart.
# ---------------------------------------------------------------------------

def test_missing_points_are_padded_with_null_not_closed_up():
    pairs = {
        "A": {"2026-01-01": 1.0, "2026-01-02": 2.0, "2026-01-03": 3.0},
        "B": {"2026-01-01": 10.0, "2026-01-03": 30.0},  # no 01-02
    }
    out = fs.to_series(pairs, keep=10)
    assert out["dates"] == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert out["closes"]["B"] == [10.0, None, 30.0]
    assert len(out["closes"]["A"]) == len(out["closes"]["B"]) == len(out["dates"])


def test_series_round_trips_through_the_published_form():
    pairs = {"A": {"2026-01-01": 1.0, "2026-01-02": 2.0}}
    assert fs.to_pairs(fs.to_series(pairs, keep=10)) == pairs


def test_keep_trims_the_oldest_points():
    pairs = {"A": {f"2026-01-{d:02d}": float(d) for d in range(1, 11)}}
    out = fs.to_series(pairs, keep=3)
    assert out["dates"] == ["2026-01-08", "2026-01-09", "2026-01-10"]


def test_padding_nulls_do_not_survive_a_round_trip_as_values():
    out = fs.to_series({"A": {"2026-01-01": 1.0}, "B": {"2026-01-02": 2.0}}, keep=10)
    assert fs.to_pairs(out) == {"A": {"2026-01-01": 1.0}, "B": {"2026-01-02": 2.0}}


# ---------------------------------------------------------------------------
# Resampling onto the weekly grid
# ---------------------------------------------------------------------------

def test_resample_takes_the_last_observation_at_or_before_each_date():
    points = [("2026-01-01", 1.0), ("2026-01-05", 5.0), ("2026-01-09", 9.0)]
    grid = ["2026-01-02", "2026-01-06", "2026-01-10"]
    assert fs.resample(points, grid) == [1.0, 5.0, 9.0]


def test_resample_is_none_before_the_series_begins():
    # Bitcoin has no 1999 price; the line must start late rather than being
    # back-filled with its first known value.
    points = [("2026-01-05", 5.0)]
    assert fs.resample(points, ["2026-01-01", "2026-01-06"]) == [None, 5.0]


def test_resample_holds_the_last_value_across_a_gap():
    points = [("2026-01-01", 1.0)]
    assert fs.resample(points, ["2026-01-01", "2026-02-01"]) == [1.0, 1.0]


def test_resample_never_invents_a_point_between_observations():
    # Downsampling only ever selects a real observation; no interpolation.
    points = [("2026-01-01", 10.0), ("2026-01-31", 20.0)]
    assert fs.resample(points, ["2026-01-15"]) == [10.0]


# ---------------------------------------------------------------------------
# Derived valuations
# ---------------------------------------------------------------------------

def _write(tmp_path, name, body):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def test_market_cap_is_shares_times_price_in_billions(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "DATA_DIR", str(tmp_path))
    _write(tmp_path, "meta.json", {"symbols": {"AAPL": {"shares": 15e9, "eps": 7.0}}})
    _write(tmp_path, "latest.json", {"symbols": {"AAPL": {"c": 200.0}}})
    fs.derive_valuations()
    out = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert out["symbols"]["AAPL"]["mcap"] == 3000.0
    assert out["symbols"]["AAPL"]["pe"] == 28.6


def test_multi_class_filers_get_no_market_cap(tmp_path, monkeypatch):
    # EDGAR reports shares per class with no usable class dimension, so
    # Berkshire returns ~941k Class A shares against a Class B price. A
    # market cap three orders of magnitude wrong is worse than a dash.
    assert "BRK-B" in config.MULTI_CLASS
    monkeypatch.setattr(fs, "DATA_DIR", str(tmp_path))
    _write(tmp_path, "meta.json", {"symbols": {"BRK-B": {"shares": 941481, "eps": None}}})
    _write(tmp_path, "latest.json", {"symbols": {"BRK-B": {"c": 500.0}}})
    fs.derive_valuations()
    out = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert out["symbols"]["BRK-B"]["mcap"] is None


def test_multi_class_filers_keep_their_pe(tmp_path, monkeypatch):
    # EPS is per-share already, so the ratio is still right even when the
    # share count is not.
    monkeypatch.setattr(fs, "DATA_DIR", str(tmp_path))
    _write(tmp_path, "meta.json", {"symbols": {"GOOGL": {"shares": 0, "eps": 10.0}}})
    _write(tmp_path, "latest.json", {"symbols": {"GOOGL": {"c": 150.0}}})
    fs.derive_valuations()
    out = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert out["symbols"]["GOOGL"]["mcap"] is None
    assert out["symbols"]["GOOGL"]["pe"] == 15.0


def test_negative_eps_yields_no_pe(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "DATA_DIR", str(tmp_path))
    _write(tmp_path, "meta.json", {"symbols": {"X": {"shares": 1e9, "eps": -2.0}}})
    _write(tmp_path, "latest.json", {"symbols": {"X": {"c": 10.0}}})
    fs.derive_valuations()
    out = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert out["symbols"]["X"]["pe"] is None


def test_valuations_are_left_alone_when_no_prices_exist(tmp_path, monkeypatch):
    # The weekend path runs before any price has been fetched on a fresh
    # deploy; it must not wipe stored values with None.
    monkeypatch.setattr(fs, "DATA_DIR", str(tmp_path))
    _write(tmp_path, "meta.json", {"symbols": {"AAPL": {"shares": 1e9, "mcap": 123.0}}})
    fs.derive_valuations()
    out = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert out["symbols"]["AAPL"]["mcap"] == 123.0


# ---------------------------------------------------------------------------
# Configuration invariants
# ---------------------------------------------------------------------------

def test_the_daily_budget_actually_fits():
    # 25 requests/day is a hard cap. Equities are fetched every weekday;
    # the comparison series rotate on a 72h cycle.
    per_day = len(config.ALL_SYMBOLS) + len(config.CROSS_ASSET) / 3
    assert per_day <= config.AV_DAILY_BUDGET - 2, (
        f"{per_day:.1f} average requests/day leaves no retry headroom"
    )


def test_every_tracked_symbol_has_a_name_and_sector():
    for symbol in config.ALL_SYMBOLS:
        assert config.display_name(symbol) != symbol or symbol in config.PROXIES
        assert config.sector(symbol)


def test_proxies_are_not_also_watchlist_tickers():
    assert not set(config.PROXIES) & set(config.TICKERS)


def test_fred_backed_series_are_declared_so_the_notice_renders():
    for key in config.FRED_BACKED:
        assert key in config.CROSS_ASSET


# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------

def test_env_file_populates_missing_variables(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("FOO_TEST=bar\n# a comment\n\n", encoding="utf-8")
    monkeypatch.delenv("FOO_TEST", raising=False)
    fs.load_env(str(path))
    assert os.environ["FOO_TEST"] == "bar"


def test_real_environment_wins_over_the_file(tmp_path, monkeypatch):
    # CI has no .env; secrets arrive as real variables and must not be
    # overridden by a stray file.
    path = tmp_path / ".env"
    path.write_text("FOO_TEST=from-file\n", encoding="utf-8")
    monkeypatch.setenv("FOO_TEST", "from-env")
    fs.load_env(str(path))
    assert os.environ["FOO_TEST"] == "from-env"


def test_quotes_are_stripped(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text('FOO_TEST="quoted"\n', encoding="utf-8")
    monkeypatch.delenv("FOO_TEST", raising=False)
    fs.load_env(str(path))
    assert os.environ["FOO_TEST"] == "quoted"


def test_a_missing_env_file_is_not_an_error():
    fs.load_env("definitely-not-here.env")


# ---------------------------------------------------------------------------
# Empty-run guards
#
# Found in production: with no ALPHAVANTAGE_API_KEY in CI, every fetch on the
# weekend path failed, each one was swallowed by the per-symbol except, and
# the job exited 0 having published an empty file. A green build on a total
# failure -- the exact silent death this project exists to avoid.
# ---------------------------------------------------------------------------

def test_weekend_refuses_to_report_success_having_fetched_nothing(tmp_path, monkeypatch):
    import pytest

    from app import alphavantage as av

    monkeypatch.setattr(fs, "DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="empty run"):
        fs.run_weekend(av.Budget(25), half=0)


def test_weekend_tolerates_a_bad_fetch_when_history_is_already_stored(tmp_path, monkeypatch):
    # A transient failure must not trip the guard -- only a run that leaves
    # nothing behind at all.
    from app import alphavantage as av

    monkeypatch.setattr(fs, "DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    _write(tmp_path, "weekly.json", {"dates": ["2026-01-02"], "closes": {"SPY": [100.0]}})
    monkeypatch.setattr(fs, "run_cross_asset", lambda *a, **k: None)
    monkeypatch.setattr(fs, "run_fundamentals", lambda *a, **k: None)
    fs.run_weekend(av.Budget(25), half=0)
    assert (tmp_path / "weekly.json").exists()


def test_main_fails_when_no_prices_survive(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(fs, "DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PETRA_FORCE_MODE", "weekday")
    monkeypatch.setattr(fs, "run_weekday", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="no usable latest.json"):
        fs.main()
