import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pandas as pd

from metrics_normalizer import metric_to_subscore, normalize_bounded_metric, clamp

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# TODO: Move indicator calculations to a dedicated indicators.py file to avoid duplication
def _calculate_sma(data: pd.Series, window: int) -> pd.Series:
    """Calculates the Simple Moving Average."""
    if data is None or len(data) < window:
        return pd.Series(dtype=np.float64)
    return data.rolling(window=window).mean()

# --- Constants ---
# Default weights as per the spec
DEFAULT_FUNDAMENTAL_WEIGHTS = {
    'revenue_3yr_cagr': 0.15,
    'eps_1y_growth': 0.10,
    'roe': 0.10,
    'free_cashflow_yield': 0.10,
    'debt_equity': 0.10,
    'ev_ebit': 0.10,
    'pe_ttm': 0.05,
    'earnings_trend_months': 0.10,
}

# Special weights for the dividend scan
DIVIDEND_SCENARIO_FUNDAMENTAL_WEIGHTS = {
    'payout_ratio': 0.20,
    'debt_equity': 0.15,
    'free_cashflow_yield': 0.15,
    'roe': 0.10,
    'revenue_3yr_cagr': 0.10,
}

# Special weights for the divergence scan, focusing on quality over valuation
DIVERGENCE_SCENARIO_FUNDAMENTAL_WEIGHTS = {
    'revenue_3yr_cagr': 0.25,
    'eps_1y_growth': 0.25,
    'roe': 0.20,
    'free_cashflow_yield': 0.15,
    'debt_equity': 0.15,
}

DEFAULT_REBOUND_SCORE_WEIGHTS = {
    'tech': 0.55,
    'fund': 0.30,
    'market': 0.15,
}

# Define which metrics are "higher is better" for normalization
HIGHER_IS_BETTER_METRICS = {
    'revenue_3yr_cagr', 'eps_1y_growth', 'net_margin', 'roe',
    'free_cashflow_yield', 'earnings_trend_months'
}


def passes_market_context_filter(index_data: pd.DataFrame, sma_period: int) -> bool:
    """
    Checks if the market index is in a positive trend (above its SMA).
    This is a hard filter used by some scenarios before detailed analysis.
    """
    if index_data is None or index_data.empty:
        logging.warning("Market index data is missing, skipping context filter.")
        return False # Default to not passing if data is missing
    sma = _calculate_sma(index_data['Close'], sma_period)
    if sma.empty:
        logging.warning(f"Could not calculate {sma_period}-day SMA for market index.")
        return False
    latest_price = index_data['Close'].iloc[-1]
    latest_sma = sma.iloc[-1]
    if pd.isna(latest_price) or pd.isna(latest_sma):
        logging.warning("Latest price or SMA for index is NaN.")
        return False
    return latest_price > latest_sma


# --- Core Scoring Functions ---

def compute_fundamental_score(
    fundamentals: Dict[str, Any],
    sector: str,
    sector_stats: Dict[str, Any],
    weights: Dict[str, float]
) -> Tuple[int, Dict[str, Any]]:
    """
    Computes the Fundamental Quality Score (0-100) from a set of metrics.

    Args:
        fundamentals: A dictionary of the ticker's fundamental metrics.
        sector: The ticker's sector.
        sector_stats: A nested dictionary containing medians and std_devs for all sectors.
        weights: A dictionary of weights for each fundamental metric.

    Returns:
        A tuple containing the final fundamental score and a breakdown of sub-scores.
    """
    total_score = 0.0
    total_weight = 0.0
    breakdown = {}

    sector_data = sector_stats.get(sector, {})
    sector_median_values = sector_data.get('medians', {})
    sector_std_devs = sector_data.get('std_devs', {})

    for metric, weight in weights.items():
        value = fundamentals.get(metric)
        if value is None:
            continue

        sub_score = 0
        if metric == 'payout_ratio':
            # Special handling for bounded metrics like payout ratio
            sub_score = normalize_bounded_metric(value, ideal_range=(0.15, 0.50), acceptable_range=(0.0, 0.80))
        else:
            # Standard normalization for unbounded metrics
            median = sector_median_values.get(metric)
            std_dev = sector_std_devs.get(metric)

            if median is not None and std_dev is not None:
                higher_is_better = metric in HIGHER_IS_BETTER_METRICS
                sub_score = metric_to_subscore(value, median, std_dev, higher_is_better)

        breakdown[f"{metric}_sub_score"] = sub_score
        total_score += sub_score * weight
        total_weight += weight

    # Normalize the final score based on the weights that were actually used
    if total_weight == 0:
        return 0, breakdown

    final_score = int(round(clamp((total_score / total_weight), 0, 100)))
    return final_score, breakdown


def compute_market_context_score(index_ohlc: Optional[pd.DataFrame]) -> int:
    """
    Computes the Market Context Score (0-100).
    Returns 100 for bullish, 20 for bearish, 50 for neutral/no data.
    """
    if index_ohlc is None or index_ohlc.empty:
        logging.warning("Market index data is missing, returning neutral market context score.")
        return 50 # Neutral

    try:
        # Using 50-day SMA for market context as was done in the original file
        sma50 = index_ohlc['Close'].rolling(window=50).mean()
        if sma50.empty or len(sma50) < 1:
             logging.warning("Could not calculate 50-day SMA for market index.")
             return 50 # Neutral

        latest_price = index_ohlc['Close'].iloc[-1]
        latest_sma = sma50.iloc[-1]

        if pd.isna(latest_price) or pd.isna(latest_sma):
            logging.warning("Latest price or SMA for index is NaN.")
            return 50 # Neutral

        if latest_price > latest_sma:
            return 100 # Bullish
        else:
            return 20 # Bearish

    except Exception as e:
        logging.error(f"Error computing market context score: {e}")
        return 50 # Neutral on error


def compute_rebound_score(
    tech_score: int,
    fund_score: int,
    market_score: int,
    weights: Dict[str, float]
) -> int:
    """
    Computes the final, composite Rebound Score based on the three sub-scores
    and their respective weights.
    """
    score = (tech_score * weights['tech'] +
             fund_score * weights['fund'] +
             market_score * weights['market'])

    return int(round(clamp(score, 0, 100)))


# --- Example Usage (for testing) ---
if __name__ == "__main__":
    print("--- Testing Scoring Module ---")

    # --- 1. Setup Mock Data ---
    mock_cache_dir = Path("./mock_cache/fundamentals")
    mock_cache_dir.mkdir(parents=True, exist_ok=True)

    # Mock data for two tech companies and one financial company
    mock_tickers_data = {
        "TECH_A": {
            "ticker": "TECH_A", "last_update": "...", "sector": "Technology",
            "metrics": {'revenue_3yr_cagr': 0.20, 'roe': 0.25, 'debt_equity': 0.4, 'pe_ttm': 25.0}
        },
        "TECH_B": {
            "ticker": "TECH_B", "last_update": "...", "sector": "Technology",
            "metrics": {'revenue_3yr_cagr': 0.10, 'roe': 0.15, 'debt_equity': 0.6, 'pe_ttm': 15.0}
        },
        "FIN_A": {
            "ticker": "FIN_A", "last_update": "...", "sector": "Financial Services",
            "metrics": {'revenue_3yr_cagr': 0.05, 'roe': 0.10, 'debt_equity': 1.5, 'pe_ttm': 10.0}
        }
    }
    for ticker, data in mock_tickers_data.items():
        with open(mock_cache_dir / f"{ticker}.json", 'w') as f:
            json.dump(data, f)

    mock_sector_stats = {
        "Technology": {
            "medians": {'revenue_3yr_cagr': 0.15, 'roe': 0.20, 'debt_equity': 0.5},
            "std_devs": {'revenue_3yr_cagr': 0.0707, 'roe': 0.0707, 'debt_equity': 0.1414}
        }
    }

    # Our target ticker for scoring
    our_ticker_fundamentals = mock_tickers_data['TECH_A']['metrics']
    our_ticker_sector = mock_tickers_data['TECH_A']['sector']

    print(f"\nTarget Ticker Metrics (TECH_A): {our_ticker_fundamentals}")
    print(f"Sector Stats (Technology): {mock_sector_stats['Technology']}")

    # --- 2. Test Fundamental Score ---
    print("\n--- Computing Fundamental Score ---")

    # Using a subset of weights for this test
    test_fund_weights = {'revenue_3yr_cagr': 0.5, 'roe': 0.3, 'debt_equity': 0.2}

    fund_score, fund_breakdown = compute_fundamental_score(
        fundamentals=our_ticker_fundamentals,
        sector=our_ticker_sector,
        sector_stats=mock_sector_stats,
        weights=test_fund_weights
    )

    print(f"Fundamental Score: {fund_score}")
    print(f"Breakdown: {fund_breakdown}")
    # Expected:
    # rev_cagr: val=0.20, mean=0.15, std=0.07 -> z=0.7 -> score=57
    # roe: val=0.25, mean=0.20, std=0.07 -> z=0.7 -> score=57
    # d/e: val=0.4, mean=0.5, std=0.14 -> z=-0.7 -> score=57 (lower is better)
    # Final = 57*0.5 + 57*0.3 + 57*0.2 = 57

    # --- 3. Test Placeholder Scores ---
    print("\n--- Computing Placeholder Scores ---")
    tech_score, tech_breakdown = compute_technical_score(pd.DataFrame(), {})
    market_score = compute_market_context_score(None)
    print(f"Technical Score (placeholder): {tech_score}")
    print(f"Market Context Score (placeholder): {market_score}")

    # --- 4. Test Final Rebound Score ---
    print("\n--- Computing Final Rebound Score ---")
    rebound_score = compute_rebound_score(
        tech_score=tech_score,
        fund_score=fund_score,
        market_score=market_score,
        weights=DEFAULT_REBOUND_SCORE_WEIGHTS
    )
    print(f"Final Rebound Score: {rebound_score}")
    expected_rebound = int(round(75 * 0.55 + 57 * 0.30 + 100 * 0.15))
    print(f"(Calculation: 75*0.55 + {fund_score}*0.30 + 100*0.15 = {expected_rebound})")

    # --- 5. Clean up mock cache ---
    import shutil
    shutil.rmtree("./mock_cache")
    print("\n--- Test complete ---")
