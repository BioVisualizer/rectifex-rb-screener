# ticker_manager.py
# Contains the TickerManagerDialog for editing ticker lists.

import os
import pandas as pd
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QListWidget,
    QPushButton, QLineEdit, QMessageBox, QDialogButtonBox, QLabel
)
from PyQt6.QtCore import Qt
import logging

import config
import data_loader

class TickerManagerDialog(QDialog):
    """A dialog for users to add, remove, and manage ticker lists."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ticker List Manager")
        self.setGeometry(150, 150, 500, 600)

        self.current_index_name = None
        self.user_lists_path = config.USER_TICKER_DIR

        # Ensure user ticker directory exists
        try:
            self.user_lists_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Directory Error", f"Could not create directory:\n{self.user_lists_path}\n\nError: {e}")
            # Allow dialog to open but functionality will be limited

        self._setup_ui()
        self._connect_signals()

        # Load initial data
        self.index_combo.setCurrentIndex(0)
        self.load_tickers_for_selected_index()

    def _setup_ui(self):
        """Initializes the widgets and layout of the dialog."""
        main_layout = QVBoxLayout(self)

        # --- Index Selection ---
        selection_layout = QHBoxLayout()
        selection_layout.addWidget(QLabel("Select Index to Edit:"))
        self.index_combo = QComboBox()
        self.index_combo.addItems(config.INDICES.keys())
        selection_layout.addWidget(self.index_combo)
        main_layout.addLayout(selection_layout)

        # --- Ticker List ---
        self.ticker_list_widget = QListWidget()
        main_layout.addWidget(self.ticker_list_widget)

        # --- Add/Remove Controls ---
        add_remove_layout = QHBoxLayout()
        self.ticker_input = QLineEdit()
        self.ticker_input.setPlaceholderText("Enter new ticker (e.g., AAPL)")
        self.add_button = QPushButton("Add")
        self.remove_button = QPushButton("Remove Selected")
        add_remove_layout.addWidget(self.ticker_input)
        add_remove_layout.addWidget(self.add_button)
        add_remove_layout.addWidget(self.remove_button)
        main_layout.addLayout(add_remove_layout)

        # --- Save/Reset/Close Buttons ---
        self.status_label = QLabel("Changes are not saved until you click 'Save'.")
        self.status_label.setStyleSheet("font-style: italic; color: grey;")
        main_layout.addWidget(self.status_label)

        button_layout = QHBoxLayout()
        self.reset_button = QPushButton("Reset to Default")
        self.save_button = QPushButton("Save and Close")
        self.cancel_button = QPushButton("Cancel")

        button_layout.addWidget(self.reset_button)
        button_layout.addStretch()
        button_layout.addWidget(self.cancel_button)
        button_layout.addWidget(self.save_button)
        main_layout.addLayout(button_layout)

    def _connect_signals(self):
        """Connects widget signals to their respective slots."""
        self.index_combo.currentIndexChanged.connect(self.load_tickers_for_selected_index)
        self.add_button.clicked.connect(self.add_ticker)
        self.ticker_input.returnPressed.connect(self.add_ticker)
        self.remove_button.clicked.connect(self.remove_ticker)
        self.save_button.clicked.connect(self.save_and_close)
        self.reset_button.clicked.connect(self.reset_to_default)
        self.cancel_button.clicked.connect(self.reject)

    def _get_user_list_path(self, index_name: str) -> config.Path:
        """Constructs the path for a user-defined ticker list file."""
        sanitized_name = index_name.replace(" ", "_").lower()
        return self.user_lists_path / f"{sanitized_name}_user.csv"

    def load_tickers_for_selected_index(self):
        """Loads the tickers for the currently selected index into the list widget."""
        self.current_index_name = self.index_combo.currentText()

        self.ticker_list_widget.clear()

        # This will be modified in a later step to prioritize user lists.
        # For now, it will load the default list.
        # The new implementation of get_ticker_list will handle the logic.
        try:
            tickers = data_loader.get_ticker_list(self.current_index_name)
            self.ticker_list_widget.addItems(tickers)
            self.status_label.setText(f"Loaded {len(tickers)} default tickers for {self.current_index_name}.")
        except Exception as e:
            QMessageBox.warning(self, "Load Error", f"Failed to load tickers for {self.current_index_name}.\n\nError: {e}")

    def add_ticker(self):
        """Adds a new ticker from the input field to the list."""
        ticker = self.ticker_input.text().strip().upper()
        if not ticker:
            return

        # Check for duplicates
        if self.ticker_list_widget.findItems(ticker, Qt.MatchFlag.MatchExactly):
            QMessageBox.warning(self, "Duplicate", f"Ticker '{ticker}' is already in the list.")
            return

        self.ticker_list_widget.addItem(ticker)
        self.ticker_list_widget.sortItems()
        self.ticker_input.clear()
        self.status_label.setText(f"'{ticker}' added. Remember to save your changes.")

    def remove_ticker(self):
        """Removes the selected ticker from the list."""
        selected_items = self.ticker_list_widget.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Selection Error", "Please select a ticker to remove.")
            return

        for item in selected_items:
            self.ticker_list_widget.takeItem(self.ticker_list_widget.row(item))
        self.status_label.setText("Ticker(s) removed. Remember to save your changes.")

    def save_list_to_file(self):
        """Saves the current list of tickers to a user-specific CSV file."""
        user_list_file = self._get_user_list_path(self.current_index_name)

        tickers = [self.ticker_list_widget.item(i).text() for i in range(self.ticker_list_widget.count())]

        try:
            df = pd.DataFrame(tickers, columns=["Ticker"])
            df.to_csv(user_list_file, index=False)
            logging.info(f"Successfully saved user-defined list to {user_list_file}")
            return True
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save ticker list to:\n{user_list_file}\n\nError: {e}")
            return False

    def save_and_close(self):
        """Saves the list and closes the dialog."""
        if self.save_list_to_file():
            QMessageBox.information(self, "Saved", f"Your custom ticker list for '{self.current_index_name}' has been saved.")
            self.accept()

    def reset_to_default(self):
        """Deletes the user's custom list file, reverting to the default list."""
        user_list_file = self._get_user_list_path(self.current_index_name)

        if not user_list_file.exists():
            QMessageBox.information(self, "No Custom List", "No custom list exists for this index. The default list is already active.")
            return

        reply = QMessageBox.question(self, "Confirm Reset",
                                     f"Are you sure you want to delete your custom list for '{self.current_index_name}'?\n\nThis will revert to the default ticker list the next time you open this dialog or run a scan.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            try:
                os.remove(user_list_file)
                QMessageBox.information(self, "Reset Complete", "The custom list has been deleted. The application will now use the default list.")
                # Reload the list to show the default tickers
                self.load_tickers_for_selected_index()
            except OSError as e:
                QMessageBox.critical(self, "Error", f"Failed to delete the custom list file.\n\nError: {e}")
