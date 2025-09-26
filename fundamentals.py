import asyncio
import json
import logging
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Callable, Optional

import numpy as np
import pandas as pd
import yfinance as yf
# from curl_cffi.requests import AsyncSession # This was causing issues

import config
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def _fetch_single_ticker_fundamentals(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Asynchronously fetches and processes fundamental data for a single stock
    with retries and robust error handling.
    """
    loop = asyncio.get_running_loop()
    max_retries = 3
    base_wait_time = 2
    for attempt in range(max_retries):
        try:
            # yf.Ticker and .info are blocking calls, run in executor
            stock = await loop.run_in_executor(None, yf.Ticker, ticker)
            info = await loop.run_in_executor(None, getattr, stock, 'info')

            if not isinstance(info, dict) or not info:
                logger.warning(f"No valid info dictionary returned for {ticker}, skipping.")
                return None

            # Process the info dict to make it serializable and handle numpy arrays/series
            serializable_info = {}
            for key, value in info.items():
                # 1. Handle array-like types first to avoid ambiguous truth checks later.
                if isinstance(value, (np.ndarray, pd.Series)):
                    serializable_info[key] = value.tolist() if value.size > 0 else None
                    continue

                # 2. Handle numpy/pandas numeric types.
                if isinstance(value, (np.generic, np.number)):
                    serializable_info[key] = value.item()
                    continue

                # 3. Now it should be safe to check for NaN on scalar values.
                # This also handles Python's `None`.
                try:
                    if pd.isna(value):
                        serializable_info[key] = None
                        continue
                except (TypeError, ValueError):
                    # This can happen for unsupported types in pd.isna, just pass them through
                    pass

                # 4. If none of the above, assume the value is already serializable.
                serializable_info[key] = value
            return serializable_info

        except Exception as e:
            # Handle non-transient yfinance errors without retrying
            if isinstance(e, ValueError) and "The truth value of an" in str(e) and "is ambiguous" in str(e):
                logger.warning(f"Skipping {ticker} due to yfinance internal data error: {e}")
                return None

            logger.warning(f"Attempt {attempt + 1} for {ticker} fundamentals failed: {e}")
            if attempt < max_retries - 1:
                wait_time = base_wait_time * (2 ** attempt) + random.uniform(0, 1)
                await asyncio.sleep(wait_time)  # Use non-blocking sleep
            else:
                logger.error(f"All attempts to fetch fundamentals for {ticker} failed.")
    return None


CACHE_DIR = Path.home() / ".local" / "share" / "rectifex" / "cache"
FUNDAMENTALS_DIR = CACHE_DIR / "fundamentals"
SECTOR_MEDIANS_FILE = CACHE_DIR / "sector_medians.json"

class FundamentalDataHandler:
    def __init__(self):
        FUNDAMENTALS_DIR.mkdir(parents=True, exist_ok=True)
        # yfinance will manage its own session internally.
        # self.session = AsyncSession(impersonate="chrome110") # This was causing issues.

    def _get_cached_data(self, ticker: str) -> Optional[Dict[str, Any]]:
        cache_file = FUNDAMENTALS_DIR / f"{ticker}.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f: data = json.load(f)
                last_update = datetime.fromisoformat(data['last_update'])
                if datetime.now(timezone.utc) - last_update < timedelta(days=config.FUNDAMENTAL_CACHE_EXPIRY_DAYS):
                    return data
            except Exception as e:
                logging.warning(f"Cache for {ticker} is corrupt, refetching. Error: {e}")
        return None

    def _save_to_cache(self, ticker: str, data: Dict[str, Any]):
        cache_file = FUNDAMENTALS_DIR / f"{ticker}.json"
        try:
            with open(cache_file, 'w') as f: json.dump(data, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save fundamental cache for {ticker}. Error: {e}")

    async def _fetch_single_ticker(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Asynchronously fetches fundamental data using the robust asynchronous wrapper.
        """
        info = await _fetch_single_ticker_fundamentals(ticker)

        if info:
            data_packet = {
                "ticker": ticker,
                "last_update": datetime.now(timezone.utc).isoformat(),
                "info": info
            }
            self._save_to_cache(ticker, data_packet)
            return data_packet
        return None

    async def get_fundamentals_for_tickers(self, tickers: List[str],
                                           progress_callback: Optional[Callable] = None,
                                           is_cancelled_callback: Optional[Callable] = None) -> Dict[str, Dict]:
        is_cancelled = is_cancelled_callback or (lambda: False)
        results, tickers_to_fetch = {}, []
        for ticker in tickers:
            if is_cancelled(): break
            cached_data = self._get_cached_data(ticker)
            if cached_data: results[ticker] = cached_data
            else: tickers_to_fetch.append(ticker)
        if not tickers_to_fetch or is_cancelled(): return results

        semaphore = asyncio.Semaphore(8)
        async def fetch_with_semaphore(ticker: str):
            async with semaphore:
                if is_cancelled(): return ticker, None
                return ticker, await self._fetch_single_ticker(ticker)

        tasks = [asyncio.create_task(fetch_with_semaphore(t)) for t in tickers_to_fetch]
        for future in asyncio.as_completed(tasks):
            if is_cancelled():
                for task in tasks:
                    task.cancel()
                break
            try:
                ticker, data = await future
                if data: results[ticker] = data
                if progress_callback: progress_callback.emit(f"Fetched fundamentals for {ticker}")
            except asyncio.CancelledError: pass
        return results

    def compute_and_save_sector_medians(self):
        # ... (Implementation remains the same)
        pass

    async def get_full_ticker_info(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Asynchronously fetches the full, raw fundamental data for a single ticker
        using the robust asynchronous wrapper.
        """
        return await _fetch_single_ticker_fundamentals(ticker)