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

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Constants ---
CACHE_DIR = Path.home() / ".local" / "share" / "rectifex" / "cache"
FUNDAMENTALS_DIR = CACHE_DIR / "fundamentals"
SECTOR_MEDIANS_FILE = CACHE_DIR / "sector_medians.json"
CACHE_EXPIRY_DAYS = 7


class FundamentalDataHandler:
    """
    Handles fetching, caching, and processing of fundamental stock data.
    Implements the logic as per the September 2025 spec.
    """

    def __init__(self):
        FUNDAMENTALS_DIR.mkdir(parents=True, exist_ok=True)

    def _get_cached_data(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Checks for and loads non-expired cached data for a ticker."""
        cache_file = FUNDAMENTALS_DIR / f"{ticker}.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    data = json.load(f)
                last_update = datetime.fromisoformat(data['last_update'])
                if datetime.now(timezone.utc) - last_update < timedelta(days=CACHE_EXPIRY_DAYS):
                    logging.info(f"Loading fundamentals for {ticker} from cache.")
                    return data
            except (json.JSONDecodeError, KeyError, Exception) as e:
                logging.warning(f"Cache for {ticker} is corrupt or invalid, refetching. Error: {e}")
        return None

    def _save_to_cache(self, ticker: str, data: Dict[str, Any]):
        """Saves fundamental data to a JSON cache file."""
        cache_file = FUNDAMENTALS_DIR / f"{ticker}.json"
        try:
            with open(cache_file, 'w') as f:
                json.dump(data, f, indent=2)
            logging.info(f"Saved fundamentals for {ticker} to cache.")
        except Exception as e:
            logging.error(f"Failed to save fundamental cache for {ticker}. Error: {e}")

    def _calculate_metrics(self, stock: yf.Ticker) -> Optional[Dict[str, Any]]:
        """
        Calculates all required fundamental metrics from raw yfinance data.
        Returns a dictionary of metrics, or None if essential data is missing.
        """
        info = stock.info
        if not info or info.get('marketCap') is None:
            logging.warning(f"No valid basic info for {stock.ticker}")
            return None

        # Use TTM financials where possible
        financials = stock.financials
        balance_sheet = stock.balance_sheet
        cashflow = stock.cashflow

        if financials.empty or balance_sheet.empty or cashflow.empty:
            logging.warning(f"Financial statements are missing for {stock.ticker}")
            return None

        metrics = {}
        try:
            # --- Revenue & EPS Growth ---
            total_revenue = financials.loc['Total Revenue']
            if len(total_revenue.dropna()) >= 4:
                start_rev = total_revenue.iloc[3]
                end_rev = total_revenue.iloc[0]
                if start_rev and start_rev > 0 and end_rev:
                    metrics['revenue_3yr_cagr'] = ((end_rev / start_rev) ** (1/3)) - 1
            else:
                metrics['revenue_3yr_cagr'] = info.get('revenueGrowth') # Fallback

            basic_eps = financials.loc['Basic EPS']
            if len(basic_eps.dropna()) >= 2:
                start_eps = basic_eps.iloc[1]
                end_eps = basic_eps.iloc[0]
                if start_eps and start_eps != 0:
                    metrics['eps_1y_growth'] = (end_eps - start_eps) / abs(start_eps)
            else:
                metrics['eps_1y_growth'] = info.get('earningsGrowth') # Fallback

            # --- Margins and Ratios ---
            metrics['net_margin'] = info.get('profitMargins')
            metrics['roe'] = info.get('returnOnEquity')
            metrics['debt_equity'] = info.get('debtToEquity')
            if metrics['debt_equity'] is not None and metrics['debt_equity'] > 10:
                metrics['debt_equity'] /= 100.0 # Correction for yfinance sometimes returning %

            # --- Valuation & Yield ---
            fcf = cashflow.loc['Free Cash Flow'].iloc[0] if 'Free Cash Flow' in cashflow.index else info.get('freeCashflow')
            mcap = info.get('marketCap')
            if fcf and mcap:
                metrics['free_cashflow_yield'] = fcf / mcap

            metrics['pe_ttm'] = info.get('trailingPE')
            metrics['ev_ebit'] = info.get('enterpriseToEbitda') # Using EBITDA as close proxy for EBIT
            metrics['payout_ratio'] = info.get('payoutRatio')

            # --- Trends (Optional) ---
            try:
                earnings_dates = stock.earnings_dates
                if earnings_dates is not None and not earnings_dates.empty:
                    recent_4q = earnings_dates.tail(4)
                    metrics['earnings_trend_months'] = int(recent_4q[recent_4q['Surprise(%)'] > 0].shape[0])
            except Exception:
                metrics['earnings_trend_months'] = None # Gracefully fail

            # Replace numpy types with standard Python types for JSON serialization
            for key, value in metrics.items():
                if isinstance(value, (np.generic, np.number)):
                    metrics[key] = value.item()
                elif pd.isna(value):
                    metrics[key] = None

            return metrics

        except (KeyError, IndexError, TypeError, Exception) as e:
            logging.warning(f"Could not compute all metrics for {stock.ticker}. Error: {e}")
            return None

    async def _fetch_single_ticker(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Fetches and processes fundamental data for a single ticker.
        This runs synchronous yfinance calls in a separate thread.
        """
        try:
            logging.info(f"Fetching fundamentals for {ticker} from yfinance.")
            stock = await asyncio.to_thread(yf.Ticker, ticker)

            # Run all blocking calls in a thread
            metrics = await asyncio.to_thread(self._calculate_metrics, stock)

            if not metrics:
                return None

            # Final structure as per spec
            data_packet = {
                "ticker": stock.ticker,
                "last_update": datetime.now(timezone.utc).isoformat(),
                "sector": stock.info.get('sector', 'N/A'),
                "metrics": metrics
            }

            self._save_to_cache(ticker, data_packet)
            return data_packet

        except Exception as e:
            logging.error(f"Failed to fetch or process data for {ticker}: {e}", exc_info=False)
            return None

    async def get_fundamentals_for_tickers(self, tickers: List[str],
                                           progress_callback: Optional[Callable] = None,
                                           is_cancelled_callback: Optional[Callable] = None) -> Dict[str, Dict]:
        """
        Asynchronously retrieves fundamental data for a list of tickers, using cache first.
        A semaphore limits concurrency to avoid rate-limiting.
        """
        is_cancelled = is_cancelled_callback if is_cancelled_callback else lambda: False
        results = {}
        tickers_to_fetch = []
        semaphore = asyncio.Semaphore(3)  # Limit concurrent yfinance requests

        for ticker in tickers:
            cached_data = self._get_cached_data(ticker)
            if cached_data:
                results[ticker] = cached_data
            else:
                tickers_to_fetch.append(ticker)

        if not tickers_to_fetch:
            if progress_callback: progress_callback("All fundamental data loaded from cache.")
            return results

        async def fetch_with_semaphore(ticker: str):
            async with semaphore:
                if is_cancelled(): return ticker, None
                await asyncio.sleep(random.uniform(0.1, 0.3)) # Small random delay
                data = await self._fetch_single_ticker(ticker)
                return ticker, data

        tasks = [fetch_with_semaphore(t) for t in tickers_to_fetch]
        fetched_count = 0
        total_to_fetch = len(tickers_to_fetch)

        for future in asyncio.as_completed(tasks):
            if is_cancelled():
                logging.info("Fundamental fetch cancelled. Aborting remaining tasks.")
                for task in tasks: task.cancel()
                break

            try:
                ticker, data = await future
                if data:
                    results[ticker] = data

                fetched_count += 1
                if progress_callback:
                    progress_callback(f"Fetched fundamentals for {ticker} ({fetched_count}/{total_to_fetch})")
            except asyncio.CancelledError:
                pass

        return results

    def compute_and_save_sector_medians(self):
        """
        Reads all cached fundamental files, computes medians for key metrics
        per sector, and saves them to a JSON file.
        """
        logging.info("Computing sector medians from cached data...")
        all_metrics = []
        for file_path in FUNDAMENTALS_DIR.glob("*.json"):
            try:
                with open(file_path, 'r') as f:
                    data = json.load(f)
                if 'sector' in data and data['sector'] != 'N/A' and 'metrics' in data:
                    metrics = data['metrics']
                    metrics['sector'] = data['sector']
                    all_metrics.append(metrics)
            except (json.JSONDecodeError, KeyError, Exception) as e:
                logging.warning(f"Could not process cache file {file_path.name}: {e}")

        if not all_metrics:
            logging.warning("No valid fundamental data found to compute sector medians.")
            return

        df = pd.DataFrame(all_metrics)

        # Metrics to compute medians for, as per spec
        median_metrics = [
            'revenue_3yr_cagr', 'eps_1y_growth', 'net_margin', 'roe',
            'debt_equity', 'free_cashflow_yield', 'pe_ttm', 'ev_ebit'
        ]

        # Ensure columns exist, fill missing with NaN
        for metric in median_metrics:
            if metric not in df.columns:
                df[metric] = np.nan

        # Calculate medians and standard deviations
        grouped = df.groupby('sector')[median_metrics]
        sector_medians = grouped.median()
        sector_std_devs = grouped.std()

        # Combine into the desired final structure
        output_data = {}
        for sector in sector_medians.index:
            medians = sector_medians.loc[sector].replace(np.nan, None).to_dict()
            std_devs = sector_std_devs.loc[sector].replace(np.nan, None).to_dict()
            output_data[sector] = {
                "medians": medians,
                "std_devs": std_devs
            }

        try:
            with open(SECTOR_MEDIANS_FILE, 'w') as f:
                json.dump(output_data, f, indent=2)
            logging.info(f"Successfully saved sector stats (medians, stddevs) to {SECTOR_MEDIANS_FILE}")
        except Exception as e:
            logging.error(f"Failed to save sector stats file: {e}")

# --- Example Usage (for testing) ---
import random

async def main():
    print("--- Testing FundamentalDataHandler ---")
    handler = FundamentalDataHandler()

    # Clean cache for a clean test run on some tickers
    test_tickers = ['AAPL', 'MSFT', 'GOOGL', 'JPM', 'NONEXISTENTTICKER', 'TM']
    for ticker in test_tickers:
        (FUNDAMENTALS_DIR / f"{ticker}.json").unlink(missing_ok=True)

    def progress_reporter(msg: str):
        print(f"PROGRESS: {msg}")

    print(f"\nFetching data for: {test_tickers}")
    fundamental_data = await handler.get_fundamentals_for_tickers(
        test_tickers,
        progress_callback=progress_reporter
    )

    print("\n--- Results ---")
    for ticker, data in fundamental_data.items():
        print(f"\n{ticker}:")
        if data:
            print(f"  Sector: {data.get('sector')}")
            print("  Metrics:")
            for key, value in data.get('metrics', {}).items():
                # Format floats to 4 decimal places for cleaner output
                val_str = f"{value:.4f}" if isinstance(value, float) else str(value)
                print(f"    {key:<22}: {val_str}")
        else:
            print("  No data found.")

    print("\n--- Testing Sector Median Calculation ---")
    handler.compute_and_save_sector_medians()
    if SECTOR_MEDIANS_FILE.exists():
        with open(SECTOR_MEDIANS_FILE, 'r') as f:
            print("Medians file content:")
            print(f.read())

    print("\n--- Test complete ---")


if __name__ == "__main__":
    asyncio.run(main())
