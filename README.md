# Rectifex RB - Global Rebound Stock Screener

This project is a Python desktop application designed to screen global stocks to identify potential long-rebound candidates. It serves as a filter to generate a manageable list of stocks for deeper manual analysis.

The application is built with Python and PyQt6, and it is designed to be packaged as a Flatpak for Kubuntu and other Linux distributions.

## Features

*   **Global Stock Scanning**: Scans major indices like the S&P 500, Nasdaq 100, DAX, STOXX Europe 600, and Nikkei 225.
*   **Data Caching**: Caches downloaded stock data to speed up subsequent scans and reduce API calls.
*   **Advanced Filtering**: Applies a multi-stage filtering process:
    1.  **Market Context Filter**: Ensures the broader market is in an uptrend before searching for long candidates.
    2.  **Liquidity Filter**: Filters for stocks with sufficient market capitalization and trading volume.
    3.  **Core Signal Filter**: Identifies stocks that are oversold (based on RSI) and near key technical support levels (200-day SMA, 90-day low).
*   **Scoring System**: Ranks qualified candidates with a "Rebound Score" from 0-100.
*   **Interactive GUI**: A responsive user interface built with PyQt6 that runs the analysis in a background thread. Features include:
    *   Sortable results table.
    *   Pop-up chart window for visual analysis of any candidate.
    *   Export results to CSV or Excel (XLSX).

## How to Extend the Stock Universe

The application's stock universe is determined by the ticker lists it loads on startup. While it attempts to scrape the latest lists from Wikipedia for US and German indices, the most reliable way to manage and expand the lists for European and Japanese markets is by editing the local CSV files.

The fallback data files are located in the `data/` directory:

*   `data/sp500_tickers.csv`
*   `data/nasdaq100_tickers.csv`
*   `data/dax_tickers.csv`
*   `data/stoxx600_tickers.csv`
*   `data/nikkei225_tickers.csv`

To expand the number of scanned tickers, particularly for the STOXX 600 and Nikkei 225, simply open the corresponding CSV file and add new ticker symbols, one per line.

**Important:** The tickers must be in a format that `yfinance` can understand.
*   For most exchanges, this is the standard ticker symbol.
*   For German stocks (XETRA), add the suffix `.DE` (e.g., `SAP.DE`).
*   For Japanese stocks (Tokyo Stock Exchange), add the suffix `.T` (e.g., `6758.T`).
*   For other European exchanges, the suffix varies (e.g., `.PA` for Paris, `.L` for London, `.MI` for Milan).

By manually curating these lists, you have full control over the stock universe the application analyzes.
