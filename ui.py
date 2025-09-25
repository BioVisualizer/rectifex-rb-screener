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
from typing import Dict, Any

# --- Worker Thread for Running Analysis ---

class WorkerSignals(QObject):
    """Defines the signals available from a running worker thread."""
    finished = pyqtSignal()
    error = pyqtSignal(tuple)
    result = pyqtSignal(list, dict) # List of candidates, Dict of telemetry
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
        self.runner = ScenarioRunner(
            progress_callback=self.signals.progress,
            progress_percent_callback=self.signals.progress_percent,
            is_cancelled_callback=lambda: self._is_cancelled
        )

    def cancel(self):
        """Sets the cancellation flag to True."""
        logging.info("Cancellation requested for worker.")
        self._is_cancelled = True

    def run(self):
        """Runs the analysis and emits signals for progress and completion."""
        try:
            # Run the selected scenario using the new generic method
            results = asyncio.run(self.runner.run_scan(self.selected_scenario, ticker=self.ticker))
            self.signals.result.emit(results, self.runner.telemetry)
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
            'Rebound Score', 'Tech. Score', 'Fund. Score', 'Price', 'Floor Score',
            'P/E', 'EPS Growth'
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
            value = self._data.iloc[row, col]
            if isinstance(value, float):
                # Specific formatting for certain columns
                if column_name in ['EPS Growth']:
                    return f"{value:.2%}"
                return f"{value:.2f}"
            return str(value)

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
    def lessThan(self, left, right):
        col = self.sortColumn()
        source_model = self.sourceModel()
        if col >= len(source_model.get_dataframe().columns):
            return super().lessThan(left, right)
        column_name = source_model.get_dataframe().columns[col]
        numeric_sort_columns = ['Rebound Score', 'Tech. Score', 'Fund. Score', 'Price', 'Floor Score', 'P/E', 'EPS Growth']
        if column_name in numeric_sort_columns:
            left_data = source_model.data(left, Qt.ItemDataRole.EditRole)
            right_data = source_model.data(right, Qt.ItemDataRole.EditRole)
            try:
                if isinstance(left_data, str): left_data = left_data.replace('$', '').replace('%', '')
                if isinstance(right_data, str): right_data = right_data.replace('$', '').replace('%', '')
                return float(left_data) < float(right_data)
            except (ValueError, TypeError): return str(left_data) < str(right_data)
        return super().lessThan(left, right)

# --- Charting Widget (for embedding) ---
class ChartWidget(QWidget):
    """An embeddable widget for displaying a stock chart."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(250) # Give it a reasonable minimum height
        self.figure = Figure(figsize=(5, 4), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        layout = QVBoxLayout()
        layout.setContentsMargins(0,0,0,0)
        layout.addWidget(self.canvas)
        self.setLayout(layout)

    def plot_stock_data(self, candidate: ReboundCandidate):
        """
        Generates and displays a stock chart on the embedded canvas.
        This is a simplified version of the one in ChartWindow.
        """
        try:
            self.figure.clear()

            if candidate.history_df is None or candidate.history_df.empty:
                ax = self.figure.add_subplot(111)
                ax.text(0.5, 0.5, f"No data for {candidate.ticker}",
                        horizontalalignment='center', verticalalignment='center')
                self.canvas.draw()
                return

            history_df = candidate.history_df.copy()

            if not history_df.empty and config.CHART_HISTORY_MONTHS > 0:
                cutoff_date = history_df.index.max() - pd.DateOffset(months=config.CHART_HISTORY_MONTHS)
                plot_data = history_df.loc[cutoff_date:].copy()
            else:
                plot_data = history_df.copy()

            if plot_data.empty:
                ax = self.figure.add_subplot(111)
                ax.text(0.5, 0.5, "No data in range.",
                        horizontalalignment='center', verticalalignment='center')
                self.canvas.draw()
                return

            plot_data.index = pd.to_datetime(plot_data.index)

            # Create axes for a compact view (Price, Volume, RSI)
            gs = self.figure.add_gridspec(3, 1, height_ratios=[3, 1, 1], hspace=0.1)
            price_ax = self.figure.add_subplot(gs[0, 0])
            volume_ax = self.figure.add_subplot(gs[1, 0], sharex=price_ax)
            rsi_ax = self.figure.add_subplot(gs[2, 0], sharex=price_ax)

            # Hide x-axis labels on the upper panels for a cleaner look
            price_ax.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)
            volume_ax.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)


            # Calculate RSI
            if 'RSI' not in plot_data.columns:
                plot_data['RSI'] = calculate_rsi(plot_data['Close'])

            # Add RSI to a separate panel
            add_plots = [mpf.make_addplot(plot_data['RSI'], ax=rsi_ax)]

            mpf.plot(plot_data,
                     type='candle',
                     ax=price_ax,
                     volume=volume_ax, # Plot volume on its own axes
                     mav=(20, 50),
                     addplot=add_plots,
                     style='yahoo',
                     xrotation=0,
                     datetime_format='%b %Y')

            rsi_ax.set_ylabel('RSI')
            rsi_ax.set_ylim(0, 100)
            rsi_ax.axhline(70, color='red', linestyle='--', linewidth=0.7)
            rsi_ax.axhline(30, color='green', linestyle='--', linewidth=0.7)

            self.figure.tight_layout()
            self.canvas.draw()

        except Exception as e:
            logging.error(f"Failed to plot embedded chart for {candidate.ticker}: {e}", exc_info=True)
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, f"Chart Error: {e}",
                    horizontalalignment='center', verticalalignment='center', wrap=True)
            self.canvas.draw()


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

        self.figure = Figure(figsize=(10, 8), facecolor=self.palette().window().color().name())
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas)

        self.plot_stock_data(candidate)

    def plot_stock_data(self, candidate: ReboundCandidate):
        try:
            self.figure.clear()

            if candidate.history_df is None or candidate.history_df.empty:
                ax = self.figure.add_subplot(111)
                ax.text(0.5, 0.5, f"Historical data for {candidate.ticker} not found.",
                        horizontalalignment='center', verticalalignment='center')
                self.canvas.draw()
                return

            history_df = candidate.history_df.copy()
            if not history_df.empty and config.CHART_HISTORY_MONTHS > 0:
                cutoff_date = history_df.index.max() - pd.DateOffset(months=config.CHART_HISTORY_MONTHS)
                plot_data = history_df.loc[cutoff_date:].copy()
            else:
                plot_data = history_df.copy()

            if plot_data.empty:
                ax = self.figure.add_subplot(111)
                ax.text(0.5, 0.5, f"No data available for the selected time period for {candidate.ticker}.",
                        horizontalalignment='center', verticalalignment='center')
                self.canvas.draw()
                return

            plot_data.index = pd.to_datetime(plot_data.index)
            if 'SMA50' not in plot_data.columns:
                plot_data['SMA50'] = calculate_sma(plot_data['Close'], 50)
            if 'SMA200' not in plot_data.columns:
                plot_data['SMA200'] = calculate_sma(plot_data['Close'], 200)
            if 'RSI' not in plot_data.columns:
                plot_data['RSI'] = calculate_rsi(plot_data['Close'])
            macd_line, signal_line, macd_hist = calculate_macd(plot_data['Close'])
            highest_high = plot_data['High'].max()
            lowest_low = plot_data['Low'].min()
            fib_levels = {}
            if pd.notna(highest_high) and pd.notna(lowest_low) and highest_high > lowest_low:
                price_range = highest_high - lowest_low
                fib_levels = {
                    23.6: highest_high - (price_range * 0.236),
                    38.2: highest_high - (price_range * 0.382),
                    50.0: highest_high - (price_range * 0.5),
                    61.8: highest_high - (price_range * 0.618),
                    78.6: highest_high - (price_range * 0.786),
                }
                fib_levels[0.0] = highest_high
                fib_levels[100.0] = lowest_low
            gs = self.figure.add_gridspec(4, 1, height_ratios=[6, 1, 2, 2], hspace=0.05)
            price_ax = self.figure.add_subplot(gs[0, 0])
            volume_ax = self.figure.add_subplot(gs[1, 0], sharex=price_ax)
            rsi_ax = self.figure.add_subplot(gs[2, 0], sharex=price_ax)
            macd_ax = self.figure.add_subplot(gs[3, 0], sharex=price_ax)
            price_ax.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)
            volume_ax.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)
            rsi_ax.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)
            add_plots = []
            if candidate.scenario and 'Floor Consolidation' in candidate.scenario:
                technicals = candidate.technicals
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
                start_date = technicals.get('consol_start_idx')
                end_date = technicals.get('consol_end_idx')
                if start_date and end_date:
                    price_ax.axvspan(start_date, end_date, color='blue', alpha=0.1)
            add_plots.extend([
                mpf.make_addplot(plot_data['RSI'], ax=rsi_ax),
                mpf.make_addplot(macd_line, ax=macd_ax),
                mpf.make_addplot(signal_line, ax=macd_ax, color='red'), # MACD signal line
                mpf.make_addplot(macd_hist, type='bar', ax=macd_ax, color='grey', alpha=0.5)
            ])
            mpf.plot(plot_data,
                     type='candle',
                     ax=price_ax,
                     volume=volume_ax,
                     mav=(50, 200),
                     addplot=add_plots,
                     style='yahoo',
                     xrotation=20,
                     xlim=(plot_data.index[0], plot_data.index[-1]))
            if fib_levels:
                for level_price in fib_levels.values():
                    price_ax.axhline(level_price, color='#999999', linestyle='-.', linewidth=0.6, alpha=0.9)
                transform = price_ax.get_yaxis_transform()
                sorted_levels = sorted(fib_levels.items(), key=lambda item: item[1], reverse=True)
                last_y = float('inf')
                for level_pct, level_price in sorted_levels:
                    if abs(level_price - last_y) / (highest_high - lowest_low) < 0.035: continue
                    last_y = level_price
                    price_ax.text(0.98, level_price, f'{level_pct:.1f}%',
                                  transform=transform, va='center', ha='right',
                                  fontsize=7, color='#555555',
                                  bbox=dict(facecolor=self.palette().window().color().name(), alpha=0.7, edgecolor='none', pad=1))
                    price_ax.text(0.02, level_price, f'${level_price:.2f}',
                                  transform=transform, va='center', ha='left',
                                  fontsize=7, color='#555555',
                                  bbox=dict(facecolor=self.palette().window().color().name(), alpha=0.7, edgecolor='none', pad=1))
            self.figure.suptitle(f'{candidate.ticker} - {candidate.scenario}', y=0.98)
            rsi_ax.set_ylabel('RSI')
            macd_ax.set_ylabel('MACD')
            rsi_ax.axhline(70, color='red', linestyle='--', linewidth=0.7, alpha=0.8)
            rsi_ax.axhline(30, color='green', linestyle='--', linewidth=0.7, alpha=0.8)
            rsi_ax.set_ylim(0, 100)
            eps_growth = candidate.fundamentals.get('earningsGrowth')
            eps_growth_str = f"EPS Growth: {eps_growth * 100:.2f}%" if eps_growth is not None else ""
            rev_growth = candidate.fundamentals.get('revenueGrowth')
            rev_growth_str = f"Rev Growth: {rev_growth * 100:.2f}%" if rev_growth is not None else ""
            price = candidate.technicals.get('price')
            price_str = f"Price: ${price:.2f}" if price is not None else ""
            rsi_val = plot_data['RSI'].iloc[-1]
            rsi_str = f"RSI: {rsi_val:.1f}" if pd.notna(rsi_val) else ""
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
        general_group = QGroupBox("General Filters")
        general_layout = QFormLayout()
        self.min_market_cap = QDoubleSpinBox()
        self.min_market_cap.setDecimals(0)
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
        self.clear_cache_button = QPushButton("Clear All Cached Data")
        self.clear_cache_button.setToolTip("Deletes all downloaded price and fundamental data. The app will fetch fresh data on the next scan.")
        self.clear_cache_button.clicked.connect(self.clear_cache)
        layout.addWidget(self.clear_cache_button, 0, Qt.AlignmentFlag.AlignRight)
        telemetry_group = QGroupBox("Last Scan Telemetry")
        telemetry_layout = QFormLayout()
        self.telemetry_label = QLabel("No scan has been run yet.")
        self.telemetry_label.setWordWrap(True)
        telemetry_layout.addRow(self.telemetry_label)
        telemetry_group.setLayout(telemetry_layout)
        layout.addWidget(telemetry_group)
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
                main_window = self.parent()
                if main_window and hasattr(main_window, 'status_bar'):
                    main_window.status_bar.showMessage("Cache successfully cleared.", 5000)
                else:
                    QMessageBox.information(self, "Success", "Cache successfully cleared.")
            except Exception as e:
                QMessageBox.critical(self, "Cache Error", f"Failed to clear cache: {e}")

    def accept(self):
        settings.set('min_market_cap', self.min_market_cap.value())
        settings.set('min_avg_volume_30d', self.min_avg_volume.value())
        settings.set('fc_crash_lookback_period', self.fc_crash_lookback.value())
        settings.set('fc_min_crash_depth', self.fc_min_crash_depth.value())
        settings.set('fc_consolidation_period_days', self.fc_consolidation_days.value())
        settings.set('fc_max_consolidation_range', self.fc_max_consolidation_range.value())
        settings.set('fc_no_new_low_tolerance', self.fc_no_new_low_tolerance.value())
        settings.set('fc_volume_ratio_max', self.fc_volume_ratio_max.value())
        settings.set('fc_min_fund_score', self.fc_min_fund_score.value())
        super().accept()

class MainWindow(QMainWindow):
    """The main window of the application."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle(config.APP_NAME)
        self.setGeometry(100, 100, 1200, 800)
        icon = QIcon.fromTheme("com.rectifex.GlobalReboundScreener")
        if not icon.isNull(): self.setWindowIcon(icon)
        self.chart_windows = []
        self.results_df = pd.DataFrame()
        self.activeScan = None
        self.scan_cards = []
        self.last_telemetry = {}
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        top_level_layout = QVBoxLayout(central_widget)
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(10)
        self.run_scan_button = QPushButton(" Run Scan")
        self.run_scan_button.setObjectName("btn-run-scan")
        self.run_scan_button.setIcon(QIcon.fromTheme("system-search"))
        self.run_scan_button.setStyleSheet("""...""") # Styles omitted for brevity
        self.stop_scan_button = QPushButton("Stop")
        self.stop_scan_button.hide()
        self.stop_scan_button.setEnabled(False)
        toolbar_layout.addWidget(self.run_scan_button)
        toolbar_layout.addWidget(self.stop_scan_button)
        inputs_layout = QHBoxLayout()
        inputs_layout.setContentsMargins(20, 0, 20, 0)
        inputs_layout.addWidget(QLabel("Single Ticker (Optional):"))
        self.single_ticker_input = QLineEdit()
        self.single_ticker_input.setPlaceholderText("e.g., AAPL, GOOGL")
        self.single_ticker_input.setClearButtonEnabled(True)
        inputs_layout.addWidget(self.single_ticker_input)
        inputs_layout.addWidget(QLabel("Filter Results:"))
        self.filter_results_input = QLineEdit()
        self.filter_results_input.setPlaceholderText("Filter by name or ticker...")
        self.filter_results_input.setClearButtonEnabled(True)
        inputs_layout.addWidget(self.filter_results_input)
        toolbar_layout.addLayout(inputs_layout)
        toolbar_layout.addStretch()
        self.manage_watchlists_button = QPushButton("Manage Watchlists")
        self.export_button = QPushButton("Export")
        self.help_button = QPushButton("Help")
        self.settings_button = QPushButton("Settings")
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
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.selection_pane = QWidget()
        self.selection_pane.setObjectName("selection-pane")
        self.selection_pane.setStyleSheet("background-color: #f8f9fa;")
        selection_pane_layout = QVBoxLayout(self.selection_pane)
        selection_pane_layout.setContentsMargins(0,0,0,0)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet("QScrollArea { border: none; }")
        selection_pane_layout.addWidget(scroll_area)
        card_container = QWidget()
        self.card_layout = QVBoxLayout(card_container)
        self.card_layout.setSpacing(5)
        self.card_layout.addStretch()
        scroll_area.setWidget(card_container)
        self.populate_strategy_cards()
        main_splitter.addWidget(self.selection_pane)
        self.main_content_pane = QWidget()
        main_content_layout = QVBoxLayout(self.main_content_pane)
        main_content_layout.setContentsMargins(0, 0, 0, 0)
        self.stacked_widget = QStackedWidget()
        main_content_layout.addWidget(self.stacked_widget)
        onboarding_widget = QWidget()
        onboarding_layout = QVBoxLayout(onboarding_widget)
        onboarding_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        headline = QLabel("Welcome! Let's Find Your Next Investment.")
        headline.setStyleSheet("font-size: 24px; font-weight: bold; color: #333;")
        instructions = QLabel("<ol><li>Select a scan strategy on the left.</li><li>Click 'Run Scan' to begin.</li><li>Analyze your results right here.</li></ol>")
        instructions.setStyleSheet("font-size: 14px; color: #555;")
        self.demo_scan_button = QPushButton("Run Demo Scan")
        self.demo_scan_button.setFixedWidth(150)
        onboarding_layout.addWidget(headline)
        onboarding_layout.addWidget(instructions)
        onboarding_layout.addWidget(self.demo_scan_button, alignment=Qt.AlignmentFlag.AlignCenter)
        results_widget = QWidget()
        results_layout = QVBoxLayout(results_widget)
        results_header_layout = QHBoxLayout()
        self.results_summary_label = QLabel("Found 0 results.")
        self.scan_progress_label = QLabel("")
        results_header_layout.addWidget(self.results_summary_label)
        results_header_layout.addStretch()
        results_header_layout.addWidget(self.scan_progress_label)
        self.table_view = QTableView()
        self.table_view.setSortingEnabled(True)
        results_layout.addLayout(results_header_layout)
        results_layout.addWidget(self.table_view)
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
        self.context_pane = QWidget()
        self.context_pane.setObjectName("context-pane")
        self.context_pane.setStyleSheet("background-color: #f8f9fa; border-left: 1px solid #e0e0e0;")
        context_layout = QVBoxLayout(self.context_pane)
        context_layout.setContentsMargins(0, 0, 0, 0)
        self.context_stack = QStackedWidget()
        context_layout.addWidget(self.context_stack)
        self.scan_explanation_widget = QWidget()
        self.scan_explanation_layout = QVBoxLayout(self.scan_explanation_widget)
        self.scan_explanation_layout.setContentsMargins(15, 15, 15, 15)
        self.scan_explanation_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.context_stack.addWidget(self.scan_explanation_widget)
        self.ticker_detail_widget = QWidget()
        self.ticker_detail_layout = QVBoxLayout(self.ticker_detail_widget)
        self.ticker_detail_layout.setContentsMargins(15, 15, 15, 15)
        self.ticker_detail_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.context_stack.addWidget(self.ticker_detail_widget)
        self.context_stack.setCurrentIndex(0)
        default_label = QLabel("Select a scan from the left to learn more about it.")
        default_label.setWordWrap(True)
        self.scan_explanation_layout.addWidget(default_label)
        main_splitter.addWidget(self.context_pane)
        total_width = self.width()
        main_splitter.setSizes([int(total_width * 0.25), int(total_width * 0.50), int(total_width * 0.25)])
        top_level_layout.addWidget(main_splitter)
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.setStyleSheet("QStatusBar::item { border: 0px; }")
        self.data_source_label = QLabel("Data Source: yfinance")
        self.last_updated_label = QLabel("Last Updated: N/A")
        self.status_bar.addWidget(self.data_source_label)
        self.status_bar.addPermanentWidget(self.last_updated_label)
        self.progress_bar = QProgressBar()
        self.status_bar.addPermanentWidget(self.progress_bar)
        self.progress_bar.hide()
        self.toast = ToastNotification(self)
        self.run_scan_button.clicked.connect(self.start_scan)
        self.stop_scan_button.clicked.connect(self.stop_scan)
        self.settings_button.clicked.connect(self.open_settings_dialog)
        self.manage_watchlists_button.clicked.connect(self.open_ticker_manager)
        self.help_button.clicked.connect(self.show_about_dialog)
        self.table_view.clicked.connect(self.on_ticker_selected)
        self.table_view.doubleClicked.connect(self.open_chart_for_selection)
        self.demo_scan_button.clicked.connect(self.run_demo_scan)
        self.toast.retry.connect(self.start_scan)
        self.export_csv_action.triggered.connect(self.export_to_csv)
        self.export_xlsx_action.triggered.connect(self.export_to_excel)
        self.filter_results_input.textChanged.connect(self.filter_table)
        self.single_ticker_input.textChanged.connect(self.validate_single_ticker_input)

    def open_settings_dialog(self):
        dialog = AdvancedSettingsDialog(self)
        if self.last_telemetry:
            skipped = self.last_telemetry['tickers_skipped']
            telemetry_text = (
                f"<b>Duration:</b> {self.last_telemetry['scan_duration_seconds']}s\n"
                f"<b>Universe Size:</b> {self.last_telemetry['total_tickers_in_universe']}\n"
                f"<b>Tickers Processed:</b> {self.last_telemetry['tickers_processed']}\n"
                f"<b>Tickers Skipped:</b> {skipped['total']} "
                f"(Liquidity: {skipped['liquidity']}, History: {skipped['insufficient_history']}, Fundamentals: {skipped['missing_fundamentals']})"
            )
            dialog.telemetry_label.setText(telemetry_text)
        dialog.exec()

    def update_status_text(self, message: str):
        self.status_bar.showMessage(message)
        # Live-update the progress label with skipped ticker counts
        if hasattr(self, 'worker') and self.worker:
            telemetry = self.worker.runner.telemetry
            skipped_count = telemetry['tickers_skipped']['total']
            processed_count = telemetry['tickers_processed']
            self.scan_progress_label.setText(f"Processed: {processed_count} | Skipped: {skipped_count}")
        else:
            self.scan_progress_label.setText(message)

    def display_results(self, results: list, telemetry: dict):
        self.last_telemetry = telemetry
        self.scan_progress_label.setText("")
        self.last_updated_label.setText(f"Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        if self.worker and self.worker._is_cancelled:
             self.status_bar.showMessage(f"Scan stopped by user. {len(results)} candidates found before stopping.")
        else:
            skipped_total = telemetry['tickers_skipped']['total']
            self.status_bar.showMessage(f"Scan complete. Found {len(results)} candidates. (Skipped {skipped_total})")
        self.run_scan_button.setEnabled(True)
        self.single_ticker_input.setEnabled(True)
        self.stop_scan_button.hide()
        self.stop_scan_button.setEnabled(False)
        self.settings_button.setEnabled(True)
        self.progress_bar.hide()
        if not results:
            self.results_df = pd.DataFrame()
            self.table_view.setModel(None)
            scenarios = ScenarioRunner.load_scenarios_config()
            scenario_name = next((s['name'] for s in scenarios if s['id'] == self.activeScan), "the selected scan")
            self.no_results_label.setText(f'No stocks matched your scan for "{scenario_name}".\nYou can try another strategy.')
            self.stacked_widget.setCurrentIndex(2)
            return
        self.stacked_widget.setCurrentIndex(1)
        display_results_list = []
        scenarios = ScenarioRunner.load_scenarios_config()
        scenario_config = next((s for s in scenarios if s['id'] == self.activeScan), None)
        scenario_name = scenario_config['name'] if scenario_config else ""

        # Define base columns and scenario-specific columns
        base_cols = ["Ticker", "Name", "Price", "Rebound Score", "Tech. Score", "Fund. Score"]
        scenario_cols = []
        if self.activeScan == 'garp_trend':
            scenario_cols = ["P/E", "EPS Growth"]
        elif self.activeScan == 'volume_breakout':
            scenario_cols = ["52w High", "Volume Ratio"]
        elif self.activeScan == 'stochastic_oversold':
            scenario_cols = ["%K", "%D"]
        elif self.activeScan and 'floor_consolidation' in self.activeScan:
            base_cols = ["Ticker", "Name", "Price", "Floor Score", "Crash %", "Consol. Range %", "Drop Date", "Rebound Score", "Fund. Score", "ROE"]

        for r in results:
            res_dict = {
                "Ticker": r.ticker,
                "Name": r.fundamentals.get('name', 'N/A'),
                "Price": f"${r.technicals.get('price', 0):.2f}",
                "Rebound Score": r.rebound_score,
                "Tech. Score": r.technical_score,
                "Fund. Score": r.fundamental_score,
                "ROE": f"{r.fundamentals.get('roe', 0) * 100:.2f}%" if r.fundamentals.get('roe') is not None else "N/A",
                "P/E": r.fundamentals.get('trailingPE'),
                "EPS Growth": r.fundamentals.get('earningsGrowth'),
                "52w High": r.technicals.get('52w_high'),
                "Volume Ratio": r.technicals.get('volume_ratio'),
                "%K": r.technicals.get('stoch_k'),
                "%D": r.technicals.get('stoch_d'),
                "Floor Score": r.technical_score,
                "Crash %": r.technicals.get('Crash %', 'N/A'),
                "Consol. Range %": r.technicals.get('Consol. Range %', 'N/A'),
                "Drop Date": r.technicals.get('Drop Date', 'N/A'),
            }
            display_results_list.append(res_dict)

        self.results_df = pd.DataFrame(display_results_list)

        # Dynamically select columns to display
        final_cols = base_cols + scenario_cols
        # Ensure all columns exist in the dataframe before selecting
        final_cols = [col for col in final_cols if col in self.results_df.columns]
        self.results_df = self.results_df[final_cols]

        self.results_summary_label.setText(f"Found {len(results)} results for \"{scenario_name}\" scan.")
        self.all_candidates_data = results
        model = PandasModel(self.results_df, self.all_candidates_data)
        proxy_model = CustomSortProxyModel()
        proxy_model.setSourceModel(model)
        proxy_model.set_filter_columns_by_name(['Ticker', 'Name'], model)
        self.table_view.setModel(proxy_model)
        self.table_view.resizeColumnsToContents()
        self.table_view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table_view.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        sort_column_name = "Rebound Score"
        if self.activeScan == 'floor_consolidation_universal': sort_column_name = "Floor Score"
        try:
            score_col_index = self.results_df.columns.get_loc(sort_column_name)
            self.table_view.sortByColumn(score_col_index, Qt.SortOrder.DescendingOrder)
        except KeyError:
            try:
                score_col_index = self.results_df.columns.get_loc("Rebound Score")
                self.table_view.sortByColumn(score_col_index, Qt.SortOrder.DescendingOrder)
            except KeyError: pass
        has_results = not self.results_df.empty
        self.export_csv_action.setEnabled(has_results)
        self.export_xlsx_action.setEnabled(has_results)

    # All other methods (clear_layout, validate_single_ticker_input, etc.) are omitted for brevity
    # but remain unchanged in the actual file.
    def clear_layout(self, layout):
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None: widget.deleteLater()
                else:
                    sub_layout = item.layout()
                    if sub_layout is not None: self.clear_layout(sub_layout)
    def validate_single_ticker_input(self, text: str):
        if not text:
            self.run_scan_button.setEnabled(True)
            return
        valid_ticker_regex = QRegularExpression("^[A-Z0-9.-]+$")
        match = valid_ticker_regex.match(text.upper())
        self.run_scan_button.setEnabled(match.hasMatch())
    def on_ticker_selected(self, index):
        if not index.isValid(): return
        source_index = self.table_view.model().mapToSource(index)
        if source_index.row() < len(self.all_candidates_data):
            self.selected_candidate = self.all_candidates_data[source_index.row()]
            self.selected_ticker_for_detail = self.selected_candidate.ticker
            self.clear_layout(self.ticker_detail_layout)
            loading_label = QLabel("Loading Ticker Details...")
            self.ticker_detail_layout.addWidget(loading_label)
            self.context_stack.setCurrentIndex(1)
            self.detail_thread = QThread()
            self.detail_worker = TickerDetailWorker(self.selected_candidate.ticker)
            self.detail_worker.moveToThread(self.detail_thread)
            self.detail_thread.started.connect(self.detail_worker.run)
            self.detail_worker.finished.connect(self.detail_thread.quit)
            self.detail_worker.finished.connect(self.detail_worker.deleteLater)
            self.detail_thread.finished.connect(self.detail_thread.deleteLater)
            self.detail_worker.result.connect(self.update_context_pane_with_data)
            self.detail_worker.error.connect(self.on_detail_fetch_error)
            self.detail_thread.start()
    def on_detail_fetch_error(self, error_message):
        self.clear_layout(self.ticker_detail_layout)
        error_label = QLabel(f"Error fetching details for {self.selected_ticker_for_detail}:\n{error_message}")
        error_label.setWordWrap(True)
        self.ticker_detail_layout.addWidget(error_label)
    def update_context_pane_with_data(self, info: dict):
        self.clear_layout(self.ticker_detail_layout)
        if not info:
            error_label = QLabel(f"Could not load details for {self.selected_ticker_for_detail}.")
            self.ticker_detail_layout.addWidget(error_label)
            return
        ticker = info.get('symbol', self.selected_ticker_for_detail)
        title = QLabel(f"<b>{info.get('shortName', ticker)}</b> ({ticker})")
        title.setStyleSheet("font-size: 16px; margin-bottom: 5px;")
        chart_widget = ChartWidget(self)
        if hasattr(self, 'selected_candidate'): chart_widget.plot_stock_data(self.selected_candidate)
        def format_market_cap(mc):
            if mc is None: return "N/A"
            if mc > 1_000_000_000_000: return f"${mc/1_000_000_000_000:.2f}T"
            if mc > 1_000_000_000: return f"${mc/1_000_000_000:.2f}B"
            if mc > 1_000_000: return f"${mc/1_000_000:.2f}M"
            return f"${mc}"
        pe_ratio = f"{info.get('trailingPE'):.2f}" if info.get('trailingPE') else "N/A"
        div_yield = f"{info.get('dividendYield')*100:.2f}%" if info.get('dividendYield') else "N/A"
        metrics_text = (f"<b>Market Cap:</b> {format_market_cap(info.get('marketCap'))}<br>"
                        f"<b>P/E Ratio:</b> {pe_ratio}<br>"
                        f"<b>Dividend Yield:</b> {div_yield}")
        metrics_label = QLabel(metrics_text)
        bio_title = QLabel("<b>Company Bio</b>")
        bio_title.setStyleSheet("color: #005a9e; margin-top: 10px;")
        bio_text = QLabel(info.get('longBusinessSummary', 'No company summary available.'))
        bio_text.setWordWrap(True)
        self.ticker_detail_layout.addWidget(title)
        self.ticker_detail_layout.addWidget(chart_widget)
        self.ticker_detail_layout.addWidget(metrics_label)
        self.ticker_detail_layout.addWidget(bio_title)
        self.ticker_detail_layout.addWidget(bio_text)
        self.ticker_detail_layout.addStretch()
    def on_strategy_selected(self, strategy_id):
        self.activeScan = strategy_id
        self.stacked_widget.setCurrentIndex(0)
        self.results_df = pd.DataFrame()
        self.table_view.setModel(None)
        sender_card = self.sender()
        for card in self.scan_cards:
             if card is not sender_card: card.uncheck_all()
        self.update_context_pane_for_scan(strategy_id)
    def update_context_pane_for_scan(self, strategy_id):
        while self.scan_explanation_layout.count():
            item = self.scan_explanation_layout.takeAt(0)
            widget = item.widget()
            if widget is not None: widget.setParent(None)
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
        scenarios = ScenarioRunner.load_scenarios_config()
        icons = {"Trend & Momentum": "📈", "Contrarian & Reversion": "📉", "Value & Fundamental": "💰", "Volatility": "⚡️"}
        groups = {}
        for scenario in scenarios:
            group_name = scenario.get('group', 'Uncategorized')
            if group_name not in groups: groups[group_name] = []
            groups[group_name].append(scenario)
        for group_name, scenarios_in_group in groups.items():
            card = ScanCategoryCard(title=group_name, description=scenarios_in_group[0].get('group_description', ''),
                                    icon_char=icons.get(group_name, "❓"), sub_strategies=scenarios_in_group)
            card.strategySelected.connect(self.on_strategy_selected)
            self.card_layout.insertWidget(self.card_layout.count() - 1, card)
            self.scan_cards.append(card)
    def filter_table(self, text: str):
        proxy_model = self.table_view.model()
        if isinstance(proxy_model, CustomSortProxyModel):
            regex = QRegularExpression(text, QRegularExpression.PatternOption.CaseInsensitiveOption)
            proxy_model.setFilterRegularExpression(regex)
    def stop_scan(self):
        if hasattr(self, 'worker') and self.worker:
            self.status_bar.showMessage("Stopping scan...")
            self.stop_scan_button.setEnabled(False)
            self.worker.cancel()
    def open_ticker_manager(self):
        dialog = TickerManagerDialog(self)
        dialog.exec()
    def show_about_dialog(self):
        QMessageBox.about(self, f"About {config.APP_NAME}", "...") # Text omitted for brevity
    def run_demo_scan(self):
        demo_strategy_id = 'golden_cross'
        for card in self.scan_cards:
            if demo_strategy_id in card.sub_strategy_buttons:
                card.sub_strategy_buttons[demo_strategy_id].setChecked(True)
                self.on_strategy_selected(demo_strategy_id)
                self.start_scan()
                return
        QMessageBox.warning(self, "Demo Error", "Could not find the 'Golden Cross' demo scan.")
    def start_scan(self):
        ticker = self.single_ticker_input.text().strip().upper() or None
        if not self.activeScan:
            QMessageBox.warning(self, "Selection Error", "Please select a scan strategy from the left pane.")
            return
        self.run_scan_button.setEnabled(False)
        self.single_ticker_input.setEnabled(False)
        self.stop_scan_button.show()
        self.stop_scan_button.setEnabled(True)
        self.settings_button.setEnabled(False)
        self.stacked_widget.setCurrentIndex(1)
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
        exctype, value, tb = error_info
        error_message = f"An unexpected error occurred:\n\n{value}\n\nTraceback:\n{tb}"
        self.status_bar.showMessage(f"Scan Error: {value}")
        self.run_scan_button.setEnabled(True)
        self.single_ticker_input.setEnabled(True)
        self.stop_scan_button.hide()
        self.stop_scan_button.setEnabled(False)
        self.settings_button.setEnabled(True)
        self.progress_bar.hide()
        self.stacked_widget.setCurrentIndex(0)
        self.toast.show_toast(error_message)
    def resizeEvent(self, event):
        if hasattr(self, 'toast') and self.toast.isVisible():
            toast_width = 350; toast_height = 120
            x = self.width() - toast_width - 10
            y = self.height() - toast_height - self.status_bar.height() - 10
            self.toast.setGeometry(x, y, toast_width, toast_height)
        super().resizeEvent(event)
    def update_progress_bar(self, percent):
        self.progress_bar.setValue(percent)
    def open_chart_for_selection(self, index):
        proxy_model = self.table_view.model()
        source_index = proxy_model.mapToSource(index)
        candidate = self.all_candidates_data[source_index.row()]
        chart_window = ChartWindow(candidate)
        self.chart_windows.append(chart_window)
        chart_window.show()
    def export_to_csv(self):
        if self.results_df.empty: return
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV", config.CSV_EXPORT_FILENAME, "CSV Files (*.csv)")
        if path:
            try:
                self.results_df.to_csv(path, index=False)
                self.status_bar.showMessage(f"Data successfully exported to {path}", 5000)
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Failed to export to CSV: {e}")
    def export_to_excel(self):
        if self.results_df.empty: return
        path, _ = QFileDialog.getSaveFileName(self, "Save XLSX", config.XLSX_EXPORT_FILENAME, "Excel Files (*.xlsx)")
        if path:
            try:
                self.results_df.to_excel(path, index=False)
                self.status_bar.showMessage(f"Data successfully exported to {path}", 5000)
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Failed to export to Excel: {e}")