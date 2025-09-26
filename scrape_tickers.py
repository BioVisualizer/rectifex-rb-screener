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
        tables = pd.read_html(StringIO(response.text))
    except Exception as e:
        print(f"ERROR: Could not fetch or parse the webpage for {index_name}. Error: {e}")
        return

    if not tables:
        print(f"ERROR: No tables found on the page for {index_name}.")
        return

    # Find the correct table by checking for expected columns
    df = None
    for table in tables:
        if any(col in table.columns for col in columns_to_try):
            df = table
            break

    if df is None:
        print(f"ERROR: Could not find a table with any of the expected columns {columns_to_try} for {index_name}.")
        return

    ticker_col = next((col for col in columns_to_try if col in df.columns), None)

    if not ticker_col:
        print(f"ERROR: This should not happen. Ticker column not found after table was selected for {index_name}.")
        return

    raw_tickers = df[ticker_col].dropna().unique().tolist()
    print(f"Found {len(raw_tickers)} unique tickers for {index_name}. Now verifying...")

    verified_tickers = []
    for i, ticker in enumerate(raw_tickers):
        # DO NOT MODIFY the ticker. yfinance can handle 'BRK-B' and 'ADS.DE' correctly.
        cleaned_ticker = str(ticker)

        print(f"Verifying [{i+1}/{len(raw_tickers)}]: {cleaned_ticker}...", end='', flush=True)
        try:
            stock = yf.Ticker(cleaned_ticker)
            hist = stock.history(period="5d")
            if not hist.empty:
                print(" -> VALID")
                verified_tickers.append(cleaned_ticker)
            else:
                print(" -> INVALID (no history)")
        except Exception:
            print(" -> INVALID (exception)")
        time.sleep(0.1)

    print(f"\nVerification complete. Found {len(verified_tickers)} valid tickers for {index_name}.")

    output_path = Path(output_filename)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_df = pd.DataFrame(sorted(verified_tickers), columns=["Ticker"])
    output_df.to_csv(output_path, index=False)
    print(f"Saved verified tickers to {output_path}")

def scrape_dax():
    scrape_and_save_tickers(
        index_name="DAX",
        url="https://en.wikipedia.org/wiki/DAX",
        columns_to_try=['Ticker'],
        output_filename="data/dax_tickers.csv"
    )

def scrape_stoxx600():
    scrape_and_save_tickers(
        index_name="STOXX 600",
        url="https://en.wikipedia.org/wiki/STOXX_Europe_600",
        columns_to_try=['Ticker'], # Wikipedia uses 'Ticker'
        output_filename="data/stoxx600_tickers.csv"
    )

def scrape_nasdaq100():
    scrape_and_save_tickers(
        index_name="NASDAQ 100",
        url="https://www.slickcharts.com/nasdaq100",
        columns_to_try=['Symbol'], # Slickcharts uses 'Symbol'
        output_filename="data/nasdaq100_tickers.csv"
    )

def scrape_sp500():
     scrape_and_save_tickers(
        index_name="S&P 500",
        url="https://www.slickcharts.com/sp500",
        columns_to_try=['Symbol'], # Slickcharts uses 'Symbol'
        output_filename="data/sp500_tickers.csv"
    )

if __name__ == '__main__':
    scrape_dax()
    print("\n" + "="*40 + "\n")
    scrape_stoxx600()
    print("\n" + "="*40 + "\n")
    scrape_nasdaq100()
    print("\n" + "="*40 + "\n")
    scrape_sp500()
    print("\n" + "="*40 + "\n")
    print("All scraping tasks complete.")