import unittest
import os
import json
from pathlib import Path
import shutil

# Add project root to path to allow direct imports
import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from metrics_normalizer import zscore, metric_to_subscore, normalize_bounded_metric
from scoring import compute_fundamental_score, compute_rebound_score, HIGHER_IS_BETTER_METRICS

class TestMetricsNormalizer(unittest.TestCase):

    def test_zscore(self):
        self.assertAlmostEqual(zscore(120, 100, 10), 2.0)
        self.assertAlmostEqual(zscore(85, 100, 10), -1.5)
        self.assertEqual(zscore(100, 100, 0), 0.0)
        self.assertEqual(zscore(None, 100, 10), 0.0)

    def test_metric_to_subscore(self):
        # Higher is better
        self.assertEqual(metric_to_subscore(15, 5, 5, True), 70)
        self.assertEqual(metric_to_subscore(5, 5, 5, True), 50)
        self.assertEqual(metric_to_subscore(-5, 5, 5, True), 30)
        self.assertEqual(metric_to_subscore(100, 5, 5, True), 100) # Clamped
        self.assertEqual(metric_to_subscore(-100, 5, 5, True), 0) # Clamped

        # Lower is better
        self.assertEqual(metric_to_subscore(0.1, 0.5, 0.2, False), 70)
        self.assertEqual(metric_to_subscore(0.5, 0.5, 0.2, False), 50)
        self.assertEqual(metric_to_subscore(0.9, 0.5, 0.2, False), 30)

    def test_normalize_bounded_metric(self):
        ideal = (0.2, 0.6)
        acceptable = (0.0, 0.8)
        self.assertEqual(normalize_bounded_metric(0.4, ideal, acceptable), 100)
        self.assertEqual(normalize_bounded_metric(0.1, ideal, acceptable), 50)
        self.assertEqual(normalize_bounded_metric(0.7, ideal, acceptable), 50)
        self.assertEqual(normalize_bounded_metric(0.0, ideal, acceptable), 0)
        self.assertEqual(normalize_bounded_metric(0.9, ideal, acceptable), 0)
        self.assertEqual(normalize_bounded_metric(None, ideal, acceptable), 0)


class TestScoring(unittest.TestCase):

    def setUp(self):
        """Set up a mock cache directory and data for testing."""
        self.mock_cache_dir = Path("./mock_test_cache/fundamentals")
        self.mock_cache_dir.mkdir(parents=True, exist_ok=True)

        self.mock_tickers_data = {
            "TECH_A": {
                "ticker": "TECH_A", "last_update": "...", "sector": "Technology",
                "metrics": {'revenue_3yr_cagr': 0.20, 'roe': 0.25, 'debt_equity': 0.4}
            },
            "TECH_B": {
                "ticker": "TECH_B", "last_update": "...", "sector": "Technology",
                "metrics": {'revenue_3yr_cagr': 0.10, 'roe': 0.15, 'debt_equity': 0.6}
            },
        }
        for ticker, data in self.mock_tickers_data.items():
            with open(self.mock_cache_dir / f"{ticker}.json", 'w') as f:
                json.dump(data, f)

        self.mock_sector_stats = {
            "Technology": {
                "medians": {'revenue_3yr_cagr': 0.15, 'roe': 0.20, 'debt_equity': 0.5},
                "std_devs": {'revenue_3yr_cagr': 0.0707, 'roe': 0.0707, 'debt_equity': 0.1414}
            }
        }
        self.test_fund_weights = {'revenue_3yr_cagr': 0.5, 'roe': 0.3, 'debt_equity': 0.2}

    def tearDown(self):
        """Remove the mock cache directory after tests."""
        shutil.rmtree("./mock_test_cache")

    def test_compute_fundamental_score(self):
        our_ticker_fundamentals = self.mock_tickers_data['TECH_A']['metrics']
        our_ticker_sector = self.mock_tickers_data['TECH_A']['sector']

        fund_score, breakdown = compute_fundamental_score(
            fundamentals=our_ticker_fundamentals,
            sector=our_ticker_sector,
            sector_stats=self.mock_sector_stats,
            weights=self.test_fund_weights
        )
        # Expected std devs: rev_cagr=0.0707, roe=0.0707, d/e=0.1414
        # z_rev = (0.2-0.15)/0.0707 = 0.707 -> score = 50 + 7.07 = 57
        # z_roe = (0.25-0.2)/0.0707 = 0.707 -> score = 50 + 7.07 = 57
        # z_de = (0.4-0.5)/0.1414 = -0.707 -> score = 50 - (-7.07) = 57 (lower is better)
        # Final score = 57*0.5 + 57*0.3 + 57*0.2 = 57
        self.assertEqual(fund_score, 57)
        self.assertEqual(breakdown['revenue_3yr_cagr_sub_score'], 57)
        self.assertEqual(breakdown['roe_sub_score'], 57)
        self.assertEqual(breakdown['debt_equity_sub_score'], 57)

    def test_compute_rebound_score(self):
        weights = {'tech': 0.55, 'fund': 0.30, 'market': 0.15}
        score = compute_rebound_score(tech_score=75, fund_score=57, market_score=50, weights=weights)
        # 75*0.55 + 57*0.30 + 50*0.15 = 41.25 + 17.1 + 7.5 = 65.85 -> 66
        self.assertEqual(score, 66)
        # Test clamping
        score_clamped_high = compute_rebound_score(100, 100, 100, weights)
        self.assertEqual(score_clamped_high, 100)
        score_clamped_low = compute_rebound_score(0, 0, 0, weights)
        self.assertEqual(score_clamped_low, 0)


if __name__ == '__main__':
    unittest.main()
