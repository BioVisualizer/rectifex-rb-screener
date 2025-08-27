# analysis.py
# Contains the logic for filtering, indicators (RSI, SMA), and scoring.

import pandas as pd
import numpy as np
import yfinance as yf
import logging

import config
import data_loader

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 1. Indicator Calculation Functions ---

def calculate_sma(data: pd.Series, window: int) -> pd.Series:
    """Calculates the Simple Moving Average."""
    if data is None or len(data) < window:
        return pd.Series(dtype=np.float64)
    return data.rolling(window=window).mean()

def calculate_rsi(data: pd.Series, window: int = 14) -> pd.Series:
    """Calculates the Relative Strength Index (RSI)."""
    if data is None or len(data) < window:
        return pd.Series(dtype=np.float64)

    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()

    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

# --- 2. Filtering Functions ---

def passes_market_context_filter(index_data: pd.DataFrame) -> bool:
    """
    Checks if the market index is in a positive trend (above its 50-day SMA).
    """
    if index_data is None or index_data.empty:
        logging.warning("Market index data is missing, skipping context filter.")
        return False

    sma_50 = calculate_sma(index_data['Close'], config.MARKET_CONTEXT_SMA)
    if sma_50.empty:
        logging.warning("Could not calculate 50-day SMA for market index.")
        return False

    latest_price = index_data['Close'].iloc[-1]
    latest_sma = sma_50.iloc[-1]

    if pd.isna(latest_price) or pd.isna(latest_sma):
        logging.warning("Latest price or SMA for index is NaN.")
        return False

    return latest_price > latest_sma

import json
from datetime import datetime, timedelta

def get_ticker_info_cached(ticker: str) -> dict | None:
    """
    Gets fundamental info for a ticker, using a local JSON cache.
    The cache is valid for the same duration as the price data.
    """
    info_cache_dir = config.CACHE_DIR / "info"
    info_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = info_cache_dir / f"{ticker}.json"

    # Check for a valid cache file
    if cache_file.exists():
        mod_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if datetime.now() - mod_time < timedelta(hours=config.CACHE_EXPIRY_HOURS):
            logging.info(f"Loading {ticker} INFO from cache.")
            try:
                with open(cache_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logging.warning(f"Could not read info cache for {ticker}, refetching. Error: {e}")

    # If no valid cache, fetch from yfinance
    logging.info(f"Downloading {ticker} INFO from yfinance.")
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        # yfinance can return empty info dicts for some tickers
        if not info or info.get('marketCap') is None:
            logging.warning(f"No valid info returned from yfinance for {ticker}")
            return None

        with open(cache_file, 'w') as f:
            json.dump(info, f)

        return info
    except Exception as e:
        # yfinance often throws errors for delisted/invalid tickers, which is expected.
        logging.warning(f"Could not download info for {ticker}: {e}")
        return None


def passes_liquidity_filter(ticker_info: dict) -> bool:
    """
    Checks if a stock meets the minimum market cap and volume requirements.
    """
    if not ticker_info:
        return False

    market_cap = ticker_info.get('marketCap', 0)
    # yfinance provides 'averageVolume' (for 3 months) and 'averageVolume10days'
    # We use 'averageVolume' as a proxy for the 30-day average volume.
    avg_volume = ticker_info.get('averageVolume', 0)

    if market_cap and avg_volume and market_cap > config.MIN_MARKET_CAP and avg_volume > config.MIN_AVG_VOLUME_30D:
        return True
    return False

def check_core_signal(data: pd.DataFrame) -> tuple[bool, float, float, float]:
    """
    Checks if a stock meets the core rebound signal criteria.

    Returns:
        - bool: True if the stock is a candidate.
        - float: The current RSI value.
        - float: The percentage distance to the 200-day SMA.
        - float: The percentage distance to the 90-day low.
    """
    # Calculate all necessary indicators
    data['RSI'] = calculate_rsi(data['Close'], config.RSI_PERIOD)
    data['SMA200'] = calculate_sma(data['Close'], config.SMA_SUPPORT_PERIOD)
    data['Low90D'] = data['Low'].rolling(window=config.LOWEST_LOW_PERIOD).min()

    # Get the latest values
    latest_data = data.iloc[-1]
    current_price = latest_data['Close']
    rsi = latest_data['RSI']
    sma200 = latest_data['SMA200']
    low90d = latest_data['Low90D']

    if pd.isna(current_price) or pd.isna(rsi):
        return False, np.nan, np.nan, np.nan

    # Calculate distances to support levels (in percent)
    dist_to_sma = ((current_price - sma200) / sma200) * 100 if pd.notna(sma200) and sma200 > 0 else np.inf
    dist_to_low = ((current_price - low90d) / low90d) * 100 if pd.notna(low90d) and low90d > 0 else np.inf

    # Condition A: Strongly oversold
    is_candidate_A = rsi < config.RSI_OVERSOLD_STRONG

    # Condition B: Oversold at support
    is_candidate_B = False
    if rsi < config.RSI_OVERSOLD_WEAK:
        # Check if price is within the proximity threshold of either support
        if 0 <= dist_to_sma <= config.SUPPORT_PROXIMITY_THRESHOLD:
            is_candidate_B = True
        elif 0 <= dist_to_low <= config.SUPPORT_PROXIMITY_THRESHOLD:
            is_candidate_B = True

    is_candidate = is_candidate_A or is_candidate_B
    return is_candidate, rsi, dist_to_sma, dist_to_low

# --- 3. Scoring Function ---

def calculate_rebound_score(rsi: float, dist_to_sma: float, dist_to_low: float) -> int:
    """
    Calculates the rebound score based on RSI and proximity to support.
    """
    # RSI Score
    rsi_score = max(0, (config.RSI_SCORE_CEILING - rsi) * (100 / (config.RSI_SCORE_CEILING - config.RSI_OVERSOLD_STRONG)))
    rsi_score = min(100, rsi_score) # Cap at 100

    # Proximity Score
    # Use the distance to the *closer* of the two valid support levels
    prox_dist = np.inf
    if dist_to_sma >= 0:
        prox_dist = min(prox_dist, dist_to_sma)
    if dist_to_low >= 0:
        prox_dist = min(prox_dist, dist_to_low)

    if prox_dist > config.PROXIMITY_SCORE_CEILING:
        proximity_score = 0
    else:
        proximity_score = (config.PROXIMITY_SCORE_CEILING - prox_dist) * (100 / config.PROXIMITY_SCORE_CEILING)

    proximity_score = max(0, min(100, proximity_score))

    # Final weighted score
    final_score = (0.6 * rsi_score) + (0.4 * proximity_score)

    return int(final_score)

# --- 4. Main Analysis Pipeline ---

def run_analysis(progress_callback=None):
    """
    Runs the full analysis pipeline.
    Emits progress updates via the optional progress_callback.
    Returns a list of dictionaries, where each dict is a candidate stock.
    """
    def emit_progress(message):
        logging.info(message)
        if progress_callback:
            progress_callback.emit(message)

    emit_progress("Starting global rebound scan...")
    all_tickers = data_loader.get_all_tickers()

    index_data_cache = {}
    market_context_ok = {}
    all_candidates = []
    total_tickers = sum(len(t) for t in all_tickers.values())
    processed_tickers = 0

    for market, tickers in all_tickers.items():
        emit_progress(f"--- Processing Market: {market} ({len(tickers)} tickers) ---")

        index_ticker = next((d['index_ticker'] for d in config.INDICES.values() if d['market'] == market), None)

        if not index_ticker:
            emit_progress(f"No index ticker found for market '{market}'. Skipping market.")
            processed_tickers += len(tickers)
            continue

        if index_ticker not in index_data_cache:
            emit_progress(f"Fetching data for market index: {index_ticker}")
            index_data_cache[index_ticker] = data_loader.get_stock_data(index_ticker)
            market_context_ok[market] = passes_market_context_filter(index_data_cache[index_ticker])

        if not market_context_ok.get(market):
            emit_progress(f"Market context for {market} ({index_ticker}) is bearish. Skipping all tickers in this market.")
            processed_tickers += len(tickers)
            continue

        emit_progress(f"Market context for {market} ({index_ticker}) is bullish. Proceeding with analysis.")

        for i, ticker in enumerate(tickers):
            processed_tickers += 1
            progress_percent = int((processed_tickers / total_tickers) * 100)
            emit_progress(f"Analyzing [{processed_tickers}/{total_tickers}] ({progress_percent}%) {ticker}...")

            try:
                # 1. Liquidity Filter (with caching)
                stock_info = get_ticker_info_cached(ticker)
                if not passes_liquidity_filter(stock_info):
                    continue

                stock_data = data_loader.get_stock_data(ticker)
                if stock_data is None or stock_data.empty:
                    continue

                is_candidate, rsi, dist_sma, dist_low = check_core_signal(stock_data.copy())

                if not is_candidate:
                    continue

                emit_progress(f"!!! {ticker} is a potential candidate! Calculating score...")
                score = calculate_rebound_score(rsi, dist_sma, dist_low)

                candidate = {
                    "Ticker": ticker,
                    "Name": stock_info.get('shortName', 'N/A'),
                    "Market": market,
                    "Score": score,
                    "RSI": round(rsi, 2),
                    "Price": round(stock_data['Close'].iloc[-1], 2),
                    "Dist_SMA(%)": round(dist_sma, 2) if dist_sma != np.inf else 'N/A',
                    "Dist_Low(%)": round(dist_low, 2) if dist_low != np.inf else 'N/A',
                }
                all_candidates.append(candidate)
                emit_progress(f"Added {ticker} to candidates list with score {score}.")

            except Exception as e:
                emit_progress(f"ERROR: An error occurred while processing {ticker}: {e}")

    emit_progress(f"Analysis complete. Found {len(all_candidates)} candidates.")
    return all_candidates


if __name__ == '__main__':
    # Example usage for testing
    print("--- Running analysis.py stand-alone test ---")

    # Mock the progress_callback to just print
    class MockEmitter:
        def emit(self, message):
            print(f"CALLBACK: {message}")

    mock_emitter = MockEmitter()

    data_loader.get_all_tickers = lambda: {
        'US': ['AAPL', 'MSFT', 'PYPL'],
        'DE': ['SAP.DE', 'VOW3.DE']
    }

    results = run_analysis(progress_callback=mock_emitter)

    print("\n--- Analysis Results ---")
    if results:
        results_df = pd.DataFrame(results)
        print(results_df)
    else:
        print("No candidates found in the test run.")

    print("\n--- Test complete ---")
