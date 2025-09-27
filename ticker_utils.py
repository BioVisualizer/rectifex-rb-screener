"""Utility helpers for working with ticker symbols."""

from __future__ import annotations

import re

# Pre-compile a regex for share-class tickers that Yahoo expects with hyphen.
# Require the base symbol to start with a letter so exchange suffixes like ``4188.T``
# are not mis-identified as share classes.
_SHARE_CLASS_PATTERN = re.compile(r"^(?P<base>[A-Z]+[A-Z0-9]*)\.(?P<class>[A-Z])$")

# Yahoo only expects a hyphen for a very small set of US share-class suffixes
# (mainly ``.A``/``.B``/``.C``). Suffixes such as ``.L`` or ``.F`` indicate the
# exchange rather than a share class, so we explicitly guard against rewriting
# those tickers to avoid breaking lookups for markets like London or Frankfurt.
_SHARE_CLASS_SUFFIXES = frozenset({"A", "B", "C"})


def normalize_ticker_for_yfinance(ticker: str) -> str:
    """Return a Yahoo Finance compatible ticker symbol.

    Yahoo uses hyphen separators for share-class tickers such as ``BRK.B`` or
    ``BF.B`` while exchange suffixes (e.g. ``.DE``, ``.T``) retain their dot.
    The Flatpak build was emitting repeated fetch errors for tickers that
    include a dot because they were forwarded to :mod:`yfinance` unchanged.
    This helper normalises the known share-class pattern so that the rest of
    the codebase can continue using the canonical dot notation everywhere else.

    Parameters
    ----------
    ticker:
        The ticker symbol provided by the user or configuration. The helper is
        intentionally conservative and only rewrites the subset of symbols that
        Yahoo requires with a hyphen. All other symbols are returned verbatim
        so that region/exchange suffixes remain intact.
    """
    if not ticker:
        return ticker

    cleaned = ticker.strip()
    if not cleaned:
        return cleaned

    upper_cleaned = cleaned.upper()
    match = _SHARE_CLASS_PATTERN.match(upper_cleaned)
    if match and match.group('class') in _SHARE_CLASS_SUFFIXES:
        return f"{match.group('base')}-{match.group('class')}"

    return cleaned

