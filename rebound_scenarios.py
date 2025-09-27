# rebound_scenarios.py
# Contains the logic for different screening scenarios.

import pandas as pd
import numpy as np
import logging
from typing import List, Dict, Any, Callable, Optional
import json
from abc import ABC, abstractmethod
import time
import asyncio
import functools

# App-specific imports
import config
import data_loader
from data_structures import ReboundCandidate, safe_get
from fundamentals import FundamentalDataHandler
from scoring import compute_floor_score
from settings_manager import settings

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

def calculate_bollinger_bands(data: pd.Series, window=20, num_std_dev=2) -> tuple[pd.Series, pd.Series, pd.Series]:
    if data is None or len(data) < window:
        return pd.Series(dtype=np.float64), pd.Series(dtype=np.float64), pd.Series(dtype=np.float64)
    rolling_mean = data.rolling(window=window).mean()
    rolling_std = data.rolling(window=window).std()
    upper_band = rolling_mean + (rolling_std * num_std_dev)
    lower_band = rolling_mean - (rolling_std * num_std_dev)
    return upper_band, rolling_mean, lower_band

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class BaseScenario(ABC):
    def __init__(self, name: str, progress_callback: Callable, is_cancelled_callback: Callable):
        self._name = name
        self.progress_callback = progress_callback
        self.is_cancelled = is_cancelled_callback

    @abstractmethod
    def run(self, stock_data: pd.DataFrame, stock_info: Dict) -> Optional[ReboundCandidate]:
        pass

# --- SCENARIO IMPLEMENTATIONS ---

class ClassicOversoldScenario(BaseScenario):
    def run(self, stock_data: pd.DataFrame, stock_info: Dict) -> Optional[ReboundCandidate]:
        params = settings.get('classic_oversold_params', {"rsi_threshold": 30, "sma200_dist_pct": 5, "low90_dist_pct": 3})
        stock_data['rsi'] = calculate_rsi(stock_data['Close'])
        stock_data['sma200'] = calculate_sma(stock_data['Close'], 200)
        if len(stock_data) < 90 or 'sma200' not in stock_data.columns or stock_data['sma200'].isnull().all(): return None

        low_90d = stock_data['Low'][-90:].min()
        latest_price = stock_data['Close'].iloc[-1]

        if stock_data['rsi'].iloc[-1] > params['rsi_threshold']: return None
        if abs(latest_price - stock_data['sma200'].iloc[-1]) / stock_data['sma200'].iloc[-1] > params['sma200_dist_pct'] / 100: return None
        if abs(latest_price - low_90d) / low_90d > params['low90_dist_pct'] / 100: return None

        technicals = {'price': latest_price, 'rsi': round(stock_data['rsi'].iloc[-1], 2)}
        fundamentals = {'name': stock_info.get('shortName', 'N/A')}
        score = 100 - stock_data['rsi'].iloc[-1]
        return ReboundCandidate(ticker=stock_info.get('symbol'), scenario=self._name, rebound_score=int(score), technical_score=int(score), fundamentals=fundamentals, history_df=stock_data, technicals=technicals)

class FloorConsolidationScenario(BaseScenario):
    def run(self, stock_data: pd.DataFrame, stock_info: Dict) -> Optional[ReboundCandidate]:
        lookback = settings.get('fc_crash_lookback_period')
        min_depth = settings.get('fc_min_crash_depth')
        consol_days = settings.get('fc_consolidation_period_days')
        max_range = settings.get('fc_max_consolidation_range')
        volume_ratio_max = settings.get('fc_volume_ratio_max', 1.0) # Default to 1.0 if not set
        no_new_low_tolerance = settings.get('fc_no_new_low_tolerance', 0.03)

        if len(stock_data) < lookback:
            return None

        # --- 1. Find the Peak and the subsequent Crash ---
        lookback_data = stock_data.iloc[-lookback:]
        peak_idx = lookback_data['High'].idxmax()
        peak_price = lookback_data['High'].max()

        data_after_peak = lookback_data.loc[peak_idx:]
        if len(data_after_peak) < consol_days:
            return None

        trough_price_after_peak = data_after_peak['Low'].min()
        crash_depth_pct = (peak_price - trough_price_after_peak) / peak_price if peak_price > 0 else 0

        if crash_depth_pct < min_depth:
            return None

        # --- 2. Analyze the Consolidation Period ---
        consol_data = stock_data.iloc[-consol_days:]
        consol_low = consol_data['Low'].min()
        consol_high = consol_data['High'].max()

        if consol_low < trough_price_after_peak * (1 - no_new_low_tolerance):
            return None

        consolidation_range_pct = (consol_high - consol_low) / consol_low if consol_low > 0 else 0
        if consolidation_range_pct > max_range:
            return None

        # --- 3. Analyze Volume ---
        pre_crash_period_end_idx = stock_data.index.get_loc(peak_idx)
        pre_crash_period_start_idx = max(0, pre_crash_period_end_idx - 60)
        pre_crash_data = stock_data.iloc[pre_crash_period_start_idx:pre_crash_period_end_idx]

        if pre_crash_data.empty:
            return None

        pre_crash_avg_volume = pre_crash_data['Volume'].mean()
        consol_avg_volume = consol_data['Volume'].mean()
        volume_ratio = consol_avg_volume / pre_crash_avg_volume if pre_crash_avg_volume > 0 else float('inf')

        if volume_ratio > volume_ratio_max:
            return None

        # --- 4. All checks passed, compute score and create candidate ---
        current_price = stock_data['Close'].iloc[-1]
        score, score_breakdown = compute_floor_score(
            consolidation_range_pct=consolidation_range_pct,
            max_consolidation_range=max_range,
            crash_depth_pct=crash_depth_pct,
            volume_ratio=volume_ratio,
            current_price=current_price,
            consol_low=consol_low,
            consol_high=consol_high
        )

        technicals = {
            'price': current_price,
            'Crash %': f"{crash_depth_pct:.1%}",
            'Consol. Range %': f"{consolidation_range_pct:.1%}",
            'Drop Date': peak_idx.strftime('%Y-%m-%d'),
            'period_high_val': peak_price,
            'period_high_idx': peak_idx,
            'drop_low_val': trough_price_after_peak,
            'consol_start_idx': consol_data.index[0],
            'consol_end_idx': consol_data.index[-1],
        }
        fundamentals = {'name': stock_info.get('shortName', 'N/A')}

        return ReboundCandidate(
            ticker=stock_info.get('symbol'),
            scenario=self._name,
            rebound_score=score,
            technical_score=score,
            fundamentals=fundamentals,
            history_df=stock_data,
            technicals=technicals,
            score_breakdown=score_breakdown
        )

class MeanReversionScenario(BaseScenario):
    def run(self, stock_data: pd.DataFrame, stock_info: Dict) -> Optional[ReboundCandidate]:
        if len(stock_data) < 20: return None
        upper, middle, lower = calculate_bollinger_bands(stock_data['Close'])
        if lower.isnull().all(): return None

        latest_price = stock_data['Close'].iloc[-1]
        lower_band = lower.iloc[-1]

        if latest_price > lower_band: return None

        technicals = {'price': latest_price, 'lower_bb': lower_band}
        fundamentals = {'name': stock_info.get('shortName', 'N/A')}
        return ReboundCandidate(ticker=stock_info.get('symbol'), scenario=self._name, rebound_score=75, technical_score=75, fundamentals=fundamentals, history_df=stock_data, technicals=technicals)

class MomentumBreakoutScenario(BaseScenario):
    def run(self, stock_data: pd.DataFrame, stock_info: Dict) -> Optional[ReboundCandidate]:
        if len(stock_data) < 252: return None
        high_52w = stock_data['High'][:-1].max()
        latest_price = stock_data['Close'].iloc[-1]
        if latest_price < high_52w: return None

        technicals = {'price': latest_price, '52w_high': high_52w}
        fundamentals = {'name': stock_info.get('shortName', 'N/A')}
        return ReboundCandidate(ticker=stock_info.get('symbol'), scenario=self._name, rebound_score=85, technical_score=85, fundamentals=fundamentals, history_df=stock_data, technicals=technicals)

class GoldenCrossScenario(BaseScenario):
    def run(self, stock_data: pd.DataFrame, stock_info: Dict) -> Optional[ReboundCandidate]:
        if len(stock_data) < 200: return None
        stock_data['sma50'] = calculate_sma(stock_data['Close'], 50)
        stock_data['sma200'] = calculate_sma(stock_data['Close'], 200)
        if stock_data['sma50'].isnull().all() or stock_data['sma200'].isnull().all(): return None

        sma50_today = stock_data['sma50'].iloc[-1]
        sma200_today = stock_data['sma200'].iloc[-1]
        sma50_yesterday = stock_data['sma50'].iloc[-2]
        sma200_yesterday = stock_data['sma200'].iloc[-2]

        if not (sma50_yesterday < sma200_yesterday and sma50_today > sma200_today): return None

        technicals = {'price': stock_data['Close'].iloc[-1], 'sma50': sma50_today, 'sma200': sma200_today}
        fundamentals = {'name': stock_info.get('shortName', 'N/A')}
        return ReboundCandidate(ticker=stock_info.get('symbol'), scenario=self._name, rebound_score=90, technical_score=90, fundamentals=fundamentals, history_df=stock_data, technicals=technicals)

class QualityPullbackScenario(BaseScenario):
    def run(self, stock_data: pd.DataFrame, stock_info: Dict) -> Optional[ReboundCandidate]:
        params = settings.get('quality_pullback_params', {"sma50_proximity_pct": 3})
        if len(stock_data) < 200: return None
        stock_data['sma50'] = calculate_sma(stock_data['Close'], 50)
        stock_data['sma200'] = calculate_sma(stock_data['Close'], 200)
        if stock_data['sma50'].isnull().all() or stock_data['sma200'].isnull().all(): return None

        if stock_data['sma50'].iloc[-1] < stock_data['sma200'].iloc[-1]: return None

        latest_price = stock_data['Close'].iloc[-1]
        sma50 = stock_data['sma50'].iloc[-1]
        if abs(latest_price - sma50) / sma50 > params['sma50_proximity_pct'] / 100: return None

        if safe_get(stock_info, 'revenueGrowth', 0) <= 0 or safe_get(stock_info, 'earningsGrowth', 0) <= 0: return None

        technicals = {'price': latest_price, 'sma50': sma50}
        fundamentals = {'name': stock_info.get('shortName', 'N/A')}
        return ReboundCandidate(ticker=stock_info.get('symbol'), scenario=self._name, rebound_score=80, technical_score=80, fundamentals=fundamentals, history_df=stock_data, technicals=technicals)

class FundamentalDivergenceScenario(BaseScenario):
    def run(self, stock_data: pd.DataFrame, stock_info: Dict) -> Optional[ReboundCandidate]:
        params = settings.get('fundamental_divergence_params', {
            "min_revenue_growth": 0.03,
            "min_earnings_growth": 0.02,
            "min_return_on_equity": 0.08,
            "max_debt_to_equity": 2.0,
            "lookback_days": 180,
            "min_price_return_pct": -0.35,
            "max_price_return_pct": 0.10,
            "range_lookback_days": 60,
            "max_range_pct": 0.30,
            "min_metrics_to_pass": 2,
            "min_avg_volume": 100000,
        })

        lookback_days = params.get('lookback_days', 180)
        if stock_data is None or len(stock_data) < max(lookback_days, 60):
            return None

        # --- Check liquidity to avoid illiquid names ---
        avg_volume_30d = stock_data['Volume'].iloc[-30:].mean() if 'Volume' in stock_data.columns and len(stock_data) >= 30 else None
        if avg_volume_30d is None or pd.isna(avg_volume_30d) or avg_volume_30d < params.get('min_avg_volume', 0):
            return None

        # --- Fundamental strength signals (lenient: need only a subset to pass) ---
        fundamentals_map = {
            'revenueGrowth': (safe_get(stock_info, 'revenueGrowth'), params.get('min_revenue_growth', 0)),
            'earningsGrowth': (safe_get(stock_info, 'earningsGrowth'), params.get('min_earnings_growth', 0)),
            'returnOnEquity': (safe_get(stock_info, 'returnOnEquity'), params.get('min_return_on_equity', 0)),
            'profitMargins': (safe_get(stock_info, 'profitMargins'), 0),
        }
        debt_to_equity = safe_get(stock_info, 'debtToEquity')

        positive_metrics = 0
        fundamentals_summary: Dict[str, Any] = {'name': stock_info.get('shortName', 'N/A')}

        for metric, (value, threshold) in fundamentals_map.items():
            if value is not None:
                fundamentals_summary[metric] = value
                if metric == 'profitMargins':
                    if value > 0:
                        positive_metrics += 1
                elif value >= threshold:
                    positive_metrics += 1

        if debt_to_equity is not None:
            fundamentals_summary['debtToEquity'] = debt_to_equity
            if debt_to_equity <= params.get('max_debt_to_equity', float('inf')):
                positive_metrics += 1

        if positive_metrics < params.get('min_metrics_to_pass', 2):
            return None

        # --- Price behaviour checks: underperformance / stagnation ---
        lookback_slice = stock_data.iloc[-lookback_days:]
        start_close = lookback_slice['Close'].iloc[0]
        latest_close = lookback_slice['Close'].iloc[-1]
        if pd.isna(start_close) or pd.isna(latest_close) or start_close <= 0:
            return None

        price_return = (latest_close / start_close) - 1
        if price_return > params.get('max_price_return_pct', 0.10) or price_return < params.get('min_price_return_pct', -0.35):
            return None

        high_in_period = lookback_slice['High'].max() if 'High' in lookback_slice.columns else lookback_slice['Close'].max()
        drawdown_pct = None
        if high_in_period and not pd.isna(high_in_period) and high_in_period > 0:
            drawdown_pct = (latest_close / high_in_period) - 1

        range_lookback = min(params.get('range_lookback_days', 60), len(stock_data))
        recent_slice = stock_data.iloc[-range_lookback:]
        recent_close_max = recent_slice['Close'].max()
        recent_close_min = recent_slice['Close'].min()
        recent_close_mean = recent_slice['Close'].mean()
        price_range_pct = None
        if recent_close_mean and not pd.isna(recent_close_mean) and recent_close_mean > 0:
            price_range_pct = (recent_close_max - recent_close_min) / recent_close_mean

        if price_range_pct is not None and price_range_pct > params.get('max_range_pct', 0.30):
            return None

        # --- Compute a simple rebound score emphasising fundamentals ---
        max_possible_metrics = max(params.get('min_metrics_to_pass', 2), len(fundamentals_map) + 1)
        score_base = 55
        score = score_base + int(min(positive_metrics, max_possible_metrics) / max_possible_metrics * 40)
        if drawdown_pct is not None and drawdown_pct < 0:
            # Reward deeper discounts up to -35%
            score += int(min(abs(drawdown_pct), 0.35) / 0.35 * 5)
        score = max(50, min(score, 95))

        technicals = {
            'price': round(latest_close, 2),
            '6m_return_pct': f"{price_return:.1%}",
            'avg_volume_30d': int(avg_volume_30d) if avg_volume_30d is not None else None,
        }
        if drawdown_pct is not None:
            technicals['drawdown_from_high'] = f"{drawdown_pct:.1%}"
        if price_range_pct is not None:
            technicals['range_pct'] = f"{price_range_pct:.1%}"

        return ReboundCandidate(
            ticker=stock_info.get('symbol'),
            scenario=self._name,
            rebound_score=score,
            technical_score=score,
            fundamentals=fundamentals_summary,
            history_df=stock_data,
            technicals=technicals,
        )

class VolatilitySqueezeScenario(BaseScenario):
    def run(self, stock_data: pd.DataFrame, stock_info: Dict) -> Optional[ReboundCandidate]:
        params = settings.get('volatility_squeeze_params', {"bbw_percentile": 10})
        if len(stock_data) < 20: return None
        upper, middle, lower = calculate_bollinger_bands(stock_data['Close'])
        if upper.isnull().all(): return None

        bb_width = (upper - lower) / middle
        if bb_width.iloc[-1] < bb_width.quantile(params['bbw_percentile'] / 100):
            technicals = {'price': stock_data['Close'].iloc[-1], 'bb_width': bb_width.iloc[-1]}
            fundamentals = {'name': stock_info.get('shortName', 'N/A')}
            return ReboundCandidate(ticker=stock_info.get('symbol'), scenario=self._name, rebound_score=70, technical_score=70, fundamentals=fundamentals, history_df=stock_data, technicals=technicals)
        return None

class HighQualityDividendScenario(BaseScenario):
    def run(self, stock_data: pd.DataFrame, stock_info: Dict) -> Optional[ReboundCandidate]:
        params = settings.get('high_quality_dividend_params', {"min_yield": 0.03, "max_payout_ratio": 0.7, "max_debt_equity": 1.0})

        div_yield = safe_get(stock_info, 'dividendYield')
        payout_ratio = safe_get(stock_info, 'payoutRatio')
        debt_to_equity = safe_get(stock_info, 'debtToEquity')

        if not all([div_yield, payout_ratio, debt_to_equity]): return None
        if div_yield < params['min_yield']: return None
        if payout_ratio > params['max_payout_ratio']: return None
        if debt_to_equity > params['max_debt_equity']: return None

        technicals = {'price': stock_data['Close'].iloc[-1]}
        fundamentals = {'name': stock_info.get('shortName', 'N/A'), 'dividendYield': div_yield}
        return ReboundCandidate(ticker=stock_info.get('symbol'), scenario=self._name, rebound_score=88, technical_score=88, fundamentals=fundamentals, history_df=stock_data, technicals=technicals)

class GarpTrendScenario(BaseScenario):
    def run(self, stock_data: pd.DataFrame, stock_info: Dict) -> Optional[ReboundCandidate]:
        earnings_growth = safe_get(stock_info, 'earningsGrowth')
        pe_ratio = safe_get(stock_info, 'trailingPE')
        if not (earnings_growth is not None and pe_ratio is not None and earnings_growth > 0.10 and 0 < pe_ratio < 25): return None
        stock_data['SMA50'] = calculate_sma(stock_data['Close'], 50)
        if stock_data['SMA50'].isnull().all(): return None
        latest_price, sma50 = stock_data['Close'].iloc[-1], stock_data['SMA50'].iloc[-1]
        if pd.isna(latest_price) or pd.isna(sma50) or latest_price <= sma50: return None
        technicals = {'price': round(latest_price, 2), 'sma50': round(sma50, 2)}
        fundamentals = {'earningsGrowth': earnings_growth, 'trailingPE': pe_ratio, 'name': stock_info.get('shortName', 'N/A')}
        return ReboundCandidate(ticker=stock_info.get('symbol'), scenario=self._name, rebound_score=100, technical_score=100, fundamentals=fundamentals, history_df=stock_data, technicals=technicals)

class VolumeBreakoutScenario(BaseScenario):
    def run(self, stock_data: pd.DataFrame, stock_info: Dict) -> Optional[ReboundCandidate]:
        if len(stock_data) < 252: return None
        high_52w = stock_data['High'][:-1].max()
        avg_volume_20d = stock_data['Volume'].iloc[-21:-1].mean()
        if pd.isna(high_52w) or pd.isna(avg_volume_20d) or avg_volume_20d == 0: return None
        latest_price, latest_volume = stock_data['Close'].iloc[-1], stock_data['Volume'].iloc[-1]
        if not (latest_price >= (high_52w * 0.98) and latest_volume > (avg_volume_20d * 1.5)): return None
        volume_ratio = latest_volume / avg_volume_20d
        technicals = {'price': round(latest_price, 2), '52w_high': round(high_52w, 2), 'volume_ratio': round(volume_ratio, 2)}
        fundamentals = {'name': stock_info.get('shortName', 'N/A')}
        score = int(min(100, (volume_ratio / 3.0) * 100))
        return ReboundCandidate(ticker=stock_info.get('symbol'), scenario=self._name, rebound_score=score, technical_score=score, fundamentals=fundamentals, history_df=stock_data, technicals=technicals)

class StochasticOversoldScenario(BaseScenario):
    def run(self, stock_data: pd.DataFrame, stock_info: Dict) -> Optional[ReboundCandidate]:
        stock_data['stoch_k'], stock_data['stoch_d'] = calculate_stochastic(stock_data['High'], stock_data['Low'], stock_data['Close'])
        if 'stoch_k' not in stock_data.columns or 'stoch_d' not in stock_data.columns or len(stock_data) < 2: return None
        k_today, k_yesterday = stock_data['stoch_k'].iloc[-1], stock_data['stoch_k'].iloc[-2]
        d_today, d_yesterday = stock_data['stoch_d'].iloc[-1], stock_data['stoch_d'].iloc[-2]
        if pd.isna(k_today) or pd.isna(d_today) or pd.isna(k_yesterday) or pd.isna(d_yesterday): return None
        if not (k_yesterday < d_yesterday and k_today > d_today and k_today < 20 and d_today < 20): return None
        technicals = {'price': round(stock_data['Close'].iloc[-1], 2), 'stoch_k': round(k_today, 2), 'stoch_d': round(d_today, 2)}
        fundamentals = {'name': stock_info.get('shortName', 'N/A')}
        score = int(100 - (k_today / 20.0 * 100))
        return ReboundCandidate(ticker=stock_info.get('symbol'), scenario=self._name, rebound_score=score, technical_score=score, fundamentals=fundamentals, history_df=stock_data, technicals=technicals)


class ScenarioRunner:
    @staticmethod
    def load_scenarios_config() -> List[Dict[str, Any]]:
        try:
            with open(config.BASE_DIR / 'scenarios.json', 'r') as f: return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logging.error(f"Could not load scenarios.json: {e}"); return []

    SCENARIO_CLASS_MAP = {
        "ClassicOversoldScenario": ClassicOversoldScenario,
        "FloorConsolidationScenario": FloorConsolidationScenario,
        "MeanReversionScenario": MeanReversionScenario,
        "MomentumBreakoutScenario": MomentumBreakoutScenario,
        "GoldenCrossScenario": GoldenCrossScenario,
        "QualityPullbackScenario": QualityPullbackScenario,
        "FundamentalDivergenceScenario": FundamentalDivergenceScenario,
        "VolatilitySqueezeScenario": VolatilitySqueezeScenario,
        "HighQualityDividendScenario": HighQualityDividendScenario,
        "GarpTrendScenario": GarpTrendScenario,
        "VolumeBreakoutScenario": VolumeBreakoutScenario,
        "StochasticOversoldScenario": StochasticOversoldScenario,
    }
    def __init__(self, progress_callback: Callable = None, progress_percent_callback: Callable = None, is_cancelled_callback: Callable = None):
        self.progress_callback = progress_callback
        self.progress_percent_callback = progress_percent_callback
        self.is_cancelled_callback = is_cancelled_callback or (lambda: False)
        self.scenarios_config = self.load_scenarios_config()
        self.fundamental_handler = FundamentalDataHandler()
        self.telemetry = {
            "scan_duration_seconds": 0,
            "total_tickers_in_universe": 0,
            "tickers_processed": 0,
            "tickers_skipped": {
                "total": 0,
                "missing_fundamentals": 0,
                "insufficient_history": 0,
                "liquidity": 0,
                "other": 0,
            },
            "data_fetch_failures": {},
        }

    def cancel(self):
        """Allows the AnalysisWorker to signal cancellation."""
        self.is_cancelled_callback = lambda: True

    async def _validate_ticker_list(self, tickers: List[str]) -> List[str]:
        """
        Performs a quick validation on a list of tickers to see if they return any data.
        This is a pre-filtering step to avoid wasting time on delisted/invalid tickers.
        """
        if not tickers:
            return []

        if self.progress_callback:
            self.progress_callback.emit("Validating ticker universe...")

        validated_tickers = []
        total_tickers = len(tickers)

        async def check_ticker(ticker, semaphore):
            async with semaphore:
                if self.is_cancelled_callback():
                    return None
                # Use a short period and fewer retries for a quick check.
                # fetch_history is already async, so we can await it directly.
                validation_period = getattr(config, "VALIDATION_HISTORY_PERIOD", "6mo")
                df, _ = await data_loader.fetch_history(ticker=ticker, period=validation_period, retries=1)
                return ticker if df is not None and not df.empty else None

        semaphore = asyncio.Semaphore(20)  # Use higher concurrency for these quick checks
        tasks = [check_ticker(t, semaphore) for t in tickers]

        processed_count = 0
        for future in asyncio.as_completed(tasks):
            if self.is_cancelled_callback():
                break
            result = await future
            if result:
                validated_tickers.append(result)

            processed_count += 1
            if self.progress_percent_callback:
                # We can allocate, say, the first 30% of the progress bar to validation
                progress_pct = int((processed_count / total_tickers) * 30)
                self.progress_percent_callback.emit(progress_pct)

            if self.progress_callback:
                 self.progress_callback.emit(f"Validating tickers ({processed_count}/{total_tickers})...")

        num_valid = len(validated_tickers)
        if total_tickers > 0:
            valid_pct = (num_valid / total_tickers) * 100
            if self.progress_callback:
                self.progress_callback.emit(f"Validation complete. Found {num_valid}/{total_tickers} ({valid_pct:.1f}%) valid tickers.")

        return validated_tickers

    def _get_scenario_instance(self, scenario_id: str) -> Optional[BaseScenario]:
        scenario_config = next((s for s in self.scenarios_config if s['id'] == scenario_id), None)
        if not scenario_config:
            if self.progress_callback: self.progress_callback.emit(f"Error: Scenario with ID '{scenario_id}' not found.")
            return None
        class_name = scenario_config.get("class")
        ScenarioClass = self.SCENARIO_CLASS_MAP.get(class_name)
        if not ScenarioClass:
            if self.progress_callback: self.progress_callback.emit(f"Error: Scenario class '{class_name}' not implemented.")
            return None
        return ScenarioClass(name=scenario_config['name'], progress_callback=self.progress_callback, is_cancelled_callback=self.is_cancelled_callback)

    async def run_scan(self, scenario_id: str, ticker: str = None) -> List[ReboundCandidate]:
        start_time = time.time()
        scenario_instance = self._get_scenario_instance(scenario_id)
        if not scenario_instance: return []

        scenario_params = next((s.get('params', {}) for s in self.scenarios_config if s['id'] == scenario_id), {})

        all_tickers_by_market = {"CUSTOM": [ticker]} if ticker else data_loader.get_all_tickers()
        all_tickers_flat = [t for sublist in all_tickers_by_market.values() for t in sublist]
        self.telemetry['total_tickers_in_universe'] = len(all_tickers_flat)

        # --- Pre-scan validation ---
        valid_tickers = await self._validate_ticker_list(all_tickers_flat)
        if self.is_cancelled_callback(): return []

        total_tickers_to_scan = len(valid_tickers)
        if total_tickers_to_scan == 0:
            if self.progress_callback: self.progress_callback.emit("No valid tickers found to scan.")
            self.telemetry['scan_duration_seconds'] = round(time.time() - start_time, 2)
            return []
        # --- End validation ---

        historical_data_map = await data_loader.get_historical_data_for_tickers(valid_tickers, self.progress_callback, self.is_cancelled_callback)
        self.telemetry['data_fetch_failures'] = data_loader.get_last_failed_tickers()
        fundamental_data_map = await self.fundamental_handler.get_fundamentals_for_tickers(valid_tickers, self.progress_callback, self.is_cancelled_callback)

        all_candidates = []
        for i, ticker_val in enumerate(valid_tickers):
            if self.is_cancelled_callback(): break

            if self.progress_callback:
                self.progress_callback.emit(f"Analyzing {i + 1}/{total_tickers_to_scan}: {ticker_val}")

            if self.progress_percent_callback:
                # Validation uses 0-30%, so scanning uses 30-100% of the progress bar
                progress_pct = 30 + int((i + 1) / total_tickers_to_scan * 70)
                self.progress_percent_callback.emit(progress_pct)

            self.telemetry['tickers_processed'] += 1

            fund_data_packet = fundamental_data_map.get(ticker_val)
            if not fund_data_packet or 'info' not in fund_data_packet:
                self.telemetry['tickers_skipped']['total'] += 1
                self.telemetry['tickers_skipped']['missing_fundamentals'] += 1
                continue

            stock_info = fund_data_packet['info']

            # Add a defensive check to ensure stock_info is a dictionary.
            if not stock_info:
                self.telemetry['tickers_skipped']['total'] += 1
                self.telemetry['tickers_skipped']['missing_fundamentals'] += 1
                continue

            min_market_cap = settings.get('min_market_cap')
            min_avg_volume = settings.get('min_avg_volume_30d')
            if (stock_info.get('marketCap', 0) or 0) < min_market_cap or \
               (stock_info.get('averageVolume', 0) or 0) < min_avg_volume:
                self.telemetry['tickers_skipped']['total'] += 1
                self.telemetry['tickers_skipped']['liquidity'] += 1
                continue

            stock_data = historical_data_map.get(ticker_val)
            min_history = scenario_params.get('min_history')
            if stock_data is None or (min_history and len(stock_data) < min_history):
                self.telemetry['tickers_skipped']['total'] += 1
                self.telemetry['tickers_skipped']['insufficient_history'] += 1
                continue

            required_info = scenario_params.get('required_info', [])
            if any(safe_get(stock_info, key) is None for key in required_info):
                self.telemetry['tickers_skipped']['total'] += 1
                self.telemetry['tickers_skipped']['missing_fundamentals'] += 1
                continue

            try:
                candidate = scenario_instance.run(stock_data, stock_info)
                if candidate: all_candidates.append(candidate)
            except Exception as e:
                self.telemetry['tickers_skipped']['total'] += 1
                self.telemetry['tickers_skipped']['other'] += 1
                logging.error(f"Error processing {ticker_val}: {e}", exc_info=True)

        self.telemetry['scan_duration_seconds'] = round(time.time() - start_time, 2)
        return all_candidates