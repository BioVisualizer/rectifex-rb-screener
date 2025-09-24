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

The application uses eight different scenarios to find potential candidates, each suited to a different trading style and concept.

### Classic Oversold
-   **Concept:** A contrarian, mean-reversion strategy. It operates on the idea that a stock's price, after a sharp decline, will likely bounce back (revert) to its long-term average. The scan identifies stocks that are technically "oversold" (indicated by a low Relative Strength Index - RSI) and are approaching a significant historical support level (like the 200-day moving average).
-   **Suitability:** Best for short- to medium-term traders who are comfortable with contrarian plays and believe the market has overreacted to negative news. It aims to identify potential bottoming-out points.
-   **Limitations:** This strategy can be risky and is sometimes referred to as "catching a falling knife." A stock can remain oversold for an extended period, and support levels can break, leading to further declines. It is most effective when confirmed by other indicators or a bullish market context.

### Quality Stock Pullback
-   **Concept:** A trend-following strategy, often summarized as "buying the dip." It looks for fundamentally strong companies that are in a confirmed long-term uptrend and have experienced a temporary, minor price drop, bringing them closer to a short-term support level like the 50-day moving average.
-   **Suitability:** Ideal for traders who prefer to follow the trend rather than bet against it. It offers a chance to enter a strong, upward-moving stock at a more reasonable price point (GARP - Growth at a Reasonable Price).
-   **Limitations:** A minor pullback can sometimes be the beginning of a major trend reversal. The 50-day moving average is not a guaranteed support level. The fundamental metrics are based on past performance and do not guarantee future results.

### Fundamental Divergence
-   **Concept:** A value-oriented, contrarian strategy that seeks to find a mismatch between a company's strong financial health and its recent lackluster stock performance. The scan looks for companies with solid fundamentals (e.g., good growth, low debt) whose stock price has been stagnating or underperforming the market.
-   **Suitability:** Best for patient, long-term investors who conduct their own fundamental analysis. It can help uncover potentially undervalued "hidden gems" before they are discovered by the broader market.
-   **Limitations:** The market can ignore an "undervalued" stock for a long time, leading to a "value trap." There may be valid reasons for the poor stock performance that are not captured by the screener's fundamental metrics.

### Momentum Breakout
-   **Concept:** A classic momentum strategy based on the principle that "winners keep winning." It identifies stocks that are breaking out to new 52-week highs, especially when accompanied by a surge in trading volume. This suggests strong buying interest and the potential for continued upward movement.
-   **Suitability:** For active traders who want to ride strong, established trends. It focuses on stocks that are already demonstrating significant positive momentum.
-   **Limitations:** This strategy carries the risk of buying at the peak (a "false breakout"). A stock can quickly reverse after hitting a new high. It requires disciplined risk management, such as using tight stop-losses.

### Golden Cross
-   **Concept:** A long-term trend-following signal. A Golden Cross occurs when a shorter-term moving average (typically the 50-day) crosses above a longer-term moving average (typically the 200-day). It is widely regarded as a signal of a potential major, long-term uptrend.
-   **Suitability:** For long-term investors and position traders who are looking to identify major shifts in a stock's primary trend. It can be used to confirm the start of a new bull phase for a stock.
-   **Limitations:** This is a lagging indicator, meaning a significant portion of the price move may have already occurred by the time the signal appears. It can also generate false signals in choppy, sideways markets where the moving averages cross back and forth frequently.

### Mean Reversion (Bollinger Bands)
-   **Concept:** A short-term, mean-reversion strategy that uses Bollinger Bands to identify statistically oversold conditions. When a stock's price touches or closes below its lower Bollinger Band, it is considered to be far from its recent average price and may be due for a bounce.
-   **Suitability:** For short-term "swing" traders looking for quick rebound opportunities. It provides clear, statistically-defined entry points for bounce plays.
-   **Limitations:** In a strong, sustained downtrend, a stock can "walk the band" by continuously trading at or near the lower band without reverting to the mean. This signal is purely technical and ignores all fundamental factors.

### Volatility Squeeze
-   **Concept:** A pre-breakout or volatility-based strategy. It identifies stocks where price volatility has contracted to an unusually low level (i.e., the Bollinger Bands have narrowed significantly). This "squeeze" often precedes a period of high volatility—a significant price move or breakout.
-   **Suitability:** For traders who want to position themselves *before* a major price move occurs. It allows for setting up trades with well-defined risk (e.g., placing stops outside the narrow consolidation range).
-   **Limitations:** The scan does not predict the *direction* of the breakout, which could be up or down. A stock can also remain in a low-volatility state for a longer-than-expected period. It requires a plan for how to trade the eventual breakout in either direction.

### High-Quality Dividend
-   **Concept:** A value and income-investing strategy. It focuses not just on a high dividend yield, but on the *sustainability* of that dividend. It filters for companies with healthy financials (e.g., a reasonable payout ratio, low debt) to avoid "yield traps"—stocks with high but risky dividends that are likely to be cut.
-   **Suitability:** For long-term, income-oriented investors who prioritize receiving a steady stream of cash flow from their investments over short-term capital appreciation.
-   **Limitations:** A history of stable dividends does not guarantee future payments, as they can be cut at any time. The strategy is less focused on growth and may underperform in strong bull markets where growth stocks are favored.

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

## Explanation of Scan Results (Scores and Metrics)

The results table contains several columns with technical terms and scores. Here is a detailed explanation of what they mean and how they are calculated.

### Core Information
*   **Ticker**: The unique symbol for a stock on its exchange (e.g., `AAPL` for Apple).
*   **Name**: The company's name.
*   **Price**: The most recent closing price.
*   **Change %**: The percentage price change over the last trading day. *(Note: This is currently a placeholder value).*
*   **Sparkline**: A mini-chart showing the price trend over the last 30 days.

### The Scoring System

The application uses a multi-layered scoring system to rank candidates. The final **Rebound Score** is a composite of three sub-scores: Technical, Fundamental, and Market Context.

*   **Rebound Score (0-100)**: This is the final, weighted average score that indicates the overall quality of a setup. A higher score is better. It is calculated with the following default weights:
    *   `Technical Score: 55%`
    *   `Fundamental Score: 30%`
    *   `Market Context Score: 15%`

*   **Tech. Score (0-100)**: This score evaluates the strength of the technical chart pattern. **Its calculation is entirely dependent on the selected scan scenario.** For example:
    *   In a **Classic Oversold** scan, it's a blend of how "oversold" the RSI is and how close the price is to a major support level (200-day average or 90-day low).
    *   In a **Momentum Breakout** scan, it's based on the volume surge and the strength of the price breakout above the 52-week high.
    *   Each scenario has its own unique logic for this score.

*   **Fund. Score (0-100)**: This score measures a company's financial health. A company's key metrics are compared to the median values of other companies in the same sector. A score of 50 means the company is average for its sector; a score above 50 is above average. The score is calculated from the metrics listed below.

*   **Market Score (0-100)**: This score assesses the health of the broader market (e.g., the S&P 500 for US stocks). It is a simple gauge:
    *   **100 (Bullish)**: The index is trading above its 50-day moving average.
    *   **20 (Bearish)**: The index is trading below its 50-day moving average.
    *   **50 (Neutral)**: The market data could not be retrieved.

### Key Fundamental Metrics

These are some of the key financial ratios used to calculate the **Fund. Score**. The screener automatically judges whether a higher or lower value is better (e.g., high growth is good, high debt is bad).

*   **ROE (Return on Equity)**: A measure of profitability. It shows how effectively a company uses shareholder investments to generate profit. A higher ROE is generally better.
*   **P/E Ratio (PE_TTM)**: The Price-to-Earnings ratio compares the company's stock price to its earnings per share over the trailing twelve months (TTM). A lower P/E can indicate a stock is "cheaper" or undervalued.
*   **Debt/Equity Ratio**: Measures a company's financial leverage by dividing its total debt by its shareholder equity. A high ratio indicates high debt, which can be a risk.
*   **Revenue Growth (3Y CAGR)**: The Compound Annual Growth Rate of a company's revenue over the last three years. It shows the company's ability to increase its sales over time.
*   **EPS Growth (1Y)**: The growth in a company's Earnings Per Share over the last year. Strong EPS growth is a positive sign of increasing profitability.
*   **Free Cash Flow Yield**: This metric compares the free cash flow a company generates to its market value. A higher yield can suggest a company is undervalued and has strong cash generation.
*   **Payout Ratio**: Used in the "High-Quality Dividend" scan, this shows the percentage of a company's earnings that it pays out to shareholders as dividends. A very high ratio (e.g., >80%) can be a warning sign that the dividend is unsustainable.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
