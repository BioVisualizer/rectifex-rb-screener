import time
import pandas as pd
import yfinance as yf
import requests
from io import StringIO
from pathlib import Path

def scrape_and_save_tickers(index_name: str, url: str, columns_to_try: list, output_filename: str):
    """
    Generic function to scrape tickers from a table on a webpage, verify them, and save to CSV.
    """
    print(f"--- Starting {index_name} Ticker Scraping and Verification ---")

    headers = {'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0'}

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        # Use StringIO to avoid pandas FutureWarning
        tables = pd.read_html(StringIO(response.text))
    except Exception as e:
        print(f"ERROR: Could not fetch or parse the webpage for {index_name}. Error: {e}")
        return

    if not tables:
        print(f"ERROR: No tables found on the page for {index_name}.")
        return

    df = tables[0]

    ticker_col = next((col for col in columns_to_try if col in df.columns), None)
    if not ticker_col:
        print(f"ERROR: Could not find any of the expected ticker columns {columns_to_try} in the table for {index_name}.")
        print(f"Available columns: {df.columns.tolist()}")
        return

    raw_tickers = df[ticker_col].dropna().unique().tolist()
    print(f"Found {len(raw_tickers)} unique tickers for {index_name}. Now verifying...")

    verified_tickers = []
    for i, ticker in enumerate(raw_tickers):
        # yfinance often uses '-' instead of '.' (e.g., BRK-B)
        cleaned_ticker = str(ticker).replace('.', '-')

        print(f"Verifying [{i+1}/{len(raw_tickers)}]: {cleaned_ticker}...", end='', flush=True)
        try:
            stock = yf.Ticker(cleaned_ticker)
            # A quick history check is more reliable than .info
            hist = stock.history(period="5d")
            if not hist.empty:
                print(" -> VALID")
                verified_tickers.append(cleaned_ticker)
            else:
                print(" -> INVALID (no history)")
        except Exception:
            print(" -> INVALID (exception)")
        time.sleep(0.1) # Be polite to the server

    print(f"\nVerification complete. Found {len(verified_tickers)} valid tickers for {index_name}.")

    # Ensure the data directory exists
    output_path = Path(output_filename)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Save to CSV
    output_df = pd.DataFrame(sorted(verified_tickers), columns=["Ticker"])
    output_df.to_csv(output_path, index=False)
    print(f"Saved verified tickers to {output_path}")


def scrape_stoxx600():
    # Slickcharts is a reliable source for index components
    scrape_and_save_tickers(
        index_name="STOXX 600",
        url="https://www.slickcharts.com/stoxx600",
        columns_to_try=['Symbol'],
        output_filename="data/stoxx600_tickers.csv"
    )

def scrape_dax():
    scrape_and_save_tickers(
        index_name="DAX",
        url="https://www.slickcharts.com/dax",
        columns_to_try=['Symbol'],
        output_filename="data/dax_tickers.csv"
    )

def scrape_nasdaq100():
    scrape_and_save_tickers(
        index_name="NASDAQ 100",
        url="https://www.slickcharts.com/nasdaq100",
        columns_to_try=['Symbol'],
        output_filename="data/nasdaq100_tickers.csv"
    )

def scrape_sp500():
     scrape_and_save_tickers(
        index_name="S&P 500",
        url="https://www.slickcharts.com/sp500",
        columns_to_try=['Symbol'],
        output_filename="data/sp500_tickers.csv"
    )

if __name__ == '__main__':
    # Add other scrapers here as needed
    scrape_dax()
    print("\n" + "="*40 + "\n")
    scrape_stoxx600()
    print("\n" + "="*40 + "\n")
    scrape_nasdaq100()
    print("\n" + "="*40 + "\n")
    scrape_sp500()
    print("\n" + "="*40 + "\n")
    print("All scraping tasks complete.")