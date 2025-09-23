# ui.py
# Contains all PyQt6 classes for the user interface.

import sys
import logging
import pandas as pd
import shutil
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableView, QStatusBar, QFileDialog, QMessageBox, QHeaderView,
    QProgressBar, QComboBox, QLabel, QLineEdit, QSplitter, QTreeWidget, QTreeWidgetItem,
    QStackedWidget, QButtonGroup, QDialog, QDialogButtonBox, QCheckBox
)
from datetime import datetime
from pathlib import Path
from PyQt6.QtCore import (
    QObject, QThread, pyqtSignal, QAbstractTableModel, Qt, QSortFilterProxyModel, QRegularExpression
)
from PyQt6.QtGui import QColor, QIcon, QPixmap, QPainter, QPainterPath
from PyQt6.QtWidgets import QFrame, QScrollArea, QMenu

# For charting
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import mplfinance as mpf

# App-specific imports
import asyncio
import config
import data_loader
from settings_manager import settings
from ticker_manager import TickerManagerDialog
from data_structures import ReboundCandidate
from rebound_scenarios import ScenarioRunner, calculate_rsi, calculate_sma, calculate_macd
from scoring import DEFAULT_REBOUND_SCORE_WEIGHTS
from fundamentals import FundamentalDataHandler

# --- Worker Threads & Signals ---

class MainScanSignals(QObject):
    """Defines the signals available from the main analysis worker thread."""
    finished = pyqtSignal()
    error = pyqtSignal(tuple)
    result = pyqtSignal(list)
    progress = pyqtSignal(str)
    progress_percent = pyqtSignal(int)

class TickerDetailSignals(QObject):
    """Defines signals for the ticker detail fetching worker."""
    finished = pyqtSignal()
    error = pyqtSignal(tuple)
    result = pyqtSignal(object) # Emits the 'info' dict

class AnalysisWorker(QObject):
    """Worker thread for running the stock analysis to prevent GUI freezing."""
    def __init__(self, selected_scenario: str, ticker: str = None):
        super().__init__()
        self.signals = MainScanSignals()
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
            results = asyncio.run(runner.run_scan(self.selected_scenario, ticker=self.ticker))
            self.signals.result.emit(results)
        except Exception as e:
            import traceback
            self.signals.error.emit((type(e), e, traceback.format_exc()))
        finally:
            self.signals.finished.emit()

class TickerDetailWorker(QObject):
    """A dedicated worker to fetch full ticker details in the background."""
    def __init__(self, ticker: str):
        super().__init__()
        self.ticker = ticker
        self.signals = TickerDetailSignals()

    def run(self):
        """Executes the async fetch operation."""
        try:
            fund_handler = FundamentalDataHandler()
            info = asyncio.run(fund_handler.get_full_ticker_info(self.ticker))
            self.signals.result.emit(info)
        except Exception as e:
            import traceback
            self.signals.error.emit((type(e), e, traceback.format_exc()))
        finally:
            self.signals.finished.emit()


# --- Pandas DataFrame Model for QTableView ---

class PandasModel(QAbstractTableModel):
    """A model to interface a pandas DataFrame with a QTableView."""
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
        if role == Qt.ItemDataRole.EditRole:
            return self._data.iloc[row, col]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.BackgroundRole:
            if "Rebound Score" in self._data.columns:
                score = self._data.iloc[row]["Rebound Score"]
                if score > 80: return QColor("#d4edda")
                elif score > 60: return QColor("#fff3cd")
                elif score == 0: return QColor("#f8d7da")
        if role == Qt.ItemDataRole.ToolTipRole and column_name == "Rebound Score":
            base_tooltip = self.tr("Composite score (0-100) combining technical, fundamental, and market factors.")
            if row < len(self.candidates_data):
                candidate = self.candidates_data[row]
                tech_w = DEFAULT_REBOUND_SCORE_WEIGHTS['tech'] * 100
                fund_w = DEFAULT_REBOUND_SCORE_WEIGHTS['fund'] * 100
                mark_w = DEFAULT_REBOUND_SCORE_WEIGHTS['market'] * 100
                breakdown_lines = [
                    f"\n\n--- {self.tr('Score Composition')} ---",
                    f"- {self.tr('Technical Score')}: {candidate.technical_score} ({self.tr('Weight')}: {tech_w:.0f}%)",
                    f"- {self.tr('Fundamental Score')}: {candidate.fundamental_score} ({self.tr('Weight')}: {fund_w:.0f}%)",
                    f"- {self.tr('Market Context')}: {candidate.market_context_score} ({self.tr('Weight')}: {mark_w:.0f}%)"
                ]
                if candidate.score_breakdown:
                    breakdown_lines.append(f"\n--- {self.tr('Details')} ---")
                    for key, value in candidate.score_breakdown.items():
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

# ... (CustomSortProxyModel remains unchanged) ...
class CustomSortProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter_column_indices = []
    def set_filter_columns_by_name(self, column_names: list, source_model: QAbstractTableModel):
        if not source_model or not hasattr(source_model, 'get_dataframe'):
            self._filter_column_indices = []
            return
        df_columns = source_model.get_dataframe().columns.tolist()
        self._filter_column_indices = [df_columns.index(name) for name in column_names if name in df_columns]
        self.invalidateFilter()
    def filterAcceptsRow(self, source_row, source_parent):
        regex = self.filterRegularExpression()
        if not regex.pattern(): return True
        columns_to_check = self._filter_column_indices
        if not columns_to_check: columns_to_check = range(self.sourceModel().columnCount())
        for col_index in columns_to_check:
            source_index = self.sourceModel().index(source_row, col_index, source_parent)
            cell_data = self.sourceModel().data(source_index, Qt.ItemDataRole.DisplayRole)
            if cell_data and regex.match(str(cell_data)).hasMatch(): return True
        return False
    def lessThan(self, left, right):
        col = self.sortColumn()
        source_model = self.sourceModel()
        if col >= len(source_model.get_dataframe().columns): return super().lessThan(left, right)
        column_name = source_model.get_dataframe().columns[col]
        numeric_sort_columns = ['Rebound Score', 'Tech. Score', 'Fund. Score', 'Price']
        if column_name in numeric_sort_columns:
            left_data = source_model.data(left, Qt.ItemDataRole.EditRole)
            right_data = source_model.data(right, Qt.ItemDataRole.EditRole)
            try:
                if isinstance(left_data, str): left_data = left_data.replace('$', '')
                if isinstance(right_data, str): right_data = right_data.replace('$', '')
                return float(left_data) < float(right_data)
            except (ValueError, TypeError):
                return str(left_data) < str(right_data)
        return super().lessThan(left, right)

# --- Charting Window ---
class ChartWindow(QWidget):
    """A separate window for displaying a detailed, scalable stock chart."""
    def __init__(self, candidate: ReboundCandidate):
        super().__init__()
        self.candidate = candidate
        self.setWindowTitle(f"Chart for {self.candidate.ticker} - {config.APP_NAME}")
        self.setGeometry(150, 150, 950, 750)
        layout = QVBoxLayout(self)
        self.setLayout(layout)
        self.figure = Figure(figsize=(10, 8), facecolor=self.palette().window().color().name())
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas)
        self.plot_stock_data(candidate)

    def plot_stock_data(self, candidate: ReboundCandidate):
        """Generates and displays a detailed stock chart on the embedded canvas."""
        try:
            self.figure.clear()
            if candidate.history_df is None or candidate.history_df.empty:
                ax = self.figure.add_subplot(111)
                ax.text(0.5, 0.5, f"Historical data for {candidate.ticker} not found.", horizontalalignment='center', verticalalignment='center')
                self.canvas.draw()
                return
            history_df = candidate.history_df.copy()
            if not history_df.empty and config.CHART_HISTORY_MONTHS > 0:
                cutoff_date = history_df.index.max() - pd.DateOffset(months=config.CHART_HISTORY_MONTHS)
                # Explicitly create a copy to avoid the SettingWithCopyWarning
                plot_data = history_df.loc[cutoff_date:].copy()
            else:
                plot_data = history_df.copy()
            if 'SMA50' not in plot_data.columns: plot_data['SMA50'] = calculate_sma(plot_data['Close'], 50)
            if 'SMA200' not in plot_data.columns: plot_data['SMA200'] = calculate_sma(plot_data['Close'], 200)
            if 'RSI' not in plot_data.columns: plot_data['RSI'] = calculate_rsi(plot_data['Close'])
            macd_line, signal_line, macd_hist = calculate_macd(plot_data['Close'])
            highest_high, lowest_low = plot_data['High'].max(), plot_data['Low'].min()
            fib_levels, hlines_fib = {}, {}
            if pd.notna(highest_high) and pd.notna(lowest_low) and highest_high > lowest_low:
                price_range = highest_high - lowest_low
                fib_levels = {
                    23.6: highest_high - (price_range * 0.236), 38.2: highest_high - (price_range * 0.382),
                    50.0: highest_high - (price_range * 0.5), 61.8: highest_high - (price_range * 0.618),
                    78.6: highest_high - (price_range * 0.786), 0.0: highest_high, 100.0: lowest_low,
                }
                hlines_fib = dict(hlines=list(fib_levels.values()), colors=['#999999']*len(fib_levels), linestyle='-.', linewidths=0.6, alpha=0.9)
            gs = self.figure.add_gridspec(4, 1, height_ratios=[6, 1, 2, 2], hspace=0.05)
            price_ax = self.figure.add_subplot(gs[0, 0])
            volume_ax = self.figure.add_subplot(gs[1, 0], sharex=price_ax)
            rsi_ax = self.figure.add_subplot(gs[2, 0], sharex=price_ax)
            macd_ax = self.figure.add_subplot(gs[3, 0], sharex=price_ax)
            for ax in [price_ax, volume_ax, rsi_ax]: ax.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)
            add_plots = [
                mpf.make_addplot(plot_data['RSI'], ax=rsi_ax), mpf.make_addplot(macd_line, ax=macd_ax),
                mpf.make_addplot(signal_line, ax=macd_ax, color='red'), mpf.make_addplot(macd_hist, type='bar', ax=macd_ax, color='grey', alpha=0.5)
            ]
            mpf.plot(plot_data, type='candle', ax=price_ax, volume=volume_ax, mav=(50, 200), addplot=add_plots, style='yahoo', xrotation=20, hlines=hlines_fib if hlines_fib else None)
            if fib_levels:
                transform = price_ax.get_yaxis_transform()
                sorted_levels = sorted(fib_levels.items(), key=lambda item: item[1], reverse=True)
                last_y = float('inf')
                for level_pct, level_price in sorted_levels:
                    if abs(level_price - last_y) / (highest_high - lowest_low) < 0.035: continue
                    last_y = level_price
                    price_ax.text(0.98, level_price, f'{level_pct:.1f}%', transform=transform, va='center', ha='right', fontsize=7, color='#555555', bbox=dict(facecolor=self.palette().window().color().name(), alpha=0.7, edgecolor='none', pad=1))
                    price_ax.text(0.02, level_price, f'${level_price:.2f}', transform=transform, va='center', ha='left', fontsize=7, color='#555555', bbox=dict(facecolor=self.palette().window().color().name(), alpha=0.7, edgecolor='none', pad=1))
            self.figure.suptitle(f'{candidate.ticker} - {candidate.scenario}', y=0.98)
            price_ax.legend(loc='upper left'); rsi_ax.set_ylabel('RSI'); macd_ax.set_ylabel('MACD')
            rsi_ax.axhline(70, color='red', linestyle='--', linewidth=0.7, alpha=0.8); rsi_ax.axhline(30, color='green', linestyle='--', linewidth=0.7, alpha=0.8)
            rsi_ax.set_ylim(0, 100)
            eps_growth = candidate.fundamentals.get('earningsGrowth'); eps_growth_str = f"EPS Growth: {eps_growth * 100:.2f}%" if eps_growth is not None else ""
            rev_growth = candidate.fundamentals.get('revenueGrowth'); rev_growth_str = f"Rev Growth: {rev_growth * 100:.2f}%" if rev_growth is not None else ""
            price = candidate.technicals.get('price'); price_str = f"Price: ${price:.2f}" if price is not None else ""
            rsi_val = plot_data['RSI'].iloc[-1]; rsi_str = f"RSI: {rsi_val:.1f}" if pd.notna(rsi_val) else ""
            info_parts = [f"Scenario: {candidate.scenario}", price_str, rsi_str, eps_growth_str, rev_growth_str]
            info_text = " | ".join(filter(None, info_parts))
            self.figure.text(0.5, 0.94, info_text, ha='center', va='center', fontsize=8, color='#333333', bbox=dict(boxstyle='round,pad=0.4', fc='yellow', alpha=0.5, ec='none'))
            self.figure.subplots_adjust(top=0.90, bottom=0.08, left=0.08, right=0.95, hspace=0.15)
            self.canvas.draw()
        except Exception as e:
            logging.error(f"Failed to plot chart for {candidate.ticker}: {e}", exc_info=True)
            self.figure.clear()
            ax = self.figure.add_subplot(111); ax.text(0.5, 0.5, f"Could not generate chart for {candidate.ticker}:\n\n{e}", horizontalalignment='center', verticalalignment='center', wrap=True)
            self.canvas.draw(); QMessageBox.critical(self, "Chart Error", f"Could not generate chart for {candidate.ticker}:\n\n{e}")

# ... (ToastNotification and ScanCategoryCard remain unchanged) ...
class ToastNotification(QFrame):
    retry = pyqtSignal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ToastNotification"); self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("QFrame#ToastNotification { background-color: #333333; border: 1px solid #444444; border-radius: 8px; } QLabel { color: white; }")
        layout = QVBoxLayout(self); title_label = QLabel("<b>Scan Failed</b>"); title_label.setStyleSheet("font-size: 14px;")
        self.message_label = QLabel("An error occurred. Please try again."); button_layout = QHBoxLayout()
        retry_button = QPushButton("Retry"); details_button = QPushButton("Show Details")
        button_layout.addStretch(); button_layout.addWidget(retry_button); button_layout.addWidget(details_button)
        layout.addWidget(title_label); layout.addWidget(self.message_label); layout.addLayout(button_layout)
        retry_button.clicked.connect(self.retry.emit); retry_button.clicked.connect(self.hide_toast); details_button.clicked.connect(self.show_details)
        self.detailed_error = ""; self.hide()
    def show_toast(self, detailed_error: str):
        self.detailed_error = detailed_error; self.parent().resizeEvent(None); self.show(); QTimer.singleShot(10000, self.hide_toast)
    def hide_toast(self): self.hide()
    def show_details(self):
        msg_box = QMessageBox(self); msg_box.setIcon(QMessageBox.Icon.Critical); msg_box.setWindowTitle("Error Details")
        msg_box.setText("An unexpected error occurred during the scan."); msg_box.setDetailedText(self.detailed_error)
        msg_box.setStyleSheet("QLabel{color: black;} QTextEdit{color: black; background-color: white;}"); msg_box.exec()
class ScanCategoryCard(QFrame):
    strategySelected = pyqtSignal(str)
    def __init__(self, title, description, icon_char, sub_strategies, parent=None):
        super().__init__(parent); self.sub_strategy_buttons = {}
        self.setObjectName("ScanCategoryCard"); self.setFrameShape(QFrame.Shape.StyledPanel); self.setFrameShadow(QFrame.Shadow.Raised)
        self.setStyleSheet("QFrame#ScanCategoryCard { background-color: #ffffff; border: 1px solid #e0e0e0; border-radius: 8px; margin: 5px; }")
        layout = QVBoxLayout(self); layout.setContentsMargins(15, 15, 15, 15); layout.setSpacing(10)
        title_label = QLabel(f'<span style="font-size: 20px;">{icon_char}</span>&nbsp;&nbsp;<b style="font-size: 16px;">{title}</b>'); title_label.setTextFormat(Qt.TextFormat.RichText)
        desc_label = QLabel(description); desc_label.setWordWrap(True); desc_label.setStyleSheet("color: #666;")
        sub_strategies_layout = QVBoxLayout(); sub_strategies_layout.setContentsMargins(10, 5, 0, 5); sub_strategies_layout.setSpacing(8)
        for strategy in sub_strategies:
            btn = QPushButton(strategy['name']); btn.setObjectName("SubStrategyButton"); btn.setCheckable(True)
            btn.setStyleSheet("QPushButton#SubStrategyButton { text-align: left; padding: 8px; border: 1px solid transparent; border-radius: 4px; background-color: transparent; } QPushButton#SubStrategyButton:hover { background-color: #f0f8ff; } QPushButton#SubStrategyButton:checked { background-color: #e6f3ff; border-color: #a0c4e8; font-weight: bold; }")
            btn.clicked.connect(lambda checked, s_id=strategy['id']: self.strategySelected.emit(s_id)); self.sub_strategy_buttons[strategy['id']] = btn; sub_strategies_layout.addWidget(btn)
        layout.addWidget(title_label); layout.addWidget(desc_label); layout.addLayout(sub_strategies_layout)
    def uncheck_all(self):
        for btn in self.sub_strategy_buttons.values(): btn.setChecked(False)

# --- Settings Dialog ---
class SettingsDialog(QDialog):
    """A dialog for configuring user settings."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Settings"))
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        lang_layout = QHBoxLayout()
        lang_label = QLabel(self.tr("UI Language:"))
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("English", "en"); self.lang_combo.addItem("German (Deutsch)", "de"); self.lang_combo.addItem("Spanish (Español)", "es")
        lang_layout.addWidget(lang_label); lang_layout.addWidget(self.lang_combo); layout.addLayout(lang_layout)
        current_lang = settings.get("language", "en"); index = self.lang_combo.findData(current_lang)
        if index != -1: self.lang_combo.setCurrentIndex(index)
        layout.addSpacing(20)
        clear_cache_button = QPushButton(self.tr("Clear Scanner Cache"))
        clear_cache_button.clicked.connect(self.clear_cache); layout.addWidget(clear_cache_button)
        layout.addStretch()
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept); button_box.rejected.connect(self.reject); layout.addWidget(button_box)
    def accept(self):
        selected_lang_code = self.lang_combo.currentData(); settings.set("language", selected_lang_code)
        QMessageBox.information(self, self.tr("Restart Required"), self.tr("The language change will take effect the next time you start the application."))
        super().accept()
    def clear_cache(self):
        reply = QMessageBox.question(self, self.tr("Confirm Cache Deletion"), self.tr("Are you sure you want to delete all cached data from {cache_dir}?").format(cache_dir=config.CACHE_DIR), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                shutil.rmtree(config.CACHE_DIR); config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
                QMessageBox.information(self, self.tr("Success"), self.tr("Cache successfully cleared."))
            except Exception as e:
                QMessageBox.critical(self, self.tr("Cache Error"), self.tr("Failed to clear cache: {error}").format(error=e))

class MainWindow(QMainWindow):
    """The main window of the application."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle(config.APP_NAME)
        self.setGeometry(100, 100, 1200, 800)
        icon = QIcon.fromTheme("com.rectifex.GlobalReboundScreener")
        if not icon.isNull(): self.setWindowIcon(icon)
        self.chart_windows = []; self.results_df = pd.DataFrame(); self.activeScan = None
        self.scan_cards = []; self.strategy_button_group = QButtonGroup(self); self.strategy_button_group.setExclusive(True)
        central_widget = QWidget(); self.setCentralWidget(central_widget); top_level_layout = QVBoxLayout(central_widget)
        toolbar_layout = QHBoxLayout(); toolbar_layout.setSpacing(10)
        self.run_scan_button = QPushButton(self.tr(" Run Scan")); self.run_scan_button.setObjectName("btn-run-scan")
        self.run_scan_button.setIcon(QIcon.fromTheme("system-search"))
        self.run_scan_button.setStyleSheet("QPushButton#btn-run-scan { background-color: #3498db; color: white; font-weight: bold; border: none; padding: 8px 16px; border-radius: 4px; } QPushButton#btn-run-scan:hover { background-color: #2980b9; } QPushButton#btn-run-scan:disabled { background-color: #a0a0a0; }")
        self.stop_scan_button = QPushButton(self.tr("Stop")); self.stop_scan_button.hide(); self.stop_scan_button.setEnabled(False)
        toolbar_layout.addWidget(self.run_scan_button); toolbar_layout.addWidget(self.stop_scan_button)
        inputs_layout = QHBoxLayout(); inputs_layout.setContentsMargins(20, 0, 20, 0)
        inputs_layout.addWidget(QLabel(self.tr("Single Ticker (Optional):")))
        self.single_ticker_input = QLineEdit(); self.single_ticker_input.setPlaceholderText(self.tr("e.g., AAPL, GOOGL")); self.single_ticker_input.setClearButtonEnabled(True)
        inputs_layout.addWidget(self.single_ticker_input)
        inputs_layout.addWidget(QLabel(self.tr("Filter Results:"))); self.filter_results_input = QLineEdit()
        self.filter_results_input.setPlaceholderText(self.tr("Filter by name or ticker...")); self.filter_results_input.setClearButtonEnabled(True)
        inputs_layout.addWidget(self.filter_results_input); toolbar_layout.addLayout(inputs_layout)
        toolbar_layout.addStretch()
        self.manage_watchlists_button = QPushButton(self.tr("Manage Watchlists")); self.export_button = QPushButton(self.tr("Export"))
        self.help_button = QPushButton(self.tr("Help")); self.settings_button = QPushButton(self.tr("Settings"))
        export_menu = QMenu(self); self.export_csv_action = export_menu.addAction(self.tr("Export CSV")); self.export_xlsx_action = export_menu.addAction(self.tr("Export XLSX"))
        self.export_button.setMenu(export_menu); self.export_csv_action.setEnabled(False); self.export_xlsx_action.setEnabled(False)
        toolbar_layout.addWidget(self.manage_watchlists_button); toolbar_layout.addWidget(self.export_button)
        toolbar_layout.addWidget(self.help_button); toolbar_layout.addWidget(self.settings_button)
        top_level_layout.addLayout(toolbar_layout)
        main_splitter = QSplitter(Qt.Orientation.Horizontal); self.selection_pane = QWidget(); self.selection_pane.setObjectName("selection-pane")
        self.selection_pane.setStyleSheet("background-color: #f8f9fa;"); selection_pane_layout = QVBoxLayout(self.selection_pane)
        selection_pane_layout.setContentsMargins(0,0,0,0); scroll_area = QScrollArea(); scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); scroll_area.setStyleSheet("QScrollArea { border: none; }")
        selection_pane_layout.addWidget(scroll_area); card_container = QWidget(); self.card_layout = QVBoxLayout(card_container)
        self.card_layout.setSpacing(5); self.card_layout.addStretch(); scroll_area.setWidget(card_container)
        self.populate_strategy_cards(); main_splitter.addWidget(self.selection_pane)
        self.main_content_pane = QWidget(); self.main_content_pane.setObjectName("main-content-pane"); main_content_layout = QVBoxLayout(self.main_content_pane)
        main_content_layout.setContentsMargins(0, 0, 0, 0); self.main_content_pane.setStyleSheet("background-color: white; border: none;")
        self.stacked_widget = QStackedWidget(); main_content_layout.addWidget(self.stacked_widget)
        onboarding_widget = QWidget(); onboarding_layout = QVBoxLayout(onboarding_widget); onboarding_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        onboarding_widget.setStyleSheet("background-color: #ffffff; border: none;")
        headline = QLabel(self.tr("Welcome! Let's Find Your Next Investment.")); headline.setStyleSheet("font-size: 24px; font-weight: bold; color: #333;")
        instructions = QLabel(self.tr("""<ol><li>Select a scan strategy on the left.</li><li>Click 'Run Scan' to begin.</li><li>Analyze your results right here.</li></ol>"""))
        instructions.setStyleSheet("font-size: 14px; color: #555;"); self.demo_scan_button = QPushButton(self.tr("Run Demo Scan")); self.demo_scan_button.setFixedWidth(150)
        onboarding_layout.addWidget(headline); onboarding_layout.addWidget(instructions); onboarding_layout.addWidget(self.demo_scan_button, alignment=Qt.AlignmentFlag.AlignCenter)
        results_widget = QWidget(); results_layout = QVBoxLayout(results_widget); results_header_layout = QHBoxLayout()
        self.results_summary_label = QLabel(self.tr("Found 0 results.")); self.scan_progress_label = QLabel("")
        results_header_layout.addWidget(self.results_summary_label); results_header_layout.addStretch(); results_header_layout.addWidget(self.scan_progress_label)
        self.table_view = QTableView(); self.table_view.setSortingEnabled(True); results_layout.addLayout(results_header_layout); results_layout.addWidget(self.table_view)
        no_results_widget = QWidget(); no_results_layout = QVBoxLayout(no_results_widget); no_results_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_results_label = QLabel(self.tr("No stocks matched your scan.")); self.no_results_label.setStyleSheet("font-size: 16px; color: #777;")
        no_results_layout.addWidget(self.no_results_label); self.stacked_widget.addWidget(onboarding_widget); self.stacked_widget.addWidget(results_widget)
        self.stacked_widget.addWidget(no_results_widget); main_splitter.addWidget(self.main_content_pane)
        self.context_pane = QWidget(); self.context_pane.setObjectName("context-pane"); self.context_pane.setStyleSheet("background-color: #f8f9fa; border-left: 1px solid #e0e0e0;")
        context_layout = QVBoxLayout(self.context_pane); context_layout.setContentsMargins(0, 0, 0, 0); self.context_stack = QStackedWidget()
        context_layout.addWidget(self.context_stack); self.scan_explanation_widget = QWidget(); self.scan_explanation_layout = QVBoxLayout(self.scan_explanation_widget)
        self.scan_explanation_layout.setContentsMargins(15, 15, 15, 15); self.scan_explanation_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.context_stack.addWidget(self.scan_explanation_widget); self.ticker_detail_widget = QWidget()
        self.ticker_detail_layout = QVBoxLayout(self.ticker_detail_widget); self.ticker_detail_layout.setContentsMargins(15, 15, 15, 15)
        self.ticker_detail_layout.setAlignment(Qt.AlignmentFlag.AlignTop); self.context_stack.addWidget(self.ticker_detail_widget)
        self.context_stack.setCurrentIndex(0); default_label = QLabel(self.tr("Select a scan from the left to learn more about it."))
        default_label.setWordWrap(True); self.scan_explanation_layout.addWidget(default_label); main_splitter.addWidget(self.context_pane)
        total_width = self.width(); main_splitter.setSizes([int(total_width * 0.25), int(total_width * 0.50), int(total_width * 0.25)])
        top_level_layout.addWidget(main_splitter); self.status_bar = QStatusBar(); self.setStatusBar(self.status_bar)
        self.status_bar.setStyleSheet("QStatusBar::item { border: 0px; }"); self.data_source_label = QLabel(self.tr("Data Source: yfinance"))
        self.last_updated_label = QLabel(self.tr("Last Updated: N/A")); self.status_bar.addWidget(self.data_source_label)
        self.status_bar.addPermanentWidget(self.last_updated_label); self.progress_bar = QProgressBar(); self.status_bar.addPermanentWidget(self.progress_bar)
        self.progress_bar.hide(); self.toast = ToastNotification(self); self.run_scan_button.clicked.connect(self.start_scan)
        self.stop_scan_button.clicked.connect(self.stop_scan); self.settings_button.clicked.connect(self.open_settings_dialog)
        self.manage_watchlists_button.clicked.connect(self.open_ticker_manager); self.help_button.clicked.connect(self.show_about_dialog)
        self.table_view.clicked.connect(self.on_ticker_selected); self.table_view.doubleClicked.connect(self.open_chart_for_selection)
        self.demo_scan_button.clicked.connect(self.run_demo_scan); self.toast.retry.connect(self.start_scan)
        self.export_csv_action.triggered.connect(self.export_to_csv); self.export_xlsx_action.triggered.connect(self.export_to_excel)
        self.filter_results_input.textChanged.connect(self.filter_table); self.single_ticker_input.textChanged.connect(self.validate_single_ticker_input)
    def validate_single_ticker_input(self, text: str):
        if not text: self.run_scan_button.setEnabled(True); return
        valid_ticker_regex = QRegularExpression("^[A-Z0-9.-]+$"); match = valid_ticker_regex.match(text.upper())
        if match.hasMatch(): self.run_scan_button.setEnabled(True)
        else: self.run_scan_button.setEnabled(False)
    def on_ticker_selected(self, index):
        if not index.isValid(): return
        source_index = self.table_view.model().mapToSource(index)
        if source_index.row() >= len(self.all_candidates_data): return
        candidate = self.all_candidates_data[source_index.row()]
        self.context_stack.setCurrentIndex(1)
        while self.ticker_detail_layout.count():
            item = self.ticker_detail_layout.takeAt(0)
            if item.widget(): item.widget().setParent(None)
        loading_label = QLabel(self.tr("Loading Ticker Details...")); loading_label.setObjectName("loadingLabel")
        self.ticker_detail_layout.addWidget(loading_label)
        self.detail_thread = QThread(); self.detail_worker = TickerDetailWorker(candidate.ticker)
        self.detail_worker.moveToThread(self.detail_thread)
        self.detail_thread.started.connect(self.detail_worker.run)
        self.detail_worker.signals.result.connect(self._populate_context_pane)
        self.detail_worker.signals.error.connect(self.scan_error)
        self.detail_worker.signals.finished.connect(self.detail_thread.quit)
        self.detail_worker.signals.finished.connect(self.detail_worker.deleteLater)
        self.detail_thread.finished.connect(self.detail_thread.deleteLater)
        self.detail_thread.start()
    def _populate_context_pane(self, info: dict):
        loading_label = self.ticker_detail_layout.findChild(QLabel, "loadingLabel")
        if loading_label: loading_label.setParent(None)
        if not info:
            error_label = QLabel(self.tr("Could not load details.")); self.ticker_detail_layout.addWidget(error_label); return
        title = QLabel(f"<b>{info.get('shortName', info.get('symbol'))}</b> ({info.get('symbol')})")
        title.setStyleSheet("font-size: 16px; margin-bottom: 5px;")
        def format_market_cap(mc):
            if mc is None: return "N/A"
            if mc > 1_000_000_000_000: return f"${mc/1_000_000_000_000:.2f}T"
            if mc > 1_000_000_000: return f"${mc/1_000_000_000:.2f}B"
            if mc > 1_000_000: return f"${mc/1_000_000:.2f}M"
            return f"${mc}"
        pe_ratio = f"{info.get('trailingPE'):.2f}" if info.get('trailingPE') else "N/A"
        div_yield = f"{info.get('dividendYield')*100:.2f}%" if info.get('dividendYield') else "N/A"
        metrics_text = (f"<b>{self.tr('Market Cap')}:</b> {format_market_cap(info.get('marketCap'))}<br>"
                        f"<b>{self.tr('P/E Ratio')}:</b> {pe_ratio}<br>"
                        f"<b>{self.tr('Dividend Yield')}:</b> {div_yield}")
        metrics_label = QLabel(metrics_text)
        bio_title = QLabel(f"<b>{self.tr('Company Bio')}</b>"); bio_title.setStyleSheet("color: #005a9e; margin-top: 10px;")
        bio_text = QLabel(info.get('longBusinessSummary', self.tr('No company summary available.'))); bio_text.setWordWrap(True)
        self.ticker_detail_layout.addWidget(title); self.ticker_detail_layout.addWidget(metrics_label)
        self.ticker_detail_layout.addWidget(bio_title); self.ticker_detail_layout.addWidget(bio_text); self.ticker_detail_layout.addStretch()
    def on_strategy_selected(self, strategy_id):
        self.activeScan = strategy_id; self.stacked_widget.setCurrentIndex(0)
        self.results_df = pd.DataFrame(); self.table_view.setModel(None)
        sender_button = self.sender()
        if sender_button:
            parent_card = sender_button.parent()
            for card in self.scan_cards:
                if card is not parent_card: card.uncheck_all()
        self.update_context_pane_for_scan(strategy_id)
    def update_context_pane_for_scan(self, strategy_id):
        while self.scan_explanation_layout.count():
            item = self.scan_explanation_layout.takeAt(0)
            if item.widget(): item.widget().setParent(None)
        scenarios = ScenarioRunner.load_scenarios_config()
        scenario_data = next((s for s in scenarios if s['id'] == strategy_id), None)
        if scenario_data:
            title = QLabel(f"<b>{scenario_data.get('name', 'N/A')}</b>"); title.setStyleSheet("font-size: 16px; margin-bottom: 5px;")
            what_it_is_title = QLabel(f"<b>{self.tr('What It Is')}</b>"); what_it_is_title.setStyleSheet("color: #005a9e; margin-top: 10px;")
            what_it_is_text = QLabel(scenario_data.get('what_it_is', self.tr('No details available.'))); what_it_is_text.setWordWrap(True)
            best_for_title = QLabel(f"<b>{self.tr('Best For')}</b>"); best_for_title.setStyleSheet("color: #005a9e; margin-top: 10px;")
            best_for_text = QLabel(scenario_data.get('best_for', self.tr('No details available.'))); best_for_text.setWordWrap(True)
            self.scan_explanation_layout.addWidget(title); self.scan_explanation_layout.addWidget(what_it_is_title)
            self.scan_explanation_layout.addWidget(what_it_is_text); self.scan_explanation_layout.addWidget(best_for_title)
            self.scan_explanation_layout.addWidget(best_for_text); self.scan_explanation_layout.addStretch()
        self.context_stack.setCurrentIndex(0)
    def populate_strategy_cards(self):
        scenarios = ScenarioRunner.load_scenarios_config()
        icons = {"Trend & Momentum": "[Trend]", "Contrarian & Reversion": "[Reversion]", "Value & Fundamental": "[Value]", "Volatility": "[Volatility]"}
        groups = {}
        for scenario in scenarios:
            group_name = scenario.get('group', 'Uncategorized')
            if group_name not in groups: groups[group_name] = []
            groups[group_name].append(scenario)
        for group_name, scenarios_in_group in groups.items():
            card = ScanCategoryCard(group_name, scenarios_in_group[0].get('group_description', ''), icons.get(group_name, "[?]"), scenarios_in_group)
            card.strategySelected.connect(self.on_strategy_selected)
            for btn in card.sub_strategy_buttons.values(): self.strategy_button_group.addButton(btn)
            self.card_layout.insertWidget(self.card_layout.count() - 1, card); self.scan_cards.append(card)
    def filter_table(self, text: str):
        proxy_model = self.table_view.model()
        if isinstance(proxy_model, CustomSortProxyModel):
            regex = QRegularExpression(text, QRegularExpression.PatternOption.CaseInsensitiveOption)
            proxy_model.setFilterRegularExpression(regex)
    def stop_scan(self):
        if hasattr(self, 'worker') and self.worker:
            self.status_bar.showMessage(self.tr("Stopping scan...")); self.stop_scan_button.setEnabled(False); self.worker.cancel()
    def open_ticker_manager(self):
        dialog = TickerManagerDialog(self); dialog.exec()
    def open_settings_dialog(self):
        dialog = SettingsDialog(self); dialog.exec()
    def show_about_dialog(self):
        about_text = f"""<b>{config.APP_NAME} v2.0</b>
<p>{self.tr("This application scans global stock markets to identify potential investment candidates based on a variety of technical and fundamental scenarios.")}</p>
<p>{self.tr("Developed by Lukas Morcinek.")}</p><hr><p><b>{self.tr("Disclaimer:")}</b></p>
<p>{self.tr("This tool is for informational purposes only and does not constitute financial advice. Always conduct your own thorough research before making any investment decisions.")}</p>"""
        QMessageBox.about(self, self.tr("About") + f" {config.APP_NAME}", about_text)
    def run_demo_scan(self):
        demo_strategy_id = 'golden_cross'
        for card in self.scan_cards:
            if demo_strategy_id in card.sub_strategy_buttons:
                card.sub_strategy_buttons[demo_strategy_id].setChecked(True); self.on_strategy_selected(demo_strategy_id); self.start_scan(); return
        QMessageBox.warning(self, self.tr("Demo Error"), self.tr("Could not find the 'Golden Cross' demo scan."))
    def start_scan(self):
        ticker = self.single_ticker_input.text().strip().upper() or None
        if not self.activeScan:
            QMessageBox.warning(self, self.tr("Selection Error"), self.tr("Please select a scan strategy from the left pane.")); return
        self.run_scan_button.setEnabled(False); self.single_ticker_input.setEnabled(False)
        self.stop_scan_button.show(); self.stop_scan_button.setEnabled(True); self.settings_button.setEnabled(False)
        self.stacked_widget.setCurrentIndex(1); self.scan_progress_label.setText(self.tr("Starting scan..."))
        self.status_bar.showMessage(self.tr("Starting scan...")); self.progress_bar.setValue(0); self.progress_bar.show()
        selected_scenario_id = self.activeScan
        self.thread = QThread(); self.worker = AnalysisWorker(selected_scenario=selected_scenario_id, ticker=ticker)
        self.worker.moveToThread(self.thread); self.thread.started.connect(self.worker.run)
        self.worker.signals.finished.connect(self.thread.quit); self.worker.signals.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater); self.worker.signals.result.connect(self.display_results)
        self.worker.signals.progress.connect(self.update_status_text); self.worker.signals.progress_percent.connect(self.update_progress_bar)
        self.worker.signals.error.connect(self.scan_error)
        self.thread.start()
    def scan_error(self, error_info):
        exctype, value, tb = error_info
        error_message = f"{self.tr('An unexpected error occurred:')}\n\n{value}\n\nTraceback:\n{tb}"
        self.status_bar.showMessage(f"{self.tr('Scan Error:')} {value}"); self.run_scan_button.setEnabled(True)
        self.single_ticker_input.setEnabled(True); self.stop_scan_button.hide(); self.stop_scan_button.setEnabled(False)
        self.settings_button.setEnabled(True); self.progress_bar.hide(); self.stacked_widget.setCurrentIndex(0)
        self.toast.show_toast(error_message)
    def resizeEvent(self, event):
        if hasattr(self, 'toast') and self.toast.isVisible():
            toast_width = 350; toast_height = 120
            x = self.width() - toast_width - 10; y = self.height() - toast_height - self.status_bar.height() - 10
            self.toast.setGeometry(x, y, toast_width, toast_height)
        super().resizeEvent(event)
    def update_status_text(self, message):
        self.status_bar.showMessage(message); self.scan_progress_label.setText(message)
    def update_progress_bar(self, percent): self.progress_bar.setValue(percent)
    def display_results(self, results):
        self.scan_progress_label.setText("")
        self.last_updated_label.setText(f"{self.tr('Last Updated:')} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        if self.worker and self.worker._is_cancelled:
             self.status_bar.showMessage(self.tr("Scan stopped by user. {n} candidates found before stopping.").format(n=len(results)))
        else:
            self.status_bar.showMessage(self.tr("Scan complete. Found {n} candidates.").format(n=len(results)))
        self.run_scan_button.setEnabled(True); self.single_ticker_input.setEnabled(True)
        self.stop_scan_button.hide(); self.stop_scan_button.setEnabled(False); self.settings_button.setEnabled(True)
        self.progress_bar.hide()
        if not results:
            self.results_df = pd.DataFrame(); self.table_view.setModel(None)
            scenarios = ScenarioRunner.load_scenarios_config()
            scenario_name = next((s['name'] for s in scenarios if s['id'] == self.activeScan), self.tr("the selected scan"))
            self.no_results_label.setText(self.tr('No stocks matched your scan for "{scenario_name}".\nYou can try another strategy.').format(scenario_name=scenario_name))
            self.stacked_widget.setCurrentIndex(2); return
        self.stacked_widget.setCurrentIndex(1)
        display_results_list = []
        scenarios = ScenarioRunner.load_scenarios_config()
        scenario_name = next((s['name'] for s in scenarios if s['id'] == self.activeScan), "")
        for r in results:
            roe_val = r.fundamentals.get('roe'); roe_str = f"{roe_val * 100:.2f}%" if roe_val is not None else "N/A"
            res_dict = {
                self.tr("Ticker"): r.ticker, self.tr("Name"): r.fundamentals.get('name', 'N/A'),
                self.tr("Price"): f"${r.technicals.get('price', 0):.2f}", self.tr("Change %"): "0.00%",
                self.tr("Rebound Score"): r.rebound_score, self.tr("Tech. Score"): r.technical_score,
                self.tr("Fund. Score"): r.fundamental_score, self.tr("ROE"): roe_str, self.tr("Sparkline"): ""
            }
            display_results_list.append(res_dict)
        self.results_df = pd.DataFrame(display_results_list)
        self.results_summary_label.setText(self.tr("Found {n} results for \"{scenario_name}\" scan.").format(n=len(results), scenario_name=scenario_name))
        self.all_candidates_data = results
        model = PandasModel(self.results_df, self.all_candidates_data); proxy_model = CustomSortProxyModel()
        proxy_model.setSourceModel(model); proxy_model.set_filter_columns_by_name([self.tr('Ticker'), self.tr('Name')], model)
        self.table_view.setModel(proxy_model); self.table_view.resizeColumnsToContents()
        self.table_view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table_view.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        try:
            score_col_index = self.results_df.columns.get_loc(self.tr("Rebound Score"))
            self.table_view.sortByColumn(score_col_index, Qt.SortOrder.DescendingOrder)
        except KeyError: pass
        has_results = not self.results_df.empty
        self.export_csv_action.setEnabled(has_results); self.export_xlsx_action.setEnabled(has_results)
    def open_chart_for_selection(self, index):
        proxy_model = self.table_view.model()
        source_index = proxy_model.mapToSource(index)
        candidate = self.all_candidates_data[source_index.row()]
        chart_window = ChartWindow(candidate)
        self.chart_windows.append(chart_window); chart_window.show()
    def export_to_csv(self):
        if self.results_df.empty: return
        path, _ = QFileDialog.getSaveFileName(self, self.tr("Save CSV"), config.CSV_EXPORT_FILENAME, self.tr("CSV Files (*.csv)"))
        if path:
            try:
                self.results_df.to_csv(path, index=False)
                self.status_bar.showMessage(self.tr("Data successfully exported to {path}").format(path=path), 5000)
            except Exception as e:
                QMessageBox.critical(self, self.tr("Export Error"), self.tr("Failed to export to CSV: {error}").format(error=e))
    def export_to_excel(self):
        if self.results_df.empty: return
        path, _ = QFileDialog.getSaveFileName(self, self.tr("Save XLSX"), config.XLSX_EXPORT_FILENAME, self.tr("Excel Files (*.xlsx)"))
        if path:
            try:
                self.results_df.to_excel(path, index=False)
                self.status_bar.showMessage(self.tr("Data successfully exported to {path}").format(path=path), 5000)
            except Exception as e:
                QMessageBox.critical(self, self.tr("Export Error"), self.tr("Failed to export to Excel: {error}").format(error=e))
