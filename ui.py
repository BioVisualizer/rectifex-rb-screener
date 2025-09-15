# ui.py
# Contains all PyQt6 classes for the user interface.

import sys
import logging
import pandas as pd
import shutil
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableView, QStatusBar, QFileDialog, QMessageBox, QHeaderView,
    QProgressBar, QComboBox, QLabel, QLineEdit
)
from PyQt6.QtCore import (
    QObject, QThread, pyqtSignal, QAbstractTableModel, Qt, QSortFilterProxyModel, QRegularExpression
)
from PyQt6.QtGui import QColor, QIcon

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
from fundamental_fetcher import FundamentalFetcher
from rebound_scenarios import ScenarioRunner, calculate_rsi, calculate_sma, calculate_macd

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
    def __init__(self, selected_scenario: str):
        super().__init__()
        self.signals = WorkerSignals()
        self.selected_scenario = selected_scenario
        self._is_cancelled = False

    def cancel(self):
        """Sets the cancellation flag to True."""
        logging.info("Cancellation requested for worker.")
        self._is_cancelled = True

    def run(self):
        """Runs the analysis and emits signals for progress and completion."""
        try:
            fetcher = FundamentalFetcher()
            runner = ScenarioRunner(
                fundamental_fetcher=fetcher,
                progress_callback=self.signals.progress,
                progress_percent_callback=self.signals.progress_percent,
                is_cancelled_callback=lambda: self._is_cancelled
            )

            # Run the selected scenario using the new generic method
            results = asyncio.run(runner.run_scenario(self.selected_scenario))
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
            'Score', 'RSI', 'Price', 'Dist_SMA(%)', 'Dist_Low(%)'
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
            score = self._data.iloc[row]["Score"]
            if score > 80:
                return QColor("#d4edda")
            elif score > 60:
                return QColor("#fff3cd")

        if role == Qt.ItemDataRole.ToolTipRole and column_name == "Score":
            base_tooltip = "Overall score (0-100) that rates the rebound potential. Higher is better."

            # Get the full candidate data for this row to create a detailed tooltip
            if row < len(self.candidates_data):
                candidate = self.candidates_data[row]
                # Only the 'Classic Oversold' scenario has this specific breakdown
                if candidate.scenario == "Classic Oversold":
                    rsi_score = candidate.technicals.get('rsi_score')
                    prox_score = candidate.technicals.get('prox_score')
                    if rsi_score is not None and prox_score is not None:
                        breakdown_tooltip = f"\n\nBreakdown ('Classic Oversold'):\n- RSI Score: {int(rsi_score)} / 60\n- Proximity Score: {int(prox_score)} / 40"
                        return base_tooltip + breakdown_tooltip
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
        Custom sorting logic. It performs numerical comparison for the 'Score'
        column and falls back to default string comparison for all others.
        """
        col = self.sortColumn()
        source_model = self.sourceModel()

        # Ensure the column index is valid for the source model's dataframe
        if col >= len(source_model.get_dataframe().columns):
            return super().lessThan(left, right)

        column_name = source_model.get_dataframe().columns[col]

        if column_name == "Score":
            left_data = source_model.data(left, Qt.ItemDataRole.EditRole)
            right_data = source_model.data(right, Qt.ItemDataRole.EditRole)

            try:
                # Perform numerical comparison
                return float(left_data) < float(right_data)
            except (ValueError, TypeError):
                # Fallback for any non-numeric data
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
        """
        try:
            # Clear previous figure content before drawing a new plot
            self.figure.clear()

            if candidate.history_df is None or candidate.history_df.empty:
                ax = self.figure.add_subplot(111)
                ax.text(0.5, 0.5, f"Historical data for {candidate.ticker} not found.",
                        horizontalalignment='center', verticalalignment='center')
                self.canvas.draw()
                return

            plot_data = candidate.history_df.copy()

            # --- Ensure all indicators for plotting are present ---
            if 'SMA50' not in plot_data.columns:
                plot_data['SMA50'] = calculate_sma(plot_data['Close'], 50)
            if 'SMA200' not in plot_data.columns:
                plot_data['SMA200'] = calculate_sma(plot_data['Close'], 200)
            if 'RSI' not in plot_data.columns:
                plot_data['RSI'] = calculate_rsi(plot_data['Close'])

            macd_line, signal_line, macd_hist = calculate_macd(plot_data['Close'])

            # --- Create addplots ---
            add_plots = [
                mpf.make_addplot(plot_data['SMA50'], color='blue', width=0.7),
                mpf.make_addplot(plot_data['SMA200'], color='orange', width=0.7),
                mpf.make_addplot(plot_data['RSI'], panel=2, color='purple', ylabel='RSI', width=0.8),
                mpf.make_addplot(macd_hist, type='bar', panel=3, color='grey', alpha=0.5, ylabel='MACD'),
                mpf.make_addplot(macd_line, panel=3, color='blue', width=0.7),
                mpf.make_addplot(signal_line, panel=3, color='red', width=0.7)
            ]

            # --- Manually create axes to plot on our existing Figure ---
            gs = self.figure.add_gridspec(4, 1, height_ratios=[6, 1, 2, 2])
            price_ax = self.figure.add_subplot(gs[0, 0])
            volume_ax = self.figure.add_subplot(gs[1, 0], sharex=price_ax)
            rsi_ax = self.figure.add_subplot(gs[2, 0], sharex=price_ax)
            macd_ax = self.figure.add_subplot(gs[3, 0], sharex=price_ax)

            # Hide tick labels on shared x-axes for a cleaner look
            price_ax.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)
            volume_ax.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)
            rsi_ax.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)

            # --- Plot data using mplfinance on the created axes ---
            mpf.plot(plot_data,
                     type='candle',
                     ax=price_ax,
                     volume=volume_ax,
                     addplot=add_plots,
                     style='yahoo', # The style will be applied to the axes
                     xrotation=20
                    )

            # --- Post-processing on axes ---
            self.figure.suptitle(f'{candidate.ticker} - {candidate.scenario}', y=0.98)

            rsi_ax.axhline(70, color='red', linestyle='--', linewidth=0.7, alpha=0.8)
            rsi_ax.axhline(30, color='green', linestyle='--', linewidth=0.7, alpha=0.8)

            eps_growth = candidate.fundamentals.get('earningsGrowth')
            eps_growth_str = f"{eps_growth * 100:.2f}%" if eps_growth is not None else "N/A"
            rev_growth = candidate.fundamentals.get('revenueGrowth')
            rev_growth_str = f"{rev_growth * 100:.2f}%" if rev_growth is not None else "N/A"
            price = candidate.technicals.get('price')
            price_str = f"${price:.2f}" if price is not None else "N/A"
            rsi_val = plot_data['RSI'].iloc[-1]
            rsi_str = f"{rsi_val:.1f}" if pd.notna(rsi_val) else "N/A"

            info_text = (f"Scenario: {candidate.scenario}\nPrice: {price_str}\nRSI: {rsi_str}\n"
                         f"EPS Growth: {eps_growth_str}\nRev Growth: {rev_growth_str}")
            price_ax.text(0.02, 0.98, info_text, transform=price_ax.transAxes, fontsize=9,
                          verticalalignment='top', bbox=dict(boxstyle='round,pad=0.5', fc='yellow', alpha=0.5))

            self.figure.tight_layout(rect=[0, 0, 1, 0.96]) # Adjust layout to make room for suptitle
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
        main_layout = QVBoxLayout(central_widget)

        controls_layout = QHBoxLayout()
        self.scenarioComboBox = QComboBox()
        self.scenarioComboBox.setObjectName("scenarioComboBox")
        # Populate scenarios dynamically from the ScenarioRunner
        self.scenarioComboBox.addItems(ScenarioRunner.get_available_scenarios())

        self.scan_button = QPushButton("Start Scan")
        self.stop_scan_button = QPushButton("Stop Scan")
        self.clear_cache_button = QPushButton("Clear Cache")
        self.manage_tickers_button = QPushButton("Manage Tickers")
        self.help_button = QPushButton("Help")
        self.export_csv_button = QPushButton("Export to CSV")
        self.export_excel_button = QPushButton("Export to XLSX")

        self.export_csv_button.setEnabled(False)
        self.export_excel_button.setEnabled(False)
        self.stop_scan_button.hide()
        self.stop_scan_button.setEnabled(False)


        controls_layout.addWidget(self.scenarioComboBox)
        controls_layout.addWidget(self.scan_button)
        controls_layout.addWidget(self.stop_scan_button)
        controls_layout.addWidget(self.clear_cache_button)
        controls_layout.addWidget(self.manage_tickers_button)
        controls_layout.addStretch()
        controls_layout.addWidget(self.export_csv_button)
        controls_layout.addWidget(self.export_excel_button)
        controls_layout.addWidget(self.help_button)

        main_layout.addLayout(controls_layout)

        # Add search bar below the main controls
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Filter Results:"))
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("Enter Ticker or Name to filter...")
        self.search_input.setClearButtonEnabled(True)
        search_layout.addWidget(self.search_input)
        main_layout.addLayout(search_layout)

        self.table_view = QTableView()
        self.table_view.setSortingEnabled(True)
        main_layout.addWidget(self.table_view)

        # Status bar with progress bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.progress_bar = QProgressBar()
        self.status_bar.addPermanentWidget(self.progress_bar)
        self.progress_bar.hide()

        # --- Connections ---
        self.scan_button.clicked.connect(self.start_scan)
        self.stop_scan_button.clicked.connect(self.stop_scan)
        self.clear_cache_button.clicked.connect(self.clear_cache)
        self.manage_tickers_button.clicked.connect(self.open_ticker_manager)
        self.help_button.clicked.connect(self.show_about_dialog)
        self.table_view.doubleClicked.connect(self.open_chart_for_selection)
        self.export_csv_button.clicked.connect(self.export_to_csv)
        self.export_excel_button.clicked.connect(self.export_to_excel)
        self.search_input.textChanged.connect(self.filter_table)

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
        <p>This application scans global stock markets to identify potential long-rebound candidates based on technical analysis criteria.</p>
        <p>This application was developed by Lukas Morcinek.</p>
        <hr>
        <p><b>Scanning Scenarios & Score</b></p>
        <p>The application uses different scenarios to find potential candidates:</p>
        <ul>
            <li><b>Classic Oversold:</b> Looks for technically oversold stocks (low RSI) near strong, long-term support levels (200-day average or 90-day low).</li>
            <li><b>Quality Stock Pullback:</b> Finds fundamentally strong companies in a healthy uptrend that have experienced a minor price dip towards their 50-day average.</li>
            <li><b>Momentum Breakout:</b> Identifies stocks hitting new 52-week highs on high trading volume, signaling strong upward momentum.</li>
            <li><b>Golden Cross:</b> Detects when a stock's 50-day moving average has recently crossed above its 200-day average, a strong long-term bullish signal.</li>
            <li><b>Mean Reversion (Bollinger Bands):</b> Finds stocks trading at or below their lower Bollinger Band, suggesting a statistically oversold condition and a potential rebound.</li>
            <li><b>Volatility Squeeze:</b> Flags stocks where price volatility has become unusually low (narrow Bollinger Bands), which often precedes a large price move.</li>
            <li><b>High-Quality Dividend:</b> A value-focused scan that looks for stocks with an attractive dividend yield, but filters for sustainability (payout ratio) and financial health (low debt).</li>
        </ul>
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

    def start_scan(self):
        """Sets up and starts the analysis worker thread."""
        self.scan_button.setEnabled(False)
        self.stop_scan_button.show()
        self.stop_scan_button.setEnabled(True)
        self.clear_cache_button.setEnabled(False)
        self.status_bar.showMessage("Starting scan...")
        self.progress_bar.setValue(0)
        self.progress_bar.show()

        selected_scenario = self.scenarioComboBox.currentText()

        self.thread = QThread()
        self.worker = AnalysisWorker(selected_scenario=selected_scenario)
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
            res_dict = {
                "Ticker": r.ticker,
                "Name": r.fundamentals.get('name', 'N/A'),
                "Scenario": r.scenario,
                "Score": r.score,
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


        # Automatically sort by the 'Score' column, descending
        try:
            score_col_index = self.results_df.columns.get_loc("Score")
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
