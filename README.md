# Rectifex RB - Global Rebound Stock Screener v2.0

This project is a Python desktop application designed to screen global stocks to identify potential investment candidates based on a variety of technical and fundamental scenarios. It serves as a powerful filter to generate a manageable list of stocks for deeper manual analysis.

The application is built with Python and PyQt6, and it is designed to be packaged as a Flatpak for Linux distributions.

![App Screenshot](placeholder.png) <!-- I will need to add a real screenshot path later if available -->

## Key Features

*   **Modern User Interface**: A responsive, three-pane interface allows for intuitive workflow:
    *   **Strategy Pane**: Select your desired scan from categorized cards.
    *   **Main Content Pane**: View onboarding instructions, detailed scan results, or an empty state message.
    *   **Contextual Pane**: Instantly see details about the selected scan strategy or a summary of a selected stock, including a mini-chart and company bio.
*   **Diverse Scanning Scenarios**: Choose from over 10 unique scanning strategies, now grouped by trading concept: Contrarian, Trend-Following, Value, and Volatility. Includes the new **Floor Consolidation** scan to find stocks bottoming out after a crash.
*   **Advanced Charting**: The pop-up chart window provides deep analysis tools:
    *   Candlestick chart with 50/200-day moving averages.
    *   **Fibonacci Retracement** levels calculated for the visible chart period.
    *   **MACD** and **RSI** indicator panels.
    *   Scenario-specific visualizations, such as marking the crash and consolidation phases for the Floor Consolidation scan.
*   **Customizable Scans**:
    *   **Advanced Settings**: Fine-tune global filters like minimum market cap and average volume. Adjust specific parameters for complex scans like Floor Consolidation.
    *   **Ticker Manager**: Manage your own custom ticker lists (watchlists) directly within the application.
    *   **Single Ticker Analysis**: Run any scan on a single stock for quick analysis.
*   **Data Caching**: Caches downloaded stock data to speed up subsequent scans and reduce API calls. The cache can be cleared from the Advanced Settings menu.
*   **Rich Results Table**:
    *   Sortable and filterable results.
    *   Detailed tooltips with score breakdowns.
    *   Export results to CSV or Excel (XLSX).

## Scanning Scenarios

The application uses a categorized library of scenarios to find potential candidates, each suited to a different trading style.

### Contrarian & Reversion (Find stocks moving against the trend)
*   **Classic Oversold**: Identifies stocks that are technically "oversold" (low RSI) and approaching significant support levels (e.g., 200-day SMA). Best for short- to medium-term mean-reversion traders.
*   **Floor Consolidation - Universal**: A powerful new scan that finds stocks that have entered a stable, low-volume consolidation phase after a significant price crash, indicating a potential bottom.
*   **Floor Consolidation - Quality**: A variant of the Universal scan that adds strict filters for fundamental strength (high Fundamental Score) and a prior long-term uptrend, aiming to find high-quality companies that are showing signs of stabilization.
*   **Mean Reversion (Bollinger Bands)**: A short-term strategy that identifies statistically oversold conditions when a stock's price touches or closes below its lower Bollinger Band.
*   **Stochastic Oversold**: Finds stocks that are in an oversold condition based on the Stochastic Oscillator, signaling a potential upward reversal. It identifies when the %K line has crossed above the %D line while both are in 'oversold' territory (below 20).

### Trend & Momentum (Find stocks with strong price momentum)
*   **Momentum Breakout**: A classic momentum strategy that identifies stocks breaking out to new 52-week highs on a surge in trading volume.
*   **Golden Cross**: A long-term trend-following signal that occurs when the 50-day moving average crosses above the 200-day moving average, signaling a potential major uptrend.
*   **Volume-Confirmed Breakout**: Identifies stocks breaking out to new highs with a significant increase in trading volume, confirming investor interest. It finds stocks whose price is within 2% of their 52-week high, and where the most recent day's volume is at least 50% higher than the 20-day average.

### Value & Fundamental (Find quality companies at a fair price)
*   **GARP with Trend Filter**: A 'Growth at a Reasonable Price' (GARP) scan that also requires a positive short-term price trend. It looks for companies with solid earnings growth and a reasonable valuation (P/E ratio), but only considers those whose stock price is currently trading above its 50-day moving average.
*   **Quality Stock Pullback**: "Buys the dip" on fundamentally strong companies in a long-term uptrend that have experienced a minor pullback to a short-term support level like the 50-day moving average.
*   **Fundamental Divergence**: A value-oriented strategy that finds companies with solid fundamentals whose stock price has been stagnating or underperforming the market.
*   **High-Quality Dividend**: Focuses not just on high dividend yield, but on the *sustainability* of that dividend by filtering for companies with healthy financials (e.g., reasonable payout ratio, low debt).

### Volatility (Find stocks poised for a big move)
*   **Volatility Squeeze**: Identifies stocks where price volatility has contracted to an unusually low level (narrow Bollinger Bands), which often precedes a significant price breakout (in either direction).

## Installation and Usage

These instructions explain how to build and install the application on a Debian-based system like Kubuntu using the provided Flatpak manifest.

### 1. Install Prerequisites
First, you need to install `flatpak` and `flatpak-builder`.

```bash
sudo apt update
sudo apt install flatpak flatpak-builder
```

### 2. Add Flathub Remote
If you don't have it already, add the Flathub repository to download the necessary KDE SDK for the build.

```bash
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
```

### 3. Build and Install the Application
From the project's root directory (where `flathub.json` is located), run the build command:

```bash
flatpak-builder build-dir flathub.json --user --install --force-clean
```
**Note:** The `--user` flag installs the application for your local user account, so you do not need to manually specify a username.

### 4. Run the Application
After installation, find "Rectifex RB" in your application menu or run it from the command line:
```bash
flatpak run com.rectifex.GlobalReboundScreener
```

## How to Manage Tickers

The application scans major world indices by default. You can add your own custom stock lists (watchlists) using the built-in **Ticker Manager**.

1.  Click the **"Manage Watchlists"** button in the top toolbar.
2.  In the dialog, you can create new lists (e.g., "My Tech Stocks") or edit existing ones.
3.  Add ticker symbols to your lists, one per line.
4.  These lists will appear in the "Index" selection dropdown for you to scan.

**Important:** Tickers must be in a format that `yfinance` can understand (e.g., `AAPL`, `MSFT`, `6758.T` for a stock on the Tokyo exchange, `SAP.DE` for a stock on XETRA).

### Hinweis zur Datenverfügbarkeit
Die App ist auf kostenlos verfügbare Daten von Yahoo Finance angewiesen. Insbesondere bei kleineren oder internationalen Aktien kann es vorkommen, dass bestimmte Fundamentaldaten (z.B. Gewinnwachstum) nicht verfügbar sind. Ticker, für die essenzielle Daten fehlen, werden vom jeweiligen Scan automatisch ausgeschlossen, um die Ergebnisqualität zu sichern.

## Explanation of Scan Results

The results table contains several columns with technical terms and scores.

### Core Information
*   **Ticker**: The unique symbol for a stock.
*   **Name**: The company's name.
*   **Price**: The most recent closing price.

### The Scoring System
*   **Rebound Score (0-100)**: The final, weighted average score indicating the overall quality of a setup, combining the Technical, Fundamental, and Market Context scores.
*   **Tech. Score (0-100)**: Evaluates the strength of the technical chart pattern based on the selected scan scenario.
*   **Floor Score (0-100)**: A specialized technical score used *only* for the **Floor Consolidation** scans. It measures the quality of the bottoming pattern based on crash depth, consolidation tightness, and volume reduction.
*   **Fund. Score (0-100)**: Measures a company's financial health compared to its sector peers. A score of 50 is average; higher is better.
*   **Market Score (0-100)**: A simple gauge of the broader market trend (100 for Bullish, 20 for Bearish).

### Key Metrics
*   **For Floor Consolidation Scans**:
    *   **Crash %**: The percentage drop from the recent peak to the subsequent low.
    *   **Consol. Range %**: The tightness of the price range during the consolidation phase. Lower is better.
    *   **Drop Date**: The date the stock hit its low after the crash.
*   **For Fundamental Scans**:
    *   **ROE (Return on Equity)**, **P/E Ratio**, **Debt/Equity**, **Revenue Growth**, **EPS Growth**, etc. These metrics are used to calculate the **Fund. Score**.

## Disclaimer

This application is for informational and educational purposes only. It is not financial advice. Investing in stocks involves risk. Always conduct your own thorough research and consult with a qualified financial advisor before making any investment decisions.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.