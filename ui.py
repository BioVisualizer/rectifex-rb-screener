# ui.py
# Contains all PyQt6 classes for the user interface.

import sys
import logging
import pandas as pd
import shutil
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableView, QStatusBar, QFileDialog, QMessageBox, QHeaderView,
    QProgressBar, QComboBox, QLabel, QLineEdit, QSplitter, QTreeWidget, QTreeWidgetItem,
    QStackedWidget
)
from PyQt6.QtCore import (
    QObject, QThread, pyqtSignal, QAbstractTableModel, Qt, QSortFilterProxyModel, QRegularExpression
)
from PyQt6.QtGui import QColor, QIcon, QPixmap, QPainter, QPainterPath
from PyQt6.QtWidgets import QFrame, QScrollArea, QMenu, QButtonGroup

# For charting
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import mplfinance as mpf

# App-specific imports
import asyncio
import config
import data_loader
from ticker_manager import TickerManagerDialog
from data_structures import ReboundCandidate
from rebound_scenarios import ScenarioRunner, calculate_rsi, calculate_sma, calculate_macd
from scoring import DEFAULT_REBOUND_SCORE_WEIGHTS

# --- Worker Thread for Running Analysis ---

class WorkerSignals(QObject):
    """Defines the signals available from a running worker thread."""
    finished = pyqtSignal()
    error = pyqtSignal(tuple)
    result = pyqtSignal(list)
    progress = pyqtSignal(str)
    progress_percent = pyqtSignal(int)

class AnalysisWorker(QObject):
    """Worker thread for running the stock analysis to prevent GUI freezing."""
    def __init__(self, selected_scenario: str, ticker: str = None):
        super().__init__()
        self.signals = WorkerSignals()
        self.selected_scenario = selected_scenario
        self.ticker = ticker
        self._is_cancelled = False

    def cancel(self):
        """Sets the cancellation flag to True."""
        logging.info("Cancellation requested for worker.")
        self._is_cancelled = True

    def run(self):
        """Runs the analysis and emits signals for progress and completion."""
        try:
            runner = ScenarioRunner(
                progress_callback=self.signals.progress,
                progress_percent_callback=self.signals.progress_percent,
                is_cancelled_callback=lambda: self._is_cancelled
            )

            # Run the selected scenario using the new generic method
            results = asyncio.run(runner.run_scan(self.selected_scenario, ticker=self.ticker))
            self.signals.result.emit(results)
        except Exception as e:
            import traceback
            self.signals.error.emit((type(e), e, traceback.format_exc()))
        finally:
            self.signals.finished.emit()


class TickerDetailWorker(QObject):
    """Worker thread for fetching detailed ticker information."""
    finished = pyqtSignal()
    result = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, ticker: str):
        super().__init__()
        self.ticker = ticker

    def run(self):
        """Fetches the data and emits the result."""
        try:
            from fundamentals import FundamentalDataHandler
            fund_handler = FundamentalDataHandler()
            info = asyncio.run(fund_handler.get_full_ticker_info(self.ticker))
            if info: # Ensure info is not None before emitting
                self.result.emit(info)
        except Exception as e:
            logging.error(f"Failed to fetch ticker details for {self.ticker}: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            self.finished.emit()


# --- Pandas DataFrame Model for QTableView ---

class PandasModel(QAbstractTableModel):
    """A model to interface a pandas DataFrame with a QTableView."""
    # We also pass the raw candidate data to have access to full details for tooltips
    def __init__(self, data=pd.DataFrame(), candidates_data=[], parent=None):
        super().__init__(parent)
        self._data = data
        self.candidates_data = candidates_data
        self.numeric_columns = [
            'Rebound Score', 'Tech. Score', 'Fund. Score', 'Price', 'Floor Score'
        ]

    def rowCount(self, parent=None):
        return self._data.shape[0]

    def columnCount(self, parent=None):
        return self._data.shape[1]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()
        column_name = self._data.columns[col]

        if role == Qt.ItemDataRole.DisplayRole:
            return str(self._data.iloc[row, col])

        # Return raw data for sorting role
        if role == Qt.ItemDataRole.EditRole:
            return self._data.iloc[row, col]

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if column_name in self.numeric_columns:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

        if role == Qt.ItemDataRole.BackgroundRole:
            if "Rebound Score" in self._data.columns:
                score = self._data.iloc[row]["Rebound Score"]
                if score > 80:
                    return QColor("#d4edda")  # Green
                elif score > 60:
                    return QColor("#fff3cd")  # Yellow
                elif score == 0:
                    return QColor("#f8d7da")  # Red

        if role == Qt.ItemDataRole.ToolTipRole and column_name == "Rebound Score":
            base_tooltip = "Composite score (0-100) combining technical, fundamental, and market factors."

            if row < len(self.candidates_data):
                candidate = self.candidates_data[row]

                # Build a detailed breakdown string
                tech_w = DEFAULT_REBOUND_SCORE_WEIGHTS['tech'] * 100
                fund_w = DEFAULT_REBOUND_SCORE_WEIGHTS['fund'] * 100
                mark_w = DEFAULT_REBOUND_SCORE_WEIGHTS['market'] * 100

                breakdown_lines = [
                    f"\n\n--- Score Composition ---",
                    f"- Technical Score: {candidate.technical_score} (Weight: {tech_w:.0f}%)",
                    f"- Fundamental Score: {candidate.fundamental_score} (Weight: {fund_w:.0f}%)",
                    f"- Market Context: {candidate.market_context_score} (Weight: {mark_w:.0f}%)"
                ]

                if candidate.score_breakdown:
                    breakdown_lines.append("\n--- Details ---")
                    for key, value in candidate.score_breakdown.items():
                        # Make keys more readable
                        readable_key = key.replace('_sub_score', '').replace('_', ' ').title()
                        breakdown_lines.append(f"- {readable_key}: {value}")

                return base_tooltip + "\n".join(breakdown_lines)

            return base_tooltip

        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return str(self._data.columns[section])
        return None

    def get_dataframe(self):
        return self._data


class CustomSortProxyModel(QSortFilterProxyModel):
    """
    A custom proxy model to handle numerical sorting for specific columns
    and text-based filtering across designated columns.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter_column_indices = []

    def set_filter_columns_by_name(self, column_names: list, source_model: QAbstractTableModel):
        """
        Sets the columns to be searched by their string names.
        This must be called after a source model is set.
        """
        if not source_model or not hasattr(source_model, 'get_dataframe'):
            self._filter_column_indices = []
            return

        df_columns = source_model.get_dataframe().columns.tolist()
        self._filter_column_indices = [df_columns.index(name) for name in column_names if name in df_columns]
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        """
        Custom filter logic. It checks if the filter regex matches in any of the
        specified columns.
        """
        regex = self.filterRegularExpression()
        if not regex.pattern():
            return True  # No filter set, accept all rows

        # If no columns are specified for filtering, check all columns by default
        columns_to_check = self._filter_column_indices
        if not columns_to_check:
            columns_to_check = range(self.sourceModel().columnCount())

        for col_index in columns_to_check:
            source_index = self.sourceModel().index(source_row, col_index, source_parent)
            cell_data = self.sourceModel().data(source_index, Qt.ItemDataRole.DisplayRole)
            if cell_data and regex.match(str(cell_data)).hasMatch():
                return True

        return False

    def lessThan(self, left, right):
        """
        Custom sorting logic. It performs numerical comparison for score
        columns and falls back to default string comparison for all others.
        """
        col = self.sortColumn()
        source_model = self.sourceModel()

        if col >= len(source_model.get_dataframe().columns):
            return super().lessThan(left, right)

        column_name = source_model.get_dataframe().columns[col]

        # List of columns to sort numerically
        numeric_sort_columns = ['Rebound Score', 'Tech. Score', 'Fund. Score', 'Price', 'Floor Score']

        if column_name in numeric_sort_columns:
            left_data = source_model.data(left, Qt.ItemDataRole.EditRole)
            right_data = source_model.data(right, Qt.ItemDataRole.EditRole)

            try:
                # For Price, remove '$' if present
                if isinstance(left_data, str):
                    left_data = left_data.replace('$', '')
                if isinstance(right_data, str):
                    right_data = right_data.replace('$', '')

                return float(left_data) < float(right_data)
            except (ValueError, TypeError):
                # Fallback for any non-numeric data like 'N/A'
                return str(left_data) < str(right_data)

        # Default behavior for all other columns
        return super().lessThan(left, right)


# --- Charting Window ---
class ChartWindow(QWidget):
    """A separate window for displaying a detailed, scalable stock chart."""
    def __init__(self, candidate: ReboundCandidate):
        super().__init__()
        self.candidate = candidate
        self.setWindowTitle(f"Chart for {self.candidate.ticker} - {config.APP_NAME}")
        self.setGeometry(150, 150, 950, 750) # Start with a good default size

        layout = QVBoxLayout()
        self.setLayout(layout)

        # Create a Matplotlib figure and a canvas widget
        # The `facecolor` can be set to match the window's background
        self.figure = Figure(figsize=(10, 8), facecolor=self.palette().window().color().name())
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas)

        self.plot_stock_data(candidate)

    def plot_stock_data(self, candidate: ReboundCandidate):
        """
        Generates and displays a detailed stock chart on the embedded canvas.
        This version plots indicators manually to work with an embedded canvas.
        """
        try:
            self.figure.clear()

            if candidate.history_df is None or candidate.history_df.empty:
                ax = self.figure.add_subplot(111)
                ax.text(0.5, 0.5, f"Historical data for {candidate.ticker} not found.",
                        horizontalalignment='center', verticalalignment='center')
                self.canvas.draw()
                return

            history_df = candidate.history_df.copy()

            # Limit data to the configured number of months for charting
            if not history_df.empty and config.CHART_HISTORY_MONTHS > 0:
                cutoff_date = history_df.index.max() - pd.DateOffset(months=config.CHART_HISTORY_MONTHS)
                plot_data = history_df.loc[cutoff_date:].copy()
            else:
                plot_data = history_df.copy()

            # --- Indicator Calculations ---
            if 'SMA50' not in plot_data.columns:
                plot_data['SMA50'] = calculate_sma(plot_data['Close'], 50)
            if 'SMA200' not in plot_data.columns:
                plot_data['SMA200'] = calculate_sma(plot_data['Close'], 200)
            if 'RSI' not in plot_data.columns:
                plot_data['RSI'] = calculate_rsi(plot_data['Close'])

            macd_line, signal_line, macd_hist = calculate_macd(plot_data['Close'])

            # --- Fibonacci Retracement Calculation ---
            highest_high = plot_data['High'].max()
            lowest_low = plot_data['Low'].min()
            fib_levels = {}
            hlines_fib = {}

            # We need a meaningful price range to draw the levels
            if pd.notna(highest_high) and pd.notna(lowest_low) and highest_high > lowest_low:
                price_range = highest_high - lowest_low
                fib_levels = {
                    23.6: highest_high - (price_range * 0.236),
                    38.2: highest_high - (price_range * 0.382),
                    50.0: highest_high - (price_range * 0.5),
                    61.8: highest_high - (price_range * 0.618),
                    78.6: highest_high - (price_range * 0.786),
                }
                # Also include the 0% and 100% levels for reference
                fib_levels[0.0] = highest_high
                fib_levels[100.0] = lowest_low

                hlines_fib = dict(
                    hlines=[level for level in fib_levels.values()],
                    colors=['#999999'] * len(fib_levels), # A neutral grey
                    linestyle='-.',
                    linewidths=0.6,
                    alpha=0.9
                )

            # --- Axes Creation ---
            gs = self.figure.add_gridspec(4, 1, height_ratios=[6, 1, 2, 2], hspace=0.05)
            price_ax = self.figure.add_subplot(gs[0, 0])
            volume_ax = self.figure.add_subplot(gs[1, 0], sharex=price_ax)
            rsi_ax = self.figure.add_subplot(gs[2, 0], sharex=price_ax)
            macd_ax = self.figure.add_subplot(gs[3, 0], sharex=price_ax)

            # Hide tick labels on shared x-axes for a cleaner look
            price_ax.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)
            volume_ax.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)
            rsi_ax.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)

            # --- Scenario-Specific Visualizations ---
            add_plots = [] # Start with an empty list

            if candidate.scenario and 'Floor Consolidation' in candidate.scenario:
                technicals = candidate.technicals

                # 1. Markers for high and low
                high_marker_data = pd.Series(float('nan'), index=plot_data.index)
                low_marker_data = pd.Series(float('nan'), index=plot_data.index)

                high_idx = technicals.get('period_high_idx')
                low_idx = technicals.get('drop_low_idx')

                if high_idx and high_idx in high_marker_data.index:
                    high_marker_data.loc[high_idx] = technicals['period_high_val'] * 1.05
                if low_idx and low_idx in low_marker_data.index:
                    low_marker_data.loc[low_idx] = technicals['drop_low_val'] * 0.95

                if not high_marker_data.isnull().all():
                    add_plots.append(mpf.make_addplot(high_marker_data, type='scatter', marker='v', markersize=100, color='red', ax=price_ax))
                if not low_marker_data.isnull().all():
                    add_plots.append(mpf.make_addplot(low_marker_data, type='scatter', marker='^', markersize=100, color='green', ax=price_ax))

                # 2. Shaded consolidation rectangle
                start_date = technicals.get('consol_start_idx')
                end_date = technicals.get('consol_end_idx')
                if start_date and end_date:
                    price_ax.axvspan(start_date, end_date, color='blue', alpha=0.1)

            # --- Plotting with mplfinance ---
            # Create a list of additional plots for the other axes/panels.
            # By passing the `ax` to `make_addplot`, we tell mplfinance to draw
            # on our existing axes instead of creating new panels.
            # Add standard indicators after scenario-specific ones
            add_plots.extend([
                mpf.make_addplot(plot_data['RSI'], ax=rsi_ax),
                mpf.make_addplot(macd_line, ax=macd_ax),
                mpf.make_addplot(signal_line, ax=macd_ax, color='red'), # MACD signal line
                mpf.make_addplot(macd_hist, type='bar', ax=macd_ax, color='grey', alpha=0.5)
            ])

            # The main plot call uses the main axes and adds the others.
            mpf.plot(plot_data,
                     type='candle',
                     ax=price_ax,
                     volume=volume_ax,
                     mav=(50, 200),
                     addplot=add_plots,
                     style='yahoo',
                     xrotation=20,
                     hlines=hlines_fib if hlines_fib else None)

            # --- Add Fibonacci Labels ---
            if fib_levels:
                # Use a transform that combines data y-coords with axes x-coords
                transform = price_ax.get_yaxis_transform()
                # Sort levels for cleaner labeling if they overlap
                sorted_levels = sorted(fib_levels.items(), key=lambda item: item[1], reverse=True)

                last_y = float('inf')
                for level_pct, level_price in sorted_levels:
                    # Simple logic to avoid label overlap
                    if abs(level_price - last_y) / (highest_high - lowest_low) < 0.035: # min 3.5% gap
                         continue
                    last_y = level_price

                    # Place percentage on the right, price on the left, both inside the chart
                    price_ax.text(0.98, level_price, f'{level_pct:.1f}%',
                                  transform=transform, va='center', ha='right',
                                  fontsize=7, color='#555555',
                                  bbox=dict(facecolor=self.palette().window().color().name(), alpha=0.7, edgecolor='none', pad=1))
                    price_ax.text(0.02, level_price, f'${level_price:.2f}',
                                  transform=transform, va='center', ha='left',
                                  fontsize=7, color='#555555',
                                  bbox=dict(facecolor=self.palette().window().color().name(), alpha=0.7, edgecolor='none', pad=1))


            # --- Final Styling ---
            self.figure.suptitle(f'{candidate.ticker} - {candidate.scenario}', y=0.98)
            price_ax.legend(loc='upper left')
            rsi_ax.set_ylabel('RSI')
            macd_ax.set_ylabel('MACD')

            rsi_ax.axhline(70, color='red', linestyle='--', linewidth=0.7, alpha=0.8)
            rsi_ax.axhline(30, color='green', linestyle='--', linewidth=0.7, alpha=0.8)
            rsi_ax.set_ylim(0, 100)

            # Add single-line info text bar between title and plot
            eps_growth = candidate.fundamentals.get('earningsGrowth')
            eps_growth_str = f"EPS Growth: {eps_growth * 100:.2f}%" if eps_growth is not None else ""
            rev_growth = candidate.fundamentals.get('revenueGrowth')
            rev_growth_str = f"Rev Growth: {rev_growth * 100:.2f}%" if rev_growth is not None else ""
            price = candidate.technicals.get('price')
            price_str = f"Price: ${price:.2f}" if price is not None else ""
            rsi_val = plot_data['RSI'].iloc[-1]
            rsi_str = f"RSI: {rsi_val:.1f}" if pd.notna(rsi_val) else ""

            # Filter out empty strings and join with a separator
            info_parts = [f"Scenario: {candidate.scenario}", price_str, rsi_str, eps_growth_str, rev_growth_str]
            info_text = " | ".join(filter(None, info_parts))

            self.figure.text(0.5, 0.94, info_text, ha='center', va='center', fontsize=8,
                             color='#333333', bbox=dict(boxstyle='round,pad=0.4', fc='yellow', alpha=0.5, ec='none'))

            self.figure.subplots_adjust(top=0.90, bottom=0.08, left=0.08, right=0.95, hspace=0.15)
            self.canvas.draw()

        except Exception as e:
            logging.error(f"Failed to plot chart for {candidate.ticker}: {e}", exc_info=True)
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, f"Could not generate chart for {candidate.ticker}:\n\n{e}",
                    horizontalalignment='center', verticalalignment='center', wrap=True)
            self.canvas.draw()
            QMessageBox.critical(self, "Chart Error", f"Could not generate chart for {candidate.ticker}:\n\n{e}")


from PyQt6.QtCore import QTimer, QPropertyAnimation, QEasingCurve, QRect
from PyQt6.QtWidgets import QDialog, QFormLayout, QDialogButtonBox, QSpinBox, QDoubleSpinBox, QGroupBox
from settings_manager import settings

class AdvancedSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Advanced Scan Settings")
        self.setMinimumWidth(450)

        layout = QVBoxLayout(self)

        # General Settings Group
        general_group = QGroupBox("General Filters")
        general_layout = QFormLayout()
        self.min_market_cap = QSpinBox()
        self.min_market_cap.setRange(0, 100_000_000_000)
        self.min_market_cap.setSingleStep(100_000_000)
        self.min_market_cap.setValue(settings.get('min_market_cap'))
        self.min_market_cap.setToolTip("Minimum market capitalization in USD.")
        general_layout.addRow("Min. Market Cap:", self.min_market_cap)

        self.min_avg_volume = QSpinBox()
        self.min_avg_volume.setRange(0, 100_000_000)
        self.min_avg_volume.setSingleStep(50_000)
        self.min_avg_volume.setValue(settings.get('min_avg_volume_30d'))
        self.min_avg_volume.setToolTip("Minimum 30-day average trading volume.")
        general_layout.addRow("Min. Avg. Volume (30d):", self.min_avg_volume)
        general_group.setLayout(general_layout)
        layout.addWidget(general_group)

        # Floor Consolidation Settings Group
        fc_group = QGroupBox("Floor Consolidation Scan")
        fc_layout = QFormLayout()

        self.fc_crash_lookback = QSpinBox()
        self.fc_crash_lookback.setRange(30, 365)
        self.fc_crash_lookback.setValue(settings.get('fc_crash_lookback_period'))
        self.fc_crash_lookback.setToolTip("Days to look back for a crash peak (~6mo default).")
        fc_layout.addRow("Crash Lookback Period (days):", self.fc_crash_lookback)

        self.fc_min_crash_depth = QDoubleSpinBox()
        self.fc_min_crash_depth.setRange(0.10, 0.90)
        self.fc_min_crash_depth.setSingleStep(0.05)
        self.fc_min_crash_depth.setValue(settings.get('fc_min_crash_depth'))
        self.fc_min_crash_depth.setToolTip("Minimum drop from the peak to be considered a crash (e.g., 0.25 for 25%).")
        fc_layout.addRow("Min. Crash Depth (%):", self.fc_min_crash_depth)

        self.fc_consolidation_days = QSpinBox()
        self.fc_consolidation_days.setRange(10, 120)
        self.fc_consolidation_days.setValue(settings.get('fc_consolidation_period_days'))
        self.fc_consolidation_days.setToolTip("Days to analyze for the consolidation floor (~3mo default).")
        fc_layout.addRow("Consolidation Period (days):", self.fc_consolidation_days)

        self.fc_max_consolidation_range = QDoubleSpinBox()
        self.fc_max_consolidation_range.setRange(0.05, 0.50)
        self.fc_max_consolidation_range.setSingleStep(0.01)
        self.fc_max_consolidation_range.setValue(settings.get('fc_max_consolidation_range'))
        self.fc_max_consolidation_range.setToolTip("Maximum price fluctuation within the floor (e.g., 0.15 for 15%).")
        fc_layout.addRow("Max. Consolidation Range (%):", self.fc_max_consolidation_range)

        self.fc_no_new_low_tolerance = QDoubleSpinBox()
        self.fc_no_new_low_tolerance.setRange(0.00, 0.20)
        self.fc_no_new_low_tolerance.setSingleStep(0.01)
        self.fc_no_new_low_tolerance.setValue(settings.get('fc_no_new_low_tolerance'))
        self.fc_no_new_low_tolerance.setToolTip("How much a new low can undercut the initial drop low (e.g., 0.03 for 3%).")
        fc_layout.addRow("New Low Tolerance (%):", self.fc_no_new_low_tolerance)

        self.fc_volume_ratio_max = QDoubleSpinBox()
        self.fc_volume_ratio_max.setRange(0.10, 2.00)
        self.fc_volume_ratio_max.setSingleStep(0.10)
        self.fc_volume_ratio_max.setValue(settings.get('fc_volume_ratio_max'))
        self.fc_volume_ratio_max.setToolTip("Consolidation volume must be <= this ratio of pre-crash volume (e.g., 0.70).")
        fc_layout.addRow("Max. Volume Ratio:", self.fc_volume_ratio_max)

        self.fc_min_fund_score = QSpinBox()
        self.fc_min_fund_score.setRange(0, 100)
        self.fc_min_fund_score.setValue(settings.get('fc_min_fund_score'))
        self.fc_min_fund_score.setToolTip("Minimum Fundamental Score for the 'Quality' version of the scan.")
        fc_layout.addRow("Min. Fundamental Score (Quality):", self.fc_min_fund_score)

        fc_group.setLayout(fc_layout)
        layout.addWidget(fc_group)

        # Clear Cache Button
        self.clear_cache_button = QPushButton("Clear All Cached Data")
        self.clear_cache_button.setToolTip("Deletes all downloaded price and fundamental data. The app will fetch fresh data on the next scan.")
        layout.addWidget(self.clear_cache_button, 0, Qt.AlignmentFlag.AlignRight)

        # OK and Cancel buttons
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def clear_cache(self):
        reply = QMessageBox.question(self, "Confirm Cache Deletion",
                                     f"Are you sure you want to delete all cached data from {config.CACHE_DIR}?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                shutil.rmtree(config.CACHE_DIR)
                config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
                QMessageBox.information(self, "Success", "Cache successfully cleared.")
            except Exception as e:
                QMessageBox.critical(self, "Cache Error", f"Failed to clear cache: {e}")

    def accept(self):
        # General
        settings.set('min_market_cap', self.min_market_cap.value())
        settings.set('min_avg_volume_30d', self.min_avg_volume.value())
        # Floor Consolidation
        settings.set('fc_crash_lookback_period', self.fc_crash_lookback.value())
        settings.set('fc_min_crash_depth', self.fc_min_crash_depth.value())
        settings.set('fc_consolidation_period_days', self.fc_consolidation_days.value())
        settings.set('fc_max_consolidation_range', self.fc_max_consolidation_range.value())
        settings.set('fc_no_new_low_tolerance', self.fc_no_new_low_tolerance.value())
        settings.set('fc_volume_ratio_max', self.fc_volume_ratio_max.value())
        settings.set('fc_min_fund_score', self.fc_min_fund_score.value())

        super().accept()

# --- Main Application Window ---

class ToastNotification(QFrame):
    """
    A non-intrusive toast/snackbar notification widget for displaying errors.
    """
    retry = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ToastNotification")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            QFrame#ToastNotification {
                background-color: #333333;
                border: 1px solid #444444;
                border-radius: 8px;
            }
            QLabel {
                color: white;
            }
        """)

        layout = QVBoxLayout(self)

        title_label = QLabel("<b>Scan Failed</b>")
        title_label.setStyleSheet("font-size: 14px;")

        self.message_label = QLabel("An error occurred. Please try again.")

        button_layout = QHBoxLayout()
        retry_button = QPushButton("Retry")
        details_button = QPushButton("Show Details")
        button_layout.addStretch()
        button_layout.addWidget(retry_button)
        button_layout.addWidget(details_button)

        layout.addWidget(title_label)
        layout.addWidget(self.message_label)
        layout.addLayout(button_layout)

        retry_button.clicked.connect(self.retry.emit)
        retry_button.clicked.connect(self.hide_toast)
        details_button.clicked.connect(self.show_details)

        self.detailed_error = ""
        self.hide()

    def show_toast(self, detailed_error: str):
        self.detailed_error = detailed_error
        self.parent().resizeEvent(None) # Trigger resize to position correctly
        self.show()

        # Automatically hide after a delay
        QTimer.singleShot(10000, self.hide_toast)

    def hide_toast(self):
        self.hide()

    def show_details(self):
        QMessageBox.critical(self, "Error Details", self.detailed_error)

class ScanCategoryCard(QFrame):
    """
    A custom widget that displays a category of scans with a title, description,
    and a list of clickable sub-strategies.
    """
    # Signal emitted when a sub-strategy is clicked, passing its ID.
    strategySelected = pyqtSignal(str)

    def __init__(self, title, description, icon_char, sub_strategies, parent=None):
        super().__init__(parent)
        self.sub_strategy_buttons = {}
        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)

        self.setObjectName("ScanCategoryCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setStyleSheet("""
            QFrame#ScanCategoryCard {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                margin: 5px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # Title with Icon
        title_label = QLabel(f'<span style="font-size: 20px;">{icon_char}</span>&nbsp;&nbsp;<b style="font-size: 16px;">{title}</b>')
        title_label.setTextFormat(Qt.TextFormat.RichText)

        # Description
        desc_label = QLabel(description)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #666;")

        # Sub-strategies
        sub_strategies_layout = QVBoxLayout()
        sub_strategies_layout.setContentsMargins(10, 5, 0, 5)
        sub_strategies_layout.setSpacing(8)

        for strategy in sub_strategies:
            btn = QPushButton(strategy['name'])
            btn.setObjectName("SubStrategyButton")
            btn.setCheckable(True)
            # btn.setAutoExclusive(True) # This is now handled by QButtonGroup
            btn.setStyleSheet("""
                QPushButton#SubStrategyButton {
                    text-align: left;
                    padding: 8px;
                    border: 1px solid transparent;
                    border-radius: 4px;
                    background-color: transparent;
                }
                QPushButton#SubStrategyButton:hover {
                    background-color: #f0f8ff; /* aliceblue */
                }
                QPushButton#SubStrategyButton:checked {
                    background-color: #e6f3ff;
                    border-color: #a0c4e8;
                    font-weight: bold;
                }
            """)
            # Use a lambda to capture the current strategy's ID
            btn.clicked.connect(lambda checked, s_id=strategy['id']: self.strategySelected.emit(s_id))
            self.sub_strategy_buttons[strategy['id']] = btn
            self.button_group.addButton(btn)
            sub_strategies_layout.addWidget(btn)

        layout.addWidget(title_label)
        layout.addWidget(desc_label)
        layout.addLayout(sub_strategies_layout)

    def uncheck_all(self):
        """Unchecks all buttons in this card by deselecting the checked one."""
        checked_button = self.button_group.checkedButton()
        if checked_button:
            # Temporarily disable exclusivity to allow programmatically unchecking
            self.button_group.setExclusive(False)
            checked_button.setChecked(False)
            self.button_group.setExclusive(True)

class MainWindow(QMainWindow):
    """The main window of the application."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle(config.APP_NAME)
        self.setGeometry(100, 100, 1200, 800)

        # Set window icon
        icon = QIcon.fromTheme("com.rectifex.GlobalReboundScreener")
        if not icon.isNull():
            self.setWindowIcon(icon)

        self.chart_windows = []
        self.results_df = pd.DataFrame()
        self.activeScan = None
        self.scan_cards = [] # To hold all card widgets

        # --- Layout and Widgets ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        top_level_layout = QVBoxLayout(central_widget)

        # --- Top Toolbar ---
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(10) # Add some space between elements

        # Primary Action Button (#btn-run-scan)
        self.run_scan_button = QPushButton(" Run Scan")
        self.run_scan_button.setObjectName("btn-run-scan")
        self.run_scan_button.setIcon(QIcon.fromTheme("system-search"))
        self.run_scan_button.setStyleSheet("""
            QPushButton#btn-run-scan {
                background-color: #3498db;
                color: white;
                font-weight: bold;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton#btn-run-scan:hover {
                background-color: #2980b9;
            }
            QPushButton#btn-run-scan:disabled {
                background-color: #a0a0a0;
            }
        """)
        self.stop_scan_button = QPushButton("Stop") # Re-use existing stop button
        self.stop_scan_button.hide()
        self.stop_scan_button.setEnabled(False)

        toolbar_layout.addWidget(self.run_scan_button)
        toolbar_layout.addWidget(self.stop_scan_button)

        # Global Inputs (#global-inputs)
        inputs_layout = QHBoxLayout()
        inputs_layout.setContentsMargins(20, 0, 20, 0) # Add space around the inputs
        # Single Ticker Field
        inputs_layout.addWidget(QLabel("Single Ticker (Optional):"))
        self.single_ticker_input = QLineEdit()
        self.single_ticker_input.setPlaceholderText("e.g., AAPL, GOOGL")
        self.single_ticker_input.setClearButtonEnabled(True)
        inputs_layout.addWidget(self.single_ticker_input)
        # Filter Results Field
        inputs_layout.addWidget(QLabel("Filter Results:"))
        self.filter_results_input = QLineEdit()
        self.filter_results_input.setPlaceholderText("Filter by name or ticker...")
        self.filter_results_input.setClearButtonEnabled(True)
        inputs_layout.addWidget(self.filter_results_input)
        toolbar_layout.addLayout(inputs_layout)

        toolbar_layout.addStretch()

        # Secondary Actions (#secondary-actions)
        self.manage_watchlists_button = QPushButton("Manage Watchlists")
        self.export_button = QPushButton("Export")
        self.help_button = QPushButton("Help")
        self.settings_button = QPushButton("Settings")

        # Create Export Menu
        export_menu = QMenu(self)
        self.export_csv_action = export_menu.addAction("Export CSV")
        self.export_xlsx_action = export_menu.addAction("Export XLSX")
        self.export_button.setMenu(export_menu)
        self.export_csv_action.setEnabled(False)
        self.export_xlsx_action.setEnabled(False)

        toolbar_layout.addWidget(self.manage_watchlists_button)
        toolbar_layout.addWidget(self.export_button)
        toolbar_layout.addWidget(self.help_button)
        toolbar_layout.addWidget(self.settings_button)

        top_level_layout.addLayout(toolbar_layout)

        # --- Main Content Area (Three-Column Layout) ---
        # Create the main horizontal splitter for the three columns
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Column 1: Strategy Selection Pane
        self.selection_pane = QWidget()
        self.selection_pane.setObjectName("selection-pane")
        self.selection_pane.setStyleSheet("background-color: #f8f9fa;")
        selection_pane_layout = QVBoxLayout(self.selection_pane)
        selection_pane_layout.setContentsMargins(0,0,0,0)

        # Add a scroll area to the selection pane
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet("QScrollArea { border: none; }")
        selection_pane_layout.addWidget(scroll_area)

        # Create a container for the cards
        card_container = QWidget()
        self.card_layout = QVBoxLayout(card_container)
        self.card_layout.setSpacing(5)
        self.card_layout.addStretch() # Pushes cards to the top
        scroll_area.setWidget(card_container)

        self.populate_strategy_cards()
        main_splitter.addWidget(self.selection_pane)

        # Column 2: Main Content Pane
        self.main_content_pane = QWidget()
        self.main_content_pane.setObjectName("main-content-pane")
        main_content_layout = QVBoxLayout(self.main_content_pane)
        main_content_layout.setContentsMargins(0, 0, 0, 0)
        self.main_content_pane.setStyleSheet("background-color: white; border: none;")

        self.stacked_widget = QStackedWidget()
        main_content_layout.addWidget(self.stacked_widget)

        # -- Onboarding State Widget --
        onboarding_widget = QWidget()
        onboarding_layout = QVBoxLayout(onboarding_widget)
        onboarding_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        onboarding_widget.setStyleSheet("background-color: #ffffff; border: none;")

        headline = QLabel("Welcome! Let's Find Your Next Investment.")
        headline.setStyleSheet("font-size: 24px; font-weight: bold; color: #333;")

        instructions = QLabel(
        """
        <ol>
            <li>Select a scan strategy on the left.</li>
            <li>Click 'Run Scan' to begin.</li>
            <li>Analyze your results right here.</li>
        </ol>
        """
        )
        instructions.setStyleSheet("font-size: 14px; color: #555;")

        self.demo_scan_button = QPushButton("Run Demo Scan")
        self.demo_scan_button.setFixedWidth(150)

        onboarding_layout.addWidget(headline)
        onboarding_layout.addWidget(instructions)
        onboarding_layout.addWidget(self.demo_scan_button, alignment=Qt.AlignmentFlag.AlignCenter)

        # -- Results State Widget --
        results_widget = QWidget()
        results_layout = QVBoxLayout(results_widget)

        results_header_layout = QHBoxLayout()
        self.results_summary_label = QLabel("Found 0 results.")
        self.scan_progress_label = QLabel("") # Will show "Scanning..." text
        results_header_layout.addWidget(self.results_summary_label)
        results_header_layout.addStretch()
        results_header_layout.addWidget(self.scan_progress_label)

        self.table_view = QTableView()
        self.table_view.setSortingEnabled(True)

        results_layout.addLayout(results_header_layout)
        results_layout.addWidget(self.table_view)

        # -- No Results State Widget --
        no_results_widget = QWidget()
        no_results_layout = QVBoxLayout(no_results_widget)
        no_results_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_results_label = QLabel("No stocks matched your scan.")
        self.no_results_label.setStyleSheet("font-size: 16px; color: #777;")
        no_results_layout.addWidget(self.no_results_label)

        self.stacked_widget.addWidget(onboarding_widget)
        self.stacked_widget.addWidget(results_widget)
        self.stacked_widget.addWidget(no_results_widget)

        main_splitter.addWidget(self.main_content_pane)

        # Column 3: Contextual Information Pane
        self.context_pane = QWidget()
        self.context_pane.setObjectName("context-pane")
        self.context_pane.setStyleSheet("background-color: #f8f9fa; border-left: 1px solid #e0e0e0;")
        context_layout = QVBoxLayout(self.context_pane)
        context_layout.setContentsMargins(0, 0, 0, 0)

        self.context_stack = QStackedWidget()
        context_layout.addWidget(self.context_stack)

        # -- View 0: Scan Explanation Widget --
        self.scan_explanation_widget = QWidget()
        self.scan_explanation_layout = QVBoxLayout(self.scan_explanation_widget)
        self.scan_explanation_layout.setContentsMargins(15, 15, 15, 15)
        self.scan_explanation_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.context_stack.addWidget(self.scan_explanation_widget)

        # -- View 1: Ticker Detail Widget --
        self.ticker_detail_widget = QWidget()
        self.ticker_detail_layout = QVBoxLayout(self.ticker_detail_widget)
        self.ticker_detail_layout.setContentsMargins(15, 15, 15, 15)
        self.ticker_detail_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.context_stack.addWidget(self.ticker_detail_widget)

        # Set initial view
        self.context_stack.setCurrentIndex(0)
        # Add a default message
        default_label = QLabel("Select a scan from the left to learn more about it.")
        default_label.setWordWrap(True)
        self.scan_explanation_layout.addWidget(default_label)

        main_splitter.addWidget(self.context_pane)

        # Set initial sizes for the panes
        # Approximate ratio: 20% | 55% | 25%
        total_width = self.width()
        main_splitter.setSizes([int(total_width * 0.25), int(total_width * 0.50), int(total_width * 0.25)])

        top_level_layout.addWidget(main_splitter)

        # The table_view is now created and homed within the results_widget above

        # Status bar with progress bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.setStyleSheet("QStatusBar::item { border: 0px; }") # Remove borders

        # Footer Labels
        self.data_source_label = QLabel("Data Source: yfinance")
        self.last_updated_label = QLabel("Last Updated: N/A")
        self.status_bar.addWidget(self.data_source_label)
        self.status_bar.addPermanentWidget(self.last_updated_label)

        self.progress_bar = QProgressBar()
        self.status_bar.addPermanentWidget(self.progress_bar)
        self.progress_bar.hide()

        # Toast Notification
        self.toast = ToastNotification(self)

        # --- Connections ---
        self.run_scan_button.clicked.connect(self.start_scan)
        self.stop_scan_button.clicked.connect(self.stop_scan)
        self.settings_button.clicked.connect(self.open_settings_dialog)
        self.manage_watchlists_button.clicked.connect(self.open_ticker_manager)
        self.help_button.clicked.connect(self.show_about_dialog)
        self.table_view.clicked.connect(self.on_ticker_selected) # For context pane
        self.table_view.doubleClicked.connect(self.open_chart_for_selection) # For chart window
        self.demo_scan_button.clicked.connect(self.run_demo_scan)
        self.toast.retry.connect(self.start_scan)
        self.export_csv_action.triggered.connect(self.export_to_csv)
        self.export_xlsx_action.triggered.connect(self.export_to_excel)
        self.filter_results_input.textChanged.connect(self.filter_table)
        self.single_ticker_input.textChanged.connect(self.validate_single_ticker_input)

    def clear_layout(self, layout):
        """Safely clears all items from a layout."""
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
                else:
                    sub_layout = item.layout()
                    if sub_layout is not None:
                        self.clear_layout(sub_layout)

    def validate_single_ticker_input(self, text: str):
        """
        Validates the single ticker input field.
        Disables the 'Run Scan' button if the text is not empty and contains invalid characters.
        A valid ticker is uppercase letters, numbers, and optionally '.' or '-'.
        """
        if not text: # Empty string is valid (optional field)
            self.run_scan_button.setEnabled(True)
            return

        # Simple regex for typical ticker formats.
        # Allows sequences like 'AAPL', 'GOOGL', 'BRK-B', 'BF.B'
        valid_ticker_regex = QRegularExpression("^[A-Z0-9.-]+$")
        match = valid_ticker_regex.match(text.upper())

        if match.hasMatch():
            self.run_scan_button.setEnabled(True)
        else:
            self.run_scan_button.setEnabled(False)

    def on_ticker_selected(self, index):
        """Handles a click on a ticker in the results table."""
        if not index.isValid():
            return

        source_index = self.table_view.model().mapToSource(index)
        if source_index.row() < len(self.all_candidates_data):
            candidate = self.all_candidates_data[source_index.row()]
            self.selected_ticker_for_detail = candidate.ticker # Store for error messages

            # Clear previous content and show loading message immediately
            self.clear_layout(self.ticker_detail_layout)
            loading_label = QLabel("Loading Ticker Details...")
            self.ticker_detail_layout.addWidget(loading_label)
            self.context_stack.setCurrentIndex(1)

            # Create and run worker
            self.detail_thread = QThread()
            self.detail_worker = TickerDetailWorker(candidate.ticker)
            self.detail_worker.moveToThread(self.detail_thread)

            self.detail_thread.started.connect(self.detail_worker.run)
            self.detail_worker.finished.connect(self.detail_thread.quit)
            self.detail_worker.finished.connect(self.detail_worker.deleteLater)
            self.detail_thread.finished.connect(self.detail_thread.deleteLater)

            # Connect result signal to a new method that updates the GUI
            self.detail_worker.result.connect(self.update_context_pane_with_data)
            self.detail_worker.error.connect(self.on_detail_fetch_error)

            self.detail_thread.start()

    def on_detail_fetch_error(self, error_message):
        """Handles errors from the TickerDetailWorker."""
        # Clear the loading message
        self.clear_layout(self.ticker_detail_layout)

        error_label = QLabel(f"Error fetching details for {self.selected_ticker_for_detail}:\n{error_message}")
        error_label.setWordWrap(True)
        self.ticker_detail_layout.addWidget(error_label)

    def update_context_pane_with_data(self, info: dict):
        """Updates the context pane with fetched ticker data."""
        # Clear loading message
        self.clear_layout(self.ticker_detail_layout)

        if not info:
            error_label = QLabel(f"Could not load details for {self.selected_ticker_for_detail}.")
            self.ticker_detail_layout.addWidget(error_label)
            return

        ticker = info.get('symbol', self.selected_ticker_for_detail)

        # Populate with new data
        title = QLabel(f"<b>{info.get('shortName', ticker)}</b> ({ticker})")
        title.setStyleSheet("font-size: 16px; margin-bottom: 5px;")

        chart_placeholder = QLabel("Chart will be here")
        chart_placeholder.setMinimumHeight(150)
        chart_placeholder.setStyleSheet("background-color: #e0e0e0; border: 1px solid #ccc;")
        chart_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)

        def format_market_cap(mc):
            if mc is None: return "N/A"
            if mc > 1_000_000_000_000: return f"${mc/1_000_000_000_000:.2f}T"
            if mc > 1_000_000_000: return f"${mc/1_000_000_000:.2f}B"
            if mc > 1_000_000: return f"${mc/1_000_000:.2f}M"
            return f"${mc}"

        pe_ratio = f"{info.get('trailingPE'):.2f}" if info.get('trailingPE') else "N/A"
        div_yield = f"{info.get('dividendYield')*100:.2f}%" if info.get('dividendYield') else "N/A"

        metrics_text = (
            f"<b>Market Cap:</b> {format_market_cap(info.get('marketCap'))}<br>"
            f"<b>P/E Ratio:</b> {pe_ratio}<br>"
            f"<b>Dividend Yield:</b> {div_yield}"
        )
        metrics_label = QLabel(metrics_text)

        bio_title = QLabel("<b>Company Bio</b>")
        bio_title.setStyleSheet("color: #005a9e; margin-top: 10px;")
        bio_text = QLabel(info.get('longBusinessSummary', 'No company summary available.'))
        bio_text.setWordWrap(True)

        self.ticker_detail_layout.addWidget(title)
        self.ticker_detail_layout.addWidget(chart_placeholder)
        self.ticker_detail_layout.addWidget(metrics_label)
        self.ticker_detail_layout.addWidget(bio_title)
        self.ticker_detail_layout.addWidget(bio_text)
        self.ticker_detail_layout.addStretch()

    def on_strategy_selected(self, strategy_id):
        """Handles a click on any sub-strategy button from any card."""
        self.activeScan = strategy_id

        # When a new strategy is selected, clear the results and show the onboarding screen.
        self.stacked_widget.setCurrentIndex(0) # 0 is onboarding
        self.results_df = pd.DataFrame()
        self.table_view.setModel(None)

        # Uncheck buttons in all other cards
        sender_card = self.sender()
        for card in self.scan_cards:
             if card is not sender_card:
                card.uncheck_all()

        # Update the context pane
        self.update_context_pane_for_scan(strategy_id)

    def update_context_pane_for_scan(self, strategy_id):
        # Clear the previous content
        while self.scan_explanation_layout.count():
            item = self.scan_explanation_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        scenarios = ScenarioRunner.load_scenarios_config()
        scenario_data = next((s for s in scenarios if s['id'] == strategy_id), None)

        if scenario_data:
            title = QLabel(f"<b>{scenario_data.get('name', 'N/A')}</b>")
            title.setStyleSheet("font-size: 16px; margin-bottom: 5px;")

            what_it_is_title = QLabel("<b>What It Is</b>")
            what_it_is_title.setStyleSheet("color: #005a9e; margin-top: 10px;")
            what_it_is_text = QLabel(scenario_data.get('what_it_is', 'No details available.'))
            what_it_is_text.setWordWrap(True)

            best_for_title = QLabel("<b>Best For</b>")
            best_for_title.setStyleSheet("color: #005a9e; margin-top: 10px;")
            best_for_text = QLabel(scenario_data.get('best_for', 'No details available.'))
            best_for_text.setWordWrap(True)

            self.scan_explanation_layout.addWidget(title)
            self.scan_explanation_layout.addWidget(what_it_is_title)
            self.scan_explanation_layout.addWidget(what_it_is_text)
            self.scan_explanation_layout.addWidget(best_for_title)
            self.scan_explanation_layout.addWidget(best_for_text)
            self.scan_explanation_layout.addStretch()

        self.context_stack.setCurrentIndex(0)


    def populate_strategy_cards(self):
        """
        Loads scenarios from the runner and populates the selection pane with
        ScanCategoryCard widgets.
        """
        scenarios = ScenarioRunner.load_scenarios_config()

        # Define icons for categories
        icons = {
            "Trend & Momentum": "📈",
            "Contrarian & Reversion": "📉",
            "Value & Fundamental": "💰",
            "Volatility": "⚡️"
        }

        # Group scenarios by the 'group' key
        groups = {}
        for scenario in scenarios:
            group_name = scenario.get('group', 'Uncategorized')
            if group_name not in groups:
                groups[group_name] = []
            groups[group_name].append(scenario)

        # Create a card for each group
        for group_name, scenarios_in_group in groups.items():
            card = ScanCategoryCard(
                title=group_name,
                description=scenarios_in_group[0].get('group_description', ''), # Assuming first desc is fine
                icon_char=icons.get(group_name, "❓"),
                sub_strategies=scenarios_in_group
            )
            card.strategySelected.connect(self.on_strategy_selected)
            self.card_layout.insertWidget(self.card_layout.count() - 1, card)
            self.scan_cards.append(card)

    def filter_table(self, text: str):
        """Filters the table view based on the search input."""
        proxy_model = self.table_view.model()
        if isinstance(proxy_model, CustomSortProxyModel):
            # QRegularExpression provides more powerful filtering than simple strings
            # and allows for case-insensitivity.
            regex = QRegularExpression(text, QRegularExpression.PatternOption.CaseInsensitiveOption)
            proxy_model.setFilterRegularExpression(regex)

    def stop_scan(self):
        """Requests the worker thread to stop."""
        if hasattr(self, 'worker') and self.worker:
            self.status_bar.showMessage("Stopping scan...")
            self.stop_scan_button.setEnabled(False) # Prevent multiple clicks
            self.worker.cancel()

    def open_ticker_manager(self):
        """Opens the Ticker Manager dialog."""
        dialog = TickerManagerDialog(self)
        dialog.exec()

    def open_settings_dialog(self):
        """Opens the advanced settings dialog."""
        dialog = AdvancedSettingsDialog(self)
        dialog.exec()

    def show_about_dialog(self):
        """Shows a simple 'About' dialog."""
        about_text = f"""
        <b>{config.APP_NAME} v2.0</b>
        <p>This application scans global stock markets to identify potential investment candidates based on a variety of technical and fundamental scenarios.</p>
        <p>Developed by Lukas Morcinek.</p>
        <hr>
        <p><b>Disclaimer:</b></p>
        <p>This tool is for informational purposes only and does not constitute financial advice. Always conduct your own thorough research before making any investment decisions.</p>
        """
        QMessageBox.about(self, f"About {config.APP_NAME}", about_text)

    def run_demo_scan(self):
        """Selects a demo scan and runs it."""
        # Find the 'Golden Cross' button and click it programmatically
        demo_strategy_id = 'golden_cross'

        for card in self.scan_cards:
            if demo_strategy_id in card.sub_strategy_buttons:
                # Ensure the button is checked visually
                card.sub_strategy_buttons[demo_strategy_id].setChecked(True)
                # Manually call the selection handler
                self.on_strategy_selected(demo_strategy_id)
                # Start the scan
                self.start_scan()
                return

        QMessageBox.warning(self, "Demo Error", "Could not find the 'Golden Cross' demo scan.")

    def start_scan(self):
        """Sets up and starts the analysis worker thread."""
        ticker = self.single_ticker_input.text().strip().upper() or None

        if not self.activeScan:
            QMessageBox.warning(self, "Selection Error", "Please select a scan strategy from the left pane.")
            return

        self.run_scan_button.setEnabled(False)
        self.single_ticker_input.setEnabled(False)
        self.stop_scan_button.show()
        self.stop_scan_button.setEnabled(True)
        self.settings_button.setEnabled(False)

        # Switch to results view and show progress
        self.stacked_widget.setCurrentIndex(1) # 1 is the results_widget
        self.scan_progress_label.setText("Starting scan...")
        self.status_bar.showMessage("Starting scan...")
        self.progress_bar.setValue(0)
        self.progress_bar.show()

        selected_scenario_id = self.activeScan

        self.thread = QThread()
        self.worker = AnalysisWorker(selected_scenario=selected_scenario_id, ticker=ticker)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.signals.finished.connect(self.thread.quit)
        self.worker.signals.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.worker.signals.result.connect(self.display_results)
        self.worker.signals.progress.connect(self.update_status_text)
        self.worker.signals.progress_percent.connect(self.update_progress_bar)
        self.worker.signals.error.connect(self.scan_error)

        self.thread.start()

    def scan_error(self, error_info):
        """Shows a toast notification when an error occurs during the scan."""
        exctype, value, tb = error_info
        error_message = f"An unexpected error occurred:\n\n{value}\n\nTraceback:\n{tb}"

        self.status_bar.showMessage(f"Scan Error: {value}")
        self.run_scan_button.setEnabled(True)
        self.single_ticker_input.setEnabled(True)
        self.stop_scan_button.hide()
        self.stop_scan_button.setEnabled(False)
        self.settings_button.setEnabled(True)
        self.progress_bar.hide()

        # Show the onboarding screen on error
        self.stacked_widget.setCurrentIndex(0)

        self.toast.show_toast(error_message)

    def resizeEvent(self, event):
        """Handle window resize events to reposition the toast."""
        if hasattr(self, 'toast') and self.toast.isVisible():
            toast_width = 350
            toast_height = 120
            x = self.width() - toast_width - 10
            y = self.height() - toast_height - self.status_bar.height() - 10
            self.toast.setGeometry(x, y, toast_width, toast_height)
        super().resizeEvent(event)

    def update_status_text(self, message):
        """Updates the text in the status bar and the progress label."""
        self.status_bar.showMessage(message)
        self.scan_progress_label.setText(message)

    def update_progress_bar(self, percent):
        """Updates the progress bar value."""
        self.progress_bar.setValue(percent)

    def display_results(self, results):
        """Receives the results (List[ReboundCandidate]) and displays them."""
        self.scan_progress_label.setText("") # Clear progress text

        # Update timestamp on successful scan
        self.last_updated_label.setText(f"Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Check if the scan was cancelled, otherwise show the count
        if self.worker and self.worker._is_cancelled:
             self.status_bar.showMessage(f"Scan stopped by user. {len(results)} candidates found before stopping.")
        else:
            self.status_bar.showMessage(f"Scan complete. Found {len(results)} candidates.")

        self.run_scan_button.setEnabled(True)
        self.single_ticker_input.setEnabled(True)
        self.stop_scan_button.hide()
        self.stop_scan_button.setEnabled(False)
        self.settings_button.setEnabled(True)
        self.progress_bar.hide()

        if not results:
            self.results_df = pd.DataFrame()
            self.table_view.setModel(None)

            # Update and switch to No Results view
            scenarios = ScenarioRunner.load_scenarios_config()
            scenario_name = next((s['name'] for s in scenarios if s['id'] == self.activeScan), "the selected scan")
            self.no_results_label.setText(f'No stocks matched your scan for "{scenario_name}".\nYou can try another strategy.')
            self.stacked_widget.setCurrentIndex(2) # 2 is the no_results_widget
            return

        # Switch to the results view if we aren't already there
        self.stacked_widget.setCurrentIndex(1)

        # Convert list of ReboundCandidate objects to a list of dicts for the DataFrame
        display_results_list = []
        scenarios = ScenarioRunner.load_scenarios_config()
        scenario_config = next((s for s in scenarios if s['id'] == self.activeScan), None)
        scenario_name = scenario_config['name'] if scenario_config else ""

        is_floor_scan = self.activeScan and 'floor_consolidation' in self.activeScan

        for r in results:
            roe_val = r.fundamentals.get('roe')
            roe_str = f"{roe_val * 100:.2f}%" if roe_val is not None else "N/A"

            res_dict = {
                "Ticker": r.ticker,
                "Name": r.fundamentals.get('name', 'N/A'),
                "Price": f"${r.technicals.get('price', 0):.2f}",
                "Rebound Score": r.rebound_score,
                "Fund. Score": r.fundamental_score,
                "ROE": roe_str
            }

            if is_floor_scan:
                res_dict["Floor Score"] = r.technical_score
                res_dict["Crash %"] = r.technicals.get('Crash %', 'N/A')
                res_dict["Consol. Range %"] = r.technicals.get('Consol. Range %', 'N/A')
                res_dict["Drop Date"] = r.technicals.get('Drop Date', 'N/A')
            else:
                # Only show generic "Tech. Score" for other scans
                res_dict["Tech. Score"] = r.technical_score

            display_results_list.append(res_dict)

        self.results_df = pd.DataFrame(display_results_list)

        # Reorder columns for display consistency
        if is_floor_scan:
            cols_order = ["Ticker", "Name", "Price", "Floor Score", "Crash %", "Consol. Range %", "Drop Date", "Rebound Score", "Fund. Score", "ROE"]
            self.results_df = self.results_df[cols_order]
        self.results_summary_label.setText(f"Found {len(results)} results for \"{scenario_name}\" scan.")
        # Keep the original full candidate objects for charting and for detailed tooltips
        self.all_candidates_data = results

        model = PandasModel(self.results_df, self.all_candidates_data)
        # Use the custom proxy model for robust sorting and filtering
        proxy_model = CustomSortProxyModel()
        proxy_model.setSourceModel(model)
        # Tell the proxy model which columns to search
        proxy_model.set_filter_columns_by_name(['Ticker', 'Name'], model)

        self.table_view.setModel(proxy_model)
        self.table_view.resizeColumnsToContents()
        self.table_view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        # Allow interactive resizing for specific columns
        self.table_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive) # Ticker
        self.table_view.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive) # Szenario


        # Automatically sort by the appropriate score column, descending
        sort_column_name = "Rebound Score" # Default sort column
        if self.activeScan == 'floor_consolidation_universal':
            sort_column_name = "Floor Score"

        try:
            score_col_index = self.results_df.columns.get_loc(sort_column_name)
            self.table_view.sortByColumn(score_col_index, Qt.SortOrder.DescendingOrder)
        except KeyError:
            # Fallback to Rebound Score if the primary sort column isn't found for some reason
            try:
                score_col_index = self.results_df.columns.get_loc("Rebound Score")
                self.table_view.sortByColumn(score_col_index, Qt.SortOrder.DescendingOrder)
            except KeyError:
                pass # No score columns found to sort by

        has_results = not self.results_df.empty
        self.export_csv_action.setEnabled(has_results)
        self.export_xlsx_action.setEnabled(has_results)


    def open_chart_for_selection(self, index):
        """Opens a new chart window for the selected stock."""
        proxy_model = self.table_view.model()
        source_index = proxy_model.mapToSource(index)

        # Get the corresponding ReboundCandidate object
        candidate = self.all_candidates_data[source_index.row()]

        chart_window = ChartWindow(candidate)
        self.chart_windows.append(chart_window)
        chart_window.show()

    def export_to_csv(self):
        """Exports the current results to a CSV file."""
        if self.results_df.empty:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV", config.CSV_EXPORT_FILENAME, "CSV Files (*.csv)")
        if path:
            try:
                self.results_df.to_csv(path, index=False)
                self.status_bar.showMessage(f"Data successfully exported to {path}", 5000)
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Failed to export to CSV: {e}")

    def export_to_excel(self):
        """Exports the current results to an Excel (XLSX) file."""
        if self.results_df.empty:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save XLSX", config.XLSX_EXPORT_FILENAME, "Excel Files (*.xlsx)")
        if path:
            try:
                self.results_df.to_excel(path, index=False)
                self.status_bar.showMessage(f"Data successfully exported to {path}", 5000)
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Failed to export to Excel: {e}")
