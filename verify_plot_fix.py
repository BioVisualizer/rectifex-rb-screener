import sys
import logging
from unittest.mock import patch

# We need a QApplication instance to create widgets, but we can't show them.
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

# Now we can import our application code
from ui import ChartWindow
from data_structures import ReboundCandidate
import data_loader

logging.basicConfig(level=logging.INFO)

def run_verification():
    """
    Tests that the new ChartWindow plotting logic does not raise a ValueError.
    """
    print("--- Starting plot fix verification ---")

    # 1. Use a real ticker to get realistic data
    ticker = 'MSFT'
    candidate = ReboundCandidate(
        ticker=ticker,
        scenario="Quality Stock Pullback",
        score=80,
        tooltip_text="A valid candidate for testing",
        technicals=data_loader.get_stock_data(ticker).iloc[-1].to_dict(),
        fundamentals={'name': 'Microsoft Corp', 'earningsGrowth': 0.2, 'revenueGrowth': 0.15}
    )

    # 2. Patch QWidget.show to prevent the chart window from trying to render.
    with patch('PyQt6.QtWidgets.QWidget.show') as mock_show:

        print(f"\nInstantiating ChartWindow with a valid candidate ({ticker})...")
        try:
            # 3. Instantiate the ChartWindow. This should trigger the plotting logic.
            chart_window = ChartWindow(candidate=candidate)
            print("ChartWindow instantiated successfully.")

            print("\n--- VERIFICATION PASSED ---")
            print("The ChartWindow constructor and plotting logic completed without raising an exception.")

        except Exception as e:
            # This block should NOT be reached.
            print(f"\n--- !!! VERIFICATION FAILED !!! ---")
            logging.error("An unhandled exception escaped the ChartWindow constructor.", exc_info=True)
            return

if __name__ == "__main__":
    run_verification()
