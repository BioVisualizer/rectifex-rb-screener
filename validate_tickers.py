import asyncio
import pandas as pd
from pathlib import Path
import logging
import sys
from typing import List

# Add the project root to the path to allow importing project modules
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

try:
    import data_loader
    import config
except ImportError as e:
    print(f"Error: Could not import project modules. Make sure this script is in the project root directory.")
    print(f"Details: {e}")
    sys.exit(1)

# --- Configuration ---
# Set the logging level for this script. Use DEBUG for verbose yfinance output.
LOG_LEVEL = logging.INFO
# Use a shorter period for validation to speed things up. 3 months is enough to confirm recent data.
VALIDATION_PERIOD = "3mo"
# Number of concurrent tickers to check.
CONCURRENT_REQUESTS = 10

# --- Script ---
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def validate_ticker(ticker: str, semaphore: asyncio.Semaphore) -> str | None:
    """
    Checks a single ticker using the robust fetch_history function.
    Returns the ticker name if it's valid, otherwise None.
    """
    async with semaphore:
        # We run the synchronous fetch_history function in an executor
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(
            None,
            data_loader.fetch_history,
            ticker,
            VALIDATION_PERIOD,
            1 # Only 1 retry to speed things up
        )
        if df is not None and not df.empty:
            logger.debug(f"Ticker {ticker} is VALID.")
            return ticker
        else:
            logger.debug(f"Ticker {ticker} is INVALID.")
            return None

async def process_csv_file(csv_path: Path, semaphore: asyncio.Semaphore):
    """
    Processes a single CSV file: reads tickers, validates them, and writes a new file.
    """
    logger.info(f"--- Processing {csv_path.name} ---")
    try:
        df = pd.read_csv(csv_path)
        # Find the ticker column, defaulting to the first column if 'Ticker' not found
        if 'Ticker' in df.columns:
            ticker_col = 'Ticker'
        elif df.columns.any():
            ticker_col = df.columns[0]
        else:
            logger.warning(f"Could not find any columns in {csv_path.name}. Skipping.")
            return

        tickers_to_validate = df[ticker_col].dropna().unique().tolist()
        total_tickers = len(tickers_to_validate)
        if total_tickers == 0:
            logger.warning(f"No tickers found in {csv_path.name}. Skipping.")
            return

        logger.info(f"Found {total_tickers} unique tickers to validate.")

        # Create and run validation tasks concurrently
        tasks = [validate_ticker(t, semaphore) for t in tickers_to_validate]

        valid_tickers = []
        processed_count = 0

        for future in asyncio.as_completed(tasks):
            result = await future
            if result:
                valid_tickers.append(result)
            processed_count += 1
            # Simple text-based progress bar
            progress = int((processed_count / total_tickers) * 50) # 50-char width
            sys.stdout.write(f"\rProgress: [{'=' * progress}{' ' * (50 - progress)}] {processed_count}/{total_tickers}")
            sys.stdout.flush()

        print() # Newline after progress bar finishes

        num_valid = len(valid_tickers)
        logger.info(f"Validation complete. Found {num_valid} valid tickers out of {total_tickers} ({(num_valid/total_tickers*100):.2f}%).")

        if valid_tickers:
            # Write the new cleaned CSV file
            new_df = pd.DataFrame(sorted(valid_tickers), columns=['Ticker'])
            new_filename = f"{csv_path.stem}_validated.csv"
            new_filepath = csv_path.with_name(new_filename)
            new_df.to_csv(new_filepath, index=False)
            logger.info(f"Successfully wrote valid tickers to: {new_filepath}")
        else:
            logger.warning(f"No valid tickers found for {csv_path.name}. No new file was created.")

    except Exception as e:
        logger.error(f"Failed to process file {csv_path.name}: {e}", exc_info=True)


async def main():
    """
    Main function to find all CSVs, validate their tickers, and write new cleaned files.
    """
    data_dir = config.BASE_DIR / 'data'
    # Exclude any files that have already been validated
    csv_files = [p for p in data_dir.glob('*.csv') if '_validated.csv' not in p.name and 'master_tickers.csv' not in p.name]

    if not csv_files:
        logger.warning(f"No CSV files to validate were found in {data_dir}. (Skipping '_validated.csv' and 'master_tickers.csv' files).")
        return

    logger.info(f"Found {len(csv_files)} CSV files to validate in {data_dir}.")

    semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)

    for csv_path in csv_files:
        await process_csv_file(csv_path, semaphore)
        logger.info("-" * 20)

if __name__ == "__main__":
    # Suppress the yfinance logger during validation to keep the output clean
    # and focused on this script's feedback.
    logging.getLogger('yfinance').setLevel(logging.CRITICAL)

    print("--- Ticker List Validator ---")
    print(f"This script will check all tickers in the CSV files inside your '{config.BASE_DIR / 'data'}' directory.")
    print("It will create new files with '_validated.csv' appended, containing only the tickers that still exist on Yahoo Finance.")
    print("-" * 30)

    try:
        asyncio.run(main())
        print("-" * 30)
        print("Validation process finished.")
    except KeyboardInterrupt:
        print("\nValidation process cancelled by user.")
    except Exception as e:
        logger.error(f"An unexpected error occurred during the validation process: {e}", exc_info=True)