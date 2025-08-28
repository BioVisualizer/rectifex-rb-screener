# ui.py
# Contains all PyQt6 classes for the user interface.

import sys
import pandas as pd
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableView, QStatusBar, QFileDialog, QMessageBox, QHeaderView
)
from PyQt6.QtCore import (
    QObject, QThread, pyqtSignal, QAbstractTableModel, Qt, QSortFilterProxyModel
)
from PyQt6.QtGui import QColor

# For charting
import matplotlib
matplotlib.use('QtAgg') # Use the Qt backend for matplotlib
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import mplfinance as mpf

# App-specific imports
import config
import data_loader
import analysis

# --- Worker Thread for Running Analysis ---

class WorkerSignals(QObject):
    """
    Defines the signals available from a running worker thread.
    Supported signals are:
    - finished: No data, just indicates completion.
    - error: tuple (exctype, value, traceback.format_exc())
    - result: object data returned from processing, e.g., a list of dicts.
    - progress: str message to show in status bar.
    """
    finished = pyqtSignal()
    error = pyqtSignal(tuple)
    result = pyqtSignal(object)
    progress = pyqtSignal(str)

class AnalysisWorker(QObject):
    """
    Worker thread for running the stock analysis to prevent GUI freezing.
    """
    def __init__(self):
        super().__init__()
        self.signals = WorkerSignals()

    def run(self):
        try:
            # The run_analysis function now accepts a progress callback
            results = analysis.run_analysis(progress_callback=self.signals.progress)
            self.signals.result.emit(results)
        except Exception as e:
            import traceback
            self.signals.error.emit((type(e), e, traceback.format_exc()))
        finally:
            self.signals.finished.emit()


# --- Pandas DataFrame Model for QTableView ---

class PandasModel(QAbstractTableModel):
    """
    A model to interface a pandas DataFrame with a QTableView.
    This model provides the data and header information required by the view,
    and handles sorting when a column header is clicked. It also provides
    conditional background coloring for rows based on the 'Score'.
    """
    def __init__(self, data=pd.DataFrame(), parent=None):
        super().__init__(parent)
        self._data = data

    def rowCount(self, parent=None):
        """Returns the number of rows in the model."""
        return self._data.shape[0]

    def columnCount(self, parent=None):
        """Returns the number of columns in the model."""
        return self._data.shape[1]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        """Returns the data at the given index for the given role."""
        if index.isValid():
            if role == Qt.ItemDataRole.DisplayRole:
                return str(self._data.iloc[index.row(), index.column()])
            if role == Qt.ItemDataRole.BackgroundRole:
                # Color rows based on the 'Score' value for visual emphasis.
                score = self._data.iloc[index.row()]["Score"]
                if score > 80:
                    return QColor("#d4edda")  # Light green for high scores
                elif score > 60:
                    return QColor("#fff3cd")  # Light yellow for medium scores
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        """Returns the header data for the given section."""
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                return str(self._data.columns[section])
            if orientation == Qt.Orientation.Vertical:
                return str(self._data.index[section])
        return None

    def sort(self, column, order):
        """Sorts the model by the given column and order."""
        colname = self._data.columns[column]
        self.layoutAboutToBeChanged.emit()
        self._data.sort_values(colname, ascending=(order == Qt.SortOrder.AscendingOrder), inplace=True)
        self.layoutChanged.emit()

    def get_dataframe(self):
        """Returns the underlying pandas DataFrame."""
        return self._data


# --- Charting Window ---

class ChartWindow(QWidget):
    """
    A separate window for displaying a detailed stock chart for a given ticker.
    It shows a candlestick chart, 200-day SMA, and an RSI indicator panel.
    """
    def __init__(self, ticker):
        super().__init__()
        self.ticker = ticker
        self.setWindowTitle(f"Chart for {self.ticker} - {config.APP_NAME}")
        self.setGeometry(150, 150, 800, 600)

        # Setup the Matplotlib canvas
        layout = QVBoxLayout()
        self.setLayout(layout)
        self.canvas = FigureCanvas(Figure(figsize=(8, 6)))
        layout.addWidget(self.canvas)

        # Load and render the chart
        self.load_chart_data()

    def load_chart_data(self):
        """
        Fetches stock data, calculates indicators, and plots the chart
        using mplfinance, embedded within the PyQt widget.
        """
        data = data_loader.get_stock_data(self.ticker)
        if data is None or data.empty:
            QMessageBox.warning(self, "Data Error", f"Could not load data for {self.ticker}.")
            return

        # Keep only the last year of data for the chart
        data = data.last(f'{config.CHART_HISTORY_MONTHS}M')

        # Calculate indicators needed for the plot
        data['SMA200'] = analysis.calculate_sma(data['Close'], config.SMA_SUPPORT_PERIOD)
        data['RSI'] = analysis.calculate_rsi(data['Close'], config.RSI_PERIOD)

        # Define an additional plot for the 200-day SMA
        ap0 = [
            mpf.make_addplot(data['SMA200'], color='blue', width=0.7),
        ]

        # This is the most robust method for embedding mplfinance in PyQt.
        # It creates two subplots (axes) and passes them to mplfinance.
        fig = self.canvas.figure
        fig.clf() # Clear any previous plots

        ax1 = fig.add_subplot(3, 1, (1, 2)) # Main plot for price and volume
        ax2 = fig.add_subplot(3, 1, 3, sharex=ax1) # Shared X-axis for RSI
        ax1.set_ylabel('Price')
        ax2.set_ylabel('RSI')

        # Plot candlesticks, volume, and SMA on the first axis
        mpf.plot(data, type='candle', ax=ax1, volume=ax1.twinx(), addplot=ap0, style='yahoo',
                 title=f'{self.ticker} - {config.CHART_HISTORY_MONTHS} Months')

        # Plot RSI on the second axis
        ax2.plot(data.index, data['RSI'], color='orange')
        ax2.axhline(70, color='red', linestyle='--', linewidth=0.5)
        ax2.axhline(30, color='green', linestyle='--', linewidth=0.5)
        ax2.set_ylim(0, 100)
        ax2.grid(True)

        fig.tight_layout()
        self.canvas.draw()


# --- Main Application Window ---

class MainWindow(QMainWindow):
    """
    The main window of the application. It contains the controls, results table,
    and status bar. It manages the background thread for running the analysis.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle(config.APP_NAME)
        self.setGeometry(100, 100, 1200, 800)

        self.chart_windows = [] # To keep references to open chart windows
        self.results_df = pd.DataFrame()

        # --- Layout and Widgets ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Top bar for controls
        controls_layout = QHBoxLayout()
        self.scan_button = QPushButton("Scan starten")
        self.help_button = QPushButton("Hilfe")
        self.export_csv_button = QPushButton("Export to CSV")
        self.export_excel_button = QPushButton("Export to XLSX")
        self.export_csv_button.setEnabled(False)
        self.export_excel_button.setEnabled(False)

        controls_layout.addWidget(self.scan_button)
        controls_layout.addStretch()
        controls_layout.addWidget(self.export_csv_button)
        controls_layout.addWidget(self.export_excel_button)
        controls_layout.addWidget(self.help_button)

        # Results table
        self.table_view = QTableView()
        self.table_view.setSortingEnabled(True)
        self.table_view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        main_layout.addLayout(controls_layout)
        main_layout.addWidget(self.table_view)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # --- Connections ---
        self.scan_button.clicked.connect(self.start_scan)
        self.help_button.clicked.connect(self.show_about_dialog)
        self.table_view.doubleClicked.connect(self.open_chart_for_selection)
        self.export_csv_button.clicked.connect(self.export_to_csv)
        self.export_excel_button.clicked.connect(self.export_to_excel)

    def show_about_dialog(self):
        """Shows the 'About' dialog with application info and credits."""
        about_text = f"""
        <b>{config.APP_NAME}</b>
        <p>Version 1.0</p>
        <p>This application scans global stock markets to identify potential long-rebound candidates based on technical analysis criteria.</p>
        <p>This application was developed by Lukas Morcinek.</p>
        """
        QMessageBox.about(self, f"Über {config.APP_NAME}", about_text)

    def start_scan(self):
        """Sets up and starts the analysis worker thread."""
        self.scan_button.setEnabled(False)
        self.status_bar.showMessage("Scan wird gestartet...")

        self.thread = QThread()
        self.worker = AnalysisWorker()
        self.worker.moveToThread(self.thread)

        # Connect signals and slots
        self.thread.started.connect(self.worker.run)
        self.worker.signals.finished.connect(self.thread.quit)
        self.worker.signals.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.worker.signals.result.connect(self.display_results)
        self.worker.signals.progress.connect(self.update_status)
        self.worker.signals.error.connect(self.scan_error)

        self.thread.start()

    def scan_error(self, error_info):
        """Shows a dialog box when an error occurs during the scan."""
        exctype, value, tb = error_info
        self.status_bar.showMessage(f"Fehler beim Scan: {value}")
        self.scan_button.setEnabled(True) # Re-enable button on error
        QMessageBox.critical(
            self,
            "Scan-Fehler",
            f"Ein unerwarteter Fehler ist aufgetreten:\n\n{value}\n\nTraceback:\n{tb}"
        )

    def update_status(self, message):
        self.status_bar.showMessage(message)

    def display_results(self, results):
        """Receives the results from the worker and displays them in the table."""
        self.status_bar.showMessage(f"Scan abgeschlossen. {len(results)} Kandidaten gefunden.")
        self.scan_button.setEnabled(True)

        if not results:
            self.results_df = pd.DataFrame()
            QMessageBox.information(self, "Scan Complete", "No potential candidates found.")
        else:
            self.results_df = pd.DataFrame(results)
            # Use a proxy model for sorting
            model = PandasModel(self.results_df)
            proxy_model = QSortFilterProxyModel()
            proxy_model.setSourceModel(model)
            self.table_view.setModel(proxy_model)
            self.table_view.resizeColumnsToContents()

            # Automatically sort by the 'Score' column, descending
            try:
                score_col_index = self.results_df.columns.get_loc("Score")
                self.table_view.sortByColumn(score_col_index, Qt.SortOrder.DescendingOrder)
            except KeyError:
                logging.warning("Could not find 'Score' column for auto-sorting.")

        # Enable export buttons if there are results
        has_results = not self.results_df.empty
        self.export_csv_button.setEnabled(has_results)
        self.export_excel_button.setEnabled(has_results)

    def open_chart_for_selection(self, index):
        """Opens a new chart window for the selected stock."""
        proxy_model = self.table_view.model()
        source_index = proxy_model.mapToSource(index)

        # Assuming 'Ticker' is the first column
        ticker_col_index = self.results_df.columns.get_loc("Ticker")
        ticker = proxy_model.sourceModel()._data.iloc[source_index.row(), ticker_col_index]

        chart_window = ChartWindow(ticker)
        self.chart_windows.append(chart_window) # Keep a reference
        chart_window.show()

    def export_to_csv(self):
        if self.results_df.empty:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV", config.CSV_EXPORT_FILENAME, "CSV Files (*.csv)")
        if path:
            try:
                self.results_df.to_csv(path, index=False)
                self.status_bar.showMessage(f"Daten erfolgreich nach {path} exportiert.", 5000)
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Failed to export to CSV: {e}")

    def export_to_excel(self):
        if self.results_df.empty:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save XLSX", config.XLSX_EXPORT_FILENAME, "Excel Files (*.xlsx)")
        if path:
            try:
                self.results_df.to_excel(path, index=False)
                self.status_bar.showMessage(f"Daten erfolgreich nach {path} exportiert.", 5000)
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Failed to export to Excel: {e}")
