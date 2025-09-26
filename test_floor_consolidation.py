import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Adjust path to import from parent directory if necessary
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))

from scoring import compute_floor_score
from rebound_scenarios import FloorConsolidationScenario
from data_structures import ReboundCandidate
import config

class TestFloorScore(unittest.TestCase):

    def test_compute_floor_score_perfect(self):
        """Test a scenario that should result in a perfect score."""
        score, breakdown = compute_floor_score(
            consolidation_range_pct=0.0,
            max_consolidation_range=0.15,
            crash_depth_pct=0.60,
            volume_ratio=0.0,
            current_price=100.0,
            consol_low=100.0,
            consol_high=115.0 # Price is at the absolute bottom
        )
        self.assertEqual(score, 100)
        self.assertEqual(breakdown['consolidation_tightness_sub_score'], 100)
        self.assertEqual(breakdown['crash_severity_sub_score'], 100)
        self.assertEqual(breakdown['volume_dry_up_sub_score'], 100)
        self.assertEqual(breakdown['price_position_sub_score'], 100)

    def test_compute_floor_score_zero(self):
        """Test a scenario that should result in a zero score."""
        score, breakdown = compute_floor_score(
            consolidation_range_pct=0.15,
            max_consolidation_range=0.15,
            crash_depth_pct=0.0,
            volume_ratio=1.0,
            current_price=115.0, # Price is at the top of the range
            consol_low=100.0,
            consol_high=115.0
        )
        self.assertEqual(score, 0)
        self.assertEqual(breakdown['consolidation_tightness_sub_score'], 0)
        self.assertEqual(breakdown['crash_severity_sub_score'], 0)
        self.assertEqual(breakdown['volume_dry_up_sub_score'], 0)
        self.assertEqual(breakdown['price_position_sub_score'], 0)

    def test_compute_floor_score_mid_range(self):
        """Test a realistic, average scenario."""
        score, breakdown = compute_floor_score(
            consolidation_range_pct=0.075,  # 50% of max range -> 50 score
            max_consolidation_range=0.15,
            crash_depth_pct=0.30,          # 50% of target depth -> 50 score
            volume_ratio=0.5,              # 50% volume ratio -> 50 score
            current_price=107.5,           # 50% within range -> 50 score
            consol_low=100.0,
            consol_high=115.0
        )
        # Expected: (50 * 0.4) + (50 * 0.2) + (50 * 0.2) + (50 * 0.2) = 20 + 10 + 10 + 10 = 50
        self.assertEqual(score, 50)
        self.assertEqual(breakdown['consolidation_tightness_sub_score'], 50)
        self.assertEqual(breakdown['crash_severity_sub_score'], 50)
        self.assertEqual(breakdown['volume_dry_up_sub_score'], 50)
        self.assertEqual(breakdown['price_position_sub_score'], 50)

    def test_price_position_handles_zero_range(self):
        """Ensure price position score is 0 if high == low to avoid division by zero."""
        score, breakdown = compute_floor_score(
            consolidation_range_pct=0.0,
            max_consolidation_range=0.15,
            crash_depth_pct=0.5,
            volume_ratio=0.5,
            current_price=100.0,
            consol_low=100.0,
            consol_high=100.0 # Zero range
        )
        self.assertEqual(breakdown['price_position_sub_score'], 0)

def _generate_mock_data(
    peak_price=200,
    drop_low_price=120,
    consol_low_price=125,
    consol_high_price=140,
    pre_crash_volume=1_000_000,
    consol_volume=500_000,
    days_of_history=400,
    consol_days=60
    ):
    """Generates a simplified, deterministic DataFrame for testing."""
    dates = pd.to_datetime(pd.date_range(end=datetime.now(), periods=days_of_history, freq='B'))

    # Initialize arrays with a baseline price
    base_price = 150
    close = np.full(days_of_history, float(base_price))
    high = np.full(days_of_history, float(base_price + 1))
    low = np.full(days_of_history, float(base_price - 1))
    volume = np.full(days_of_history, float(pre_crash_volume))

    # Define key dates
    consol_start_day = days_of_history - consol_days
    drop_day = consol_start_day - 20
    peak_day = drop_day - 30

    # Set specific values for the key points
    # 1. Peak
    high[peak_day] = peak_price

    # 2. Drop Low
    low[drop_day] = drop_low_price
    # Also set close/high to make it realistic
    close[drop_day] = drop_low_price
    high[drop_day] = drop_low_price + 1

    # 3. Consolidation Period
    volume[consol_start_day:] = consol_volume
    high[consol_start_day:] = consol_high_price
    low[consol_start_day:] = consol_low_price
    close[consol_start_day:] = (consol_low_price + consol_high_price) / 2

    return pd.DataFrame({'High': high, 'Low': low, 'Close': close, 'Volume': volume}, index=dates)

class TestFloorConsolidationScenario(unittest.TestCase):

    def setUp(self):
        """Set up a default scenario instance and mock settings for tests."""
        from settings_manager import settings
        test_settings_file = Path("./test_user_config_scenario.json")
        if test_settings_file.exists():
            test_settings_file.unlink()
        self.settings = settings

        self.settings.set('fc_crash_lookback_period', 126)
        self.settings.set('fc_consolidation_period_days', 60)
        self.settings.set('fc_min_crash_depth', 0.25)
        self.settings.set('fc_max_consolidation_range', 0.15)
        self.settings.set('fc_no_new_low_tolerance', 0.03)
        self.settings.set('fc_volume_ratio_max', 0.7)

        self.scenario = FloorConsolidationScenario(
            name="Floor Consolidation - Universal",
            progress_callback=lambda x: None,
            is_cancelled_callback=lambda: False
        )

    def test_scenario_pass_case(self):
        """Test a clear pass case for the scenario."""
        mock_data = _generate_mock_data()
        result = self.scenario.run(mock_data, {'ticker': 'PASS'})
        self.assertIsNotNone(result)
        self.assertIsInstance(result, ReboundCandidate)
        self.assertGreater(result.technical_score, 0)

    def test_fail_insufficient_crash_depth(self):
        """Test failure when crash depth is too shallow."""
        dates = pd.to_datetime(pd.date_range(end=datetime.now(), periods=200, freq='D'))
        prices = np.linspace(200, 180, 200)
        mock_data = pd.DataFrame({
            'High': prices, 'Low': prices, 'Close': prices,
            'Volume': np.full(200, 1000000)
        }, index=dates)
        result = self.scenario.run(mock_data, {'ticker': 'FAIL'})
        self.assertIsNone(result)

    def test_fail_wide_consolidation_range(self):
        """Test failure when consolidation range is too wide."""
        mock_data = _generate_mock_data(consol_low_price=120, consol_high_price=150)
        result = self.scenario.run(mock_data, {'ticker': 'FAIL'})
        self.assertIsNone(result)

    def test_fail_new_low_in_consolidation(self):
        """Test failure when a new significant low is made during consolidation."""
        mock_data = _generate_mock_data(drop_low_price=120, consol_low_price=110)
        result = self.scenario.run(mock_data, {'ticker': 'FAIL'})
        self.assertIsNone(result)

    def test_fail_high_consolidation_volume(self):
        """Test failure when volume does not dry up during consolidation."""
        mock_data = _generate_mock_data(pre_crash_volume=1_000_000, consol_volume=900_000)
        result = self.scenario.run(mock_data, {'ticker': 'FAIL'})
        self.assertIsNone(result)

if __name__ == '__main__':
    unittest.main()
