import math
from typing import Tuple

def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamps a value to a specified minimum and maximum range."""
    return max(min_val, min(value, max_val))

def zscore(value: float, mean: float, std_dev: float) -> float:
    """
    Calculates the z-score.
    Handles the case where standard deviation is zero to prevent division errors.
    """
    if std_dev is None or std_dev <= 1e-9:
        return 0.0
    if value is None or mean is None:
        return 0.0
    return (value - mean) / std_dev

def metric_to_subscore(value: float, mean: float, std_dev: float, higher_is_better: bool) -> int:
    """
    Normalizes a metric to a 0-100 sub-score using its z-score relative
    to a peer group (e.g., a sector).

    The formula is:
    - For "higher is better": subscore = 50 + 10 * z
    - For "lower is better": subscore = 50 - 10 * z
    The result is clamped to the [0, 100] range.
    """
    z = zscore(value, mean, std_dev)

    if higher_is_better:
        sub = 50 + (10 * z)
    else:
        sub = 50 - (10 * z)

    return int(round(clamp(sub, 0, 100)))

def normalize_bounded_metric(value: float, ideal_range: Tuple[float, float], acceptable_range: Tuple[float, float]) -> int:
    """
    Normalizes a metric with a natural bound (like Payout Ratio) using a
    piecewise linear function.

    - Values within the ideal_range get a score of 100.
    - Values outside the ideal_range but within the acceptable_range get a
      score that declines linearly from 100 to 0.
    - Values outside the acceptable_range get a score of 0.

    Args:
        value: The metric value to normalize.
        ideal_range: A tuple (min, max) for the optimal value range (score 100).
        acceptable_range: A tuple (min, max) for the acceptable value range (score > 0).

    Returns:
        A normalized score from 0 to 100.
    """
    if value is None:
        return 0

    ideal_min, ideal_max = ideal_range
    acceptable_min, acceptable_max = acceptable_range

    if ideal_min <= value <= ideal_max:
        return 100

    score = 0.0
    if acceptable_min <= value < ideal_min:
        # Linear decay from 100 (at ideal_min) to 0 (at acceptable_min)
        range_width = ideal_min - acceptable_min
        if range_width > 1e-9:
            score = ((value - acceptable_min) / range_width) * 100
    elif ideal_max < value <= acceptable_max:
        # Linear decay from 100 (at ideal_max) to 0 (at acceptable_max)
        range_width = acceptable_max - ideal_max
        if range_width > 1e-9:
            score = (1 - ((value - ideal_max) / range_width)) * 100

    return int(round(clamp(score, 0, 100)))

# --- Example Usage (for testing) ---
if __name__ == "__main__":
    print("--- Testing Metric Normalizer ---")

    # --- Test zscore ---
    print("\nTesting zscore:")
    print(f"zscore(120, 100, 10) -> {zscore(120, 100, 10):.2f} (Expected: 2.00)")
    print(f"zscore(85, 100, 10) -> {zscore(85, 100, 10):.2f} (Expected: -1.50)")
    print(f"zscore(100, 100, 0) -> {zscore(100, 100, 0):.2f} (Expected: 0.00)")

    # --- Test metric_to_subscore ---
    print("\nTesting metric_to_subscore (e.g., for Revenue Growth - higher is better):")
    # value, mean, std_dev, higher_is_better
    print(f"Value=15, Mean=5, Std=5 -> Score: {metric_to_subscore(15, 5, 5, True)} (z=2.0, Expected Score: 70)")
    print(f"Value=5, Mean=5, Std=5 -> Score: {metric_to_subscore(5, 5, 5, True)} (z=0.0, Expected Score: 50)")
    print(f"Value=-5, Mean=5, Std=5 -> Score: {metric_to_subscore(-5, 5, 5, True)} (z=-2.0, Expected Score: 30)")
    print(f"Value=100, Mean=5, Std=5 -> Score: {metric_to_subscore(100, 5, 5, True)} (z=19, Expected Score: 100 - clamped)")

    print("\nTesting metric_to_subscore (e.g., for Debt/Equity - lower is better):")
    # value, mean, std_dev, higher_is_better
    print(f"Value=0.1, Mean=0.5, Std=0.2 -> Score: {metric_to_subscore(0.1, 0.5, 0.2, False)} (z=-2.0, Expected Score: 70)")
    print(f"Value=0.5, Mean=0.5, Std=0.2 -> Score: {metric_to_subscore(0.5, 0.5, 0.2, False)} (z=0.0, Expected Score: 50)")
    print(f"Value=0.9, Mean=0.5, Std=0.2 -> Score: {metric_to_subscore(0.9, 0.5, 0.2, False)} (z=2.0, Expected Score: 30)")
    print(f"Value=0.0, Mean=0.5, Std=0.2 -> Score: {metric_to_subscore(0.0, 0.5, 0.2, False)} (z=-2.5, Expected Score: 75)")

    # --- Test normalize_bounded_metric ---
    print("\nTesting normalize_bounded_metric (e.g., for Payout Ratio):")
    # ideal_range=(0.2, 0.6), acceptable_range=(0.0, 0.8)
    ideal = (0.2, 0.6)
    acceptable = (0.0, 0.8)
    print(f"Value=0.4 (ideal) -> Score: {normalize_bounded_metric(0.4, ideal, acceptable)} (Expected: 100)")
    print(f"Value=0.1 (below ideal) -> Score: {normalize_bounded_metric(0.1, ideal, acceptable)} (Expected: 50)")
    print(f"Value=0.7 (above ideal) -> Score: {normalize_bounded_metric(0.7, ideal, acceptable)} (Expected: 50)")
    print(f"Value=0.0 (at min) -> Score: {normalize_bounded_metric(0.0, ideal, acceptable)} (Expected: 0)")
    print(f"Value=0.8 (at max) -> Score: {normalize_bounded_metric(0.8, ideal, acceptable)} (Expected: 0)")
    print(f"Value=0.9 (outside) -> Score: {normalize_bounded_metric(0.9, ideal, acceptable)} (Expected: 0)")

    print("\n--- Test complete ---")
