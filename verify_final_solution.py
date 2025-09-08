import sys
import logging
import pandas as pd
import asyncio
from unittest.mock import patch, MagicMock

# We need a QApplication instance for some Qt internals to work
from PyQt6.QtWidgets import QApplication
# Pass '-platform offscreen' to run in a headless environment, avoiding display server issues.
app = QApplication.instance() or QApplication(['-platform', 'offscreen'])

# Now we can import our application code
from ui import ChartWindow
from data_structures import ReboundCandidate
from rebound_scenarios import ScenarioRunner
from fundamental_fetcher import FundamentalFetcher
import config

logging.basicConfig(level=logging.INFO)

async def run_verification():
    """
    Tests that the final, focused chart fix is stable and correct.
    """
    print("--- Starting final chart fix verification ---")

    fetcher = FundamentalFetcher()
    class MockSignal:
        def emit(self, *args, **kwargs): pass
    runner = ScenarioRunner(
        fundamental_fetcher=fetcher,
        progress_callback=MockSignal(),
        progress_percent_callback=MockSignal()
    )

    test_tickers = {'US': ['MSFT', 'T', 'CCI']}
    all_results = []
    with patch('data_loader.get_all_tickers') as mock_get_tickers:
        mock_get_tickers.return_value = test_tickers
        all_results.extend(runner.run_classic_oversold())
        all_results.extend(await runner.run_quality_pullback())

    if not all_results:
        print("  [FAIL] Scan returned no results with test tickers. Cannot verify.")
        return

    print(f"  [INFO] Found {len(all_results)} candidates. Verifying them all...")

    all_passed = True
    for candidate in all_results:
        print(f"  -- Verifying candidate: {candidate.ticker} ({candidate.scenario}) --")
        # Verify the history_df is present
        if not isinstance(candidate.history_df, pd.DataFrame) or candidate.history_df.empty:
            print(f"    [FAIL] history_df for {candidate.ticker} is not a valid DataFrame.")
            all_passed = False
            continue

        # Verify the necessary columns are in the df
        required_cols = {'RSI', 'SMA50', 'SMA200'}
        missing_cols = required_cols - set(candidate.history_df.columns)
        if missing_cols:
            print(f"    [FAIL] history_df is missing required columns: {missing_cols}")
            all_passed = False
            continue

        print("    [PASS] Candidate object is correctly structured.")

        with patch('PyQt6.QtWidgets.QWidget.show') as mock_show, \
             patch('PyQt6.QtWidgets.QMessageBox.warning') as mock_warning:
            try:
                chart_window = ChartWindow(candidate=candidate)
                print("    [PASS] ChartWindow instantiated successfully.")
            except Exception as e:
                print(f"    [FAIL] ChartWindow instantiation failed with an exception.")
                logging.error("An unhandled exception escaped the ChartWindow constructor.", exc_info=True)
                all_passed = False

    if all_passed:
        print("\n--- VERIFICATION PASSED ---")
    else:
        print("\n--- !!! VERIFICATION FAILED !!! ---")


if __name__ == "__main__":
    try:
        config.APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        asyncio.run(run_verification())
    except Exception as e:
        print(f"Verification script crashed: {e}")
