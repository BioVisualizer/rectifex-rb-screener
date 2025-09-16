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

## Scanning Scenarios

The application uses seven different scenarios to find potential candidates, covering a range of technical and fundamental analysis strategies.

### Classic Oversold
This scenario looks for technically oversold stocks that are near strong, long-term support levels.
- **Signal:** The stock's 14-day RSI is below an oversold threshold, and the current price is close to its 200-day moving average or 90-day low.
- **Style:** Contrarian / Mean Reversion.

### Quality Stock Pullback
This scenario looks for fundamentally strong companies in a healthy, long-term uptrend that have experienced a recent, minor price dip.
- **Signal:** The stock is in a confirmed uptrend (50-day SMA > 200-day SMA) and has pulled back to its 50-day SMA, while also meeting fundamental criteria for growth and financial health.
- **Style:** Trend Following / Growth at a Reasonable Price (GARP).

### Momentum Breakout
This scenario identifies stocks hitting new 52-week highs on high trading volume.
- **Signal:** The stock's price closes at or above its 52-week high, with volume significantly greater than its 30-day average.
- **Style:** Momentum.

### Golden Cross
This scenario detects when a stock's 50-day moving average has recently crossed above its 200-day moving average.
- **Signal:** A "Golden Cross" has occurred within the last 5 trading days, signaling a potential new long-term uptrend.
- **Style:** Long-Term Trend Following.

### Mean Reversion (Bollinger Bands)
This scenario finds stocks trading at or below their lower Bollinger Band.
- **Signal:** The stock's price touches or closes below the lower Bollinger Band, suggesting a statistically oversold condition that may revert to the mean.
- **Style:** Short-Term Contrarian / Mean Reversion.

### Volatility Squeeze
This scenario flags stocks where price volatility has become unusually low.
- **Signal:** The Bollinger Bandwidth (a measure of the distance between the upper and lower bands) is at or near its lowest point in the last six months. This often precedes a significant price move.
- **Style:** Pre-Breakout / Volatility.

### High-Quality Dividend
This is a value-focused scan that looks for stocks with an attractive and sustainable dividend.
- **Signal:** The stock has a dividend yield above a minimum threshold, a healthy payout ratio (not too high), and a low debt-to-equity ratio.
- **Style:** Value / Income Investing.

## Installation and Usage (from Source)

These instructions explain how to build and install the application on a Debian-based system like Kubuntu using the provided Flatpak manifest.

### 1. Install Prerequisites
First, you need to install `flatpak` and `flatpak-builder`.

```bash
sudo apt update
sudo apt install flatpak flatpak-builder
```

### 2. Add Flathub Remote
Flathub is the main repository for Flatpak applications and runtimes. You need to add it to download the KDE SDK, which is required for the build.

```bash
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
```

### 3. Clone the Repository
Download the source code from GitHub using the following command.

```bash
git clone https://github.com/BioVisualizer/rectifex-rb-screener.git
cd rectifex-rb-screener
```

### 4. Build and Install the Application
From the project's root directory (where `flathub.json` is located), run the following command:

```bash
flatpak-builder build-dir flathub.json --user --install --force-clean
```
*   `build-dir`: A temporary directory where the build will take place.
*   `--user`: Installs the application for the current user.
*   `--install`: Installs the application after a successful build.
*   `--force-clean`: Deletes the build directory after completion to save space.

### 5. Run the Application
After the installation is complete, you can find "Rectifex RB" in your application menu (e.g., the Kicker in Kubuntu).

Alternatively, you can run it from the command line:
```bash
flatpak run com.rectifex.GlobalReboundScreener
```

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

## Disclaimer

This application is intended for informational and educational purposes only. The data and analysis provided should not be considered as financial advice. Investing in stocks involves risk, including the possible loss of principal.

The screening results are based on technical indicators and historical data, which are not guarantees of future performance. You should always conduct your own thorough research and consult with a qualified financial advisor before making any investment decisions. The author and contributors are not responsible for any investment losses you may incur.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
