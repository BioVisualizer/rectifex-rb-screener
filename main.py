# main.py
# Entry point for the Rectifex RB application.

import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTranslator, QLibraryInfo, QLocale
from ui import MainWindow
import config
from settings_manager import settings
import os

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

    # --- Internationalization (i18n) Setup ---
    # Get language from settings, default to English
    lang = settings.get("language", "en")

    # Load the translation for the application's own strings
    translator = QTranslator()
    # The path needs to be relative to where the app is running.
    # In the Flatpak bundle, this will be /app/i18n/
    # We use an absolute path check for robustness
    i18n_path = "/app/i18n"
    if not os.path.exists(i18n_path):
        i18n_path = "i18n" # Fallback for local development

    if translator.load(f"app_{lang}", i18n_path):
        app.installTranslator(translator)
    else:
        print(f"Warning: Could not load translation for language '{lang}' from path '{i18n_path}'.")

    # Load the base Qt translations for standard dialogs (e.g., "OK", "Cancel")
    qt_translator = QTranslator()
    # QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath) points to where Qt's own translations are
    qt_translation_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if qt_translator.load(QLocale.system(), "qtbase", "_", qt_translation_path):
        app.installTranslator(qt_translator)

    app.setApplicationName("RectifexRB")
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
