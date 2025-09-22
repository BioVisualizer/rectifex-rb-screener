import json
import logging
from pathlib import Path
import config

# --- Constants ---
# Place user-specific config in the app's data directory
USER_CONFIG_FILE = config.APP_DATA_DIR / "user_config.json"

# --- Default Settings ---
# These are the settings that the app will fall back to if they are
# not present in the user_config.json file.
DEFAULT_SETTINGS = {
    "language": "en",
    "min_market_cap": 2_000_000_000, # Default to 2 Billion
    "min_avg_volume_30d": 500_000,    # Default to 500,000
    # Add other future user-configurable settings here
}

class SettingsManager:
    """
    Handles loading and saving user-configurable settings from a JSON file.
    It provides a simple get/set interface and ensures that default values
    are used if a setting is missing.
    """
    def __init__(self, file_path=USER_CONFIG_FILE):
        self.file_path = file_path
        self._settings = self._load()

    def _load(self) -> dict:
        """
        Loads settings from the JSON file. If the file doesn't exist or is
        invalid, it returns the default settings.
        """
        try:
            if self.file_path.exists():
                with open(self.file_path, 'r') as f:
                    user_settings = json.load(f)
                # Merge user settings with defaults to ensure all keys are present
                settings = DEFAULT_SETTINGS.copy()
                settings.update(user_settings)
                return settings
        except (json.JSONDecodeError, Exception) as e:
            logging.warning(f"Could not load user config file at {self.file_path}. "
                            f"Falling back to defaults. Error: {e}")

        # Return a copy of the defaults if loading fails
        return DEFAULT_SETTINGS.copy()

    def get(self, key: str, default: any = None) -> any:
        """
        Retrieves a setting value by its key.

        Args:
            key: The key of the setting to retrieve.
            default: A fallback value to return if the key is not found.
                     If not provided, the default from DEFAULT_SETTINGS is used.
        """
        if default is not None:
            return self._settings.get(key, default)
        return self._settings.get(key, DEFAULT_SETTINGS.get(key))

    def set(self, key: str, value: any):
        """
        Updates a setting value and saves the changes to the file.

        Args:
            key: The key of the setting to update.
            value: The new value for the setting.
        """
        self._settings[key] = value
        self.save()

    def save(self):
        """
        Saves the current settings dictionary to the JSON file.
        """
        try:
            # Ensure the parent directory exists
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.file_path, 'w') as f:
                json.dump(self._settings, f, indent=4)
        except Exception as e:
            logging.error(f"Failed to save user settings to {self.file_path}. Error: {e}")

# Create a global instance for easy access throughout the application
settings = SettingsManager()

# --- Example Usage ---
if __name__ == "__main__":
    print("--- Testing SettingsManager ---")

    # Use a temporary file for testing to not overwrite the user's real config
    test_file = Path("./test_user_config.json")
    if test_file.exists():
        test_file.unlink()

    test_settings = SettingsManager(file_path=test_file)

    # 1. Test getting a default value
    print(f"Initial theme (default): {test_settings.get('theme')}")
    assert test_settings.get('theme') == 'light'

    # 2. Test setting a new value
    print("Setting theme to 'dark'...")
    test_settings.set('theme', 'dark')
    print(f"New theme: {test_settings.get('theme')}")
    assert test_settings.get('theme') == 'dark'

    # 3. Test that the value was saved to the file
    print("Reloading settings from file...")
    reloaded_settings = SettingsManager(file_path=test_file)
    print(f"Reloaded theme: {reloaded_settings.get('theme')}")
    assert reloaded_settings.get('theme') == 'dark'

    # 4. Test setting another value
    print("Setting market cap to 5 Billion...")
    reloaded_settings.set('min_market_cap', 5_000_000_000)
    assert reloaded_settings.get('min_market_cap') == 5_000_000_000

    # 5. Test that both values are now in the file
    final_settings = SettingsManager(file_path=test_file)
    print(f"Final theme: {final_settings.get('theme')}")
    print(f"Final market cap: {final_settings.get('min_market_cap')}")
    assert final_settings.get('theme') == 'dark'
    assert final_settings.get('min_market_cap') == 5_000_000_000

    # Clean up the test file
    test_file.unlink()

    print("\n--- SettingsManager test complete ---")
