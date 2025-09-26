import time
import pandas as pd
import yfinance as yf
import requests
from io import StringIO
from pathlib import Path

def verify_and_save_tickers(index_name: str, tickers_to_verify: list, output_filename: str):
    """
    Verifies a list of tickers using yfinance and saves the valid ones to a CSV file.
    """
    print(f"Verifying {len(tickers_to_verify)} unique tickers for {index_name}...")

    verified_tickers = []
    for i, ticker in enumerate(tickers_to_verify):
        cleaned_ticker = str(ticker).strip()
        if not cleaned_ticker:
            continue

        print(f"Verifying [{i+1}/{len(tickers_to_verify)}]: {cleaned_ticker}...", end='', flush=True)
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
        time.sleep(0.05)

    print(f"\nVerification complete. Found {len(verified_tickers)} valid tickers for {index_name}.")

    output_path = Path(output_filename)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_df = pd.DataFrame(sorted(verified_tickers), columns=["Ticker"])
    output_df.to_csv(output_path, index=False)
    print(f"Saved verified tickers to {output_path}")


def scrape_from_generic_table(index_name: str, url: str, columns_to_try: list, output_filename: str):
    """
    Scrapes tickers from a simple, non-paginated HTML table.
    """
    print(f"--- Starting {index_name} Ticker Scraping (Generic) ---")
    headers = {'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0'}

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        tables = pd.read_html(StringIO(response.text))
    except Exception as e:
        print(f"ERROR: Could not fetch or parse the webpage for {index_name}. Error: {e}")
        return

    df = None
    for table in tables:
        if any(col in table.columns for col in columns_to_try):
            df = table
            break

    if df is None:
        print(f"ERROR: Could not find a suitable table for {index_name}.")
        return

    ticker_col = next((col for col in columns_to_try if col in df.columns), None)
    if not ticker_col:
        print(f"ERROR: Could not find ticker column for {index_name}.")
        return

    raw_tickers = df[ticker_col].dropna().unique().tolist()
    verify_and_save_tickers(index_name, raw_tickers, output_filename)


def scrape_stoxx600():
    """
    Custom scraper for the paginated STOXX 600 data from dividendmax.com.
    """
    print("--- Starting STOXX 600 Ticker Scraping (Custom) ---")

    base_url = "https://www.dividendmax.com/market-index-constituents/stoxx600?page={page}"
    headers = {'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0'}

    all_tickers = []
    exchange_to_suffix = {
        'Frankfurt Stock Exchange': '.DE', 'London Stock Exchange': '.L', 'Italian Stock Exchange': '.MI',
        'Stockholm Stock Exchange': '.ST', 'Euronext Amsterdam': '.AS', 'SIX Swiss Exchange': '.SW',
        'Euronext Paris': '.PA', 'Euronext Brussels': '.BR', 'Madrid Stock Exchange': '.MC',
        'Irish Stock Exchange': '.IR', 'Oslo Stock Exchange': '.OL', 'Helsinki Stock Exchange': '.HE',
        'Copenhagen Stock Exchange': '.CO', 'Vienna Stock Exchange': '.VI', 'Lisbon Stock Exchange': '.LS'
    }

    for page_num in range(1, 21):
        url = base_url.format(page=page_num)
        print(f"Scraping page {page_num}...")

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            tables = pd.read_html(StringIO(response.text))
        except Exception as e:
            print(f"Could not fetch or parse page {page_num}. Error: {e}")
            break

        if not tables or len(tables[0]) == 0:
            print("No more tickers found. Stopping scrape.")
            break

        df = tables[0]
        if 'Ticker' not in df.columns or 'Exchange' not in df.columns:
            print("Could not find 'Ticker' or 'Exchange' columns. Stopping.")
            break

        for index, row in df.iterrows():
            ticker = row['Ticker']
            exchange = row['Exchange']
            suffix = exchange_to_suffix.get(exchange)
            if ticker and suffix:
                all_tickers.append(f"{ticker}{suffix}")

        time.sleep(1)

    verify_and_save_tickers("STOXX 600", sorted(list(set(all_tickers))), "data/stoxx600_tickers.csv")

def scrape_dax():
    scrape_from_generic_table(
        index_name="DAX",
        url="https://en.wikipedia.org/wiki/DAX",
        columns_to_try=['Ticker'],
        output_filename="data/dax_tickers.csv"
    )

def scrape_nasdaq100():
    scrape_from_generic_table(
        index_name="NASDAQ 100",
        url="https://www.slickcharts.com/nasdaq100",
        columns_to_try=['Symbol'],
        output_filename="data/nasdaq100_tickers.csv"
    )

def scrape_sp500():
     scrape_from_generic_table(
        index_name="S&P 500",
        url="https://www.slickcharts.com/sp500",
        columns_to_try=['Symbol'],
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