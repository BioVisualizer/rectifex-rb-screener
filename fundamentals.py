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
from curl_cffi.requests import AsyncSession

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

CACHE_DIR = Path.home() / ".local" / "share" / "rectifex" / "cache"
FUNDAMENTALS_DIR = CACHE_DIR / "fundamentals"
SECTOR_MEDIANS_FILE = CACHE_DIR / "sector_medians.json"

class FundamentalDataHandler:
    def __init__(self):
        FUNDAMENTALS_DIR.mkdir(parents=True, exist_ok=True)
        self.session = AsyncSession(impersonate="chrome110")

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

    def _calculate_metrics(self, stock: yf.Ticker) -> Optional[Dict[str, Any]]:
        info = stock.info
        if not info or info.get('marketCap') is None: return None
        try:
            metrics = {
                'revenue_3yr_cagr': info.get('revenueGrowth'),
                'eps_1y_growth': info.get('earningsGrowth'),
                'net_margin': info.get('profitMargins'),
                'roe': info.get('returnOnEquity'),
                'debt_equity': info.get('debtToEquity'),
                'pe_ttm': info.get('trailingPE'),
                'payout_ratio': info.get('payoutRatio')
            }
            if metrics['debt_equity'] is not None and metrics['debt_equity'] > 10:
                metrics['debt_equity'] /= 100.0
            for key, value in metrics.items():
                if isinstance(value, (np.generic, np.number)): metrics[key] = value.item()
                elif pd.isna(value): metrics[key] = None
            return metrics
        except Exception as e:
            logging.warning(f"Could not compute all metrics for {stock.ticker}. Error: {e}")
            return None

    async def _fetch_single_ticker(self, ticker: str) -> Optional[Dict[str, Any]]:
        max_retries = 3
        base_wait_time = 2
        for attempt in range(max_retries):
            try:
                stock = await asyncio.to_thread(yf.Ticker, ticker, session=self.session)
                metrics = await asyncio.to_thread(self._calculate_metrics, stock)
                if not metrics: return None
                data_packet = {
                    "ticker": stock.ticker,
                    "last_update": datetime.now(timezone.utc).isoformat(),
                    "sector": stock.info.get('sector', 'N/A'),
                    "metrics": metrics
                }
                self._save_to_cache(ticker, data_packet)
                return data_packet
            except Exception as e:
                logging.warning(f"Attempt {attempt + 1} for {ticker} failed: {e}")
                if attempt < max_retries - 1:
                    wait_time = base_wait_time * (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(wait_time)
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

        tasks = [fetch_with_semaphore(t) for t in tickers_to_fetch]
        for future in asyncio.as_completed(tasks):
            if is_cancelled():
                for task in tasks: task.cancel()
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
        try:
            stock = await asyncio.to_thread(yf.Ticker, ticker, session=self.session)
            return await asyncio.to_thread(lambda: stock.info)
        except Exception as e:
            logging.error(f"Failed to fetch full info for {ticker}: {e}")
            return None