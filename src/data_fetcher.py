# data_fetcher.py
# Fetches price history from Yahoo Finance for every ticker in the watchlist
# and computes the technical signals used by alert_engine.py to decide
# whether a given asset deserves an alert.

import yaml
import pandas as pd
import pandas_ta_classic as ta
import yfinance as yf

HISTORY_PERIOD = "2y"
RSI_LENGTH = 14
ATR_LENGTH = 14


def load_watchlist(path="config/watchlist.yaml"):
    """
    Load the watchlist config and return a flat list of dicts:
    [{"ticker": "NVDA", "type": "stock"}, ...]
    """
    with open(path, "r") as f:
        config = yaml.safe_load(f)
    return config["assets"]


def fetch_price_history(ticker, period=HISTORY_PERIOD):
    """
    Download daily OHLCV history for a single ticker.

    auto_adjust=True is mandatory here, not optional: it corrects prices for
    dividends and stock splits. Without it, a normal dividend payout would
    look like a price drop and could trigger a false alert.
    """
    try:
        df = yf.download(
            ticker,
            period=period,
            auto_adjust=True,
            progress=False,
        )
    except yf.exceptions.YFRateLimitError:
        # yfinance hit Yahoo Finance's rate limit. Massive (formerly Polygon.io)
        # fallback is planned but not implemented yet — no API key configured.
        # For now, this ticker is skipped for this run instead of crashing
        # the whole pipeline.
        print(f"[WARN] Rate limit hit for {ticker}. Massive fallback not configured yet. Skipping.")
        return None

    if df.empty:
        print(f"[WARN] No data returned for {ticker}. Skipping.")
        return None

    # yfinance sometimes returns MultiIndex columns for a single ticker
    # depending on version/call shape; flatten to be safe.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df


def compute_technical_signals(df):
    """
    Given a price history DataFrame (with at least 200+ rows), compute the
    technical signals alert_engine.py needs:
    - 1-day and 5-day percent change (used in severity calculation)
    - RSI(14) and ATR(14) (used in severity calculation)
    - 52-week high/low (contextual only, not used in severity)
    - Golden Cross / Death Cross flag (contextual only, not used in severity)
    """
    close = df["Close"]

    # --- Returns ---
    pct_change_1d = (close.iloc[-1] / close.iloc[-2] - 1) * 100
    pct_change_5d = (close.iloc[-1] / close.iloc[-6] - 1) * 100

    # --- RSI and ATR via pandas-ta-classic ---
    rsi_series = df.ta.rsi(length=RSI_LENGTH)
    atr_series = df.ta.atr(length=ATR_LENGTH)

    rsi = rsi_series.iloc[-1]
    atr = atr_series.iloc[-1]
    atr_pct = (atr / close.iloc[-1]) * 100

    # --- 52-week range (contextual, not part of severity) ---
    last_252 = close.tail(252)
    week52_high = last_252.max()
    week52_low = last_252.min()

    # --- Golden Cross / Death Cross (contextual, not part of severity) ---
    sma50 = df.ta.sma(length=50)
    sma200 = df.ta.sma(length=200)

    golden_cross = False
    death_cross = False
    if sma50.iloc[-2] < sma200.iloc[-2] and sma50.iloc[-1] >= sma200.iloc[-1]:
        golden_cross = True
    elif sma50.iloc[-2] > sma200.iloc[-2] and sma50.iloc[-1] <= sma200.iloc[-1]:
        death_cross = True

    return {
        "last_close": close.iloc[-1],
        "pct_change_1d": pct_change_1d,
        "pct_change_5d": pct_change_5d,
        "rsi": rsi,
        "atr": atr,
        "atr_pct": atr_pct,
        "week52_high": week52_high,
        "week52_low": week52_low,
        "golden_cross": golden_cross,
        "death_cross": death_cross,
    }


def fetch_all(watchlist):
    """
    Loop through every asset in the watchlist, fetch its price history, and
    compute its technical signals. Each ticker is wrapped in its own
    try/except so a single failing ticker (bad data, network hiccup) never
    crashes the whole run — the rest of the watchlist still gets processed.
    """
    results = {}
    for asset in watchlist:
        ticker = asset["ticker"]
        try:
            df = fetch_price_history(ticker)
            if df is None:
                continue
            signals = compute_technical_signals(df)
            signals["type"] = asset["type"]
            results[ticker] = signals
        except Exception as e:
            print(f"[ERROR] Failed to process {ticker}: {e}")
            continue

    return results

def load_correlation_groups(path="config/watchlist.yaml"):
    """Load only the correlation_groups section of the watchlist config."""
    with open(path, "r") as f:
        config = yaml.safe_load(f)
    return config.get("correlation_groups", [])