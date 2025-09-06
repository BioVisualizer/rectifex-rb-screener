# ui.py
# Contains all PyQt6 classes for the user interface.

import sys
import logging
import pandas as pd
import shutil
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableView, QStatusBar, QFileDialog, QMessageBox, QHeaderView,
    QProgressBar, QComboBox
)
from PyQt6.QtCore import (
    QObject, QThread, pyqtSignal, QAbstractTableModel, Qt, QSortFilterProxyModel
)
from PyQt6.QtGui import QColor, QIcon

# For charting
import matplotlib
matplotlib.use('QtAgg')
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
from rebound_scenarios import ScenarioRunner, calculate_sma, calculate_rsi

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

            results = []
            if self.selected_scenario == "Classic Oversold":
                results = runner.run_classic_oversold()
            elif self.selected_scenario == "Quality Stock Pullback":
                # Run the async function from this synchronous thread
                results = asyncio.run(runner.run_quality_pullback())

            self.signals.result.emit(results)
        except Exception as e:
            import traceback
            self.signals.error.emit((type(e), e, traceback.format_exc()))
        finally:
            self.signals.finished.emit()


# --- Pandas DataFrame Model for QTableView ---

class PandasModel(QAbstractTableModel):
    """A model to interface a pandas DataFrame with a QTableView."""
    def __init__(self, data=pd.DataFrame(), parent=None):
        super().__init__(parent)
        self._data = data
        self.numeric_columns = [
            'AQS', 'RSI', 'Price', 'Dist_SMA(%)', 'Dist_Low(%)'
        ]

    def rowCount(self, parent=None):
        return self._data.shape[0]

    def columnCount(self, parent=None):
        return self._data.shape[1]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid():
            # When using a proxy model, we need to get the correct row from the source dataframe
            source_row = index.row()
            if self.parent() and isinstance(self.parent(), QSortFilterProxyModel):
                source_index = self.parent().mapToSource(index)
                source_row = source_index.row()

            column_name = self._data.columns[index.column()]

            if role == Qt.ItemDataRole.DisplayRole:
                return str(self._data.iloc[source_row, index.column()])

            if role == Qt.ItemDataRole.TextAlignmentRole:
                if column_name in self.numeric_columns:
                    return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

            if role == Qt.ItemDataRole.BackgroundRole:
                if "AQS" in self._data.columns:
                    score = self._data.iloc[source_row]["AQS"]
                    if score > 80:
                        return QColor("#d4edda")
                    elif score > 60:
                        return QColor("#fff3cd")

            if role == Qt.ItemDataRole.ToolTipRole and column_name == "AQS":
                if "AQSTooltip" in self._data.columns:
                    return self._data.iloc[source_row]["AQSTooltip"]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return str(self._data.columns[section])
        return None

    def get_dataframe(self):
        return self._data


# --- Charting Window ---
class ChartWindow(QWidget):
    """A separate window for displaying a detailed stock chart for a given candidate."""
    def __init__(self, candidate: ReboundCandidate):
        super().__init__()
        self.candidate = candidate
        self.setWindowTitle(f"Chart for {self.candidate.ticker} - {config.APP_NAME}")
        self.setGeometry(150, 150, 800, 600)

        layout = QVBoxLayout()
        self.setLayout(layout)
        self.canvas = FigureCanvas(Figure(figsize=(8, 6)))
        layout.addWidget(self.canvas)
        self.ax = None # To hold the main axis

        self.plot_stock_data(candidate)

    def plot_stock_data(self, candidate: ReboundCandidate):
        """Fetches stock data, calculates indicators, and plots the chart for the candidate."""
        try:
            data = data_loader.get_stock_data(candidate.ticker)
            if data is None or data.empty:
                QMessageBox.warning(self, "Data Error", f"Could not load historical data for {candidate.ticker}.")
                return

            # Address FutureWarning for .last()
            end_date = data.index[-1]
            start_date = end_date - pd.DateOffset(months=config.CHART_HISTORY_MONTHS)
            data = data.loc[start_date:end_date]

            # Calculate all required indicators
            data['SMA50'] = calculate_sma(data['Close'], 50)
            data['SMA200'] = calculate_sma(data['Close'], config.SMA_SUPPORT_PERIOD)
            data['RSI'] = calculate_rsi(data['Close'], config.RSI_PERIOD)

            # --- Plotting ---
            fig = self.canvas.figure
            fig.clf()

            # Create axes for candlestick, volume, and RSI
            gs = fig.add_gridspec(3, 1, height_ratios=[2, 1, 1])
            self.ax = fig.add_subplot(gs[0, 0]) # Main plot for price
            ax_vol = fig.add_subplot(gs[1, 0], sharex=self.ax) # Volume plot
            ax_rsi = fig.add_subplot(gs[2, 0], sharex=self.ax) # RSI plot

            # Remove x-axis labels from the main and volume plots for a cleaner look
            self.ax.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)
            ax_vol.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)

            self.ax.set_ylabel('Price')
            ax_vol.set_ylabel('Volume')
            ax_rsi.set_ylabel('RSI')

            # --- Dynamic Overlays ---
            add_plots = []
            if candidate.scenario == "Quality Stock Pullback":
                support_level = candidate.technicals.get('50_sma_value')
                if support_level:
                    # Plotting directly on the axis is fine for simple lines
                    self.ax.axhline(y=support_level, color='green', linestyle='--', label='50-Day SMA Support')
                if not data['SMA50'].empty:
                    # For more complex plots that need to align with the candlestick data, use make_addplot
                    add_plots.append(mpf.make_addplot(data['SMA50'], ax=self.ax, color='green', width=0.7))

            # Always plot 200 SMA for context
            if not data['SMA200'].empty:
                add_plots.append(mpf.make_addplot(data['SMA200'], ax=self.ax, color='blue', width=0.7))

            self.ax.set_title(f'{candidate.ticker} - {candidate.scenario}')

            # Main candle plot
            mpf.plot(data, type='candle', ax=self.ax, volume=ax_vol, addplot=add_plots, style='yahoo')

            # RSI Plot
            ax_rsi.plot(data.index, data['RSI'], color='orange')
            ax_rsi.axhline(70, color='red', linestyle='--', linewidth=0.5)
            ax_rsi.axhline(30, color='green', linestyle='--', linewidth=0.5)
            ax_rsi.set_ylim(0, 100)
            ax_rsi.grid(True)

            # --- Information Box ---
            eps_growth = candidate.fundamentals.get('earningsGrowth')
            eps_growth_str = f"{eps_growth * 100:.2f}%" if eps_growth is not None else "N/A"

            rev_growth = candidate.fundamentals.get('revenueGrowth')
            rev_growth_str = f"{rev_growth * 100:.2f}%" if rev_growth is not None else "N/A"

            price = candidate.technicals.get('price')
            price_str = f"${price:.2f}" if price is not None else "N/A"

            rsi = candidate.technicals.get('rsi')
            rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"

            info_text = (
                f"Scenario: {candidate.scenario}\n"
                f"Price: {price_str}\n"
                f"RSI: {rsi_str}\n"
                f"EPS Growth: {eps_growth_str}\n"
                f"Revenue Growth: {rev_growth_str}"
            )
            self.ax.text(0.02, 0.98, info_text, transform=self.ax.transAxes, fontsize=9,
                        verticalalignment='top', bbox=dict(boxstyle='round,pad=0.5', fc='yellow', alpha=0.5))

            self.ax.legend()
            fig.tight_layout()
            self.canvas.draw()
        except Exception as e:
            logging.error(f"Failed to plot chart for {candidate.ticker}: {e}", exc_info=True)
            QMessageBox.critical(self, "Chart Error", f"An unexpected error occurred while plotting the chart for {candidate.ticker}:\n\n{e}")


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
        self.scenarioComboBox.addItems(["Classic Oversold", "Quality Stock Pullback"])

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

        self.table_view = QTableView()
        self.table_view.setSortingEnabled(True)

        main_layout.addLayout(controls_layout)
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
        <p><b>Scanning Scenarios & AQS</b></p>
        <p>The application uses different scenarios to find potential candidates:</p>
        <ul>
            <li><b>Classic Oversold:</b> This scenario looks for technically oversold stocks that are near strong support levels (like the 200-day average or the 90-day low).</li>
            <li><b>Quality Stock Pullback:</b> This scenario looks for fundamentally sound companies in a long-term uptrend that have recently experienced a small pullback towards their 50-day average.</li>
        </ul>
        <p><b>Column Explanations:</b></p>
        <ul>
            <li><b>Ticker:</b> The stock ticker symbol of the company.</li>
            <li><b>Name:</b> The name of the company.</li>
            <li><b>Scenario:</b> The screening scenario that qualified the stock.</li>
            <li><b>AQS (Adaptive Quality Score):</b> A score (0-100) that rates the stock's quality based on available data. It adaptively calculates the score, meaning a stock isn't penalized for missing data (e.g. from yfinance). Hover over a cell to see which criteria were used in the calculation.</li>
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
        results_list_of_dicts = []
        for r in results:
            res_dict = {
                "Ticker": r.ticker,
                "Name": r.fundamentals.get('name', 'N/A'),
                "AQS": r.score,
                "Letztes Signal": r.last_signal,
                "Scenario": r.scenario,
                "Price": r.technicals.get('price', '-'),
                "AQSTooltip": r.tooltip_text # Add the tooltip text
            }
            results_list_of_dicts.append(res_dict)

        self.results_df = pd.DataFrame(results_list_of_dicts)
        self.all_candidates_data = results # Keep the original objects for charting

        model = PandasModel(self.results_df)
        proxy_model = QSortFilterProxyModel()
        proxy_model.setSourceModel(model)
        self.table_view.setModel(proxy_model)

        # Hide the 'AQSTooltip' column from the user's view
        try:
            tooltip_col_index = self.results_df.columns.get_loc("AQSTooltip")
            self.table_view.setColumnHidden(tooltip_col_index, True)
        except KeyError:
            pass # Column not found, do nothing

        self.table_view.resizeColumnsToContents()
        self.table_view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        # Allow interactive resizing for specific columns
        self.table_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive) # Ticker
        self.table_view.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive) # Szenario


        # Automatically sort by the 'AQS' column, descending
        try:
            score_col_index = self.results_df.columns.get_loc("AQS")
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
