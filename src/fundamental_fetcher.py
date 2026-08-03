# fundamental_fetcher.py
# Fetches fundamental financial metrics for individual stocks only (never ETFs,
# per watchlist.yaml's type field). Always checks the state cache first to
# avoid re-fetching data that only changes when a company reports quarterly
# earnings — refreshing every single 30-minute run would be wasteful and
# increases the risk of hitting Yahoo Finance's rate limits.

import yfinance as yf

# The specific fundamental fields we care about, and the yfinance key that
# holds each one. Centralizing this mapping in one place means adding or
# removing a metric later only requires touching this dict, not the logic below.
FUNDAMENTAL_FIELDS = {
    "pe_ratio": "trailingPE",
    "roe": "returnOnEquity",
    "profit_margin": "profitMargins",
    "debt_to_equity": "debtToEquity",
    "ev_to_ebitda": "enterpriseToEbitda",
}


def fetch_fundamentals(ticker):
    """
    Fetch fresh fundamental data for a single ticker directly from yfinance,
    with no caching logic here — this function always hits the network.
    Caching is handled one layer up, in get_fundamentals_with_cache.
    """
    info = yf.Ticker(ticker).get_info()

    data = {}
    for our_key, yf_key in FUNDAMENTAL_FIELDS.items():
        data[our_key] = info.get(yf_key)

    return data


def get_fundamentals_with_cache(ticker, state_manager):
    """
    Return fundamentals for a ticker, using the cache when possible.

    Checks state_manager's cache first (valid for up to 7 days per ticker).
    Only calls yfinance if the cache is empty or expired, and immediately
    stores whatever comes back so the next run within 7 days can reuse it.
    """
    cached = state_manager.get_cached_fundamentals(ticker)
    if cached is not None:
        return cached

    fresh_data = fetch_fundamentals(ticker)
    state_manager.cache_fundamentals(ticker, fresh_data)
    return fresh_data


def fetch_all_fundamentals(watchlist, state_manager):
    """
    Loop through the watchlist and fetch fundamentals only for assets marked
    type == "stock" — ETFs are skipped entirely, since fundamentals like P/E
    or ROE don't apply the same way to a diversified basket of companies.

    Each ticker is wrapped in try/except so a single failing stock doesn't
    stop the rest of the watchlist from being processed, same pattern used
    in data_fetcher.py's fetch_all.
    """
    results = {}
    for asset in watchlist:
        if asset["type"] != "stock":
            continue

        ticker = asset["ticker"]
        try:
            results[ticker] = get_fundamentals_with_cache(ticker, state_manager)
        except Exception as e:
            print(f"[ERROR] Failed to fetch fundamentals for {ticker}: {e}")
            continue

    return results