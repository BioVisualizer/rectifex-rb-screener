import pandas as pd
import talib

def find_recent_candlestick_patterns(history_df: pd.DataFrame, days_to_check: int = 5) -> str:
    """
    Scans the last few days of historical data for common candlestick patterns.

    Args:
        history_df: DataFrame with at least 'Open', 'High', 'Low', 'Close' columns.
        days_to_check: How many recent days to check for patterns.

    Returns:
        A string describing the most recent pattern found, or an empty string.
    """
    if history_df.empty or len(history_df) < 20: # Most patterns need some prior data
        return ""

    # Dictionary of TA-Lib pattern functions and their human-readable names
    # The functions return integers: 100 for bullish, -100 for bearish, 0 for no pattern
    PATTERNS = {
        'CDL2CROWS': 'Two Crows',
        'CDL3BLACKCROWS': 'Three Black Crows',
        'CDL3INSIDE': 'Three Inside Up/Down',
        'CDL3LINESTRIKE': 'Three-Line Strike',
        'CDL3OUTSIDE': 'Three Outside Up/Down',
        'CDL3STARSINSOUTH': 'Three Stars in the South',
        'CDL3WHITESOLDIERS': 'Three White Soldiers',
        'CDLENGULFING': 'Engulfing Pattern',
        'CDLHAMMER': 'Hammer',
        'CDLHANGINGMAN': 'Hanging Man',
        'CDLHARAMI': 'Harami Pattern',
        'CDLHARAMICROSS': 'Harami Cross',
        'CDLHIGHWAVE': 'High-Wave Candle',
        'CDLINVERTEDHAMMER': 'Inverted Hammer',
        'CDLKICKING': 'Kicking',
        'CDLMARUBOZU': 'Marubozu',
        'CDLMORNINGSTAR': 'Morning Star',
        'CDLEVENINGSTAR': 'Evening Star',
        'CDLPIERCING': 'Piercing Line',
        'CDLSHOOTINGSTAR': 'Shooting Star',
        'CDLTAKURI': 'Takuri (Dragonfly Doji)',
        'CDLDOJI': 'Doji',
    }

    # Ensure columns are in the correct format for TA-Lib
    op = history_df['Open']
    hi = history_df['High']
    lo = history_df['Low']
    cl = history_df['Close']

    recent_patterns = []

    for pattern_func_name, pattern_name in PATTERNS.items():
        try:
            pattern_func = getattr(talib, pattern_func_name)
            result = pattern_func(op, hi, lo, cl)

            # Check the last 'days_to_check' days for a signal
            last_results = result.tail(days_to_check)
            for i, value in last_results.items():
                if value != 0:
                    # Calculate how many days ago the signal occurred
                    days_ago = (history_df.index[-1] - i).days

                    signal_type = "Bullish" if value > 0 else "Bearish"

                    # Format the 'days ago' string
                    if days_ago == 0:
                        day_str = "Today"
                    elif days_ago == 1:
                        day_str = "Yesterday"
                    else:
                        day_str = f"{days_ago} days ago"

                    recent_patterns.append({
                        'days_ago': days_ago,
                        'text': f"{signal_type} {pattern_name} ({day_str})"
                    })
        except Exception:
            # Some patterns might fail on certain data, so we continue
            continue

    if not recent_patterns:
        return ""

    # Find the most recent pattern
    most_recent_pattern = min(recent_patterns, key=lambda x: x['days_ago'])

    return most_recent_pattern['text']
