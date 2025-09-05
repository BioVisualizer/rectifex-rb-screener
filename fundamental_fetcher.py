import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Callable
import logging

import yfinance as yf

# Assuming config.py is in the same directory and defines CACHE_DIR
import config

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class FundamentalFetcher:
    """
    Asynchronously fetches and caches fundamental data for stock tickers.
    """
    def __init__(self):
        self.cache_dir = config.CACHE_DIR / "fundamentals"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # We don't need an API key for yfinance
        # self.api_key = api_key

    def _get_cached_data(self, ticker: str) -> Dict[str, Any] | None:
        """Checks for and loads non-expired cached data for a ticker."""
        cache_file = self.cache_dir / f"{ticker}.json"
        if cache_file.exists():
            mod_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
            if datetime.now() - mod_time < timedelta(hours=config.CACHE_EXPIRY_HOURS):
                logging.info(f"Loading fundamentals for {ticker} from cache.")
                try:
                    with open(cache_file, 'r') as f:
                        return json.load(f)
                except Exception as e:
                    logging.warning(f"Could not read fundamental cache for {ticker}, refetching. Error: {e}")
        return None

    def _save_to_cache(self, ticker: str, data: Dict[str, Any]):
        """Saves fundamental data to a JSON cache file."""
        cache_file = self.cache_dir / f"{ticker}.json"
        try:
            with open(cache_file, 'w') as f:
                json.dump(data, f)
            logging.info(f"Saved fundamentals for {ticker} to cache.")
        except Exception as e:
            logging.error(f"Failed to save fundamental cache for {ticker}. Error: {e}")

    async def _fetch_single_ticker_data(self, ticker: str) -> Dict[str, Any] | None:
        """
        Fetches fundamental data for a single ticker using yfinance.
        This runs the synchronous yfinance call in a separate thread.
        """
        logging.info(f"Fetching fundamentals for {ticker} from yfinance.")
        try:
            # yf.Ticker is a synchronous call, so we run it in an executor
            stock = await asyncio.to_thread(yf.Ticker, ticker)
            info = await asyncio.to_thread(getattr, stock, 'info')

            if not info or info.get('marketCap') is None:
                logging.warning(f"No valid fundamental info returned for {ticker}")
                return None

            # Extract only the required fields
            required_fields = {
                'trailingEps': info.get('trailingEps'),
                'revenueGrowth': info.get('revenueGrowth'),
                'debtToEquity': info.get('debtToEquity'),
                'earningsGrowth': info.get('earningsGrowth'),
                # Add other fields as needed
            }
            return required_fields

        except Exception as e:
            logging.warning(f"Could not download fundamental info for {ticker}: {e}")
            return None

    async def get_fundamentals_for_tickers(self, tickers: List[str], progress_callback: Any = None, is_cancelled_callback: Callable = None) -> Dict[str, Dict]:
        """
        Asynchronously retrieves fundamental data for a list of tickers,
        providing progress updates along the way.
        """
        is_cancelled = is_cancelled_callback if is_cancelled_callback else lambda: False
        results = {}
        tickers_to_fetch = []

        # First, check cache for all tickers
        for ticker in tickers:
            cached_data = self._get_cached_data(ticker)
            if cached_data:
                results[ticker] = cached_data
            else:
                tickers_to_fetch.append(ticker)

        if not tickers_to_fetch:
            return results

        # Wrapper to ensure we always have the ticker context, even on error
        async def fetch_with_context(ticker: str):
            try:
                data = await self._fetch_single_ticker_data(ticker)
                return ticker, data, None
            except Exception as e:
                return ticker, None, e

        # Create tasks using the wrapper
        tasks = [fetch_with_context(t) for t in tickers_to_fetch]

        fetched_count = 0
        total_to_fetch = len(tickers_to_fetch)

        for future in asyncio.as_completed(tasks):
            if is_cancelled():
                logging.info("Async fetch cancelled by user. Remaining tasks will be abandoned.")
                break

            try:
                ticker, data, error = await future

                if error:
                    logging.error(f"Error fetching fundamental data for {ticker}: {error}")
                elif data:
                    results[ticker] = data
                    self._save_to_cache(ticker, data)

                fetched_count += 1
                if progress_callback:
                    # Provide a generic progress update or one with the ticker
                    progress_callback(f"Fetched fundamentals for {ticker} ({fetched_count}/{total_to_fetch})")
            except asyncio.CancelledError:
                logging.info("A fetch task was cancelled.")


        return results

# Example usage (for testing)
async def main():
    print("--- Testing FundamentalFetcher ---")
    fetcher = FundamentalFetcher()
    test_tickers = ['AAPL', 'MSFT', 'GOOGL', 'NONEXISTENTTICKER']

    print(f"\nFetching data for: {test_tickers}")
    fundamental_data = await fetcher.get_fundamentals_for_tickers(test_tickers)

    print("\n--- Results ---")
    for ticker, data in fundamental_data.items():
        print(f"\n{ticker}:")
        if data:
            for key, value in data.items():
                print(f"  {key}: {value}")
        else:
            print("  No data found.")

    print("\n--- Test complete ---")


if __name__ == "__main__":
    # Ensure config is loaded for standalone run
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Could not create cache dir: {e}")

    asyncio.run(main())
