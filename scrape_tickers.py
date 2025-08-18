import re
import time
import pandas as pd
import yfinance as yf
from tools import view_text_website # Assuming I can import the tool like this for scripting

def scrape_stoxx600():
    """
    Scrapes, cleans, verifies, and saves the list of STOXX 600 tickers.
    """
    print("--- Starting STOXX 600 Ticker Scraping and Verification ---")

    base_url = "https://www.dividendmax.com/market-index-constituents/stoxx600?page="
    all_tickers = []

    # There are 564 tickers, 30 per page, so about 19 pages. Let's go to 20 to be safe.
    for i in range(1, 21):
        print(f"Scraping page {i}...")
        url = base_url + str(i)
        try:
            content = view_text_website(url)
            # Simple regex to find lines that start with a company name (and link)
            # and extract the ticker and the flag.
            # Example line: [23]3i Group plc III 🇬🇧 [24]London Stock Exchange
            lines = content.split('\n')
            found_on_page = 0
            for line in lines:
                # A crude but effective way to find the relevant lines
                if line.strip().startswith('[') and 'Stock Exchange' in line:
                    parts = line.split()
                    if len(parts) > 2:
                        ticker = parts[1]
                        flag = parts[2]
                        all_tickers.append({'ticker': ticker, 'flag': flag})
                        found_on_page += 1
            if found_on_page == 0 and i > 1:
                print("No more tickers found. Stopping scrape.")
                break
        except Exception as e:
            print(f"Could not scrape page {i}. Error: {e}")
        time.sleep(1) # Be polite to the server

    print(f"Found {len(all_tickers)} raw tickers. Now cleaning and adding suffixes.")

    flag_to_suffix = {
        '🇩🇪': '.DE', '🇬🇧': '.L', '🇮🇹': '.MI', '🇸🇪': '.ST', '🇳🇱': '.AS',
        '🇨🇭': '.SW', '🇫🇷': '.PA', '🇧🇪': '.BR', '🇪🇸': '.MC', '🇮🇪': '.IR',
        '🇳🇴': '.OL', '🇫🇮': '.HE', '🇩🇰': '.CO', '🇦🇹': '.VI', '🇵🇹': '.LS'
    }

    processed_tickers = []
    for item in all_tickers:
        base_ticker = item['ticker']
        flag = item['flag']
        suffix = flag_to_suffix.get(flag)
        if suffix:
            # yfinance often uses '-' instead of '.' in tickers like 'BRK.B' -> 'BRK-B'
            # Let's replace any dots in the base ticker.
            base_ticker = base_ticker.replace('.', '-')
            processed_tickers.append(base_ticker + suffix)

    # Remove duplicates
    processed_tickers = sorted(list(set(processed_tickers)))
    print(f"Processed {len(processed_tickers)} unique tickers. Now verifying with yfinance...")

    verified_tickers = []
    for i, ticker in enumerate(processed_tickers):
        print(f"Verifying [{i+1}/{len(processed_tickers)}]: {ticker}...")
        try:
            stock = yf.Ticker(ticker)
            # .info can be slow and return errors for valid tickers sometimes.
            # A better check is to see if it has historical data.
            hist = stock.history(period="5d")
            if not hist.empty:
                print(f"  -> {ticker} is VALID.")
                verified_tickers.append(ticker)
            else:
                print(f"  -> {ticker} is INVALID (no history).")
        except Exception:
            print(f"  -> {ticker} is INVALID (exception).")
        time.sleep(0.2)

    print(f"\nVerification complete. Found {len(verified_tickers)} valid STOXX 600 tickers.")

    # Save to CSV
    df = pd.DataFrame(verified_tickers, columns=["Ticker"])
    df.to_csv("stoxx600_verified.csv", index=False)
    print("Saved verified tickers to stoxx600_verified.csv")

if __name__ == '__main__':
    scrape_stoxx600()
    # In a real run, I would add the Nikkei scraper here too.
