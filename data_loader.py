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
import random
from typing import List, Dict, Callable, Optional

# Assuming config.py is in the same directory
import config

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def ensure_cache_dir_exists():
    """Creates the cache directory if it doesn't exist."""
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logging.error(f"Failed to create cache directory {config.CACHE_DIR}: {e}")
        raise

import requests
import re

def _get_tickers_from_wiki(index_details: dict) -> list[str] | None:
    """
    Tries to scrape a list of tickers from a Wikipedia page.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0'
        }
        response = requests.get(index_details['wiki_url'], headers=headers)
        response.raise_for_status()
        tables = pd.read_html(response.text)
        ticker_col_names = ['Ticker', 'Symbol', 'Ticker symbol']
        for table in tables:
            for col_name in ticker_col_names:
                if col_name in table.columns:
                    tickers = table[col_name].dropna().tolist()
                    return [str(t).split(' ')[0] for t in tickers]
    except Exception as e:
        logging.error(f"Failed to scrape or parse Wikipedia page for {index_details['name']}: {e}")
    return None

def _get_tickers_from_csv(index_details: dict) -> list[str]:
    """
    Loads a list of tickers from a fallback CSV file using an absolute path.
    """
    # Construct an absolute path to the CSV file
    fallback_path = config.BASE_DIR / index_details['fallback_csv']
    if not fallback_path.exists():
        logging.warning(f"Fallback CSV not found at {fallback_path}")
        return []
    try:
        df = pd.read_csv(fallback_path)
        # Use the 'Ticker' column if it exists, otherwise assume the first column
        ticker_col = 'Ticker' if 'Ticker' in df.columns else df.columns[0]
        # Clean up tickers by stripping whitespace
        return [str(t).strip() for t in df[ticker_col].dropna().tolist()]
    except Exception as e:
        logging.error(f"Failed to read fallback CSV {fallback_path}: {e}")
        return []

def _post_process_tickers(tickers: list[str], market: str) -> list[str]:
    """
    Applies market-specific transformations to ticker symbols, avoiding duplicate suffixes.
    """
    processed_tickers = []
    for t in tickers:
        # Skip empty tickers and clean up whitespace
        cleaned_ticker = t.strip()
        if not cleaned_ticker:
            continue

        # Apply suffix only if one doesn't already exist
        if '.' not in cleaned_ticker:
            if market == 'DE':
                processed_tickers.append(f"{cleaned_ticker}.DE")
            elif market == 'JP':
                processed_tickers.append(f"{cleaned_ticker}.T")
            else:
                processed_tickers.append(cleaned_ticker)
        else:
            processed_tickers.append(cleaned_ticker)

    return processed_tickers

def _get_tickers_from_user_csv(index_name: str) -> list[str] | None:
    """
    Loads tickers from a user-defined CSV file if it exists.
    """
    try:
        sanitized_name = index_name.replace(" ", "_").lower()
        user_list_path = config.USER_TICKER_DIR / f"{sanitized_name}_user.csv"
        if user_list_path.exists():
            df = pd.read_csv(user_list_path)
            ticker_col = 'Ticker' if 'Ticker' in df.columns else df.columns[0]
            return df[ticker_col].dropna().tolist()
    except Exception as e:
        logging.error(f"Failed to read user-defined CSV {user_list_path}: {e}")
    return None

def get_ticker_list(index_name: str) -> list[str]:
    """
    Gets the list of constituent tickers for a given index, with robust fallbacks.
    """
    index_details = config.INDICES.get(index_name)
    if not index_details:
        return []

    # First, try to load a user-defined list
    tickers = _get_tickers_from_user_csv(index_name)

    # If the user list is not present or empty, try scraping Wikipedia
    if not tickers:
        tickers = _get_tickers_from_wiki(index_details)

    # If scraping fails or returns an empty list, use the local fallback CSV
    if not tickers:
        tickers = _get_tickers_from_csv(index_details)

    # Ensure tickers is a list before post-processing to avoid errors
    if not tickers:
        return []

    return _post_process_tickers(tickers, index_details['market'])

def get_master_ticker_list() -> list[str] | None:
    """
    Loads tickers from the master CSV file if it exists, using an absolute path.
    """
    master_list_path = config.BASE_DIR / "data" / "master_tickers.csv"
    if not master_list_path.exists():
        logging.warning(f"Master ticker list not found at {master_list_path}")
        return None
    try:
        df = pd.read_csv(master_list_path)
        ticker_col = 'Ticker' if 'Ticker' in df.columns else df.columns[0]
        return df[ticker_col].dropna().tolist()
    except Exception as e:
        logging.error(f"Failed to read master CSV {master_list_path}: {e}")
        return None

def get_all_tickers() -> dict[str, list[str]]:
    """
    Gets all tickers from all configured indices, grouped by market.
    """
    market_to_tickers = {}
    for index_name, details in config.INDICES.items():
        market = details['market']
        if market not in market_to_tickers:
            market_to_tickers[market] = set()
        tickers = get_ticker_list(index_name)
        market_to_tickers[market].update(tickers)
    master_tickers = get_master_ticker_list()
    if master_tickers:
        if "CUSTOM" not in market_to_tickers:
            market_to_tickers["CUSTOM"] = set()
        market_to_tickers["CUSTOM"].update(master_tickers)
    return {market: sorted(list(tickers)) for market, tickers in market_to_tickers.items()}

async def _fetch_single_ticker_history(ticker: str) -> pd.DataFrame | None:
    """
    Asynchronously downloads historical data for a single stock with exponential backoff.
    """
    max_retries = 3
    base_wait_time = 2
    for attempt in range(max_retries):
        try:
            loop = asyncio.get_running_loop()
            download_func = functools.partial(yf.download, tickers=ticker, period=config.DATA_PERIOD, auto_adjust=True, progress=False)
            data = await loop.run_in_executor(None, download_func)
            if data.empty:
                logging.warning(f"No data for ticker: {ticker}")
                return None
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)
            data.dropna(inplace=True)
            return data
        except Exception as e:
            error_str = str(e)
            # Handle non-transient errors by skipping the ticker immediately
            if ('YFPricesMissingError' in error_str or
                'No data found, symbol may be delisted' in error_str or
                'No objects to concatenate' in error_str or
                (isinstance(e, ValueError) and "The truth value of an" in error_str and "is ambiguous" in error_str)):
                logging.warning(f"Skipping {ticker} due to a non-transient data error: {e}")
                return None  # Do not retry for these errors

            # For other exceptions, log a warning and retry
            logging.warning(f"Attempt {attempt + 1} for {ticker} failed: {e}")
            if attempt < max_retries - 1:
                wait_time = base_wait_time * (2 ** attempt) + random.uniform(0, 1)
                await asyncio.sleep(wait_time)
    return None

async def get_historical_data_for_tickers(
    tickers: List[str],
    progress_callback: Optional[Callable] = None,
    is_cancelled_callback: Optional[Callable] = None
) -> Dict[str, pd.DataFrame]:
    """
    Asynchronously retrieves historical data for a list of tickers.
    """
    ensure_cache_dir_exists()
    is_cancelled = is_cancelled_callback or (lambda: False)
    results, tickers_to_fetch = {}, []
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
                    logging.warning(f"Cache read failed for {ticker}: {e}")
        tickers_to_fetch.append(ticker)

    if progress_callback: progress_callback.emit(f"Loaded {cache_hits}/{len(tickers)} historical records from cache.")
    if not tickers_to_fetch or is_cancelled(): return results

    semaphore = asyncio.Semaphore(8)
    async def fetch_and_cache(ticker: str):
        async with semaphore:
            if is_cancelled(): return ticker, None
            data = await _fetch_single_ticker_history(ticker)
            if data is not None and not data.empty:
                cache_file = config.CACHE_DIR / f"{ticker.replace('^', 'INDEX-')}.csv"
                await asyncio.to_thread(data.to_csv, cache_file)
            return ticker, data
    tasks = [asyncio.create_task(fetch_and_cache(t)) for t in tickers_to_fetch]

    fetched_count = 0
    for future in asyncio.as_completed(tasks):
        if is_cancelled():
            for task in tasks:
                task.cancel()
            break
        try:
            ticker, data = await future
            if data is not None: results[ticker] = data
            fetched_count += 1
            if progress_callback: progress_callback.emit(f"Fetched historical data for {ticker} ({fetched_count}/{len(tickers_to_fetch)})")
        except asyncio.CancelledError: pass
    return results

async def get_stock_data(ticker: str) -> pd.DataFrame | None:
    """(DEPRECATED) Wrapper for single ticker fetching."""
    result_dict = await get_historical_data_for_tickers([ticker])
    return result_dict.get(ticker)