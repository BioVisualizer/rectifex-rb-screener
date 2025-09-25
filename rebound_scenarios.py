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
    if data is None or len(data) < window: return pd.Series(dtype=np.float64)
    return data.rolling(window=window).mean()

def calculate_rsi(data: pd.Series, window: int = 14) -> pd.Series:
    if data is None or len(data) < window: return pd.Series(dtype=np.float64)
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3) -> tuple[pd.Series, pd.Series]:
    if close is None or len(close) < k: return pd.Series(dtype=np.float64), pd.Series(dtype=np.float64)
    low_min = low.rolling(window=k).min()
    high_max = high.rolling(window=k).max()
    stoch_k = 100 * (close - low_min) / (high_max - low_min)
    stoch_d = stoch_k.rolling(window=d).mean()
    return stoch_k, stoch_d

def calculate_macd(data: pd.Series, fast_period=12, slow_period=26, signal_period=9) -> tuple[pd.Series, pd.Series, pd.Series]:
    if data is None or len(data) < slow_period:
        return pd.Series(dtype=np.float64), pd.Series(dtype=np.float64), pd.Series(dtype=np.float64)
    ema_fast = data.ewm(span=fast_period, adjust=False).mean()
    ema_slow = data.ewm(span=slow_period, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    macd_hist = macd_line - signal_line
    return macd_line, signal_line, macd_hist

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_ticker_info_cached(ticker: str) -> dict | None:
    # ... (Implementation remains)
    return None

def passes_liquidity_filter(ticker_info: dict) -> bool:
    # ... (Implementation remains)
    return True

class BaseScenario(ABC):
    def __init__(self, name: str, progress_callback: Callable, is_cancelled_callback: Callable):
        self._name = name
        self.progress_callback = progress_callback
        self.is_cancelled = is_cancelled_callback
    # ... (Implementation remains)

class GarpTrendScenario(BaseScenario):
    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        earnings_growth = safe_get(stock_info, 'earningsGrowth')
        pe_ratio = safe_get(stock_info, 'trailingPE')
        if not (earnings_growth is not None and pe_ratio is not None and earnings_growth > 0.10 and 0 < pe_ratio < 25): return None
        stock_data['SMA50'] = calculate_sma(stock_data['Close'], 50)
        latest_price, sma50 = stock_data['Close'].iloc[-1], stock_data['SMA50'].iloc[-1]
        if pd.isna(latest_price) or pd.isna(sma50) or latest_price <= sma50: return None
        technicals = {'price': round(latest_price, 2), 'sma50': round(sma50, 2)}
        fundamentals = {'earningsGrowth': earnings_growth, 'trailingPE': pe_ratio, 'name': stock_info.get('shortName', 'N/A')}
        return ReboundCandidate(ticker=stock_info['ticker'], scenario=self.name, technical_score=100, fundamentals=fundamentals, history_df=stock_data, technicals=technicals, rebound_score=0)

class VolumeBreakoutScenario(BaseScenario):
    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        high_52w = stock_data['High'].iloc[:-1].max()
        avg_volume_20d = stock_data['Volume'].iloc[-21:-1].mean()
        if pd.isna(high_52w) or pd.isna(avg_volume_20d) or avg_volume_20d == 0: return None
        latest_price, latest_volume = stock_data['Close'].iloc[-1], stock_data['Volume'].iloc[-1]
        if not (latest_price >= (high_52w * 0.98) and latest_volume > (avg_volume_20d * 1.5)): return None
        volume_ratio = latest_volume / avg_volume_20d
        technicals = {'price': round(latest_price, 2), '52w_high': round(high_52w, 2), 'volume_ratio': round(volume_ratio, 2)}
        return ReboundCandidate(ticker=stock_info['ticker'], scenario=self.name, technical_score=int(min(100, (volume_ratio / 3.0) * 100)), fundamentals=self._get_fundamentals_for_candidate(fundamental_data, stock_info), history_df=stock_data, technicals=technicals, rebound_score=0)

class StochasticOversoldScenario(BaseScenario):
    def run(self, stock_data: pd.DataFrame, fundamental_data: Dict, stock_info: Dict) -> Optional[ReboundCandidate]:
        stock_data['stoch_k'], stock_data['stoch_d'] = calculate_stochastic(stock_data['High'], stock_data['Low'], stock_data['Close'])
        if 'stoch_k' not in stock_data.columns or 'stoch_d' not in stock_data.columns or len(stock_data) < 2: return None
        k_today, k_yesterday = stock_data['stoch_k'].iloc[-1], stock_data['stoch_k'].iloc[-2]
        d_today, d_yesterday = stock_data['stoch_d'].iloc[-1], stock_data['stoch_d'].iloc[-2]
        if pd.isna(k_today) or pd.isna(d_today) or pd.isna(k_yesterday) or pd.isna(d_yesterday): return None
        if not (k_yesterday < d_yesterday and k_today > d_today and k_today < 20 and d_today < 20): return None
        technicals = {'price': round(stock_data['Close'].iloc[-1], 2), 'stoch_k': round(k_today, 2), 'stoch_d': round(d_today, 2)}
        return ReboundCandidate(ticker=stock_info['ticker'], scenario=self.name, technical_score=int(100 - (k_today / 20.0 * 100)), fundamentals=self._get_fundamentals_for_candidate(fundamental_data, stock_info), history_df=stock_data, technicals=technicals, rebound_score=0)

# ... (Other existing scenario classes)

class ScenarioRunner:
    @staticmethod
    def load_scenarios_config() -> List[Dict[str, Any]]:
        try:
            with open('scenarios.json', 'r') as f: return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logging.error(f"Could not load scenarios.json: {e}"); return []

    SCENARIO_CLASS_MAP = {
        # ... (Existing scenarios)
        "GarpTrendScenario": GarpTrendScenario,
        "VolumeBreakoutScenario": VolumeBreakoutScenario,
        "StochasticOversoldScenario": StochasticOversoldScenario,
    }
    def __init__(self, progress_callback: Callable = None, progress_percent_callback: Callable = None, is_cancelled_callback: Callable = None):
        self.progress_callback = progress_callback
        self.progress_percent_callback = progress_percent_callback
        self.is_cancelled = is_cancelled_callback or (lambda: False)
        self.scenarios_config = ScenarioRunner.load_scenarios_config()
        self.fundamental_handler = FundamentalDataHandler()
        self.telemetry = {"scan_duration_seconds": 0, "total_tickers_in_universe": 0, "tickers_processed": 0, "tickers_skipped": {"total": 0, "missing_fundamentals": 0, "insufficient_history": 0, "liquidity": 0, "other": 0}}

    def _get_scenario_instance(self, scenario_id: str) -> Optional[BaseScenario]:
        scenario_config = next((s for s in self.scenarios_config if s['id'] == scenario_id), None)
        if not scenario_config: self.progress_callback(f"Error: Scenario with ID '{scenario_id}' not found."); return None
        class_name = scenario_config.get("class"); ScenarioClass = self.SCENARIO_CLASS_MAP.get(class_name)
        if not ScenarioClass: self.progress_callback(f"Error: Scenario class '{class_name}' not implemented."); return None
        return ScenarioClass(name=scenario_config['name'], progress_callback=self.progress_callback, is_cancelled_callback=self.is_cancelled)

    async def run_scan(self, scenario_id: str, ticker: str = None) -> List[ReboundCandidate]:
        start_time = time.time()
        scenario_instance = self._get_scenario_instance(scenario_id)
        scenario_params = next((s.get('params', {}) for s in self.scenarios_config if s['id'] == scenario_id), {})
        if not scenario_instance: return []

        all_tickers_by_market = {"CUSTOM": [ticker]} if ticker else data_loader.get_all_tickers()
        all_tickers_flat = [t for sublist in all_tickers_by_market.values() for t in sublist]
        total_tickers = len(all_tickers_flat)
        self.telemetry['total_tickers_in_universe'] = total_tickers

        historical_data_map = await data_loader.get_historical_data_for_tickers(all_tickers_flat, self.progress_callback, self.is_cancelled)
        fundamental_data_map = await self.fundamental_handler.get_fundamentals_for_tickers(all_tickers_flat, self.progress_callback, self.is_cancelled)

        all_candidates = []
        for market, tickers in all_tickers_by_market.items():
            for ticker_val in tickers:
                self.telemetry['tickers_processed'] += 1
                if self.is_cancelled(): break

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

                try:
                    candidate = scenario_instance.run(stock_data, fundamental_data_map.get(ticker_val), stock_info)
                    if candidate: all_candidates.append(candidate)
                except Exception as e:
                    self.telemetry['tickers_skipped']['total'] += 1; self.telemetry['tickers_skipped']['other'] += 1
                    logging.error(f"Error processing {ticker_val}: {e}", exc_info=True)

        self.telemetry['scan_duration_seconds'] = round(time.time() - start_time, 2)
        return all_candidates