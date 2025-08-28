# main.py
# Entry point for the Rectifex RB application.

import sys
from PyQt6.QtWidgets import QApplication
from ui import MainWindow
import config

def main():
    """Main function to run the application."""
    # Ensure the cache directory exists before starting
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Error creating cache directory: {e}")
        # Decide if the app should exit or just warn
        # For now, we'll proceed, but data loading might fail.

    app = QApplication(sys.argv)
    app.setApplicationName("RectifexRB")
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
