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
    # ... (Implementation is correct and remains unchanged)
    pass

# --- UI Component Classes ---

class PandasModel(QAbstractTableModel):
    # ... (Implementation is correct and remains unchanged)
    pass

class CustomSortProxyModel(QSortFilterProxyModel):
    # ... (Implementation is correct and remains unchanged)
    pass

class ChartWidget(QWidget):
    # ... (Implementation is correct and remains unchanged)
    pass

class ChartWindow(QWidget):
    # ... (Implementation is correct and remains unchanged)
    pass

class ToastNotification(QFrame):
    # ... (Implementation is correct and remains unchanged)
    pass

class ScanCategoryCard(QFrame):
    strategySelected = pyqtSignal(str)
    def __init__(self, title, description, icon_char, sub_strategies, parent=None):
        super().__init__(parent)
        self.sub_strategy_buttons = {}
        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)
        self.setObjectName("ScanCategoryCard")
        # ... (Rest of implementation is correct and remains unchanged)
        layout = QVBoxLayout(self)
        title_label = QLabel(f'<span style="font-size: 20px;">{icon_char}</span>&nbsp;&nbsp;<b style="font-size: 16px;">{title}</b>')
        layout.addWidget(title_label)
        desc_label = QLabel(description)
        layout.addWidget(desc_label)
        sub_strategies_layout = QVBoxLayout()
        for strategy in sub_strategies:
            btn = QPushButton(strategy['name'])
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, s_id=strategy['id']: self.strategySelected.emit(s_id))
            self.sub_strategy_buttons[strategy['id']] = btn
            self.button_group.addButton(btn)
            sub_strategies_layout.addWidget(btn)
        layout.addLayout(sub_strategies_layout)

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
        # ... (Rest of implementation is correct and remains unchanged)
        self.telemetry_label = QLabel("No scan has been run yet.")
        layout.addWidget(self.telemetry_label)
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

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
        self.run_scan_button.setObjectName("btn-run-scan")
        self.stop_scan_button = QPushButton("Stop")
        self.stop_scan_button.hide()
        toolbar_layout.addWidget(self.run_scan_button)
        toolbar_layout.addWidget(self.stop_scan_button)

        self.single_ticker_input = QLineEdit()
        self.filter_results_input = QLineEdit()
        inputs_layout = QHBoxLayout()
        inputs_layout.addWidget(QLabel("Single Ticker (Optional):"))
        inputs_layout.addWidget(self.single_ticker_input)
        inputs_layout.addWidget(QLabel("Filter Results:"))
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
        main_content_layout = QVBoxLayout(self.main_content_pane)
        self.stacked_widget = QStackedWidget()
        main_content_layout.addWidget(self.stacked_widget)

        onboarding_widget = QWidget()
        results_widget = QWidget()
        no_results_widget = QWidget()

        # Setup for results view
        results_layout = QVBoxLayout(results_widget)
        results_header_layout = QHBoxLayout()
        self.results_summary_label = QLabel("Found 0 results.")
        self.scan_progress_label = QLabel("")
        results_header_layout.addWidget(self.results_summary_label)
        results_header_layout.addStretch()
        results_header_layout.addWidget(self.scan_progress_label)
        self.table_view = QTableView()
        results_layout.addLayout(results_header_layout)
        results_layout.addWidget(self.table_view)

        self.stacked_widget.addWidget(onboarding_widget)
        self.stacked_widget.addWidget(results_widget)
        self.stacked_widget.addWidget(no_results_widget)

        main_splitter.addWidget(self.main_content_pane)

        self.context_pane = QWidget()
        main_splitter.addWidget(self.context_pane)

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