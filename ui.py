# ui.py
# Contains all PyQt6 classes for the user interface.

import sys
import pandas as pd
import shutil
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableView, QStatusBar, QFileDialog, QMessageBox, QHeaderView,
    QProgressBar
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
import config
import data_loader
import analysis
from ticker_manager import TickerManagerDialog

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
    def __init__(self):
        super().__init__()
        self.signals = WorkerSignals()

    def run(self):
        """Runs the analysis and emits signals for progress and completion."""
        try:
            # The run_analysis function now accepts a progress callback
            results = analysis.run_analysis(progress_callback=self.signals.progress, progress_percent_callback=self.signals.progress_percent)
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
        self.numeric_columns = ['Score', 'RSI', 'Price', 'Dist_SMA(%)', 'Dist_Low(%)']

    def rowCount(self, parent=None):
        return self._data.shape[0]

    def columnCount(self, parent=None):
        return self._data.shape[1]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid():
            column_name = self._data.columns[index.column()]

            if role == Qt.ItemDataRole.DisplayRole:
                return str(self._data.iloc[index.row(), index.column()])

            if role == Qt.ItemDataRole.TextAlignmentRole:
                if column_name in self.numeric_columns:
                    return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

            if role == Qt.ItemDataRole.BackgroundRole:
                score = self._data.iloc[index.row()]["Score"]
                if score > 80:
                    return QColor("#d4edda")
                elif score > 60:
                    return QColor("#fff3cd")

            if role == Qt.ItemDataRole.ToolTipRole and column_name == "Score":
                rsi_score = self._data.iloc[index.row()]["RSI_Score"]
                prox_score = self._data.iloc[index.row()]["Prox_Score"]
                return f"Score Breakdown:\n- RSI Score: {rsi_score} / 60\n- Proximity Score: {prox_score} / 40"
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return str(self._data.columns[section])
        return None

    def get_dataframe(self):
        return self._data


# --- Charting Window ---
class ChartWindow(QWidget):
    """A separate window for displaying a detailed stock chart for a given ticker."""
    def __init__(self, ticker):
        super().__init__()
        self.ticker = ticker
        self.setWindowTitle(f"Chart for {self.ticker} - {config.APP_NAME}")
        self.setGeometry(150, 150, 800, 600)

        layout = QVBoxLayout()
        self.setLayout(layout)
        self.canvas = FigureCanvas(Figure(figsize=(8, 6)))
        layout.addWidget(self.canvas)

        self.load_chart_data()

    def load_chart_data(self):
        """Fetches stock data, calculates indicators, and plots the chart."""
        data = data_loader.get_stock_data(self.ticker)
        if data is None or data.empty:
            QMessageBox.warning(self, "Data Error", f"Could not load data for {self.ticker}.")
            return

        data = data.last(f'{config.CHART_HISTORY_MONTHS}M')
        data['SMA200'] = analysis.calculate_sma(data['Close'], config.SMA_SUPPORT_PERIOD)
        data['RSI'] = analysis.calculate_rsi(data['Close'], config.RSI_PERIOD)

        ap0 = [mpf.make_addplot(data['SMA200'], color='blue', width=0.7)]

        fig = self.canvas.figure
        fig.clf()
        ax1 = fig.add_subplot(3, 1, (1, 2))
        ax2 = fig.add_subplot(3, 1, 3, sharex=ax1)
        ax1.set_ylabel('Price')
        ax2.set_ylabel('RSI')

        mpf.plot(data, type='candle', ax=ax1, volume=ax1.twinx(), addplot=ap0, style='yahoo',
                 title=f'{self.ticker} - {config.CHART_HISTORY_MONTHS} Months')

        ax2.plot(data.index, data['RSI'], color='orange')
        ax2.axhline(70, color='red', linestyle='--', linewidth=0.5)
        ax2.axhline(30, color='green', linestyle='--', linewidth=0.5)
        ax2.set_ylim(0, 100)
        ax2.grid(True)

        fig.tight_layout()
        self.canvas.draw()


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
        self.scan_button = QPushButton("Start Scan")
        self.clear_cache_button = QPushButton("Clear Cache")
        self.manage_tickers_button = QPushButton("Manage Tickers")
        self.help_button = QPushButton("Help")
        self.export_csv_button = QPushButton("Export to CSV")
        self.export_excel_button = QPushButton("Export to XLSX")

        self.export_csv_button.setEnabled(False)
        self.export_excel_button.setEnabled(False)

        controls_layout.addWidget(self.scan_button)
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
        self.clear_cache_button.clicked.connect(self.clear_cache)
        self.manage_tickers_button.clicked.connect(self.open_ticker_manager)
        self.help_button.clicked.connect(self.show_about_dialog)
        self.table_view.doubleClicked.connect(self.open_chart_for_selection)
        self.export_csv_button.clicked.connect(self.export_to_csv)
        self.export_excel_button.clicked.connect(self.export_to_excel)

        self.setup_header_tooltips()

    def open_ticker_manager(self):
        """Opens the Ticker Manager dialog."""
        dialog = TickerManagerDialog(self)
        dialog.exec()

    def setup_header_tooltips(self):
        """Sets tooltips for the table headers."""
        header = self.table_view.horizontalHeader()
        tooltip_map = {
            "Score": "Rebound-Score (0-100).\nGrün: > 80, Gelb: 60-79\nTooltip auf Zelle für Details.",
            "Dist_SMA(%)": "Prozentualer Abstand zum 200-Tage-Durchschnitt (SMA).",
            "Dist_Low(%)": "Prozentualer Abstand zum 90-Tage-Tief."
        }
        # We need to set the model first for this to work, so we'll do it after a scan.
        # This is just a placeholder for the logic. The actual setting happens in display_results.

    def show_about_dialog(self):
        """Shows the 'About' dialog with application info and credits."""
        about_text = f"""
        <b>{config.APP_NAME}</b>
        <p>Version 1.0</p>
        <p>This application scans global stock markets to identify potential long-rebound candidates based on technical analysis criteria.</p>
        <p>This application was developed by Lukas Morcinek.</p>
        <hr>
        <p><b>Disclaimer:</b></p>
        <p>This tool is for informational purposes only and does not constitute financial advice. The results are based on historical data and technical indicators, which do not guarantee future performance. Always conduct your own thorough research before making any investment decisions.</p>
        """
        QMessageBox.about(self, f"About {config.APP_NAME}", about_text)

    def start_scan(self):
        """Sets up and starts the analysis worker thread."""
        self.scan_button.setEnabled(False)
        self.clear_cache_button.setEnabled(False)
        self.status_bar.showMessage("Starting scan...")
        self.progress_bar.setValue(0)
        self.progress_bar.show()

        self.thread = QThread()
        self.worker = AnalysisWorker()
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
        """Receives the results from the worker and displays them in the table."""
        self.status_bar.showMessage(f"Scan complete. Found {len(results)} candidates.")
        self.scan_button.setEnabled(True)
        self.clear_cache_button.setEnabled(True)
        self.progress_bar.hide()

        if not results:
            self.results_df = pd.DataFrame()
            self.table_view.setModel(None)
            QMessageBox.information(self, "Scan Complete", "No potential candidates found.")
        else:
            # Create a DataFrame with only the columns to be displayed
            display_cols = ["Ticker", "Name", "Market", "Score", "RSI", "Price", "Dist_SMA(%)", "Dist_Low(%)"]
            self.results_df = pd.DataFrame(results)
            display_df = self.results_df[display_cols]

            model = PandasModel(display_df)
            proxy_model = QSortFilterProxyModel()
            proxy_model.setSourceModel(model)
            self.table_view.setModel(proxy_model)
            self.table_view.resizeColumnsToContents()
            self.table_view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            self.table_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            self.table_view.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)

            # Set Header Tooltips
            header = self.table_view.horizontalHeader()
            tooltip_map = {
                "Score": "Rebound Score (0-100).\nGreen: > 80, Yellow: 60-79\n(Hover over a cell for breakdown)",
                "Dist_SMA(%)": "Percentage distance from the 200-day Simple Moving Average.",
                "Dist_Low(%)": "Percentage distance from the 90-day low."
            }
            for i, col_name in enumerate(display_df.columns):
                if col_name in tooltip_map:
                    header.setToolTip(f"{header.toolTip()}\n{col_name}: {tooltip_map[col_name]}")


            # Automatically sort by the 'Score' column, descending
            try:
                score_col_index = display_df.columns.get_loc("Score")
                self.table_view.sortByColumn(score_col_index, Qt.SortOrder.DescendingOrder)
            except KeyError:
                logging.warning("Could not find 'Score' column for auto-sorting.")

        has_results = not self.results_df.empty
        self.export_csv_button.setEnabled(has_results)
        self.export_excel_button.setEnabled(has_results)

    def open_chart_for_selection(self, index):
        """Opens a new chart window for the selected stock."""
        proxy_model = self.table_view.model()
        source_index = proxy_model.mapToSource(index)

        ticker_col_index = self.results_df.columns.get_loc("Ticker")
        ticker = self.results_df.iloc[source_index.row(), ticker_col_index]

        chart_window = ChartWindow(ticker)
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
