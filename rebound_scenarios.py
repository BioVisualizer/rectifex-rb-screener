# rebound_scenarios.py
# Contains the logic for different screening scenarios.

import pandas as pd
import numpy as np
import logging
from typing import List, Dict, Any, Callable
import json
from datetime import datetime, timedelta
import yfinance as yf

# App-specific imports
import config
import data_loader
from data_structures import ReboundCandidate
from fundamental_fetcher import FundamentalFetcher

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# --- Helper Functions (moved from analysis.py) ---

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

def passes_market_context_filter(index_data: pd.DataFrame) -> bool:
    """Checks if the market index is in a positive trend (above its 50-day SMA)."""
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

def get_ticker_info_cached(ticker: str) -> dict | None:
    """
    Gets basic info for a ticker (like name, market cap), using a local JSON cache.
    """
    info_cache_dir = config.CACHE_DIR / "info"
    info_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = info_cache_dir / f"{ticker}.json"
    if cache_file.exists():
        mod_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if datetime.now() - mod_time < timedelta(hours=config.CACHE_EXPIRY_HOURS):
            try:
                with open(cache_file, 'r') as f:
                    return json.load(f)
            except Exception:
                pass # Cache is corrupt, refetch
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        if not info or info.get('marketCap') is None:
            return None
        with open(cache_file, 'w') as f:
            json.dump(info, f)
        return info
    except Exception:
        return None

def passes_liquidity_filter(ticker_info: dict) -> bool:
    """Checks if a stock meets the minimum market cap and volume requirements."""
    if not ticker_info:
        return False
    market_cap = ticker_info.get('marketCap', 0)
    avg_volume = ticker_info.get('averageVolume', 0)
    if market_cap and avg_volume and market_cap > config.MIN_MARKET_CAP and avg_volume > config.MIN_AVG_VOLUME_30D:
        return True
    return False


class ScenarioRunner:
    """
    Orchestrates the filtering process for different rebound scenarios.
    """
    def __init__(self, fundamental_fetcher: FundamentalFetcher, progress_callback: Callable = None, progress_percent_callback: Callable = None):
        self.fetcher = fundamental_fetcher
        self.progress_callback = progress_callback
        self.progress_percent_callback = progress_percent_callback

    def _emit_progress(self, message: str):
        logging.info(message)
        if self.progress_callback:
            self.progress_callback.emit(message)

    def _emit_percent(self, percent: int):
        if self.progress_percent_callback:
            self.progress_percent_callback.emit(percent)

    def _check_classic_signal(self, data: pd.DataFrame) -> tuple[bool, float, float, float]:
        """Checks for the 'Classic Oversold' signal."""
        data['RSI'] = calculate_rsi(data['Close'], config.RSI_PERIOD)
        data['SMA200'] = calculate_sma(data['Close'], config.SMA_SUPPORT_PERIOD)
        data['Low90D'] = data['Low'].rolling(window=config.LOWEST_LOW_PERIOD).min()

        latest_data = data.iloc[-1]
        current_price = latest_data['Close']
        rsi = latest_data['RSI']
        sma200 = latest_data['SMA200']
        low90d = latest_data['Low90D']

        if pd.isna(current_price) or pd.isna(rsi):
            return False, np.nan, np.nan, np.nan

        dist_to_sma = ((current_price - sma200) / sma200) * 100 if pd.notna(sma200) and sma200 > 0 else np.inf
        dist_to_low = ((current_price - low90d) / low90d) * 100 if pd.notna(low90d) and low90d > 0 else np.inf

        is_candidate_A = rsi < config.RSI_OVERSOLD_STRONG
        is_candidate_B = False
        if rsi < config.RSI_OVERSOLD_WEAK:
            if 0 <= dist_to_sma <= config.SUPPORT_PROXIMITY_THRESHOLD or \
               0 <= dist_to_low <= config.SUPPORT_PROXIMITY_THRESHOLD:
                is_candidate_B = True

        return is_candidate_A or is_candidate_B, rsi, dist_to_sma, dist_to_low

    def _calculate_classic_score(self, rsi: float, dist_to_sma: float, dist_to_low: float) -> tuple[int, int, int]:
        """
        Calculates the rebound score and its components.
        Returns the final score, the RSI sub-score, and the proximity sub-score.
        """
        rsi_score = max(0, (config.RSI_SCORE_CEILING - rsi) * (100 / (config.RSI_SCORE_CEILING - config.RSI_OVERSOLD_STRONG)))
        rsi_score = int(min(100, rsi_score))

        prox_dist = np.inf
        if dist_to_sma >= 0: prox_dist = min(prox_dist, dist_to_sma)
        if dist_to_low >= 0: prox_dist = min(prox_dist, dist_to_low)

        if prox_dist > config.PROXIMITY_SCORE_CEILING:
            proximity_score = 0
        else:
            proximity_score = (config.PROXIMITY_SCORE_CEILING - prox_dist) * (100 / config.PROXIMITY_SCORE_CEILING)
        proximity_score = int(max(0, min(100, proximity_score)))

        final_score = int((0.6 * rsi_score) + (0.4 * proximity_score))
        return final_score, rsi_score, proximity_score

    def run_classic_oversold(self) -> List[ReboundCandidate]:
        """
        Implements the existing logic: Oversold RSI, near 200-SMA and 90-day-low.
        Returns a list of ReboundCandidate objects.
        """
        self._emit_progress("Starting 'Classic Oversold' scan...")
        all_tickers = data_loader.get_all_tickers()
        all_candidates = []
        total_tickers = sum(len(t) for t in all_tickers.values())
        processed_tickers = 0

        index_data_cache = {}
        market_context_ok = {}

        for market, tickers in all_tickers.items():
            self._emit_progress(f"--- Processing Market: {market} ---")
            index_ticker = next((d['index_ticker'] for d in config.INDICES.values() if d['market'] == market), None)
            if not index_ticker:
                self._emit_progress(f"No index ticker for market '{market}'. Skipping.")
                processed_tickers += len(tickers)
                continue

            if index_ticker not in index_data_cache:
                index_data_cache[index_ticker] = data_loader.get_stock_data(index_ticker)
                market_context_ok[market] = passes_market_context_filter(index_data_cache[index_ticker])

            if not market_context_ok.get(market, False):
                self._emit_progress(f"Market context for {market} is bearish. Skipping tickers.")
                processed_tickers += len(tickers)
                continue

            self._emit_progress(f"Market context for {market} is bullish. Analyzing {len(tickers)} tickers.")
            for ticker in tickers:
                processed_tickers += 1
                self._emit_percent(int((processed_tickers / total_tickers) * 100))
                self._emit_progress(f"Analyzing [{processed_tickers}/{total_tickers}] {ticker}")

                stock_info = get_ticker_info_cached(ticker)
                if not passes_liquidity_filter(stock_info):
                    continue

                stock_data = data_loader.get_stock_data(ticker)
                if stock_data is None or stock_data.empty:
                    continue

                is_candidate, rsi, dist_sma, dist_low = self._check_classic_signal(stock_data.copy())
                if not is_candidate:
                    continue

                self._emit_progress(f"!!! {ticker} is a potential 'Classic Oversold' candidate!")
                score, rsi_score, prox_score = self._calculate_classic_score(rsi, dist_sma, dist_low)

                candidate = ReboundCandidate(
                    ticker=ticker,
                    scenario="Classic Oversold",
                    score=score,
                    technicals={
                        'price': round(stock_data['Close'].iloc[-1], 2),
                        'rsi': round(rsi, 2),
                        'dist_sma_200': round(dist_sma, 2),
                        'dist_low_90d': round(dist_low, 2),
                        'rsi_score': rsi_score,
                        'prox_score': prox_score
                    },
                    fundamentals={'name': stock_info.get('shortName', 'N/A')}
                )
                all_candidates.append(candidate)

        self._emit_progress(f"Scan complete. Found {len(all_candidates)} candidates.")
        return all_candidates

    async def run_quality_pullback(self) -> List[ReboundCandidate]:
        """
        Implements the new 'Quality Stock Pullback' scenario logic.
        Returns a list of ReboundCandidate objects.
        """
        self._emit_progress("Starting 'Quality Stock Pullback' scan...")
        all_tickers_by_market = data_loader.get_all_tickers()
        all_candidates = []
        total_tickers = sum(len(t) for t in all_tickers_by_market.values())
        processed_tickers = 0

        index_data_cache = {}
        market_context_ok = {}

        for market, tickers in all_tickers_by_market.items():
            self._emit_progress(f"--- Processing Market: {market} ---")

            # 1. Market Context Filter
            index_ticker = next((d['index_ticker'] for d in config.INDICES.values() if d['market'] == market), None)
            if not index_ticker:
                self._emit_progress(f"No index ticker for market '{market}'. Skipping.")
                processed_tickers += len(tickers)
                continue

            if index_ticker not in index_data_cache:
                index_data_cache[index_ticker] = data_loader.get_stock_data(index_ticker)
                market_context_ok[market] = passes_market_context_filter(index_data_cache[index_ticker])

            if not market_context_ok.get(market, False):
                self._emit_progress(f"Market context for {market} is bearish. Skipping tickers.")
                processed_tickers += len(tickers)
                continue

            self._emit_progress(f"Market context for {market} is bullish. Analyzing {len(tickers)} tickers.")

            # 2. Technical Pre-filtering for this market
            technically_strong_tickers = []
            for ticker in tickers:
                processed_tickers += 1
                self._emit_percent(int((processed_tickers / total_tickers) * 50)) # Phase 1 up to 50%
                self._emit_progress(f"[{processed_tickers}/{total_tickers}] Tech Filter: {ticker}")

                if not passes_liquidity_filter(get_ticker_info_cached(ticker)):
                    continue

                stock_data = data_loader.get_stock_data(ticker)
                if stock_data is None or len(stock_data) < 200:
                    continue

                stock_data['SMA50'] = calculate_sma(stock_data['Close'], 50)
                stock_data['SMA200'] = calculate_sma(stock_data['Close'], 200)
                latest = stock_data.iloc[-1]

                if pd.notna(latest['Close']) and pd.notna(latest['SMA50']) and pd.notna(latest['SMA200']):
                    if latest['Close'] > latest['SMA200'] and latest['SMA50'] > latest['SMA200']:
                        technically_strong_tickers.append(ticker)

            if not technically_strong_tickers:
                self._emit_progress("No technically strong tickers found in this market.")
                continue

            # 3. Fundamental Filtering for this market
            self._emit_progress(f"Fetching fundamental data for {len(technically_strong_tickers)} strong tickers in {market}...")
            fundamental_data = await self.fetcher.get_fundamentals_for_tickers(
                technically_strong_tickers,
                progress_callback=self._emit_progress
            )

            fundamentally_strong_tickers = {}
            for ticker, data in fundamental_data.items():
                if data:
                    eps_growth = data.get('earningsGrowth', 0) or 0
                    revenue_growth = data.get('revenueGrowth', 0) or 0
                    debt_to_equity = data.get('debtToEquity', float('inf')) or float('inf')
                    if eps_growth > 0.10 and revenue_growth > 0.05 and debt_to_equity < 0.7:
                        fundamentally_strong_tickers[ticker] = data

            if not fundamentally_strong_tickers:
                self._emit_progress("No fundamentally strong tickers found in this market.")
                continue

            # 4. Final Signal Filter (Pullback) for this market
            self._emit_progress(f"Final check for {len(fundamentally_strong_tickers)} tickers in {market}...")
            for ticker, fund_data in fundamentally_strong_tickers.items():
                # No need to increment processed_tickers again, just use the percentage for the second half
                self._emit_percent(50 + int((processed_tickers / total_tickers) * 50))

                stock_data = data_loader.get_stock_data(ticker)
                if stock_data is None: continue

                stock_data['SMA50'] = calculate_sma(stock_data['Close'], 50)
                latest = stock_data.iloc[-1]
                current_price = latest['Close']
                sma50 = latest['SMA50']

                if pd.notna(current_price) and pd.notna(sma50) and sma50 > 0:
                    dist_to_sma50 = abs((current_price - sma50) / sma50) * 100
                    if dist_to_sma50 <= 3.0:
                        self._emit_progress(f"!!! {ticker} is a potential 'Quality Pullback' candidate!")
                        prox_score = 100 - (dist_to_sma50 / 3.0 * 100)
                        fund_score = (fund_data.get('earningsGrowth', 0) * 100)
                        score = int(0.7 * prox_score + 0.3 * fund_score)

                        candidate = ReboundCandidate(
                            ticker=ticker, scenario="Quality Stock Pullback", score=min(100, score),
                            technicals={
                                'price': round(current_price, 2),
                                '50_sma_value': round(sma50, 2),
                                'dist_sma_50': round(dist_to_sma50, 2)
                            },
                            fundamentals=fund_data
                        )
                        all_candidates.append(candidate)

        self._emit_progress(f"Scan complete. Found {len(all_candidates)} candidates.")
        return all_candidates
