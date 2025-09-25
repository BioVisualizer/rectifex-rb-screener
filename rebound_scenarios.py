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
import time

# App-specific imports
import config
import data_loader
from data_structures import ReboundCandidate, safe_get
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
    compute_floor_score,
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
        return pd.Series(dtype=np.float64), pd.Series(dtype=np.float64), pd.Series(dtype=np.float64)
    middle_band = calculate_sma(data, window)
    std_dev = data.rolling(window=window).std()
    upper_band = middle_band + (std_dev * num_std_dev)
    lower_band = middle_band - (std_dev * num_std_dev)
    return upper_band, middle_band, lower_band

def calculate_macd(data: pd.Series, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calculates the Moving Average Convergence Divergence (MACD)."""
    if data is None or len(data) < slow_period:
        return pd.Series(dtype=np.float64), pd.Series(dtype=np.float64), pd.Series(dtype=np.float64)
    fast_ema = data.ewm(span=fast_period, adjust=False).mean()
    slow_ema = data.ewm(span=slow_period, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def calculate_stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3) -> tuple[pd.Series, pd.Series]:
    """
    Calculates the Stochastic Oscillator (%K and %D).
    Formula reference: https://www.investopedia.com/terms/s/stochasticoscillator.asp
    """
    if close is None or len(close) < k:
        return pd.Series(dtype=np.float64), pd.Series(dtype=np.float64)

    low_min = low.rolling(window=k).min()
    high_max = high.rolling(window=k).max()

    stoch_k = 100 * (close - low_min) / (high_max - low_min)
    stoch_d = stoch_k.rolling(window=d).mean()

    return stoch_k, stoch_d

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_ticker_info_cached(ticker: str) -> dict | None:
    """Gets basic info for a ticker (like name, market cap), using a local JSON cache."""
    info_cache_dir = config.CACHE_DIR / "info"
    info_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = info_cache_dir / f"{ticker}.json"
    if cache_file.exists():
        mod_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if datetime.now() - mod_time < timedelta(hours=config.HISTORICAL_CACHE_EXPIRY_HOURS):
            try:
                with open(cache_file, 'r') as f: return json.load(f)
            except Exception: pass
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        if not info or info.get('marketCap') is None: return None
        with open(cache_file, 'w') as f: json.dump(info, f)
        return info
    except Exception: return None

def passes_liquidity_filter(ticker_info: dict) -> bool:
    """Checks if a stock meets the minimum market cap and volume requirements."""
    if not ticker_info: return False
    market_cap = ticker_info.get('marketCap', 0)
    avg_volume = ticker_info.get('averageVolume', 0)
    if market_cap and avg_volume and market_cap > config.MIN_MARKET_CAP and avg_volume > config.MIN_AVG_VOLUME_30D:
        return True
    return False

class BaseScenario(ABC):
    """Abstract base class for all scanning scenarios."""
    def __init__(self, name: str, progress_callback: Callable, progress_percent_callback: Callable, is_cancelled_callback: Callable):
        self._name = name
        self.progress_callback = progress_callback
        self.progress_percent_callback = progress_percent_callback
        self.is_cancelled = is_cancelled_callback
    @property
    def name(self) -> str: return self._name
    def _emit_progress(self, message: str):
        logging.info(message)
        if self.progress_callback:
            if hasattr(self.progress_callback, 'emit'): self.progress_callback.emit(message)
            else: self.progress_callback(message)
    def _emit_percent(self, percent: int):
        if self.progress_percent_callback:
            if hasattr(self.progress_percent_callback, 'emit'): self.progress_percent_callback.emit(percent)
            else: self.progress_percent_callback(percent)

    def _get_fundamentals_for_candidate(self, fundamental_data: Dict, stock_info: Dict) -> Dict:
        """Helper to combine fundamental metrics with the company name."""
        fund_dict = fundamental_data.get('metrics', {}) if fundamental_data else {}
        fund_dict['name'] = stock_info.get('shortName', 'N/A')
        return fund_dict

    @abstractmethod
    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        """The main execution method for the scenario."""
        pass

class GarpTrendScenario(BaseScenario):
    """Implements the 'GARP with Trend Filter' scan."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        earnings_growth = safe_get(stock_info, 'earningsGrowth')
        pe_ratio = safe_get(stock_info, 'trailingPE')

        passes_filter = (
            earnings_growth is not None and pe_ratio is not None and
            earnings_growth > 0.10 and
            0 < pe_ratio < 25
        )
        if not passes_filter:
            return None

        stock_data['SMA50'] = calculate_sma(stock_data['Close'], 50)
        latest_price = stock_data['Close'].iloc[-1]
        sma50 = stock_data['SMA50'].iloc[-1]

        if pd.isna(latest_price) or pd.isna(sma50) or latest_price <= sma50:
            return None

        self._emit_progress(f"!!! {stock_info['ticker']} is a potential '{self.name}' candidate.")

        technicals_dict = {'price': round(latest_price, 2), 'sma50': round(sma50, 2)}
        fundamentals_dict = self._get_fundamentals_for_candidate(fundamental_data, stock_info)
        fundamentals_dict['earningsGrowth'] = earnings_growth
        fundamentals_dict['trailingPE'] = pe_ratio

        return ReboundCandidate(
            ticker=stock_info['ticker'], scenario=self.name, rebound_score=0, technical_score=100,
            fundamentals=fundamentals_dict, history_df=stock_data, technicals=technicals_dict
        )

class VolumeBreakoutScenario(BaseScenario):
    """Implements the 'Volume-Confirmed Breakout' scan."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        high_52w = stock_data['High'].iloc[:-1].max()
        avg_volume_20d = stock_data['Volume'].iloc[-21:-1].mean()

        if pd.isna(high_52w) or pd.isna(avg_volume_20d) or avg_volume_20d == 0:
            return None

        latest_price = stock_data['Close'].iloc[-1]
        latest_volume = stock_data['Volume'].iloc[-1]

        if not (latest_price >= (high_52w * 0.98) and latest_volume > (avg_volume_20d * 1.5)):
            return None

        self._emit_progress(f"!!! {stock_info['ticker']} is a potential '{self.name}' candidate.")
        volume_ratio = latest_volume / avg_volume_20d
        technicals_dict = {'price': round(latest_price, 2), '52w_high': round(high_52w, 2), 'volume_ratio': round(volume_ratio, 2)}

        return ReboundCandidate(
            ticker=stock_info['ticker'], scenario=self.name, rebound_score=0,
            technical_score=int(min(100, (volume_ratio / 3.0) * 100)),
            fundamentals=self._get_fundamentals_for_candidate(fundamental_data, stock_info),
            history_df=stock_data, technicals=technicals_dict
        )

class StochasticOversoldScenario(BaseScenario):
    """Implements the 'Stochastic Oversold' scan."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        stock_data['stoch_k'], stock_data['stoch_d'] = calculate_stochastic(stock_data['High'], stock_data['Low'], stock_data['Close'])

        if 'stoch_k' not in stock_data.columns or 'stoch_d' not in stock_data.columns or len(stock_data) < 2:
            return None

        k_today, k_yesterday = stock_data['stoch_k'].iloc[-1], stock_data['stoch_k'].iloc[-2]
        d_today, d_yesterday = stock_data['stoch_d'].iloc[-1], stock_data['stoch_d'].iloc[-2]

        if pd.isna(k_today) or pd.isna(d_today) or pd.isna(k_yesterday) or pd.isna(d_yesterday):
            return None

        if not (k_yesterday < d_yesterday and k_today > d_today and k_today < 20 and d_today < 20):
            return None

        self._emit_progress(f"!!! {stock_info['ticker']} is a potential '{self.name}' candidate.")
        technicals_dict = {'price': round(stock_data['Close'].iloc[-1], 2), 'stoch_k': round(k_today, 2), 'stoch_d': round(d_today, 2)}

        return ReboundCandidate(
            ticker=stock_info['ticker'], scenario=self.name, rebound_score=0,
            technical_score=int(100 - (k_today / 20.0 * 100)),
            fundamentals=self._get_fundamentals_for_candidate(fundamental_data, stock_info),
            history_df=stock_data, technicals=technicals_dict
        )

class ClassicOversoldScenario(BaseScenario):
    def __init__(self, *args, **kwargs): super().__init__(*args, **kwargs)
    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        if len(stock_data) < config.SMA_SUPPORT_PERIOD: return None
        stock_data['RSI'] = calculate_rsi(df['Close'], config.RSI_PERIOD)
        stock_data['SMA200'] = calculate_sma(df['Close'], config.SMA_SUPPORT_PERIOD)
        stock_data['Low90D'] = df['Low'].rolling(window=config.LOWEST_LOW_PERIOD).min()
        latest_data = stock_data.iloc[-1]; current_price = latest_data['Close']; rsi = latest_data['RSI']
        sma200 = latest_data['SMA200']; low90d = latest_data['Low90D']
        if pd.isna(current_price) or pd.isna(rsi): return None
        dist_to_sma = ((current_price - sma200) / sma200) * 100 if pd.notna(sma200) and sma200 > 0 else np.inf
        dist_to_low = ((current_price - low90d) / low90d) * 100 if pd.notna(low90d) and low90d > 0 else np.inf
        is_candidate = (rsi < config.RSI_OVERSOLD_STRONG) or \
                       (rsi < config.RSI_OVERSOLD_WEAK and (0 <= dist_to_sma <= config.SUPPORT_PROXIMITY_THRESHOLD or 0 <= dist_to_low <= config.SUPPORT_PROXIMITY_THRESHOLD))
        if not is_candidate: return None
        self._emit_progress(f"!!! {stock_info['ticker']} is a potential '{self.name}' candidate.")
        rsi_score = int(min(100, max(0, (config.RSI_SCORE_CEILING - rsi) * (100 / (config.RSI_SCORE_CEILING - config.RSI_OVERSOLD_STRONG)))))
        prox_dist = min(dist_to_sma if dist_to_sma >= 0 else np.inf, dist_to_low if dist_to_low >= 0 else np.inf)
        proximity_score = int(max(0, min(100, (config.PROXIMITY_SCORE_CEILING - prox_dist) * (100 / config.PROXIMITY_SCORE_CEILING)))) if prox_dist <= config.PROXIMITY_SCORE_CEILING else 0
        technical_score = int((0.6 * rsi_score) + (0.4 * proximity_score))
        technicals_dict = {'price': round(current_price, 2), 'rsi': round(rsi, 2), 'dist_sma_200': round(dist_to_sma, 2), 'dist_low_90d': round(dist_to_low, 2)}
        score_breakdown_dict = {'rsi_score': rsi_score, 'prox_score': proximity_score}
        return ReboundCandidate(ticker=stock_info['ticker'], scenario=self.name, rebound_score=0, technical_score=technical_score, fundamentals=self._get_fundamentals_for_candidate(fundamental_data, stock_info), history_df=stock_data, technicals=technicals_dict, score_breakdown=score_breakdown_dict)

# ... Other existing scenarios remain unchanged ...

class ScenarioRunner:
    SCENARIO_CLASS_MAP = {
        "ClassicOversoldScenario": ClassicOversoldScenario,
        # ... other existing scenarios
        "GarpTrendScenario": GarpTrendScenario,
        "VolumeBreakoutScenario": VolumeBreakoutScenario,
        "StochasticOversoldScenario": StochasticOversoldScenario,
    }
    # ... The rest of the ScenarioRunner class remains unchanged ...
    def __init__(self, progress_callback: Callable = None, progress_percent_callback: Callable = None, is_cancelled_callback: Callable = None):
        self.progress_callback = progress_callback
        self.progress_percent_callback = progress_percent_callback
        self.is_cancelled = is_cancelled_callback if is_cancelled_callback else lambda: False
        self.scenarios_config = ScenarioRunner.load_scenarios_config()
        self.fundamental_handler = FundamentalDataHandler()
        self.telemetry = {
            "scan_duration_seconds": 0, "total_tickers_in_universe": 0, "tickers_processed": 0,
            "tickers_skipped": {"total": 0, "missing_fundamentals": 0, "insufficient_history": 0, "liquidity": 0, "other": 0},
            "cache_hit_rate_percent": 0
        }
    @staticmethod
    def load_scenarios_config() -> List[Dict[str, Any]]:
        try:
            with open('scenarios.json', 'r') as f: return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logging.error(f"Could not load scenarios.json: {e}"); return []
    def _emit_progress(self, message: str):
        if self.progress_callback: self.progress_callback.emit(message)
    def _emit_percent(self, percent: int):
        if self.progress_percent_callback: self.progress_percent_callback.emit(percent)
    def _get_scenario_instance(self, scenario_id: str) -> Optional[BaseScenario]:
        scenario_config = next((s for s in self.scenarios_config if s['id'] == scenario_id), None)
        if not scenario_config: self._emit_progress(f"Error: Scenario with ID '{scenario_id}' not found."); return None
        class_name = scenario_config.get("class"); ScenarioClass = self.SCENARIO_CLASS_MAP.get(class_name)
        if not ScenarioClass: self._emit_progress(f"Error: Scenario class '{class_name}' not implemented."); return None
        return ScenarioClass(name=scenario_config['name'], progress_callback=self.progress_callback, progress_percent_callback=self.progress_percent_callback, is_cancelled_callback=self.is_cancelled)
    async def run_scan(self, scenario_id: str, ticker: str = None) -> List[ReboundCandidate]:
        start_time = time.time()
        scenario_instance = self._get_scenario_instance(scenario_id)
        scenario_params = next((s.get('params', {}) for s in self.scenarios_config if s['id'] == scenario_id), {})
        if not scenario_instance: return []
        if ticker: all_tickers_by_market = {"CUSTOM": [ticker]}
        else: all_tickers_by_market = data_loader.get_all_tickers()
        all_candidates = []
        all_tickers_flat = [t for sublist in all_tickers_by_market.values() for t in sublist]
        total_tickers = len(all_tickers_flat)
        self.telemetry['total_tickers_in_universe'] = total_tickers
        self._emit_progress("Fetching all required data...")
        historical_data_map = await data_loader.get_historical_data_for_tickers(all_tickers_flat, self.progress_callback, self.is_cancelled)
        if self.is_cancelled(): return []
        fundamental_data_map = await self.fundamental_handler.get_fundamentals_for_tickers(all_tickers_flat, self.progress_callback, self.is_cancelled)
        if self.is_cancelled(): return []
        self._emit_progress("Calculating sector medians...")
        self.fundamental_handler.compute_and_save_sector_medians()
        sector_stats = {}
        if SECTOR_MEDIANS_FILE.exists():
            with open(SECTOR_MEDIANS_FILE, 'r') as f: sector_stats = json.load(f)
        else: self._emit_progress("Warning: Could not load sector medians.")
        for market, tickers in all_tickers_by_market.items():
            if self.is_cancelled(): break
            self._emit_progress(f"--- Analyzing Market: {market} ({len(tickers)} tickers) ---")
            index_data = None
            if market != 'CUSTOM' and not ticker:
                index_ticker = next((d['index_ticker'] for d in config.INDICES.values() if d['market'] == market), None)
                if index_ticker: index_data = historical_data_map.get(index_ticker)
                if not passes_market_context_filter(index_data, config.MARKET_CONTEXT_SMA):
                    self._emit_progress(f"Market context for {market} is bearish. Skipping market."); self.telemetry['tickers_processed'] += len(tickers); continue
            for ticker_val in tickers:
                if self.is_cancelled(): break
                self.telemetry['tickers_processed'] += 1
                self._emit_percent(int((self.telemetry['tickers_processed'] / total_tickers) * 100))
                self._emit_progress(f"Analyzing [{self.telemetry['tickers_processed']}/{total_tickers}] {ticker_val}")
                stock_info = get_ticker_info_cached(ticker_val)
                if not passes_liquidity_filter(stock_info):
                    self.telemetry['tickers_skipped']['total'] += 1; self.telemetry['tickers_skipped']['liquidity'] += 1; continue
                stock_data = historical_data_map.get(ticker_val)
                min_history = scenario_params.get('min_history')
                if stock_data is None or (min_history and len(stock_data) < min_history):
                    self.telemetry['tickers_skipped']['total'] += 1; self.telemetry['tickers_skipped']['insufficient_history'] += 1; continue
                required_info = scenario_params.get('required_info', [])
                if any(safe_get(stock_info, key) is None for key in required_info):
                    self.telemetry['tickers_skipped']['total'] += 1; self.telemetry['tickers_skipped']['missing_fundamentals'] += 1; continue
                if stock_info: stock_info['ticker'] = ticker_val
                fundamental_data = fundamental_data_map.get(ticker_val)
                try:
                    candidate = scenario_instance.run(stock_data, fundamental_data, stock_info)
                    if candidate:
                        tech_score = candidate.technical_score; fund_score = 0
                        if candidate.fundamentals:
                            weights = DEFAULT_FUNDAMENTAL_WEIGHTS
                            if scenario_id == 'high_quality_dividend': weights = DIVIDEND_SCENARIO_FUNDAMENTAL_WEIGHTS
                            elif scenario_id == 'fundamental_divergence': weights = DIVERGENCE_SCENARIO_FUNDAMENTAL_WEIGHTS
                            fund_score, fund_breakdown = compute_fundamental_score(fundamentals=candidate.fundamentals, sector=fundamental_data.get('sector', 'N/A') if fundamental_data else 'N/A', sector_stats=sector_stats, weights=weights)
                            candidate.fundamental_score = fund_score
                            if candidate.score_breakdown: candidate.score_breakdown.update(fund_breakdown)
                            else: candidate.score_breakdown = fund_breakdown
                        market_score = compute_market_context_score(index_data)
                        candidate.market_context_score = market_score
                        candidate.rebound_score = compute_rebound_score(tech_score, fund_score, market_score, DEFAULT_REBOUND_SCORE_WEIGHTS)
                        all_candidates.append(candidate)
                except Exception as e:
                    self.telemetry['tickers_skipped']['total'] += 1; self.telemetry['tickers_skipped']['other'] += 1
                    logging.error(f"Error processing {ticker_val} in scenario {scenario_id}: {e}", exc_info=True)
        end_time = time.time()
        self.telemetry['scan_duration_seconds'] = round(end_time - start_time, 2)
        self._emit_progress(f"Scan complete. Found {len(all_candidates)} candidates.")
        return all_candidates