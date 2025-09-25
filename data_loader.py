# data_loader.py
# Responsible for downloading and caching stock data.

import os
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from pathlib import Path
import logging
import asyncio
import functools
import time
from typing import List, Dict, Callable, Optional

# Assuming config.py is in the same directory
import config

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def ensure_cache_dir_exists():
    """Creates the cache directory if it doesn't exist."""
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        logging.info(f"Cache directory ensured at: {config.CACHE_DIR}")
    except OSError as e:
        logging.error(f"Failed to create cache directory {config.CACHE_DIR}: {e}")
        raise

import requests
import re

def _get_tickers_from_wiki(index_details: dict) -> list[str] | None:
    """
    Tries to scrape a list of tickers from a Wikipedia page.
    Uses a standard browser User-Agent to avoid HTTP 403 Forbidden errors.
    Includes special handling for pages that don't use a standard table.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0'
        }
        logging.info(f"Attempting to scrape tickers for {index_details['name']} from {index_details['wiki_url']}")

        response = requests.get(index_details['wiki_url'], headers=headers)
        response.raise_for_status() # Will raise an exception for 4xx/5xx errors
        html_content = response.text

        # Special parser for Nikkei 225, which lists components in plain text.
        if index_details['name'] == "Nikkei 225":
            tickers = re.findall(r'\(TYO:\s*(\d{4})\)', html_content)
            if tickers:
                logging.info(f"Successfully scraped {len(tickers)} tickers for {index_details['name']} using regex parser.")
                return tickers
            else:
                logging.warning("Nikkei 225 regex parser failed to find tickers.")
                # Fall through to standard table parser as a backup

        # Standard parser using pandas.read_html for table-based component lists.
        tables = pd.read_html(html_content)

        ticker_col_names = ['Ticker', 'Symbol', 'Ticker symbol']

        for table in tables:
            for col_name in ticker_col_names:
                if col_name in table.columns:
                    tickers = table[col_name].dropna().tolist()
                    # Clean tickers: remove annotations like "(class A)"
                    tickers = [str(t).split(' ')[0] for t in tickers]
                    logging.info(f"Successfully scraped {len(tickers)} tickers for {index_details['name']} using table parser.")
                    return tickers

        logging.warning(f"Could not find a valid ticker column in any table for {index_details['name']}.")
        return None
    except Exception as e:
        logging.error(f"Failed to scrape or parse Wikipedia page for {index_details['name']}: {e}")
        return None

def _get_tickers_from_csv(index_details: dict) -> list[str]:
    """
    Loads a list of tickers from a fallback CSV file.
    """
    fallback_path = Path(index_details['fallback_csv'])
    logging.info(f"Loading tickers for {index_details['name']} from fallback file: {fallback_path}")
    if not fallback_path.exists():
        logging.error(f"Fallback CSV file not found: {fallback_path}")
        return []
    try:
        df = pd.read_csv(fallback_path)
        # Assuming the ticker column is the first one, or named 'Ticker'
        ticker_col = 'Ticker' if 'Ticker' in df.columns else df.columns[0]
        return df[ticker_col].dropna().tolist()
    except Exception as e:
        logging.error(f"Failed to read or parse fallback CSV {fallback_path}: {e}")
        return []

def _post_process_tickers(tickers: list[str], market: str) -> list[str]:
    """Applies market-specific transformations to ticker symbols."""
    if market == 'DE':
        # For German stocks, yfinance expects the .DE suffix
        return [f"{t}.DE" if not t.endswith('.DE') else t for t in tickers]
    if market == 'JP':
        # For Japanese stocks, yfinance expects the .T suffix
        return [f"{t}.T" if not t.endswith('.T') else t for t in tickers]
    # US and other markets often have correct tickers from Wikipedia
    return tickers

def _get_tickers_from_user_csv(index_name: str) -> list[str] | None:
    """
    Loads tickers from a user-defined CSV file if it exists.
    """
    try:
        sanitized_name = index_name.replace(" ", "_").lower()
        user_list_path = config.USER_TICKER_DIR / f"{sanitized_name}_user.csv"

        if user_list_path.exists():
            logging.info(f"Loading user-defined ticker list for {index_name} from {user_list_path}")
            df = pd.read_csv(user_list_path)
            ticker_col = 'Ticker' if 'Ticker' in df.columns else df.columns[0]
            return df[ticker_col].dropna().tolist()
    except Exception as e:
        logging.error(f"Failed to read or parse user-defined CSV {user_list_path}: {e}")
        # Fallback to default method
        return None
    return None

def get_ticker_list(index_name: str) -> list[str]:
    """
    Gets the list of constituent tickers for a given index.
    It prioritizes user-defined lists, then tries scraping Wikipedia,
    and finally falls back to a local CSV.
    """
    index_details = config.INDICES.get(index_name)
    if not index_details:
        logging.error(f"Index '{index_name}' not found in config.")
        return []

    # 1. Try to load from user-defined CSV first
    tickers = _get_tickers_from_user_csv(index_name)

    # 2. If no user list, try scraping Wikipedia
    if tickers is None:
        tickers = _get_tickers_from_wiki(index_details)

    # 3. If scraping fails, use the fallback CSV
    if tickers is None:
        tickers = _get_tickers_from_csv(index_details)

    # Post-process tickers for yfinance compatibility
    tickers = _post_process_tickers(tickers, index_details['market'])

    return tickers

def get_master_ticker_list() -> list[str] | None:
    """
    Loads tickers from the master CSV file if it exists.
    """
    master_list_path = Path("data/master_tickers.csv")
    if not master_list_path.exists():
        return None

    try:
        logging.info(f"Loading master ticker list from {master_list_path}")
        df = pd.read_csv(master_list_path)
        ticker_col = 'Ticker' if 'Ticker' in df.columns else df.columns[0]
        return df[ticker_col].dropna().tolist()
    except Exception as e:
        logging.error(f"Failed to read or parse master CSV {master_list_path}: {e}")
        return None

def get_all_tickers() -> dict[str, list[str]]:
    """
    Gets all tickers from all configured indices, grouped by market.
    If a master ticker list exists, its tickers are added to the pool.
    Returns a dictionary mapping market -> list of unique tickers.
    """
    market_to_tickers = {}
    for index_name, details in config.INDICES.items():
        market = details['market']
        if market not in market_to_tickers:
            market_to_tickers[market] = set()

        logging.info(f"Fetching tickers for index: {index_name}")
        tickers = get_ticker_list(index_name)
        market_to_tickers[market].update(tickers)

    # Check for master ticker list and add its tickers
    master_tickers = get_master_ticker_list()
    if master_tickers:
        logging.info("Found master ticker list, adding its tickers to the scan.")
        if "CUSTOM" not in market_to_tickers:
            market_to_tickers["CUSTOM"] = set()
        market_to_tickers["CUSTOM"].update(master_tickers)

    # Convert sets to lists
    return {market: sorted(list(tickers)) for market, tickers in market_to_tickers.items()}

async def _fetch_single_ticker_history(ticker: str) -> pd.DataFrame | None:
    """
    Asynchronously downloads historical data for a single stock with exponential backoff.
    """
    max_retries = 3
    base_wait_time = 2  # seconds

    for attempt in range(max_retries):
        try:
            loop = asyncio.get_running_loop()
            download_func = functools.partial(
                yf.download,
                tickers=ticker,
                period=config.DATA_PERIOD,
                auto_adjust=True,
                progress=False
            )
            data = await loop.run_in_executor(None, download_func)

            if data.empty:
                logging.warning(f"No data returned from yfinance for ticker: {ticker}")
                return None # Don't retry for empty data, it's a valid (but empty) response

            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)
            data.dropna(inplace=True)
            return data

        except Exception as e:
            # This could be a network error, yfinance error, etc.
            logging.warning(f"Attempt {attempt + 1} for {ticker} failed: {e}")
            if attempt < max_retries - 1:
                wait_time = base_wait_time * (2 ** attempt) + random.uniform(0, 1)
                logging.info(f"Retrying {ticker} in {wait_time:.2f} seconds...")
                await asyncio.sleep(wait_time)
            else:
                logging.error(f"All {max_retries} attempts failed for {ticker}. Error: {e}")
    return None

async def get_historical_data_for_tickers(
    tickers: List[str],
    progress_callback: Optional[Callable] = None,
    is_cancelled_callback: Optional[Callable] = None
) -> Dict[str, pd.DataFrame]:
    """
    Asynchronously retrieves historical data for a list of tickers.
    - Uses a local cache first.
    - Fetches missing data in parallel using a semaphore.
    - Implements exponential backoff for failed requests.
    - Returns a dictionary of {ticker: dataframe}.
    """
    ensure_cache_dir_exists()
    is_cancelled = is_cancelled_callback or (lambda: False)
    results = {}
    tickers_to_fetch = []
    cache_hits = 0

    for ticker in tickers:
        if is_cancelled(): break
        cache_file = config.CACHE_DIR / f"{ticker.replace('^', 'INDEX-')}.csv"
        if cache_file.exists():
            mod_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
            if datetime.now() - mod_time < timedelta(hours=config.HISTORICAL_CACHE_EXPIRY_HOURS):
                try:
                    df = await asyncio.to_thread(pd.read_csv, cache_file, index_col='Date', parse_dates=['Date'])
                    results[ticker] = df
                    cache_hits += 1
                    continue
                except Exception as e:
                    logging.warning(f"Could not read cache for {ticker}, refetching. Error: {e}")
        tickers_to_fetch.append(ticker)

    if progress_callback:
        progress_callback(f"Loaded {cache_hits}/{len(tickers)} historical records from cache.")

    if not tickers_to_fetch or is_cancelled():
        return results

    semaphore = asyncio.Semaphore(8)  # Limit concurrent downloads to 8 as per spec
    tasks = []

    async def fetch_and_cache(ticker: str):
        async with semaphore:
            if is_cancelled(): return ticker, None

            logging.info(f"Downloading {ticker} data from yfinance.")
            data = await _fetch_single_ticker_history(ticker)

            if data is not None and not data.empty:
                cache_file = config.CACHE_DIR / f"{ticker.replace('^', 'INDEX-')}.csv"
                await asyncio.to_thread(data.to_csv, cache_file)
                logging.info(f"Saved {ticker} data to cache.")

            return ticker, data

    for ticker in tickers_to_fetch:
        tasks.append(fetch_and_cache(ticker))

    fetched_count = 0
    total_to_fetch = len(tickers_to_fetch)
    for future in asyncio.as_completed(tasks):
        if is_cancelled():
            for task in tasks: task.cancel()
            break

        try:
            ticker, data = await future
            if data is not None:
                results[ticker] = data

            fetched_count += 1
            if progress_callback:
                progress_callback(f"Fetched historical data for {ticker} ({fetched_count}/{total_to_fetch})")
        except asyncio.CancelledError:
            pass

    return results


# --- DEPRECATED ---
# This function is kept for single-ticker calls from older parts of the code,
# but the new `get_historical_data_for_tickers` should be used for batch processing.
async def get_stock_data(ticker: str) -> pd.DataFrame | None:
    """
    (DEPRECATED) Asynchronously downloads historical data for a single stock, using a local cache.
    """
    result_dict = await get_historical_data_for_tickers([ticker])
    return result_dict.get(ticker)


if __name__ == '__main__':
    # Example usage and testing
    logging.info("--- Testing data_loader.py ---")

    # Test getting a single index list
    print("\n[1] Testing S&P 500 ticker retrieval...")
    sp500_tickers = get_ticker_list('S&P 500')
    print(f"Found {len(sp500_tickers)} tickers for S&P 500. First 5: {sp500_tickers[:5]}")

    # Test getting all tickers grouped by market
    print("\n[2] Testing retrieval of all tickers by market...")
    all_tickers_by_market = get_all_tickers()
    for market, tickers in all_tickers_by_market.items():
        print(f"Market: {market}, Found: {len(tickers)} tickers. Example: {tickers[0] if tickers else 'N/A'}")

    async def run_async_test():
        # Test getting stock data for a list of tickers (with caching)
        print("\n[3] Testing stock data retrieval for multiple tickers...")
        test_tickers = ['AAPL', 'MSFT', 'GOOG', 'TSLA', 'NONEXISTENT']

        def progress_reporter(msg: str):
            print(f"PROGRESS: {msg}")

        all_data = await get_historical_data_for_tickers(test_tickers, progress_callback=progress_reporter)

        for ticker in test_tickers:
            if ticker in all_data:
                print(f"Successfully fetched {ticker} data. Shape: {all_data[ticker].shape}")
            else:
                print(f"Failed to fetch data for {ticker}.")

    asyncio.run(run_async_test())

    print("\n--- Test complete ---")