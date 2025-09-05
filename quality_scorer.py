import pandas as pd
import numpy as np

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

def calculate_adaptive_score(ticker_info: dict, ticker_history: pd.DataFrame) -> tuple[int, str]:
    """
    Calculates an adaptive quality score based on available data.
    Returns the scaled score (0-100) and a detailed tooltip string.
    """
    score_components = {}
    total_possible_score = 0

    # Ensure ticker_history has a 'Close' column and is not empty
    if ticker_history is None or 'Close' not in ticker_history.columns or ticker_history.empty:
        return 0, "AQS: 0/100 (0/0 Pkt.)\nData history not available."

    # 1. Trend (max. 30 Pkt.) - Reliable data from history
    sma50 = calculate_sma(ticker_history['Close'], 50)
    sma200 = calculate_sma(ticker_history['Close'], 200)
    if not sma50.empty and not sma200.empty:
        latest_sma50 = sma50.iloc[-1]
        latest_sma200 = sma200.iloc[-1]
        latest_price = ticker_history['Close'].iloc[-1]
        if pd.notna(latest_sma50) and pd.notna(latest_sma200) and pd.notna(latest_price):
            total_possible_score += 30
            if latest_price > latest_sma50 > latest_sma200:
                score_components['Trend'] = (30, 30)
            elif latest_price > latest_sma200:
                score_components['Trend'] = (15, 30)
            else:
                score_components['Trend'] = (0, 30)

    # 2. Momentum (max. 20 Pkt.) - Reliable data from history
    rsi_series = calculate_rsi(ticker_history['Close'], 14)
    if not rsi_series.empty:
        latest_rsi = rsi_series.iloc[-1]
        if pd.notna(latest_rsi):
            total_possible_score += 20
            # Scale RSI: 100 -> 20 pts, 50 -> 10 pts, 30 -> 0 pts
            momentum_punkte = max(0, min(20, (latest_rsi - 30) * (20 / 70)))
            score_components['Momentum'] = (int(momentum_punkte), 20)

    # --- Fundamental data from ticker_info (might be missing) ---

    # 3. Valuation (max. 20 Pkt.) - Check availability
    pe_ratio = ticker_info.get('forwardPE')
    if pe_ratio is not None and isinstance(pe_ratio, (int, float)):
        total_possible_score += 20
        if 0 < pe_ratio < 25:
            # Lower PE is better. PE of 25 -> 0 pts, PE of 0 -> 20 pts.
            bewertung_punkte = max(0, 20 - (pe_ratio * 0.8))
            score_components['Bewertung'] = (int(bewertung_punkte), 20)
        else:
            score_components['Bewertung'] = (0, 20)

    # 4. Stability (max. 20 Pkt.) - Check availability
    beta = ticker_info.get('beta')
    if beta is not None and isinstance(beta, (int, float)):
        total_possible_score += 20
        if beta < 1.5:
            # Lower beta is better. Beta of 1.5 -> 0 pts, Beta of 0 -> 20 pts
            stabilität_punkte = max(0, (1.5 - beta) * (20 / 1.5))
            score_components['Stabilität'] = (int(stabilität_punkte), 20)
        else:
            score_components['Stabilität'] = (0, 20)

    # 5. Profitability (max. 10 Pkt.) - Check availability
    profit_margin = ticker_info.get('profitMargins')
    if profit_margin is not None and isinstance(profit_margin, (int, float)):
        total_possible_score += 10
        # Margin > 10% gets full points
        if profit_margin > 0.1:
            score_components['Profitabilität'] = (10, 10)
        elif profit_margin > 0:
            score_components['Profitabilität'] = (5, 10)
        else:
            score_components['Profitabilität'] = (0, 10)

    # Final Calculation
    total_score = sum(val[0] for val in score_components.values())

    # Scale to 100 for comparability
    scaled_score = int((total_score / total_possible_score) * 100) if total_possible_score > 0 else 0

    # Generate Tooltip
    tooltip_parts = []
    # Sort components for consistent tooltip order
    sorted_components = sorted(score_components.items(), key=lambda item: ['Trend', 'Momentum', 'Bewertung', 'Stabilität', 'Profitabilität'].index(item[0]))

    for name, val in sorted_components:
        tooltip_parts.append(f"{name}: {val[0]}/{val[1]}")

    # Add a part for unavailable data
    all_fund_keys = {'Bewertung': 'forwardPE', 'Stabilität': 'beta', 'Profitabilität': 'profitMargins'}
    available_fund_keys = {k for k, v in all_fund_keys.items() if v in ticker_info and ticker_info[v] is not None}
    unavailable_fund_keys = set(all_fund_keys.keys()) - available_fund_keys
    if unavailable_fund_keys:
        unavailable_str = ", ".join(sorted(list(unavailable_fund_keys)))
        tooltip_parts.append(f"Daten nicht verfügbar: {unavailable_str}")

    tooltip_text = f"AQS: {scaled_score}/100 ({total_score}/{total_possible_score} Pkt.)\n" + "\n".join(tooltip_parts)

    return scaled_score, tooltip_text
