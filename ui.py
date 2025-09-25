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
    QStackedWidget, QFrame, QScrollArea, QMenu, QButtonGroup, QDialog,
    QFormLayout, QDialogButtonBox, QSpinBox, QDoubleSpinBox, QGroupBox
)
from PyQt6.QtCore import (
    QObject, QThread, pyqtSignal, QAbstractTableModel, Qt, QSortFilterProxyModel,
    QRegularExpression, QTimer, QRect
)
from PyQt6.QtGui import QColor, QIcon
import mplfinance as mpf
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
import asyncio
from typing import Dict, Any

import config
import data_loader
from ticker_manager import TickerManagerDialog
from data_structures import ReboundCandidate
from rebound_scenarios import ScenarioRunner, calculate_rsi, calculate_sma, calculate_macd
from scoring import DEFAULT_REBOUND_SCORE_WEIGHTS
from settings_manager import settings

# --- Worker Threads ---

class WorkerSignals(QObject):
    finished = pyqtSignal()
    error = pyqtSignal(tuple)
    result = pyqtSignal(list, dict)
    progress = pyqtSignal(str)
    progress_percent = pyqtSignal(int)

class AnalysisWorker(QObject):
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
    def cancel(self): self._is_cancelled = True
    def run(self):
        try:
            results = asyncio.run(self.runner.run_scan(self.selected_scenario, ticker=self.ticker))
            self.signals.result.emit(results, self.runner.telemetry)
        except Exception as e:
            import traceback
            self.signals.error.emit((type(e), e, traceback.format_exc()))
        finally:
            self.signals.finished.emit()

class TickerDetailWorker(QObject):
    finished = pyqtSignal()
    result = pyqtSignal(dict)
    error = pyqtSignal(str)
    def __init__(self, ticker: str):
        super().__init__()
        self.ticker = ticker
    def run(self):
        try:
            from fundamentals import FundamentalDataHandler
            fund_handler = FundamentalDataHandler()
            info = asyncio.run(fund_handler.get_full_ticker_info(self.ticker))
            if info: self.result.emit(info)
        except Exception as e:
            logging.error(f"Failed to fetch ticker details for {self.ticker}: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            self.finished.emit()

# --- UI Component Classes ---

class PandasModel(QAbstractTableModel):
    def __init__(self, data=pd.DataFrame(), candidates_data=[], parent=None):
        super().__init__(parent)
        self._data = data
        self.candidates_data = candidates_data
        self.numeric_columns = ['Rebound Score', 'Tech. Score', 'Fund. Score', 'Price', 'Floor Score', 'P/E', 'EPS Growth']
    def rowCount(self, parent=None): return self._data.shape[0]
    def columnCount(self, parent=None): return self._data.shape[1]
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid(): return None
        row, col = index.row(), index.column()
        column_name = self._data.columns[col]
        if role == Qt.ItemDataRole.DisplayRole:
            value = self._data.iloc[row, col]
            if isinstance(value, float):
                if column_name in ['EPS Growth']: return f"{value:.2%}"
                return f"{value:.2f}"
            return str(value)
        if role == Qt.ItemDataRole.EditRole: return self._data.iloc[row, col]
        if role == Qt.ItemDataRole.TextAlignmentRole and column_name in self.numeric_columns: return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.BackgroundRole and "Rebound Score" in self._data.columns:
            score = self._data.iloc[row]["Rebound Score"]
            if score > 80: return QColor("#d4edda")
            elif score > 60: return QColor("#fff3cd")
            elif score == 0: return QColor("#f8d7da")
        if role == Qt.ItemDataRole.ToolTipRole and column_name == "Rebound Score":
            # Tooltip logic...
            return "Composite Score"
        return None
    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal: return str(self._data.columns[section])
        return None
    def get_dataframe(self): return self._data

class CustomSortProxyModel(QSortFilterProxyModel):
    # Implementation...
    pass

class ChartWidget(QWidget):
    # Implementation...
    pass

class ChartWindow(QWidget):
    # Implementation...
    pass

class ToastNotification(QFrame):
    retry = pyqtSignal()
    def __init__(self, parent=None):
        super().__init__(parent)
        # ... (Implementation remains)

class ScanCategoryCard(QFrame):
    strategySelected = pyqtSignal(str)
    def __init__(self, title, description, icon_char, sub_strategies, parent=None):
        super().__init__(parent)
        self.sub_strategy_buttons = {}
        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)
        self.setObjectName("ScanCategoryCard")
        layout = QVBoxLayout(self)
        title_label = QLabel(f'<span style="font-size: 20px;">{icon_char}</span>&nbsp;&nbsp;<b style="font-size: 16px;">{title}</b>')
        layout.addWidget(title_label)
        # ... (rest of implementation)
        for strategy in sub_strategies:
            btn = QPushButton(strategy['name'])
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, s_id=strategy['id']: self.strategySelected.emit(s_id))
            self.button_group.addButton(btn)
            layout.addWidget(btn)
    def uncheck_all(self):
        checked_button = self.button_group.checkedButton()
        if checked_button:
            self.button_group.setExclusive(False)
            checked_button.setChecked(False)
            self.button_group.setExclusive(True)

class AdvancedSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Advanced Scan Settings")
        layout = QVBoxLayout(self)
        # ... (rest of implementation)
        self.telemetry_label = QLabel("No scan has been run yet.")
        layout.addWidget(self.telemetry_label)

# --- Main Application Window ---

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(config.APP_NAME)
        self.setGeometry(100, 100, 1200, 800)
        icon = QIcon.fromTheme("com.rectifex.GlobalReboundScreener")
        if not icon.isNull(): self.setWindowIcon(icon)

        self.chart_windows, self.scan_cards, self.last_telemetry = [], [], {}
        self.results_df, self.activeScan = pd.DataFrame(), None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        top_level_layout = QVBoxLayout(central_widget)

        # Toolbar
        toolbar_layout = QHBoxLayout()
        self.run_scan_button = QPushButton(" Run Scan")
        self.stop_scan_button = QPushButton("Stop")
        self.stop_scan_button.hide()
        toolbar_layout.addWidget(self.run_scan_button)
        toolbar_layout.addWidget(self.stop_scan_button)
        self.single_ticker_input = QLineEdit()
        self.filter_results_input = QLineEdit()
        toolbar_layout.addWidget(QLabel("Single Ticker:"))
        toolbar_layout.addWidget(self.single_ticker_input)
        toolbar_layout.addWidget(QLabel("Filter Results:"))
        toolbar_layout.addWidget(self.filter_results_input)
        toolbar_layout.addStretch()
        self.manage_watchlists_button = QPushButton("Manage Watchlists")
        self.export_button = QPushButton("Export")
        self.help_button = QPushButton("Help")
        self.settings_button = QPushButton("Settings")
        toolbar_layout.addWidget(self.manage_watchlists_button)
        toolbar_layout.addWidget(self.export_button)
        toolbar_layout.addWidget(self.help_button)
        toolbar_layout.addWidget(self.settings_button)
        top_level_layout.addLayout(toolbar_layout)

        # Main Content
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.selection_pane = QWidget()
        selection_pane_layout = QVBoxLayout(self.selection_pane)
        scroll_area = QScrollArea()
        selection_pane_layout.addWidget(scroll_area)
        card_container = QWidget()
        self.card_layout = QVBoxLayout(card_container)
        scroll_area.setWidget(card_container)
        self.populate_strategy_cards()
        main_splitter.addWidget(self.selection_pane)

        self.main_content_pane = QWidget()
        # ... and so on for the rest of the UI setup ...

        top_level_layout.addWidget(main_splitter)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.progress_bar = QProgressBar()
        self.status_bar.addPermanentWidget(self.progress_bar)
        self.progress_bar.hide()

        self.toast = ToastNotification(self)

        self.connect_signals()

    def connect_signals(self):
        self.run_scan_button.clicked.connect(self.start_scan)
        self.settings_button.clicked.connect(self.open_settings_dialog)
        #... other connections

    def populate_strategy_cards(self):
        # ... (Implementation remains the same)
        pass

    def start_scan(self):
        # ... (Implementation remains the same)
        pass

    def display_results(self, results: list, telemetry: dict):
        # ... (Implementation remains the same)
        pass

    def open_settings_dialog(self):
        # ... (Implementation remains the same)
        pass

    def update_status_text(self, message: str):
        # ... (Implementation remains the same)
        pass

    def on_strategy_selected(self, strategy_id):
        # ... (Implementation remains the same)
        pass