# ui.py
# Contains all PyQt6 classes for the user interface.

import sys
import logging
import pandas as pd
import shutil
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableView, QStatusBar, QFileDialog, QMessageBox, QHeaderView,
    QProgressBar, QComboBox, QLabel, QLineEdit, QSplitter, QTreeWidget, QTreeWidgetItem
)
from PyQt6.QtCore import (
    QObject, QThread, pyqtSignal, QAbstractTableModel, Qt, QSortFilterProxyModel, QRegularExpression
)
from PyQt6.QtGui import QColor, QIcon, QPixmap

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


# --- Pandas DataFrame Model for QTableView ---

class PandasModel(QAbstractTableModel):
    """A model to interface a pandas DataFrame with a QTableView."""
    # We also pass the raw candidate data to have access to full details for tooltips
    def __init__(self, data=pd.DataFrame(), candidates_data=[], parent=None):
        super().__init__(parent)
        self._data = data
        self.candidates_data = candidates_data
        self.numeric_columns = [
            'Rebound Score', 'Tech. Score', 'Fund. Score', 'Price'
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
        numeric_sort_columns = ['Rebound Score', 'Tech. Score', 'Fund. Score', 'Price']

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
                plot_data = history_df.loc[cutoff_date:]
            else:
                plot_data = history_df

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

            # --- Plotting with mplfinance ---
            # Create a list of additional plots for the other axes/panels.
            # By passing the `ax` to `make_addplot`, we tell mplfinance to draw
            # on our existing axes instead of creating new panels.
            add_plots = [
                mpf.make_addplot(plot_data['RSI'], ax=rsi_ax),
                mpf.make_addplot(macd_line, ax=macd_ax),
                mpf.make_addplot(signal_line, ax=macd_ax, color='red'), # MACD signal line
                mpf.make_addplot(macd_hist, type='bar', ax=macd_ax, color='grey', alpha=0.5)
            ]

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


# --- Main Application Window ---
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

        # --- Layout and Widgets ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        top_level_layout = QVBoxLayout(central_widget)

        # --- Top Toolbar ---
        toolbar_layout = QHBoxLayout()
        self.scan_button = QPushButton("Run Scan")
        self.stop_scan_button = QPushButton("Stop")
        self.clear_cache_button = QPushButton("Clear Cache")
        self.manage_tickers_button = QPushButton("Manage Tickers")
        self.help_button = QPushButton("Help")
        self.export_csv_button = QPushButton("Export CSV")
        self.export_excel_button = QPushButton("Export XLSX")

        self.export_csv_button.setEnabled(False)
        self.export_excel_button.setEnabled(False)
        self.stop_scan_button.hide()
        self.stop_scan_button.setEnabled(False)

        toolbar_layout.addWidget(self.scan_button)
        toolbar_layout.addWidget(self.stop_scan_button)
        toolbar_layout.addStretch()
        toolbar_layout.addWidget(self.manage_tickers_button)
        toolbar_layout.addWidget(self.clear_cache_button)
        toolbar_layout.addWidget(self.export_csv_button)
        toolbar_layout.addWidget(self.export_excel_button)
        toolbar_layout.addWidget(self.help_button)
        top_level_layout.addLayout(toolbar_layout)

        # --- Main Content Area (Sidebar + Results) ---
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Sidebar for Scenarios
        self.scenario_tree = QTreeWidget()
        self.scenario_tree.setHeaderHidden(True)
        self.populate_scenario_tree()
        main_splitter.addWidget(self.scenario_tree)

        # Right side for results and filters
        results_area_widget = QWidget()
        results_layout = QVBoxLayout(results_area_widget)

        # Filter Ribbon
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Enter Ticker or Name...")
        self.search_input.setClearButtonEnabled(True)
        filter_layout.addWidget(self.search_input)

        filter_layout.addWidget(QLabel("Single Ticker:"))
        self.single_ticker_input = QLineEdit()
        self.single_ticker_input.setPlaceholderText("e.g., AAPL")
        self.single_ticker_scan_button = QPushButton("Scan")
        filter_layout.addWidget(self.single_ticker_input)
        filter_layout.addWidget(self.single_ticker_scan_button)
        results_layout.addLayout(filter_layout)

        self.table_view = QTableView()
        self.table_view.setSortingEnabled(True)
        results_layout.addWidget(self.table_view)

        main_splitter.addWidget(results_area_widget)
        main_splitter.setSizes([250, 950]) # Initial size ratio for sidebar and main area

        top_level_layout.addWidget(main_splitter)

        # Status bar with progress bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.progress_bar = QProgressBar()
        self.status_bar.addPermanentWidget(self.progress_bar)
        self.progress_bar.hide()

        # --- Connections ---
        self.scan_button.clicked.connect(self.start_full_scan)
        self.single_ticker_scan_button.clicked.connect(self.start_single_ticker_scan)
        self.stop_scan_button.clicked.connect(self.stop_scan)
        self.clear_cache_button.clicked.connect(self.clear_cache)
        self.manage_tickers_button.clicked.connect(self.open_ticker_manager)
        self.help_button.clicked.connect(self.show_about_dialog)
        self.table_view.doubleClicked.connect(self.open_chart_for_selection)
        self.export_csv_button.clicked.connect(self.export_to_csv)
        self.export_excel_button.clicked.connect(self.export_to_excel)
        self.search_input.textChanged.connect(self.filter_table)

    def populate_scenario_tree(self):
        """
        Loads scenarios from the runner and populates the tree widget,
        grouping them by the 'group' key from the config.
        """
        scenarios = ScenarioRunner.load_scenarios_config()

        groups = {}
        for scenario in scenarios:
            group_name = scenario.get('group', 'Uncategorized')
            if group_name not in groups:
                groups[group_name] = []
            groups[group_name].append(scenario)

        for group_name, scenarios_in_group in groups.items():
            group_item = QTreeWidgetItem(self.scenario_tree, [group_name])
            for scenario in scenarios_in_group:
                child_item = QTreeWidgetItem(group_item, [scenario['name']])
                child_item.setData(0, Qt.ItemDataRole.UserRole, scenario['id']) # Store ID
                child_item.setToolTip(0, scenario.get('description', ''))
            self.scenario_tree.expandItem(group_item) # Expand groups by default

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

    def show_about_dialog(self):
        """Shows the 'About' dialog with application info and credits."""
        about_text = f"""
        <b>{config.APP_NAME}</b>
        <p>Version 1.0</p>
        <p>This application scans global stock markets to identify potential long-rebound candidates based on a variety of technical and fundamental scenarios.</p>
        <p>This application was developed by Lukas Morcinek.</p>
        <hr>
        <p><b>Scanning Scenarios</b></p>
        <p>The application uses eight different scenarios, each suited to a different trading style and concept.</p>

        <h4>Classic Oversold</h4>
        <ul>
            <li><b>Concept:</b> A contrarian, mean-reversion strategy. It operates on the idea that a stock's price, after a sharp decline, will likely bounce back (revert) to its long-term average. The scan identifies stocks that are technically "oversold" (indicated by a low Relative Strength Index - RSI) and are approaching a significant historical support level (like the 200-day moving average).</li>
            <li><b>Suitability:</b> Best for short- to medium-term traders who are comfortable with contrarian plays and believe the market has overreacted to negative news. It aims to identify potential bottoming-out points.</li>
            <li><b>Limitations:</b> This strategy can be risky and is sometimes referred to as "catching a falling knife." A stock can remain oversold for an extended period, and support levels can break, leading to further declines. It is most effective when confirmed by other indicators or a bullish market context.</li>
        </ul>

        <h4>Quality Stock Pullback</h4>
        <ul>
            <li><b>Concept:</b> A trend-following strategy, often summarized as "buying the dip." It looks for fundamentally strong companies that are in a confirmed long-term uptrend and have experienced a temporary, minor price drop, bringing them closer to a short-term support level like the 50-day moving average.</li>
            <li><b>Suitability:</b> Ideal for traders who prefer to follow the trend rather than bet against it. It offers a chance to enter a strong, upward-moving stock at a more reasonable price point (GARP - Growth at a Reasonable Price).</li>
            <li><b>Limitations:</b> A minor pullback can sometimes be the beginning of a major trend reversal. The 50-day moving average is not a guaranteed support level. The fundamental metrics are based on past performance and do not guarantee future results.</li>
        </ul>

        <h4>Fundamental Divergence</h4>
        <ul>
            <li><b>Concept:</b> A value-oriented, contrarian strategy that seeks to find a mismatch between a company's strong financial health and its recent lackluster stock performance. The scan looks for companies with solid fundamentals (e.g., good growth, low debt) whose stock price has been stagnating or underperforming the market.</li>
            <li><b>Suitability:</b> Best for patient, long-term investors who conduct their own fundamental analysis. It can help uncover potentially undervalued "hidden gems" before they are discovered by the broader market.</li>
            <li><b>Limitations:</b> The market can ignore an "undervalued" stock for a long time, leading to a "value trap." There may be valid reasons for the poor stock performance that are not captured by the screener's fundamental metrics.</li>
        </ul>

        <h4>Momentum Breakout</h4>
        <ul>
            <li><b>Concept:</b> A classic momentum strategy based on the principle that "winners keep winning." It identifies stocks that are breaking out to new 52-week highs, especially when accompanied by a surge in trading volume. This suggests strong buying interest and the potential for continued upward movement.</li>
            <li><b>Suitability:</b> For active traders who want to ride strong, established trends. It focuses on stocks that are already demonstrating significant positive momentum.</li>
            <li><b>Limitations:</b> This strategy carries the risk of buying at the peak (a "false breakout"). A stock can quickly reverse after hitting a new high. It requires disciplined risk management, such as using tight stop-losses.</li>
        </ul>

        <h4>Golden Cross</h4>
        <ul>
            <li><b>Concept:</b> A long-term trend-following signal. A Golden Cross occurs when a shorter-term moving average (typically the 50-day) crosses above a longer-term moving average (typically the 200-day). It is widely regarded as a signal of a potential major, long-term uptrend.</li>
            <li><b>Suitability:</b> For long-term investors and position traders who are looking to identify major shifts in a stock's primary trend. It can be used to confirm the start of a new bull phase for a stock.</li>
            <li><b>Limitations:</b> This is a lagging indicator, meaning a significant portion of the price move may have already occurred by the time the signal appears. It can also generate false signals in choppy, sideways markets where the moving averages cross back and forth frequently.</li>
        </ul>

        <h4>Mean Reversion (Bollinger Bands)</h4>
        <ul>
            <li><b>Concept:</b> A short-term, mean-reversion strategy that uses Bollinger Bands to identify statistically oversold conditions. When a stock's price touches or closes below its lower Bollinger Band, it is considered to be far from its recent average price and may be due for a bounce.</li>
            <li><b>Suitability:</b> For short-term "swing" traders looking for quick rebound opportunities. It provides clear, statistically-defined entry points for bounce plays.</li>
            <li><b>Limitations:</b> In a strong, sustained downtrend, a stock can "walk the band" by continuously trading at or near the lower band without reverting to the mean. This signal is purely technical and ignores all fundamental factors.</li>
        </ul>

        <h4>Volatility Squeeze</h4>
        <ul>
            <li><b>Concept:</b> A pre-breakout or volatility-based strategy. It identifies stocks where price volatility has contracted to an unusually low level (i.e., the Bollinger Bands have narrowed significantly). This "squeeze" often precedes a period of high volatility—a significant price move or breakout.</li>
            <li><b>Suitability:</b> For traders who want to position themselves *before* a major price move occurs. It allows for setting up trades with well-defined risk (e.g., placing stops outside the narrow consolidation range).</li>
            <li><b>Limitations:</b> The scan does not predict the *direction* of the breakout, which could be up or down. A stock can also remain in a low-volatility state for a longer-than-expected period. It requires a plan for how to trade the eventual breakout in either direction.</li>
        </ul>

        <h4>High-Quality Dividend</h4>
        <ul>
            <li><b>Concept:</b> A value and income-investing strategy. It focuses not just on a high dividend yield, but on the *sustainability* of that dividend. It filters for companies with healthy financials (e.g., a reasonable payout ratio, low debt) to avoid "yield traps"—stocks with high but risky dividends that are likely to be cut.</li>
            <li><b>Suitability:</b> For long-term, income-oriented investors who prioritize receiving a steady stream of cash flow from their investments over short-term capital appreciation.</li>
            <li><b>Limitations:</b> A history of stable dividends does not guarantee future payments, as they can be cut at any time. The strategy is less focused on growth and may underperform in strong bull markets where growth stocks are favored.</li>
        </ul>
        <hr>
        <p><b>Column Explanations:</b></p>
        <ul>
            <li><b>Ticker:</b> The stock ticker symbol of the company.</li>
            <li><b>Name:</b> The name of the company.</li>
            <li><b>Scenario:</b> The screening scenario that qualified the stock.</li>
            <li><b>Score:</b> A weighted score (0-100) that rates the rebound potential. Higher is better. Hover over a cell for details.</li>
            <li><b>Price:</b> The last closing price of the stock.</li>
        </ul>
        <hr>
        <p><b>Disclaimer:</b></p>
        <p>This tool is for informational purposes only and does not constitute financial advice. The results are based on historical data and technical indicators, which do not guarantee future performance. Always conduct your own thorough research before making any investment decisions.</p>
        """
        QMessageBox.about(self, f"About {config.APP_NAME}", about_text)

    def start_full_scan(self):
        """Starts a full scan for all tickers."""
        self.start_scan()

    def start_single_ticker_scan(self):
        """Starts a scan for a single, specified ticker."""
        ticker = self.single_ticker_input.text().strip().upper()
        if not ticker:
            QMessageBox.warning(self, "Input Error", "Please enter a ticker to scan.")
            return
        self.start_scan(ticker=ticker)

    def start_scan(self, ticker: str = None):
        """Sets up and starts the analysis worker thread."""
        self.scan_button.setEnabled(False)
        self.single_ticker_scan_button.setEnabled(False)
        self.stop_scan_button.show()
        self.stop_scan_button.setEnabled(True)
        self.clear_cache_button.setEnabled(False)
        self.status_bar.showMessage("Starting scan...")
        self.progress_bar.setValue(0)
        self.progress_bar.show()

        selected_items = self.scenario_tree.selectedItems()
        if not selected_items or selected_items[0].childCount() > 0:
             QMessageBox.warning(self, "Selection Error", "Please select a specific scenario, not a group.")
             self.scan_button.setEnabled(True)
             self.single_ticker_scan_button.setEnabled(True)
             return

        selected_scenario_id = selected_items[0].data(0, Qt.ItemDataRole.UserRole)

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
        """Shows a dialog box when an error occurs during the scan."""
        exctype, value, tb = error_info
        self.status_bar.showMessage(f"Scan Error: {value}")
        self.scan_button.setEnabled(True)
        self.single_ticker_scan_button.setEnabled(True)
        self.stop_scan_button.hide()
        self.stop_scan_button.setEnabled(False)
        self.clear_cache_button.setEnabled(True)
        self.progress_bar.hide()
        QMessageBox.critical(self, "Scan Error", f"An unexpected error occurred:\n\n{value}\n\nTraceback:\n{tb}")

    def update_status_text(self, message):
        """Updates the text in the status bar."""
        self.status_bar.showMessage(message)

    def update_progress_bar(self, percent):
        """Updates the progress bar value."""
        self.progress_bar.setValue(percent)

    def display_results(self, results):
        """Receives the results (List[ReboundCandidate]) and displays them."""
        # Check if the scan was cancelled, otherwise show the count
        if self.worker and self.worker._is_cancelled:
             self.status_bar.showMessage(f"Scan stopped by user. {len(results)} candidates found before stopping.")
        else:
            self.status_bar.showMessage(f"Scan complete. Found {len(results)} candidates.")

        self.scan_button.setEnabled(True)
        self.single_ticker_scan_button.setEnabled(True)
        self.stop_scan_button.hide()
        self.stop_scan_button.setEnabled(False)
        self.clear_cache_button.setEnabled(True)
        self.progress_bar.hide()

        if not results:
            self.results_df = pd.DataFrame()
            self.table_view.setModel(None)
            QMessageBox.information(self, "Scan Complete", "No potential candidates found.")
            return

        # Convert list of ReboundCandidate objects to a list of dicts for the DataFrame
        # The main table will only show universally applicable columns.
        # Scenario-specific details (like RSI/Prox scores) are in the tooltip.
        display_results_list = []
        for r in results:
            # Format ROE as a percentage string, handling None
            roe_val = r.fundamentals.get('roe')
            roe_str = f"{roe_val * 100:.2f}%" if roe_val is not None else "N/A"

            res_dict = {
                "Ticker": r.ticker,
                "Name": r.fundamentals.get('name', 'N/A'),
                "Scenario": r.scenario,
                "Rebound Score": r.rebound_score,
                "Tech. Score": r.technical_score,
                "Fund. Score": r.fundamental_score,
                "ROE": roe_str,
                "Price": r.technicals.get('price', '-'),
            }
            display_results_list.append(res_dict)

        self.results_df = pd.DataFrame(display_results_list)
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


        # Automatically sort by the 'Rebound Score' column, descending
        try:
            score_col_index = self.results_df.columns.get_loc("Rebound Score")
            self.table_view.sortByColumn(score_col_index, Qt.SortOrder.DescendingOrder)
        except KeyError:
            pass # No score column

        has_results = not self.results_df.empty
        self.export_csv_button.setEnabled(has_results)
        self.export_excel_button.setEnabled(has_results)


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

    def clear_cache(self):
        """Deletes the contents of the cache directory."""
        reply = QMessageBox.question(self, "Confirm Cache Deletion",
                                     f"Are you sure you want to delete all cached data from {config.CACHE_DIR}?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                shutil.rmtree(config.CACHE_DIR)
                config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
                self.status_bar.showMessage("Cache successfully cleared.", 5000)
            except Exception as e:
                QMessageBox.critical(self, "Cache Error", f"Failed to clear cache: {e}")
