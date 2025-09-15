# rebound_scenarios.py
# Contains the logic for different screening scenarios.

import pandas as pd
import numpy as np
import logging
from typing import List, Dict, Any, Callable
import json
from datetime import datetime, timedelta
import yfinance as yf
from abc import ABC, abstractmethod

# App-specific imports
import config
import data_loader
from data_structures import ReboundCandidate
from fundamental_fetcher import FundamentalFetcher

# --- Indicator Functions ---

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

def calculate_bollinger_bands(data: pd.Series, window: int = 20, num_std_dev: int = 2) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calculates the Bollinger Bands."""
    if data is None or len(data) < window:
        # Return empty Series with the correct dtype to avoid issues downstream
        return pd.Series(dtype=np.float64), pd.Series(dtype=np.float64), pd.Series(dtype=np.float64)

    middle_band = calculate_sma(data, window)
    std_dev = data.rolling(window=window).std()
    upper_band = middle_band + (std_dev * num_std_dev)
    lower_band = middle_band - (std_dev * num_std_dev)

    return upper_band, middle_band, lower_band

def calculate_macd(data: pd.Series, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Calculates the Moving Average Convergence Divergence (MACD).

    Returns a tuple containing:
    - MACD line (fast_ema - slow_ema)
    - Signal line (9-period EMA of MACD line)
    - MACD Histogram (MACD line - Signal line)
    """
    if data is None or len(data) < slow_period:
        return pd.Series(dtype=np.float64), pd.Series(dtype=np.float64), pd.Series(dtype=np.float64)

    fast_ema = data.ewm(span=fast_period, adjust=False).mean()
    slow_ema = data.ewm(span=slow_period, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# --- Common Filter Functions ---

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


# --- Base Class for Scenarios ---

class BaseScenario(ABC):
    """Abstract base class for all scanning scenarios."""
    def __init__(self, name: str, fundamental_fetcher: FundamentalFetcher,
                 progress_callback: Callable, progress_percent_callback: Callable,
                 is_cancelled_callback: Callable):
        self._name = name
        self.fetcher = fundamental_fetcher
        self.progress_callback = progress_callback
        self.progress_percent_callback = progress_percent_callback
        self.is_cancelled = is_cancelled_callback

    @property
    def name(self) -> str:
        return self._name

    def _emit_progress(self, message: str):
        logging.info(message)
        if self.progress_callback:
            if hasattr(self.progress_callback, 'emit'):
                self.progress_callback.emit(message)
            else:
                self.progress_callback(message)

    def _emit_percent(self, percent: int):
        if self.progress_percent_callback:
            if hasattr(self.progress_percent_callback, 'emit'):
                self.progress_percent_callback.emit(percent)
            else:
                self.progress_percent_callback(percent)

    @abstractmethod
    async def run(self) -> List[ReboundCandidate]:
        """The main execution method for the scenario."""
        pass


# --- Concrete Scenario Implementations ---

class ClassicOversoldScenario(BaseScenario):
    """
    Implements the 'Classic Oversold' scan: Oversold RSI, near 200-SMA and 90-day-low.
    """
    def __init__(self, *args, **kwargs):
        super().__init__("Classic Oversold", *args, **kwargs)

    def _prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculates all necessary indicators and adds them to the dataframe."""
        if df is None or df.empty:
            return pd.DataFrame()
        df['RSI'] = calculate_rsi(df['Close'], config.RSI_PERIOD)
        df['SMA200'] = calculate_sma(df['Close'], config.SMA_SUPPORT_PERIOD)
        df['Low90D'] = df['Low'].rolling(window=config.LOWEST_LOW_PERIOD).min()
        return df

    def _check_signal(self, data: pd.DataFrame) -> tuple[bool, float, float, float]:
        """Checks for the 'Classic Oversold' signal."""
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

    def _calculate_score(self, rsi: float, dist_to_sma: float, dist_to_low: float) -> tuple[int, int, int]:
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

    async def run(self) -> List[ReboundCandidate]:
        self._emit_progress(f"Starting '{self.name}' scan...")
        all_tickers = data_loader.get_all_tickers()
        all_candidates = []
        total_tickers = sum(len(t) for t in all_tickers.values())
        processed_tickers = 0

        index_data_cache = {}
        market_context_ok = {}

        for market, tickers in all_tickers.items():
            if self.is_cancelled():
                break
            self._emit_progress(f"--- Processing Market: {market} ---")

            # For custom lists, we bypass the market context filter.
            if market != 'CUSTOM':
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
            else:
                self._emit_progress(f"Processing custom ticker list. Analyzing {len(tickers)} tickers.")
            for ticker in tickers:
                if self.is_cancelled():
                    self._emit_progress("Scan cancelled by user.")
                    break
                processed_tickers += 1
                self._emit_percent(int((processed_tickers / total_tickers) * 100))
                self._emit_progress(f"Analyzing [{processed_tickers}/{total_tickers}] {ticker}")

                stock_info = get_ticker_info_cached(ticker)
                if not passes_liquidity_filter(stock_info):
                    continue

                stock_data = data_loader.get_stock_data(ticker)
                if stock_data is None or stock_data.empty or len(stock_data) < config.SMA_SUPPORT_PERIOD:
                    continue

                stock_data = self._prepare_dataframe(stock_data)
                is_candidate, rsi, dist_sma, dist_low = self._check_signal(stock_data)
                if not is_candidate:
                    continue

                self._emit_progress(f"!!! {ticker} is a potential '{self.name}' candidate!")
                score, rsi_score, prox_score = self._calculate_score(rsi, dist_sma, dist_low)

                candidate = ReboundCandidate(
                    ticker=ticker,
                    scenario=self.name,
                    score=score,
                    history_df=stock_data,
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


class MeanReversionScenario(BaseScenario):
    """
    Identifies stocks trading at or below their lower Bollinger Band,
    signaling a potential "mean reversion" rebound.
    """
    def __init__(self, *args, **kwargs):
        super().__init__("Mean Reversion (Bollinger Bands)", *args, **kwargs)

    def _prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculates Bollinger Bands and adds them to the dataframe."""
        if df is None or df.empty:
            return pd.DataFrame()
        df['BB_Upper'], df['BB_Middle'], df['BB_Lower'] = calculate_bollinger_bands(df['Close'])
        return df

    def _calculate_score(self, percent_below_band: float) -> int:
        """
        Calculates score based on how far below the lower band the price is.
        The further below, the higher the score. Capped at 5% for scoring.
        """
        score_percent = min(percent_below_band, 5.0) # Cap at 5% for scoring
        # Normalize to 0-100 scale. 5% below band = score of 100.
        final_score = int((score_percent / 5.0) * 100)
        return max(0, min(100, final_score))

    async def run(self) -> List[ReboundCandidate]:
        self._emit_progress(f"Starting '{self.name}' scan...")
        all_tickers = data_loader.get_all_tickers()
        all_candidates = []
        total_tickers = sum(len(t) for t in all_tickers.values())
        processed_tickers = 0

        index_data_cache = {}
        market_context_ok = {}

        for market, tickers in all_tickers.items():
            if self.is_cancelled(): break
            self._emit_progress(f"--- Processing Market: {market} ---")

            # For custom lists, we bypass the market context filter.
            if market != 'CUSTOM':
                index_ticker = next((d['index_ticker'] for d in config.INDICES.values() if d['market'] == market), None)
                if not index_ticker:
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
            else:
                self._emit_progress(f"Processing custom ticker list. Analyzing {len(tickers)} tickers.")
            for ticker in tickers:
                if self.is_cancelled(): break
                processed_tickers += 1
                self._emit_percent(int((processed_tickers / total_tickers) * 100))
                self._emit_progress(f"Analyzing [{processed_tickers}/{total_tickers}] {ticker}")

                stock_info = get_ticker_info_cached(ticker)
                if not passes_liquidity_filter(stock_info): continue

                stock_data = data_loader.get_stock_data(ticker)
                if stock_data is None or stock_data.empty or len(stock_data) < 20: continue

                stock_data = self._prepare_dataframe(stock_data)
                latest = stock_data.iloc[-1]

                current_price = latest['Close']
                lower_band = latest['BB_Lower']

                if pd.isna(current_price) or pd.isna(lower_band) or lower_band <= 0:
                    continue

                # Signal: Price is at or below the lower Bollinger Band
                if current_price <= lower_band:
                    percent_below_band = ((lower_band - current_price) / current_price) * 100

                    self._emit_progress(f"!!! {ticker} is a potential '{self.name}' candidate!")
                    score = self._calculate_score(percent_below_band)

                    candidate = ReboundCandidate(
                        ticker=ticker,
                        scenario=self.name,
                        score=score,
                        history_df=stock_data,
                        technicals={
                            'price': round(current_price, 2),
                            'lower_band': round(lower_band, 2),
                            'percent_below_band': round(percent_below_band, 2)
                        },
                        fundamentals={'name': stock_info.get('shortName', 'N/A')}
                    )
                    all_candidates.append(candidate)

        self._emit_progress(f"Scan complete. Found {len(all_candidates)} candidates.")
        return all_candidates


class VolatilitySqueezeScenario(BaseScenario):
    """
    Identifies stocks in a 'volatility squeeze', where Bollinger Bands narrow
    significantly. This often precedes a strong price breakout.
    """
    def __init__(self, *args, **kwargs):
        super().__init__("Volatility Squeeze", *args, **kwargs)
        self.squeeze_period = 125 # ~6 months

    def _prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculates BB Width and its rolling minimum."""
        if df is None or df.empty or len(df) < self.squeeze_period:
            return pd.DataFrame()

        df['BB_Upper'], df['BB_Middle'], df['BB_Lower'] = calculate_bollinger_bands(df['Close'])
        df['BB_Width'] = (df['BB_Upper'] - df['BB_Lower']) / df['BB_Middle']
        df['BB_Width_Min'] = df['BB_Width'].rolling(window=self.squeeze_period).min()
        return df

    def _calculate_score(self, current_width: float, min_width: float) -> int:
        """
        Calculates score based on how close the current BB width is to its recent minimum.
        The closer to the minimum, the higher the score.
        """
        if pd.isna(min_width) or min_width <= 0:
            return 0

        proximity = (current_width - min_width) / min_width

        # Score based on being within 10% of the low.
        # If proximity is 0, score is 100. If 0.1 (10% above), score is 0.
        score = 100 - (proximity / 0.1 * 100)
        return int(max(0, min(100, score)))

    async def run(self) -> List[ReboundCandidate]:
        self._emit_progress(f"Starting '{self.name}' scan...")
        all_tickers = data_loader.get_all_tickers()
        all_candidates = []
        total_tickers = sum(len(t) for t in all_tickers.values())
        processed_tickers = 0

        self._emit_progress("Note: This scan ignores the general market trend filter.")

        for market, tickers in all_tickers.items():
            if self.is_cancelled(): break
            self._emit_progress(f"--- Processing Market: {market} ---")

            for ticker in tickers:
                if self.is_cancelled(): break
                processed_tickers += 1
                self._emit_percent(int((processed_tickers / total_tickers) * 100))
                self._emit_progress(f"Analyzing [{processed_tickers}/{total_tickers}] {ticker}")

                stock_info = get_ticker_info_cached(ticker)
                if not passes_liquidity_filter(stock_info): continue

                stock_data = data_loader.get_stock_data(ticker)
                if stock_data is None or len(stock_data) < self.squeeze_period: continue

                stock_data = self._prepare_dataframe(stock_data)
                if stock_data.empty: continue

                latest = stock_data.iloc[-1]
                current_width = latest['BB_Width']
                min_width = latest['BB_Width_Min']

                if pd.isna(current_width) or pd.isna(min_width):
                    continue

                if current_width <= min_width * 1.1: # Signal: within 10% of the low
                    self._emit_progress(f"!!! {ticker} is a potential '{self.name}' candidate!")
                    score = self._calculate_score(current_width, min_width)

                    candidate = ReboundCandidate(
                        ticker=ticker,
                        scenario=self.name,
                        score=score,
                        history_df=stock_data,
                        technicals={
                            'price': round(latest['Close'], 2),
                            'bb_width': round(current_width, 4),
                            'bb_width_min': round(min_width, 4)
                        },
                        fundamentals={'name': stock_info.get('shortName', 'N/A')}
                    )
                    all_candidates.append(candidate)

        self._emit_progress(f"Scan complete. Found {len(all_candidates)} candidates.")
        return all_candidates


class MomentumBreakoutScenario(BaseScenario):
    """
    Identifies stocks hitting new 52-week highs on high volume.
    """
    def __init__(self, *args, **kwargs):
        super().__init__("Momentum Breakout", *args, **kwargs)
        self.breakout_period = 252 # ~52 weeks

    def _prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculates 52-week high and average volume."""
        if df is None or df.empty or len(df) < self.breakout_period:
            return pd.DataFrame()

        # Shift by 1 to compare today's price against yesterday's 52-week high
        df['High_52W'] = df['High'].shift(1).rolling(window=self.breakout_period).max()
        df['Avg_Volume_30D'] = df['Volume'].shift(1).rolling(window=30).mean()
        return df

    def _calculate_score(self, volume_ratio: float, breakout_pct: float) -> int:
        """Calculates a score based on volume surge and breakout strength."""
        # Volume score: 1.5x avg vol = 0, 3x avg vol = 100
        volume_score = ((volume_ratio - 1.5) / 1.5) * 100
        volume_score = max(0, min(100, volume_score))

        # Strength score: 0% breakout = 0, 5% breakout = 100
        strength_score = (breakout_pct / 5.0) * 100
        strength_score = max(0, min(100, strength_score))

        # Weighted final score
        final_score = int(0.6 * volume_score + 0.4 * strength_score)
        return final_score

    async def run(self) -> List[ReboundCandidate]:
        self._emit_progress(f"Starting '{self.name}' scan...")
        all_tickers = data_loader.get_all_tickers()
        all_candidates = []
        total_tickers = sum(len(t) for t in all_tickers.values())
        processed_tickers = 0

        index_data_cache = {}
        market_context_ok = {}

        for market, tickers in all_tickers.items():
            if self.is_cancelled(): break
            self._emit_progress(f"--- Processing Market: {market} ---")

            # For custom lists, we bypass the market context filter.
            if market != 'CUSTOM':
                index_ticker = next((d['index_ticker'] for d in config.INDICES.values() if d['market'] == market), None)
                if not index_ticker:
                    processed_tickers += len(tickers)
                    continue

                if index_ticker not in index_data_cache:
                    index_data_cache[index_ticker] = data_loader.get_stock_data(index_ticker)
                    market_context_ok[market] = passes_market_context_filter(index_data_cache[index_ticker])

                if not market_context_ok.get(market, False):
                    self._emit_progress(f"Market context for {market} is bearish. Skipping breakouts.")
                    processed_tickers += len(tickers)
                    continue
                self._emit_progress(f"Market context for {market} is bullish. Analyzing {len(tickers)} tickers.")
            else:
                self._emit_progress(f"Processing custom ticker list. Analyzing {len(tickers)} tickers.")
            for ticker in tickers:
                if self.is_cancelled(): break
                processed_tickers += 1
                self._emit_percent(int((processed_tickers / total_tickers) * 100))
                self._emit_progress(f"Analyzing [{processed_tickers}/{total_tickers}] {ticker}")

                stock_info = get_ticker_info_cached(ticker)
                if not passes_liquidity_filter(stock_info): continue

                stock_data = data_loader.get_stock_data(ticker)
                if stock_data is None or len(stock_data) < self.breakout_period: continue

                stock_data = self._prepare_dataframe(stock_data)
                if stock_data.empty: continue

                latest = stock_data.iloc[-1]
                current_price = latest['Close']
                high_52w = latest['High_52W']
                current_volume = latest['Volume']
                avg_volume = latest['Avg_Volume_30D']

                if pd.isna(current_price) or pd.isna(high_52w) or pd.isna(current_volume) or pd.isna(avg_volume) or avg_volume == 0:
                    continue

                # Signal: Price breaks above the 52-week high on high volume
                if current_price > high_52w and current_volume > avg_volume * 1.5:
                    self._emit_progress(f"!!! {ticker} is a potential '{self.name}' candidate!")
                    volume_ratio = current_volume / avg_volume
                    breakout_pct = ((current_price - high_52w) / high_52w) * 100
                    score = self._calculate_score(volume_ratio, breakout_pct)

                    candidate = ReboundCandidate(
                        ticker=ticker,
                        scenario=self.name,
                        score=score,
                        history_df=stock_data,
                        technicals={
                            'price': round(current_price, 2),
                            '52w_high': round(high_52w, 2),
                            'volume_ratio': round(volume_ratio, 2),
                            'breakout_pct': round(breakout_pct, 2),
                        },
                        fundamentals={'name': stock_info.get('shortName', 'N/A')}
                    )
                    all_candidates.append(candidate)

        self._emit_progress(f"Scan complete. Found {len(all_candidates)} candidates.")
        return all_candidates


class GoldenCrossScenario(BaseScenario):
    """
    Identifies stocks that have recently experienced a 'Golden Cross',
    where the 50-day SMA crosses above the 200-day SMA.
    """
    def __init__(self, *args, **kwargs):
        super().__init__("Golden Cross", *args, **kwargs)
        self.recency_days = 5 # Look for a cross within the last 5 days
        self.min_data_days = 200

    def _prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculates 50-day and 200-day SMAs."""
        if df is None or df.empty or len(df) < self.min_data_days:
            return pd.DataFrame()

        df['SMA50'] = calculate_sma(df['Close'], 50)
        df['SMA200'] = calculate_sma(df['Close'], 200)
        return df

    def _calculate_score(self, days_ago: int) -> int:
        """Calculates score based on how recently the cross occurred."""
        # A cross today (0 days ago) gets 100. A cross 4 days ago gets 20.
        score = 100 - (days_ago * 20)
        return max(0, score)

    async def run(self) -> List[ReboundCandidate]:
        self._emit_progress(f"Starting '{self.name}' scan...")
        all_tickers = data_loader.get_all_tickers()
        all_candidates = []
        total_tickers = sum(len(t) for t in all_tickers.values())
        processed_tickers = 0

        index_data_cache = {}
        market_context_ok = {}

        for market, tickers in all_tickers.items():
            if self.is_cancelled(): break
            self._emit_progress(f"--- Processing Market: {market} ---")

            # For custom lists, we bypass the market context filter.
            if market != 'CUSTOM':
                index_ticker = next((d['index_ticker'] for d in config.INDICES.values() if d['market'] == market), None)
                if not index_ticker:
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
            else:
                self._emit_progress(f"Processing custom ticker list. Analyzing {len(tickers)} tickers.")
            for ticker in tickers:
                if self.is_cancelled(): break
                processed_tickers += 1
                self._emit_percent(int((processed_tickers / total_tickers) * 100))
                self._emit_progress(f"Analyzing [{processed_tickers}/{total_tickers}] {ticker}")

                stock_info = get_ticker_info_cached(ticker)
                if not passes_liquidity_filter(stock_info): continue

                stock_data = data_loader.get_stock_data(ticker)
                if stock_data is None or len(stock_data) < self.min_data_days: continue

                stock_data = self._prepare_dataframe(stock_data)
                if stock_data.empty or 'SMA50' not in stock_data.columns or 'SMA200' not in stock_data.columns: continue

                # Check for a cross in the last `recency_days`
                recent_data = stock_data.tail(self.recency_days + 1)
                if len(recent_data) < 2: continue

                for i in range(len(recent_data) - 1, 0, -1):
                    today = recent_data.iloc[i]
                    yesterday = recent_data.iloc[i - 1]

                    if pd.notna(today['SMA50']) and pd.notna(today['SMA200']) and \
                       pd.notna(yesterday['SMA50']) and pd.notna(yesterday['SMA200']):

                        # Check for the cross condition
                        if today['SMA50'] > today['SMA200'] and yesterday['SMA50'] <= yesterday['SMA200']:
                            days_ago = len(recent_data) - 1 - i
                            self._emit_progress(f"!!! {ticker} is a potential '{self.name}' candidate (cross {days_ago} days ago)!")
                            score = self._calculate_score(days_ago)

                            candidate = ReboundCandidate(
                                ticker=ticker,
                                scenario=self.name,
                                score=score,
                                history_df=stock_data,
                                technicals={
                                    'price': round(stock_data.iloc[-1]['Close'], 2),
                                    'cross_days_ago': days_ago,
                                    'sma_50': round(today['SMA50'], 2),
                                    'sma_200': round(today['SMA200'], 2),
                                },
                                fundamentals={'name': stock_info.get('shortName', 'N/A')}
                            )
                            all_candidates.append(candidate)
                            # Found the most recent cross, no need to look further back for this ticker
                            break

        self._emit_progress(f"Scan complete. Found {len(all_candidates)} candidates.")
        return all_candidates


class HighQualityDividendScenario(BaseScenario):
    """
    Finds stocks with high, sustainable dividends and healthy financials.
    """
    def __init__(self, *args, **kwargs):
        super().__init__("High-Quality Dividend", *args, **kwargs)
        self.min_yield = 0.03 # Minimum 3% dividend yield
        self.max_payout_ratio = 0.7 # Payout ratio cannot exceed 70%
        self.max_debt_to_equity = 1.0 # Debt-to-equity should be below 1.0

    def _calculate_score(self, dividend_yield: float, debt_to_equity: float) -> int:
        """Score is based on yield strength and low debt."""
        # Yield score (capped at 10% yield for scoring)
        yield_score = (min(dividend_yield, 0.10) / 0.10) * 100

        # Debt score (lower is better, capped at 1.0 for scoring)
        debt_score = (1 - min(debt_to_equity, self.max_debt_to_equity)) * 100

        # Weighted final score
        final_score = int(0.6 * yield_score + 0.4 * debt_score)
        return max(0, min(100, final_score))

    async def run(self) -> List[ReboundCandidate]:
        self._emit_progress(f"Starting '{self.name}' scan...")
        all_tickers_by_market = data_loader.get_all_tickers()
        all_candidates = []

        # This scan is fundamental, so we first gather all liquid tickers
        liquid_tickers = []
        self._emit_progress("Step 1: Filtering for liquid tickers...")
        for market, tickers in all_tickers_by_market.items():
            if self.is_cancelled(): break
            for ticker in tickers:
                if self.is_cancelled(): break
                stock_info = get_ticker_info_cached(ticker)
                if passes_liquidity_filter(stock_info):
                    liquid_tickers.append(ticker)

        if self.is_cancelled(): return []

        self._emit_progress(f"Step 2: Fetching fundamental data for {len(liquid_tickers)} liquid tickers...")
        fundamental_data = await self.fetcher.get_fundamentals_for_tickers(
            liquid_tickers,
            progress_callback=self._emit_progress,
            is_cancelled_callback=self.is_cancelled
        )

        if self.is_cancelled(): return []

        self._emit_progress(f"Step 3: Analyzing {len(fundamental_data)} tickers for dividend quality...")
        for i, (ticker, fund_data) in enumerate(fundamental_data.items()):
            if self.is_cancelled(): break
            self._emit_percent(int(((i + 1) / len(fundamental_data)) * 100))

            if not fund_data: continue

            div_yield = fund_data.get('dividendYield')
            payout = fund_data.get('payoutRatio')
            debt = fund_data.get('debtToEquity')

            # Ensure all required data points are present and valid
            if not all(v is not None for v in [div_yield, payout, debt]):
                continue

            # Apply the quality filters
            if div_yield >= self.min_yield and \
               0 < payout < self.max_payout_ratio and \
               debt < self.max_debt_to_equity:

                self._emit_progress(f"!!! {ticker} is a potential '{self.name}' candidate!")
                score = self._calculate_score(div_yield, debt)

                # We need historical data just for the chart, fetch it now
                stock_data = data_loader.get_stock_data(ticker)
                stock_info = get_ticker_info_cached(ticker)
                fund_data['name'] = stock_info.get('shortName', 'N/A')

                candidate = ReboundCandidate(
                    ticker=ticker,
                    scenario=self.name,
                    score=score,
                    history_df=stock_data,
                    technicals={
                        'price': round(stock_data['Close'].iloc[-1], 2) if not stock_data.empty else 'N/A',
                    },
                    fundamentals=fund_data
                )
                all_candidates.append(candidate)

        self._emit_progress(f"Scan complete. Found {len(all_candidates)} candidates.")
        return all_candidates


class QualityPullbackScenario(BaseScenario):
    """
    Implements the 'Quality Stock Pullback' scenario logic.
    """
    def __init__(self, *args, **kwargs):
        super().__init__("Quality Stock Pullback", *args, **kwargs)

    def _prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculates all necessary indicators and adds them to the dataframe."""
        if df is None or df.empty:
            return pd.DataFrame()
        df['RSI'] = calculate_rsi(df['Close'], config.RSI_PERIOD)
        df['SMA50'] = calculate_sma(df['Close'], 50)
        df['SMA200'] = calculate_sma(df['Close'], config.SMA_SUPPORT_PERIOD)
        return df

    async def run(self) -> List[ReboundCandidate]:
        self._emit_progress(f"Starting '{self.name}' scan...")
        all_tickers_by_market = data_loader.get_all_tickers()
        all_candidates = []
        total_tickers = sum(len(t) for t in all_tickers_by_market.values())
        processed_tickers = 0

        index_data_cache = {}
        market_context_ok = {}
        ticker_info_cache = {}

        for market, tickers in all_tickers_by_market.items():
            if self.is_cancelled():
                break
            self._emit_progress(f"--- Processing Market: {market} ---")

            # For custom lists, we bypass the market context filter.
            if market != 'CUSTOM':
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
            else:
                self._emit_progress(f"Processing custom ticker list. Analyzing {len(tickers)} tickers.")

            technically_strong_tickers = []
            for ticker in tickers:
                if self.is_cancelled():
                    self._emit_progress("Scan cancelled by user.")
                    break
                processed_tickers += 1
                self._emit_percent(int((processed_tickers / total_tickers) * 50))
                self._emit_progress(f"[{processed_tickers}/{total_tickers}] Tech Filter: {ticker}")

                stock_info = get_ticker_info_cached(ticker)
                if not passes_liquidity_filter(stock_info):
                    continue
                ticker_info_cache[ticker] = stock_info

                stock_data = data_loader.get_stock_data(ticker)
                if stock_data is None or len(stock_data) < 200:
                    continue

                stock_data = self._prepare_dataframe(stock_data)
                latest = stock_data.iloc[-1]

                if pd.notna(latest['Close']) and pd.notna(latest['SMA50']) and pd.notna(latest['SMA200']):
                    if latest['Close'] > latest['SMA200'] and latest['SMA50'] > latest['SMA200']:
                        ticker_info_cache[ticker]['stock_data'] = stock_data
                        technically_strong_tickers.append(ticker)

            if not technically_strong_tickers:
                self._emit_progress("No technically strong tickers found in this market.")
                continue

            self._emit_progress(f"Fetching fundamental data for {len(technically_strong_tickers)} strong tickers...")
            if self.is_cancelled():
                return all_candidates

            fundamental_data = await self.fetcher.get_fundamentals_for_tickers(
                technically_strong_tickers,
                progress_callback=self._emit_progress,
                is_cancelled_callback=self.is_cancelled
            )

            fundamentally_strong_tickers = {}
            for ticker, data in fundamental_data.items():
                if data:
                    score = 0
                    eps_growth = data.get('earningsGrowth')
                    if eps_growth is not None and eps_growth > 0.10:
                        score += 1

                    rev_growth = data.get('revenueGrowth')
                    if rev_growth is not None and rev_growth > 0.05:
                        score += 1

                    debt_to_equity = data.get('debtToEquity')
                    if debt_to_equity is not None and debt_to_equity < 0.7:
                        score += 1

                    if score >= 2:
                        fundamentally_strong_tickers[ticker] = data

            if not fundamentally_strong_tickers:
                self._emit_progress("No fundamentally strong tickers found in this market.")
                continue

            self._emit_progress(f"Final check for {len(fundamentally_strong_tickers)} tickers...")
            for i, (ticker, fund_data) in enumerate(fundamentally_strong_tickers.items()):
                if self.is_cancelled():
                    break
                self._emit_percent(50 + int(((i + 1) / len(fundamentally_strong_tickers)) * 50))

                stock_data = ticker_info_cache.get(ticker, {}).get('stock_data')
                if stock_data is None: continue

                latest = stock_data.iloc[-1]
                current_price, sma50 = latest['Close'], latest['SMA50']

                if pd.notna(current_price) and pd.notna(sma50) and sma50 > 0:
                    dist_to_sma50 = abs((current_price - sma50) / sma50) * 100
                    if dist_to_sma50 <= 3.0:
                        self._emit_progress(f"!!! {ticker} is a potential '{self.name}' candidate!")
                        prox_score = 100 - (dist_to_sma50 / 3.0 * 100)
                        fund_strength = (fund_data.get('earningsGrowth', 0) * 100)
                        score = int(0.7 * prox_score + 0.3 * fund_strength)

                        fund_data['name'] = ticker_info_cache.get(ticker, {}).get('shortName', 'N/A')

                        candidate = ReboundCandidate(
                            ticker=ticker, scenario=self.name, score=min(100, score),
                            history_df=stock_data,
                            technicals={
                                'price': round(current_price, 2),
                                'rsi': round(latest['RSI'], 2),
                                '50_sma_value': round(sma50, 2),
                                'dist_sma_50': round(dist_to_sma50, 2)
                            },
                            fundamentals=fund_data
                        )
                        all_candidates.append(candidate)

        self._emit_progress(f"Scan complete. Found {len(all_candidates)} candidates.")
        return all_candidates


# --- Scenario Runner ---

class ScenarioRunner:
    """
    Orchestrates the filtering process for different rebound scenarios.
    """
    SCENARIOS = {
        "Classic Oversold": ClassicOversoldScenario,
        "Quality Stock Pullback": QualityPullbackScenario,
        "Momentum Breakout": MomentumBreakoutScenario,
        "Golden Cross": GoldenCrossScenario,
        "Mean Reversion (Bollinger Bands)": MeanReversionScenario,
        "Volatility Squeeze": VolatilitySqueezeScenario,
        "High-Quality Dividend": HighQualityDividendScenario,
    }

    def __init__(self, fundamental_fetcher: FundamentalFetcher,
                 progress_callback: Callable = None, progress_percent_callback: Callable = None,
                 is_cancelled_callback: Callable = None):
        self.fetcher = fundamental_fetcher
        self.progress_callback = progress_callback
        self.progress_percent_callback = progress_percent_callback
        self.is_cancelled = is_cancelled_callback if is_cancelled_callback else lambda: False

    @staticmethod
    def get_available_scenarios() -> List[str]:
        """Returns a list of the names of available scenarios."""
        return list(ScenarioRunner.SCENARIOS.keys())

    async def run_scenario(self, scenario_name: str) -> List[ReboundCandidate]:
        """
        Runs the specified scenario by name.
        """
        if scenario_name not in self.SCENARIOS:
            self._emit_progress(f"Error: Scenario '{scenario_name}' not found.")
            logging.error(f"Scenario '{scenario_name}' not found.")
            return []

        ScenarioClass = self.SCENARIOS[scenario_name]
        scenario_instance = ScenarioClass(
            fundamental_fetcher=self.fetcher,
            progress_callback=self.progress_callback,
            progress_percent_callback=self.progress_percent_callback,
            is_cancelled_callback=self.is_cancelled
        )

        # Helper for progress emission in case of runner-level errors
        def _emit_progress_helper(message):
            if self.progress_callback:
                self.progress_callback.emit(message)

        try:
            return await scenario_instance.run()
        except Exception as e:
            logging.error(f"An error occurred during the '{scenario_name}' scan: {e}", exc_info=True)
            _emit_progress_helper(f"Error during scan: {e}")
            return []
