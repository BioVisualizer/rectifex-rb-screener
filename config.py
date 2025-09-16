# config.py
# Stores constant values such as index lists, filter-thresholds etc.

import os
from pathlib import Path

# -- Data Source Configuration --

# Defines the stock indices to be scanned.
# 'name': User-friendly name for the index.
# 'market': The market region (e.g., 'US', 'DE', 'EU', 'JP').
# 'wiki_url': Wikipedia URL to scrape the list of constituents.
# 'index_ticker': The ticker symbol for the main index (for market context filter).
# 'fallback_csv': Path to a local CSV file with tickers if scraping fails.
INDICES = {
    "S&P 500": {
        "name": "S&P 500",
        "market": "US",
        "wiki_url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "index_ticker": "SPY",
        "fallback_csv": "data/sp500_tickers.csv",
    },
    "Nasdaq 100": {
        "name": "Nasdaq 100",
        "market": "US",
        "wiki_url": "https://en.wikipedia.org/wiki/Nasdaq-100",
        "index_ticker": "QQQ", # Commonly tracks Nasdaq 100
        "fallback_csv": "data/nasdaq100_tickers.csv",
    },
    "DAX": {
        "name": "DAX",
        "market": "DE",
        "wiki_url": "https://en.wikipedia.org/wiki/DAX",
        "index_ticker": "^GDAXI",
        "fallback_csv": "data/dax_tickers.csv",
    },
    "STOXX Europe 600": {
        "name": "STOXX Europe 600",
        "market": "EU",
        "wiki_url": "https://en.wikipedia.org/wiki/STOXX_Europe_600",
        "index_ticker": "EXSA.DE", # iShares STOXX Europe 600 UCITS ETF
        "fallback_csv": "data/stoxx600_tickers.csv",
    },
    "Nikkei 225": {
        "name": "Nikkei 225",
        "market": "JP",
        "wiki_url": "https://en.wikipedia.org/wiki/Nikkei_225",
        "index_ticker": "^N225",
        "fallback_csv": "data/nikkei225_tickers.csv",
    },
}

# -- Caching and User Data Configuration --
APP_DATA_DIR = Path(os.path.expanduser("~")) / ".config" / "GlobalReboundScreener"
USER_TICKER_DIR = APP_DATA_DIR / "tickers"
CACHE_DIR = Path(os.path.expanduser("~")) / ".cache" / "GlobalReboundScreener"
CACHE_EXPIRY_HOURS = 22 # Cache is valid for 22 hours

# -- Data Loading Configuration --
DATA_PERIOD = "18mo" # Download 18 months of historical data

# -- Analysis & Filtering Criteria --

# 1. Market Context Filter
MARKET_CONTEXT_SMA = 50 # Days for the Simple Moving Average

# 2. Base Liquidity Filter
MIN_MARKET_CAP = 2_000_000_000 # 2 Billion USD
MIN_AVG_VOLUME_30D = 500_000 # 500,000 shares

# 3. Core Signal Filter
RSI_PERIOD = 14
RSI_OVERSOLD_STRONG = 30
RSI_OVERSOLD_WEAK = 40
SMA_SUPPORT_PERIOD = 200 # 200-day SMA for dynamic support
LOWEST_LOW_PERIOD = 90 # 90-day low for static support
SUPPORT_PROXIMITY_THRESHOLD = 3.0 # Max 3% above support zone

# -- Scoring System --
RSI_SCORE_CEILING = 50 # RSI value that corresponds to a score of 0
PROXIMITY_SCORE_CEILING = 3.0 # Distance in percent that corresponds to a score of 0

# -- GUI Configuration --
APP_NAME = "Rectifex RB - Global Rebound Stock Screener"
# The number of months of historical data to display on the chart.
CHART_HISTORY_MONTHS = 12
CSV_EXPORT_FILENAME = "rebound_candidates.csv"
XLSX_EXPORT_FILENAME = "rebound_candidates.xlsx"
