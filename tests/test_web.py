"""Checks on the front end, which has no build step and so no compiler.

web/index.html is about a thousand lines of hand-written JavaScript loaded
straight into the browser. Nothing else in the pipeline reads it, so a stray
bracket would ship and only show up as a blank page. These tests are the
compile step it does not otherwise have.
"""

import os
import re
import shutil
import subprocess

import pytest

from app import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "web", "index.html")


def _page() -> str:
    with open(PAGE, encoding="utf-8") as f:
        return f.read()


def _inline_scripts(html: str) -> list[str]:
    """Bodies of <script> tags without a src attribute."""
    return [m.group(1) for m in re.finditer(r"<script>(.*?)</script>", html, re.S)]


def _tv_symbols(html: str) -> dict[str, str]:
    """The TV_SYMBOLS object literal, read without running the page."""
    m = re.search(r"const TV_SYMBOLS = \{(.*?)\};", html, re.S)
    assert m, "TV_SYMBOLS not found in web/index.html"
    pairs = re.findall(r'"?([A-Z][A-Z.-]*)"?\s*:\s*"([A-Z]+:[A-Z.-]+)"', m.group(1))
    return dict(pairs)


def test_page_has_its_inline_scripts():
    # The theme bootstrap and the app. If this count changes, the syntax test
    # below may be silently checking less than it used to.
    assert len(_inline_scripts(_page())) == 2


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_inline_scripts_parse(tmp_path):
    for i, body in enumerate(_inline_scripts(_page())):
        path = tmp_path / f"inline_{i}.js"
        path.write_text(body, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert r.returncode == 0, f"inline script #{i} does not parse:\n{r.stderr}"


def test_every_tracked_symbol_has_an_intraday_mapping():
    # The strip shows only symbols it has an exchange-qualified name for, and
    # drops the rest without a word. Adding a ticker to config.py without
    # adding it here would quietly leave it off the strip.
    mapped = _tv_symbols(_page())
    missing = sorted((set(config.TICKERS) | set(config.PROXIES)) - set(mapped))
    assert not missing, f"add these to TV_SYMBOLS in web/index.html: {missing}"


def test_intraday_mapping_has_no_strays():
    # A mapping for a symbol Petra does not track is dead weight at best, and
    # at worst a typo of a real one.
    tracked = set(config.TICKERS) | set(config.PROXIES)
    strays = sorted(set(_tv_symbols(_page())) - tracked)
    assert not strays, f"TV_SYMBOLS maps untracked symbols: {strays}"


def test_class_shares_use_tradingviews_dot_notation():
    # Alpha Vantage writes BRK-B; TradingView writes BRK.B. Passing the dash
    # form through quotes nothing, or something else.
    for ticker, pro in _tv_symbols(_page()).items():
        if "-" in ticker:
            assert pro.split(":", 1)[1] == ticker.replace("-", "."), (ticker, pro)


def test_strip_keeps_tradingviews_attribution():
    # TradingView's terms require the attribution to stay as designed.
    html = _page()
    assert "tradingview-widget-copyright" in html
    assert "Track all markets on TradingView" in html


def test_widget_config_uses_only_documented_keys():
    # The widget ignores keys it does not know, so a typo such as
    # "colortheme" fails silently as a dark strip on a light page.
    html = _page()
    m = re.search(r"script\.textContent = JSON\.stringify\(\{(.*?)\}\);", html, re.S)
    assert m, "widget config not found"
    keys = set(re.findall(r"^\s*(\w+):", m.group(1), re.M))
    assert keys == {"symbols", "showSymbolLogo", "isTransparent", "displayMode",
                    "colorTheme", "locale"}, keys
