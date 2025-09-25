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
        self.numeric_columns = ['Rebound Score', 'Tech. Score', 'Fund. Score', 'Price', 'Floor Score', 'P/E', 'EPS Growth', 'Volume Ratio', '%K', '%D']
    def rowCount(self, parent=None): return self._data.shape[0]
    def columnCount(self, parent=None): return self._data.shape[1]
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid(): return None
        row, col = index.row(), index.column()
        column_name = self._data.columns[col]
        value = self._data.iloc[row, col]
        if role == Qt.ItemDataRole.DisplayRole:
            if pd.isna(value): return "N/A"
            if isinstance(value, float):
                if column_name in ['EPS Growth']: return f"{value:.2%}"
                if column_name in ['Price', '52w High', 'P/E', 'Volume Ratio', '%K', '%D']: return f"{value:.2f}"
            return str(value)
        if role == Qt.ItemDataRole.EditRole: return value
        if role == Qt.ItemDataRole.TextAlignmentRole and column_name in self.numeric_columns: return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.BackgroundRole and "Rebound Score" in self._data.columns:
            score = self._data.iloc[row]["Rebound Score"]
            if score > 80: return QColor("#d4edda")
            elif score > 60: return QColor("#fff3cd")
        return None
    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal: return str(self._data.columns[section])
        return None
    def get_dataframe(self): return self._data

class CustomSortProxyModel(QSortFilterProxyModel):
    def lessThan(self, left, right):
        col = self.sortColumn()
        source_model = self.sourceModel()
        if col >= len(source_model.get_dataframe().columns): return super().lessThan(left, right)
        column_name = source_model.get_dataframe().columns[col]
        numeric_sort_columns = ['Rebound Score', 'Tech. Score', 'Fund. Score', 'Price', 'Floor Score', 'P/E', 'EPS Growth', '52w High', 'Volume Ratio', '%K', '%D']
        if column_name in numeric_sort_columns:
            left_data, right_data = source_model.data(left, Qt.ItemDataRole.EditRole), source_model.data(right, Qt.ItemDataRole.EditRole)
            try:
                if isinstance(left_data, str): left_data = left_data.replace('$', '').replace('%', '')
                if isinstance(right_data, str): right_data = right_data.replace('$', '').replace('%', '')
                return float(left_data) < float(right_data)
            except (ValueError, TypeError): return str(left_data) < str(right_data)
        return super().lessThan(left, right)

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
        layout = QVBoxLayout(self)
        title_label = QLabel(f'<span style="font-size: 20px;">{icon_char}</span>&nbsp;&nbsp;<b style="font-size: 16px;">{title}</b>')
        layout.addWidget(title_label)
        desc_label = QLabel(description)
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)
        sub_strategies_layout = QVBoxLayout()
        for strategy in sub_strategies:
            btn = QPushButton(strategy['name'])
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, s_id=strategy['id']: self.strategySelected.emit(s_id))
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
        general_group = QGroupBox("General Filters")
        general_layout = QFormLayout()
        self.min_market_cap = QDoubleSpinBox()
        self.min_market_cap.setDecimals(0)
        self.min_market_cap.setRange(0, 1e12)
        self.min_market_cap.setSingleStep(1e8)
        self.min_market_cap.setValue(settings.get('min_market_cap'))
        general_layout.addRow("Min. Market Cap:", self.min_market_cap)
        self.min_avg_volume = QSpinBox()
        self.min_avg_volume.setRange(0, 100_000_000)
        self.min_avg_volume.setSingleStep(50_000)
        self.min_avg_volume.setValue(settings.get('min_avg_volume_30d'))
        general_layout.addRow("Min. Avg. Volume (30d):", self.min_avg_volume)
        general_group.setLayout(general_layout)
        layout.addWidget(general_group)
        self.clear_cache_button = QPushButton("Clear All Cached Data")
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
        reply = QMessageBox.question(self, "Confirm Cache Deletion", f"Are you sure you want to delete all cached data from {config.CACHE_DIR}?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                shutil.rmtree(config.CACHE_DIR)
                config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
                if self.parent() and hasattr(self.parent(), 'status_bar'): self.parent().status_bar.showMessage("Cache successfully cleared.", 5000)
            except Exception as e: QMessageBox.critical(self, "Cache Error", f"Failed to clear cache: {e}")
    def accept(self):
        settings.set('min_market_cap', self.min_market_cap.value())
        settings.set('min_avg_volume_30d', self.min_avg_volume.value())
        super().accept()

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
        self.setup_ui()
        self.connect_signals()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        top_level_layout = QVBoxLayout(central_widget)
        self.create_toolbar(top_level_layout)
        self.create_main_content_area(top_level_layout)
        self.create_status_bar()
        self.toast = ToastNotification(self)

    def create_toolbar(self, parent_layout):
        toolbar_layout = QHBoxLayout()
        self.run_scan_button = QPushButton(" Run Scan")
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
        parent_layout.addLayout(toolbar_layout)

    def create_main_content_area(self, parent_layout):
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
        parent_layout.addWidget(main_splitter)

    def create_status_bar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.progress_bar = QProgressBar()
        self.status_bar.addPermanentWidget(self.progress_bar)
        self.progress_bar.hide()

    def connect_signals(self):
        self.run_scan_button.clicked.connect(self.start_scan)
        self.settings_button.clicked.connect(self.open_settings_dialog)
        #... other connections

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
        if hasattr(self, 'worker') and self.worker:
            telemetry = self.worker.runner.telemetry
            self.scan_progress_label.setText(f"Processed: {telemetry['tickers_processed']} | Skipped: {telemetry['tickers_skipped']['total']}")
        else:
            self.scan_progress_label.setText(message)

    def display_results(self, results: list, telemetry: dict):
        self.last_telemetry = telemetry
        self.run_scan_button.setEnabled(True)
        # ... (rest of display_results logic)
        base_cols = ["Ticker", "Name", "Price", "Rebound Score", "Tech. Score", "Fund. Score"]
        scenario_cols = []
        if self.activeScan == 'garp_trend': scenario_cols = ["P/E", "EPS Growth"]
        elif self.activeScan == 'volume_breakout': scenario_cols = ["52w High", "Volume Ratio"]
        elif self.activeScan == 'stochastic_oversold': scenario_cols = ["%K", "%D"]
        # ... (rest of column logic)

        display_results_list = []
        for r in results:
            res_dict = {
                "Ticker": r.ticker, "Name": r.fundamentals.get('name', 'N/A'),
                "Price": r.technicals.get('price', 0), "Rebound Score": r.rebound_score,
                "Tech. Score": r.technical_score, "Fund. Score": r.fundamental_score,
                "P/E": r.fundamentals.get('trailingPE'), "EPS Growth": r.fundamentals.get('earningsGrowth'),
                "52w High": r.technicals.get('52w_high'), "Volume Ratio": r.technicals.get('volume_ratio'),
                "%K": r.technicals.get('stoch_k'), "%D": r.technicals.get('stoch_d'),
            }
            display_results_list.append(res_dict)
        self.results_df = pd.DataFrame(display_results_list)
        final_cols = [col for col in base_cols + scenario_cols if col in self.results_df.columns]
        self.results_df = self.results_df[final_cols]
        model = PandasModel(self.results_df, results)
        proxy_model = CustomSortProxyModel()
        proxy_model.setSourceModel(model)
        self.table_view.setModel(proxy_model)


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

    def on_strategy_selected(self, strategy_id):
        self.activeScan = strategy_id
        # ... (Implementation remains the same)
        pass