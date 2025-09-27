# data_loader.py
# Responsible for downloading and caching stock data.

import json
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from pathlib import Path
import logging
import asyncio
import functools
import random
import re
from typing import List, Dict, Callable, Optional, Tuple
from io import StringIO

# Assuming config.py is in the same directory
import config
from ticker_utils import normalize_ticker_for_yfinance

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

FAILURE_CACHE_EXPIRY_HOURS = getattr(config, "FAILED_HISTORY_CACHE_EXPIRY_HOURS", 6)
_last_failed_tickers: Dict[str, Dict[str, str]] = {}


_NON_RETRIABLE_ERROR_SNIPPETS = (
    "no data",  # generic catch-all for a variety of yfinance messages
    "no timezone",
    "no price",
    "No objects to concatenate",
    "not found",
    "delisted",
)


def _period_to_timedelta(period: str) -> timedelta:
    """Best-effort conversion of a yfinance period string to a timedelta."""
    if not isinstance(period, str):
        return timedelta(days=365)

    match = re.fullmatch(r"(\d+)([a-zA-Z]+)", period.strip())
    if not match:
        return timedelta(days=365)

    value, unit = match.groups()
    amount = int(value)
    unit = unit.lower()

    if unit in {"d", "day", "days"}:
        return timedelta(days=amount)
    if unit in {"wk", "w", "week", "weeks"}:
        return timedelta(weeks=amount)
    if unit in {"mo", "m", "month", "months"}:
        # Approximate a calendar month as 30 days; sufficient for logging/debugging.
        return timedelta(days=30 * amount)
    if unit in {"y", "yr", "yrs", "year", "years"}:
        return timedelta(days=365 * amount)

    return timedelta(days=365)


def _build_history_kwargs(period: str | None) -> Dict[str, object]:
    """Centralise kwargs used for yfinance history downloads."""
    kwargs: Dict[str, object] = {
        "interval": "1d",
        "auto_adjust": False,
        "actions": False,
        "prepost": False,
    }
    if period:
        kwargs["period"] = period
    return kwargs


async def fetch_history(ticker, period='1y', retries=2, backoff=1) -> Tuple[pd.DataFrame | None, Dict[str, str] | None]:
    """Robust, truly asynchronous yfinance fetch that surfaces failure details."""
    loop = asyncio.get_running_loop()
    fetch_ticker = normalize_ticker_for_yfinance(ticker)
    last_error: str | None = None
    attempts = 0

    history_kwargs = _build_history_kwargs(period)
    fallback_context: Dict[str, str] | None = None

    for attempt in range(retries + 1):
        attempts = attempt + 1
        try:
            download_func = functools.partial(
                yf.download,
                fetch_ticker,
                progress=False,
                threads=False,
                **history_kwargs,
            )
            df = await loop.run_in_executor(None, download_func)

            if df is not None and not df.empty:
                logger.debug("yf.download OK for %s", ticker)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df.dropna(inplace=True)
                return df, None

            last_error = "Empty dataframe returned from yf.download"
            logger.info(
                "yf.download returned empty dataframe for %s with params %s",
                ticker,
                history_kwargs,
            )

            ticker_func = functools.partial(yf.Ticker, fetch_ticker)
            tk = await loop.run_in_executor(None, ticker_func)
            history_func = functools.partial(tk.history, **history_kwargs)
            df2 = await loop.run_in_executor(None, history_func)

            if df2 is not None and not df2.empty:
                logger.debug("Ticker.history OK for %s", ticker)
                if isinstance(df2.columns, pd.MultiIndex):
                    df2.columns = df2.columns.get_level_values(0)
                df2.dropna(inplace=True)
                return df2, None

            last_error = "Empty dataframe returned from Ticker.history"
            logger.info(
                "Ticker.history returned empty dataframe for %s with params %s",
                ticker,
                history_kwargs,
            )

            if period:
                fallback_end = (datetime.utcnow() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                fallback_start = fallback_end - _period_to_timedelta(period)
                if fallback_start >= fallback_end:
                    fallback_start = fallback_end - timedelta(days=7)
                fallback_kwargs = _build_history_kwargs(None)
                fallback_kwargs.update({
                    "start": fallback_start,
                    "end": fallback_end,
                })
                fallback_context = {
                    "fallback_start": fallback_start.isoformat(),
                    "fallback_end": fallback_end.isoformat(),
                }
                logger.debug(
                    "Retrying %s with explicit date range start=%s end=%s",
                    ticker,
                    fallback_start,
                    fallback_end,
                )
                history_func = functools.partial(tk.history, **fallback_kwargs)
                df3 = await loop.run_in_executor(None, history_func)

                if df3 is not None and not df3.empty:
                    logger.debug("Ticker.history fallback OK for %s", ticker)
                    if isinstance(df3.columns, pd.MultiIndex):
                        df3.columns = df3.columns.get_level_values(0)
                    df3.dropna(inplace=True)
                    return df3, None

                last_error = "Empty dataframe returned from Ticker.history (explicit range)"
                logger.info(
                    "Ticker.history explicit-range fallback empty for %s with params %s",
                    ticker,
                    fallback_kwargs,
                )

        except Exception as e:
            last_error = str(e) or repr(e)
            log_message = (
                f"yfinance fetch error for {ticker} (attempt {attempt + 1}) "
                f"using symbol {fetch_ticker}: {last_error}"
            )
            if _is_non_retriable_error(last_error):
                logger.info(log_message)
            else:
                logger.warning(log_message)

        if attempt < retries:
            if last_error and _is_non_retriable_error(last_error):
                logger.debug("Encountered non-retriable error for %s; aborting retries.", ticker)
                break
            wait_time = backoff * (2 ** attempt) + random.uniform(0.2, 0.8)
            logger.debug(
                "Fetch failed for %s. Retrying in %.2f seconds (attempt %d/%d).",
                ticker,
                wait_time,
                attempt + 1,
                retries + 1,
            )
            await asyncio.sleep(wait_time)

    failure_details = {
        "reason": last_error or "No data returned from yfinance",
        "symbol": fetch_ticker,
        "attempts": attempts,
        "non_retriable": bool(last_error and _is_non_retriable_error(last_error)),
        "requested_period": period,
    }
    if fallback_context:
        failure_details.update(fallback_context)
    logger.warning(f"No data for ticker '{ticker}' after {attempts} attempt(s). Reason: {failure_details['reason']}")
    return None, failure_details


def _is_non_retriable_error(message: str) -> bool:
    normalized = message.lower()
    return any(snippet in normalized for snippet in _NON_RETRIABLE_ERROR_SNIPPETS)


def _get_failure_marker_path(ticker: str) -> Path:
    safe_name = ticker.replace('^', 'INDEX-')
    return config.CACHE_DIR / f"{safe_name}.failed.json"


def _load_failure_marker(marker_path: Path) -> Dict[str, str] | None:
    try:
        with marker_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Failed to load failure marker %s: %s", marker_path, exc)
        return None


def _register_failed_ticker(ticker: str, details: Dict[str, str]) -> None:
    global _last_failed_tickers
    _last_failed_tickers[ticker] = details


async def _persist_failure_marker(ticker: str, details: Dict[str, str]) -> None:
    marker_path = _get_failure_marker_path(ticker)
    marker_data = details.copy()
    marker_data["timestamp"] = datetime.utcnow().isoformat()

    def _write():
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        with marker_path.open("w", encoding="utf-8") as handle:
            json.dump(marker_data, handle)

    await asyncio.to_thread(_write)


async def _clear_failure_marker(ticker: str) -> None:
    marker_path = _get_failure_marker_path(ticker)

    def _remove():
        try:
            marker_path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.debug("Failed to remove failure marker %s: %s", marker_path, exc)

    await asyncio.to_thread(_remove)


def get_last_failed_tickers() -> Dict[str, Dict[str, str]]:
    return dict(_last_failed_tickers)


def ensure_cache_dir_exists():
    """Creates the cache directory if it doesn't exist."""
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logging.error(f"Failed to create cache directory {config.CACHE_DIR}: {e}")
        raise

import requests

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
        tables = pd.read_html(StringIO(response.text))
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

async def _fetch_single_ticker_history(ticker: str) -> Tuple[pd.DataFrame | None, Dict[str, str] | None]:
    """
    Asynchronously downloads historical data for a single stock using the robust
    fetch_history wrapper.
    """
    # fetch_history is now an async function and can be awaited directly.
    # It handles its own retries and runs blocking I/O in an executor internally.
    return await fetch_history(ticker=ticker, period=config.DATA_PERIOD)

async def get_historical_data_for_tickers(
    tickers: List[str],
    progress_callback: Optional[Callable] = None,
    is_cancelled_callback: Optional[Callable] = None
) -> Dict[str, pd.DataFrame]:
    """
    Asynchronously retrieves historical data for a list of tickers.
    """
    global _last_failed_tickers
    ensure_cache_dir_exists()
    is_cancelled = is_cancelled_callback or (lambda: False)
    results: Dict[str, pd.DataFrame] = {}
    tickers_to_fetch: list[str] = []
    failed_tickers: Dict[str, Dict[str, str]] = {}
    _last_failed_tickers = {}
    cache_hits = 0
    for ticker in tickers:
        if is_cancelled(): break
        cache_file = config.CACHE_DIR / f"{ticker.replace('^', 'INDEX-')}.csv"
        failure_marker = _get_failure_marker_path(ticker)
        marker_payload = _load_failure_marker(failure_marker)
        if marker_payload:
            timestamp_str = marker_payload.get("timestamp")
            marker_time = None
            if timestamp_str:
                try:
                    marker_time = datetime.fromisoformat(timestamp_str)
                except ValueError:
                    marker_time = None

            if marker_time and datetime.utcnow() - marker_time < timedelta(hours=FAILURE_CACHE_EXPIRY_HOURS):
                failure_record = {
                    "reason": marker_payload.get("reason", "Unknown error"),
                    "symbol": marker_payload.get("symbol", ticker),
                    "attempts": marker_payload.get("attempts", 0),
                    "cached": True,
                    "last_attempt_utc": timestamp_str,
                }
                failed_tickers[ticker] = failure_record
                _register_failed_ticker(ticker, failure_record)
                if progress_callback:
                    progress_callback.emit(
                        f"Skipping {ticker}: cached fetch failure ({failure_record['reason']})."
                    )
                continue

            # Marker is stale; clean it up asynchronously
            await _clear_failure_marker(ticker)

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
            if is_cancelled():
                return ticker, None
            try:
                data, failure_details = await _fetch_single_ticker_history(ticker)
                if data is not None and not data.empty:
                    cache_file = config.CACHE_DIR / f"{ticker.replace('^', 'INDEX-')}.csv"
                    await asyncio.to_thread(data.to_csv, cache_file)
                    await _clear_failure_marker(ticker)
                    return ticker, data

                if failure_details:
                    failure_record = {
                        **failure_details,
                        "cached": False,
                        "last_attempt_utc": datetime.utcnow().isoformat(),
                    }
                    failed_tickers[ticker] = failure_record
                    _register_failed_ticker(ticker, failure_record)
                    await _persist_failure_marker(ticker, failure_record)
                    if progress_callback:
                        progress_callback.emit(
                            f"No price data for {ticker}: {failure_record['reason']}"
                        )
                return ticker, None
            except Exception as e:
                logging.error(f"Error fetching or caching historical data for {ticker}: {e}", exc_info=True)
                return ticker, None
    tasks = [asyncio.create_task(fetch_and_cache(t)) for t in tickers_to_fetch]

    fetched_count = 0
    for future in asyncio.as_completed(tasks):
        if is_cancelled():
            for task in tasks:
                task.cancel()
            break
        try:
            ticker, data = await future
            if data is not None:
                results[ticker] = data
            fetched_count += 1
            if progress_callback: progress_callback.emit(f"Fetched historical data for {ticker} ({fetched_count}/{len(tickers_to_fetch)})")
        except asyncio.CancelledError: pass

    if failed_tickers and progress_callback:
        progress_callback.emit(
            f"Price data unavailable for {len(failed_tickers)} ticker(s)."
        )
    return results

async def get_stock_data(ticker: str) -> pd.DataFrame | None:
    """(DEPRECATED) Wrapper for single ticker fetching."""
    result_dict = await get_historical_data_for_tickers([ticker])
    return result_dict.get(ticker)