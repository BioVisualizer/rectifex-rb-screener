# rebound_scenarios.py
# Contains the logic for different screening scenarios.

import pandas as pd
import numpy as np
import logging
from typing import List, Dict, Any, Callable, Optional
import json
import re
from datetime import datetime, timedelta
import yfinance as yf
from abc import ABC, abstractmethod

# App-specific imports
import config
import data_loader
from data_structures import ReboundCandidate
from fundamentals import FundamentalDataHandler, FUNDAMENTALS_DIR, SECTOR_MEDIANS_FILE
from scoring import (
    compute_fundamental_score,
    compute_rebound_score,
    compute_market_context_score,
    passes_market_context_filter,
    DEFAULT_FUNDAMENTAL_WEIGHTS,
    DIVIDEND_SCENARIO_FUNDAMENTAL_WEIGHTS,
    DIVERGENCE_SCENARIO_FUNDAMENTAL_WEIGHTS,
    DEFAULT_REBOUND_SCORE_WEIGHTS,
)

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
    def __init__(self, name: str, progress_callback: Callable,
                 progress_percent_callback: Callable, is_cancelled_callback: Callable):
        self._name = name
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
    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        """
        The main execution method for the scenario.
        Receives data, returns a single candidate object or None.
        """
        pass


# --- Concrete Scenario Implementations ---

class ClassicOversoldScenario(BaseScenario):
    """
    Implements the 'Classic Oversold' scan: Oversold RSI, near 200-SMA and 90-day-low.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

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

    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        if len(stock_data) < config.SMA_SUPPORT_PERIOD:
            return None

        stock_data = self._prepare_dataframe(stock_data)
        is_candidate, rsi, dist_sma, dist_low = self._check_signal(stock_data)

        if not is_candidate:
            return None

        self._emit_progress(f"!!! {stock_info['ticker']} is a potential '{self.name}' candidate.")

        technical_score, rsi_score, prox_score = self._calculate_score(rsi, dist_sma, dist_low)

        technicals_dict = {
            'price': round(stock_data['Close'].iloc[-1], 2) if pd.notna(stock_data['Close'].iloc[-1]) else 'N/A',
            'rsi': round(rsi, 2) if pd.notna(rsi) else 'N/A',
            'dist_sma_200': round(dist_sma, 2) if pd.notna(dist_sma) else 'N/A',
            'dist_low_90d': round(dist_low, 2) if pd.notna(dist_low) else 'N/A',
        }

        score_breakdown_dict = {'rsi_score': rsi_score, 'prox_score': prox_score}

        return ReboundCandidate(
            ticker=stock_info['ticker'],
            scenario=self.name,
            rebound_score=0, # To be calculated by the runner
            technical_score=technical_score,
            fundamentals=fundamental_data,
            history_df=stock_data,
            technicals=technicals_dict,
            score_breakdown=score_breakdown_dict,
        )


class MeanReversionScenario(BaseScenario):
    """
    Identifies stocks trading at or below their lower Bollinger Band,
    signaling a potential "mean reversion" rebound.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

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

    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        if len(stock_data) < 20:
            return None

        stock_data = self._prepare_dataframe(stock_data)
        latest = stock_data.iloc[-1]
        current_price, lower_band = latest['Close'], latest['BB_Lower']

        if pd.isna(current_price) or pd.isna(lower_band):
            return None

        percent_below_band = 0
        if lower_band > 0 and current_price <= lower_band:
            percent_below_band = ((lower_band - current_price) / current_price) * 100
        else:
            return None # Not a candidate

        self._emit_progress(f"!!! {stock_info['ticker']} is a potential '{self.name}' candidate.")

        technical_score = self._calculate_score(percent_below_band)

        technicals_dict = {
            'price': round(current_price, 2),
            'lower_band': round(lower_band, 2),
            'percent_below_band': round(percent_below_band, 2)
        }

        return ReboundCandidate(
            ticker=stock_info['ticker'],
            scenario=self.name,
            rebound_score=0,
            technical_score=technical_score,
            fundamentals=fundamental_data,
            history_df=stock_data,
            technicals=technicals_dict,
            score_breakdown={'reversion_score': technical_score},
        )


class VolatilitySqueezeScenario(BaseScenario):
    """
    Identifies stocks in a 'volatility squeeze', where Bollinger Bands narrow
    significantly. This often precedes a strong price breakout.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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

    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        if len(stock_data) < self.squeeze_period:
            return None

        stock_data = self._prepare_dataframe(stock_data)
        if stock_data.empty: return None

        latest = stock_data.iloc[-1]
        current_width = latest.get('BB_Width')
        min_width = latest.get('BB_Width_Min')

        if not (pd.notna(current_width) and pd.notna(min_width) and current_width <= min_width * 1.1):
            return None

        self._emit_progress(f"!!! {stock_info['ticker']} is a potential '{self.name}' candidate.")

        technical_score = self._calculate_score(current_width, min_width)

        technicals_dict = {
            'price': round(latest['Close'], 2),
            'bb_width': round(current_width, 4),
            'bb_width_min': round(min_width, 4)
        }

        return ReboundCandidate(
            ticker=stock_info['ticker'],
            scenario=self.name,
            rebound_score=0,
            technical_score=technical_score,
            fundamentals=fundamental_data,
            history_df=stock_data,
            technicals=technicals_dict,
            score_breakdown={'squeeze_score': technical_score},
        )


class MomentumBreakoutScenario(BaseScenario):
    """
    Identifies stocks hitting new 52-week highs on high volume.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.breakout_period = 252 # ~52 weeks

    def _prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty or len(df) < self.breakout_period:
            return pd.DataFrame()
        df['High_52W'] = df['High'].shift(1).rolling(window=self.breakout_period).max()
        df['Avg_Volume_30D'] = df['Volume'].shift(1).rolling(window=30).mean()
        return df

    def _calculate_score(self, volume_ratio: float, breakout_pct: float) -> tuple[int, dict]:
        volume_score = max(0, min(100, ((volume_ratio - 1.5) / 1.5) * 100))
        strength_score = max(0, min(100, (breakout_pct / 5.0) * 100))
        final_score = int(0.6 * volume_score + 0.4 * strength_score)
        breakdown = {'volume_sub_score': int(volume_score), 'strength_sub_score': int(strength_score)}
        return final_score, breakdown

    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        if stock_data is None or len(stock_data) < self.breakout_period:
            return None

        stock_data = self._prepare_dataframe(stock_data)
        if stock_data.empty: return None

        latest = stock_data.iloc[-1]
        current_price, high_52w = latest.get('Close'), latest.get('High_52W')
        current_volume, avg_volume = latest.get('Volume'), latest.get('Avg_Volume_30D')

        if not (pd.notna(current_price) and pd.notna(high_52w) and pd.notna(current_volume) and pd.notna(avg_volume) and avg_volume > 0):
            return None

        if not (current_price > high_52w and current_volume > avg_volume * 1.5):
            return None

        self._emit_progress(f"!!! {stock_info['ticker']} is a potential '{self.name}' candidate.")

        volume_ratio = current_volume / avg_volume
        breakout_pct = ((current_price - high_52w) / high_52w) * 100
        technical_score, score_breakdown = self._calculate_score(volume_ratio, breakout_pct)

        technicals_dict = {
            'price': round(current_price, 2),
            '52w_high': round(high_52w, 2),
            'volume_ratio': round(volume_ratio, 2),
            'breakout_pct': round(breakout_pct, 2),
        }

        return ReboundCandidate(
            ticker=stock_info['ticker'], scenario=self.name, rebound_score=0,
            technical_score=technical_score, history_df=stock_data,
            fundamentals=fundamental_data,
            technicals=technicals_dict, score_breakdown=score_breakdown
        )


class GoldenCrossScenario(BaseScenario):
    """
    Identifies stocks that have recently experienced a 'Golden Cross',
    where the 50-day SMA crosses above the 200-day SMA.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recency_days = 5
        self.min_data_days = 200

    def _prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty or len(df) < self.min_data_days:
            return pd.DataFrame()
        df['SMA50'] = calculate_sma(df['Close'], 50)
        df['SMA200'] = calculate_sma(df['Close'], 200)
        return df

    def _calculate_score(self, days_ago: int) -> int:
        score = 100 - (days_ago * 20)
        return int(max(0, score))

    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        if stock_data is None or len(stock_data) < self.min_data_days:
            return None

        stock_data = self._prepare_dataframe(stock_data)
        if stock_data.empty or 'SMA50' not in stock_data.columns or 'SMA200' not in stock_data.columns:
            return None

        recent_data = stock_data.tail(self.recency_days + 1)
        if len(recent_data) < 2: return None

        cross_found, days_ago = False, -1
        for i in range(len(recent_data) - 1, 0, -1):
            today, yesterday = recent_data.iloc[i], recent_data.iloc[i - 1]
            if pd.notna(today['SMA50']) and pd.notna(today['SMA200']) and pd.notna(yesterday['SMA50']) and pd.notna(yesterday['SMA200']):
                if today['SMA50'] > today['SMA200'] and yesterday['SMA50'] <= yesterday['SMA200']:
                    days_ago = len(recent_data) - 1 - i
                    cross_found = True
                    break

        if not cross_found:
            return None

        self._emit_progress(f"!!! {stock_info['ticker']} is a potential '{self.name}' candidate.")

        technical_score = self._calculate_score(days_ago)
        latest = stock_data.iloc[-1]
        technicals_dict = {
            'price': round(latest['Close'], 2) if pd.notna(latest['Close']) else 'N/A',
            'cross_days_ago': days_ago,
            'sma_50': round(latest['SMA50'], 2) if pd.notna(latest['SMA50']) else 'N/A',
            'sma_200': round(latest['SMA200'], 2) if pd.notna(latest['SMA200']) else 'N/A',
        }

        return ReboundCandidate(
            ticker=stock_info['ticker'], scenario=self.name, rebound_score=0,
            technical_score=technical_score, history_df=stock_data,
            fundamentals=fundamental_data,
            technicals=technicals_dict, score_breakdown={'recency_sub_score': technical_score}
        )


class HighQualityDividendScenario(BaseScenario):
    """
    Finds stocks with high, sustainable dividends and healthy financials.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.min_yield = 0.03
        self.max_payout_ratio = 0.7
        self.max_debt_to_equity = 1.0

    def _calculate_score(self, dividend_yield: float, debt_to_equity: float) -> tuple[int, dict]:
        yield_score = (min(dividend_yield, 0.10) / 0.10) * 100
        debt_score = (1 - min(debt_to_equity, self.max_debt_to_equity)) * 100
        final_score = int(0.6 * yield_score + 0.4 * debt_score)
        breakdown = {'yield_sub_score': int(yield_score), 'debt_sub_score': int(debt_score)}
        return max(0, min(100, final_score)), breakdown

    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        if not (fundamental_data and 'metrics' in fundamental_data):
            return None

        fund_metrics = fundamental_data['metrics']
        div_yield = fund_metrics.get('dividendYield')
        payout = fund_metrics.get('payoutRatio')
        debt = fund_metrics.get('debtToEquity')

        if not all(v is not None for v in [div_yield, payout, debt]):
            return None

        if not (div_yield >= self.min_yield and 0 < payout < self.max_payout_ratio and debt < self.max_debt_to_equity):
            return None

        self._emit_progress(f"!!! {stock_info['ticker']} is a potential '{self.name}' candidate.")

        technical_score, score_breakdown = self._calculate_score(div_yield, debt)

        technicals_dict = {
            'price': round(stock_data['Close'].iloc[-1], 2) if not stock_data.empty else 'N/A'
        }

        return ReboundCandidate(
            ticker=stock_info['ticker'], scenario=self.name, rebound_score=0,
            technical_score=technical_score, history_df=stock_data,
            fundamentals=fundamental_data,
            technicals=technicals_dict, score_breakdown=score_breakdown
        )


class FundamentalDivergenceScenario(BaseScenario):
    """
    Identifies fundamentally strong stocks whose price has been stagnating or underperforming,
    creating a potential 'value' or 'contrarian' opportunity.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scan_period = config.FD_PRICE_RANGE_PERIOD

    def _prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty or len(df) < self.scan_period:
            return pd.DataFrame()
        df['SMA50'] = calculate_sma(df['Close'], 50)
        df['SMA200'] = calculate_sma(df['Close'], 200)
        recent_prices = df['Close'].tail(self.scan_period)
        if not recent_prices.empty:
            max_price, min_price, last_price = recent_prices.max(), recent_prices.min(), recent_prices.iloc[-1]
            if last_price > 0:
                df['PriceRange120D'] = (max_price - min_price) / last_price
        return df

    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        if stock_data is None or len(stock_data) < self.scan_period:
            return None

        sector_stats = {}
        if SECTOR_MEDIANS_FILE.exists():
            with open(SECTOR_MEDIANS_FILE, 'r') as f:
                sector_stats = json.load(f)

        prelim_fundamental_score = 0
        if fundamental_data and 'metrics' in fundamental_data:
            prelim_fundamental_score, _ = compute_fundamental_score(
                fundamentals=fundamental_data['metrics'], sector=fundamental_data.get('sector', 'N/A'),
                sector_stats=sector_stats, weights=DIVERGENCE_SCENARIO_FUNDAMENTAL_WEIGHTS)

        if prelim_fundamental_score < 45:
            return None

        stock_data = self._prepare_dataframe(stock_data)
        if stock_data.empty or 'SMA50' not in stock_data.columns or 'SMA200' not in stock_data.columns:
            return None

        latest = stock_data.iloc[-1]
        if pd.isna(latest['SMA50']) or pd.isna(latest['SMA200']) or latest['SMA200'] <= 0:
            return None

        sma_diff_pct = abs(latest['SMA50'] - latest['SMA200']) / latest['SMA200']
        price_range_120d = latest.get('PriceRange120D', 999)

        range_score = 0
        if price_range_120d <= config.FD_MAX_PRICE_RANGE_STRONG: range_score = 100
        elif price_range_120d <= config.FD_MAX_PRICE_RANGE_WEAK: range_score = 50

        sma_score = 0
        if sma_diff_pct <= config.FD_MAX_SMA_DIFF_PERCENT: sma_score = 100
        elif sma_diff_pct <= config.FD_MAX_SMA_DIFF_PERCENT * 2: sma_score = 50

        technical_score = int(range_score * 0.6 + sma_score * 0.4)
        score_breakdown = {'range_sub_score': range_score, 'sma_sub_score': sma_score}

        if technical_score < 50:
            return None

        self._emit_progress(f"!!! {stock_info['ticker']} is a potential '{self.name}' candidate.")

        technicals_dict = {
            'price': round(latest['Close'], 2),
            'price_range_120d_pct': round(price_range_120d * 100, 2) if price_range_120d != 999 else 'N/A',
            'sma50_vs_sma200_pct': round(sma_diff_pct * 100, 2),
        }

        return ReboundCandidate(
            ticker=stock_info['ticker'], scenario=self.name, rebound_score=0,
            technical_score=technical_score, history_df=stock_data,
            fundamentals=fundamental_data,
            technicals=technicals_dict, score_breakdown=score_breakdown
        )


class QualityPullbackScenario(BaseScenario):
    """
    Implements the 'Quality Stock Pullback' scenario logic.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        df['RSI'] = calculate_rsi(df['Close'], config.RSI_PERIOD)
        df['SMA50'] = calculate_sma(df['Close'], 50)
        df['SMA200'] = calculate_sma(df['Close'], config.SMA_SUPPORT_PERIOD)
        return df

    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        if stock_data is None or len(stock_data) < 200:
            return None

        stock_data = self._prepare_dataframe(stock_data)
        latest = stock_data.iloc[-1]

        if not (pd.notna(latest['Close']) and pd.notna(latest['SMA50']) and pd.notna(latest['SMA200']) and \
                latest['Close'] > latest['SMA200'] and latest['SMA50'] > latest['SMA200']):
            return None

        current_price, sma50 = latest['Close'], latest['SMA50']
        dist_to_sma50 = -1
        if pd.notna(current_price) and pd.notna(sma50) and sma50 > 0:
            dist_to_sma50 = abs((current_price - sma50) / sma50) * 100
            if dist_to_sma50 > 3.0:
                return None
        else:
            return None

        self._emit_progress(f"!!! {stock_info['ticker']} is a potential '{self.name}' candidate.")

        prox_score = 100 - (dist_to_sma50 / 3.0 * 100)
        technical_score = int(max(0, min(100, prox_score)))

        technicals_dict = {
            'price': round(current_price, 2),
            'rsi': round(latest['RSI'], 2) if pd.notna(latest['RSI']) else 'N/A',
            'dist_sma_50': round(dist_to_sma50, 2)
        }

        return ReboundCandidate(
            ticker=stock_info['ticker'], scenario=self.name, rebound_score=0,
            technical_score=technical_score, history_df=stock_data,
            fundamentals=fundamental_data,
            technicals=technicals_dict, score_breakdown={'proximity_sub_score': technical_score}
        )


# --- Scenario Runner ---

class ScenarioRunner:
    """
    Orchestrates the screening process. This class is responsible for:
    - Loading scenario configurations from JSON.
    - Fetching all necessary data (OHLC, fundamentals) for a set of tickers.
    - Iterating through tickers and passing their data to the appropriate scenario class for analysis.
    - Collecting and returning the results.
    """

    SCENARIO_CLASS_MAP = {
        "ClassicOversoldScenario": ClassicOversoldScenario,
        "QualityPullbackScenario": QualityPullbackScenario,
        "FundamentalDivergenceScenario": FundamentalDivergenceScenario,
        "MomentumBreakoutScenario": MomentumBreakoutScenario,
        "GoldenCrossScenario": GoldenCrossScenario,
        "MeanReversionScenario": MeanReversionScenario,
        "VolatilitySqueezeScenario": VolatilitySqueezeScenario,
        "HighQualityDividendScenario": HighQualityDividendScenario,
    }

    def __init__(self, progress_callback: Callable = None, progress_percent_callback: Callable = None,
                 is_cancelled_callback: Callable = None):
        self.progress_callback = progress_callback
        self.progress_percent_callback = progress_percent_callback
        self.is_cancelled = is_cancelled_callback if is_cancelled_callback else lambda: False
        self.scenarios_config = ScenarioRunner.load_scenarios_config()
        self.fundamental_handler = FundamentalDataHandler()

    @staticmethod
    def load_scenarios_config() -> List[Dict[str, Any]]:
        """Loads scenario definitions from the JSON file."""
        try:
            with open('scenarios.json', 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logging.error(f"Could not load scenarios.json: {e}")
            return []

    def _emit_progress(self, message: str):
        if self.progress_callback:
            self.progress_callback.emit(message)

    def _emit_percent(self, percent: int):
        if self.progress_percent_callback:
            self.progress_percent_callback.emit(percent)

    def _get_scenario_instance(self, scenario_id: str) -> Optional[BaseScenario]:
        """Factory method to create a scenario instance from its ID."""
        scenario_config = next((s for s in self.scenarios_config if s['id'] == scenario_id), None)
        if not scenario_config:
            self._emit_progress(f"Error: Scenario with ID '{scenario_id}' not found.")
            return None

        class_name = scenario_config.get("class")
        ScenarioClass = self.SCENARIO_CLASS_MAP.get(class_name)
        if not ScenarioClass:
            self._emit_progress(f"Error: Scenario class '{class_name}' not implemented.")
            return None

        return ScenarioClass(
            name=scenario_config['name'],
            progress_callback=self.progress_callback,
            progress_percent_callback=self.progress_percent_callback,
            is_cancelled_callback=self.is_cancelled
        )

    async def run_scan(self, scenario_id: str, ticker: str = None) -> List[ReboundCandidate]:
        """
        Runs a full scan for a given scenario ID.
        """
        scenario_instance = self._get_scenario_instance(scenario_id)
        if not scenario_instance:
            return []

        if ticker:
            all_tickers_by_market = {"CUSTOM": [ticker]}
        else:
            all_tickers_by_market = data_loader.get_all_tickers()

        all_candidates = []
        total_tickers = sum(len(t) for t in all_tickers_by_market.values())
        processed_tickers = 0

        for market, tickers in all_tickers_by_market.items():
            if self.is_cancelled(): break
            self._emit_progress(f"--- Processing Market: {market} ({len(tickers)} tickers) ---")

            index_data = None
            if market != 'CUSTOM' and not ticker:
                index_ticker = next((d['index_ticker'] for d in config.INDICES.values() if d['market'] == market), None)
                if index_ticker:
                    index_data = await data_loader.get_stock_data(index_ticker)

                # Market Context Filter
                if not passes_market_context_filter(index_data, config.MARKET_CONTEXT_SMA):
                    self._emit_progress(f"Market context for {market} is bearish. Skipping market.")
                    processed_tickers += len(tickers)
                    continue

            # --- Centralized Data Fetching ---
            self._emit_progress(f"Fetching fundamental data for {len(tickers)} tickers in {market}...")
            fundamental_data_map = await self.fundamental_handler.get_fundamentals_for_tickers(
                tickers, self.progress_callback, self.is_cancelled)

            # --- Compute Sector Medians ---
            # This is critical. After fetching all data, we compute the aggregate
            # statistics before starting the analysis.
            self._emit_progress(f"Calculating sector medians for {market}...")
            self.fundamental_handler.compute_and_save_sector_medians()

            # --- Pre-load sector stats for the upcoming analysis ---
            sector_stats = {}
            if SECTOR_MEDIANS_FILE.exists():
                with open(SECTOR_MEDIANS_FILE, 'r') as f:
                    sector_stats = json.load(f)
            else:
                self._emit_progress(f"Warning: Could not load sector medians after calculation.")


            # --- Analysis Loop ---
            for ticker_val in tickers:
                if self.is_cancelled(): break
                processed_tickers += 1
                self._emit_percent(int((processed_tickers / total_tickers) * 100))
                self._emit_progress(f"Analyzing [{processed_tickers}/{total_tickers}] {ticker_val}")

                # Get all data for the current ticker
                stock_info = get_ticker_info_cached(ticker_val)
                if not passes_liquidity_filter(stock_info): continue

                # FIX: Add the ticker to the info dict so scenarios can access it.
                if stock_info:
                    stock_info['ticker'] = ticker_val

                stock_data = await data_loader.get_stock_data(ticker_val)
                if stock_data is None or stock_data.empty: continue

                fundamental_data = fundamental_data_map.get(ticker_val)

                # The scenario's run method is now synchronous and receives all data
                try:
                    candidate = scenario_instance.run(stock_data, fundamental_data, stock_info)
                    if candidate:
                        # The runner is now responsible for the final score composition
                        tech_score = candidate.technical_score
                        fund_score = 0
                        if fundamental_data:
                            # Select the correct weights for the scenario
                            if scenario_id == 'high_quality_dividend':
                                weights = DIVIDEND_SCENARIO_FUNDAMENTAL_WEIGHTS
                            elif scenario_id == 'fundamental_divergence':
                                weights = DIVERGENCE_SCENARIO_FUNDAMENTAL_WEIGHTS
                            else:
                                weights = DEFAULT_FUNDAMENTAL_WEIGHTS

                            fund_score, fund_breakdown = compute_fundamental_score(
                                fundamentals=fundamental_data['metrics'],
                                sector=fundamental_data.get('sector', 'N/A'),
                                sector_stats=sector_stats,
                                weights=weights
                            )
                            candidate.fundamental_score = fund_score
                            candidate.score_breakdown.update(fund_breakdown)

                        market_score = compute_market_context_score(index_data)
                        candidate.market_context_score = market_score

                        candidate.rebound_score = compute_rebound_score(tech_score, fund_score, market_score, DEFAULT_REBOUND_SCORE_WEIGHTS)

                        all_candidates.append(candidate)

                except Exception as e:
                    logging.error(f"Error processing {ticker_val} in scenario {scenario_id}: {e}", exc_info=True)

        self._emit_progress(f"Scan complete. Found {len(all_candidates)} candidates.")
        return all_candidates
