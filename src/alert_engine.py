# alert_engine.py
# Combines technical signals (from data_fetcher.py), fundamentals
# (from fundamental_fetcher.py), and alert cooldown (from state_manager.py)
# to decide which assets deserve an alert in this run, and with what content.

ATR_MULTIPLIER_1D = 1.5
ATR_MULTIPLIER_5D = 2.5
RSI_UPPER_BOUND = 70
RSI_LOWER_BOUND = 30


def severity_1d(pct_change_1d, atr_pct):
    """
    Severity from the 1-day return, relative to a threshold based on the
    asset's own volatility (ATR%). A severity >= 1.0 means the move exceeded
    the threshold and is a candidate for alerting.
    """
    if atr_pct is None or atr_pct == 0:
        return 0.0
    threshold = atr_pct * ATR_MULTIPLIER_1D
    return abs(pct_change_1d) / threshold


def severity_5d(pct_change_5d, atr_pct):
    """Same idea as severity_1d, but for the 5-day return and a wider multiplier."""
    if atr_pct is None or atr_pct == 0:
        return 0.0
    threshold = atr_pct * ATR_MULTIPLIER_5D
    return abs(pct_change_5d) / threshold


def severity_rsi(rsi):
    """
    Severity from RSI, only when RSI is outside the neutral 30-70 band.
    Returns 0.0 (never triggers) when RSI is inside the band — this metric
    doesn't participate in the decision at all in that case.
    """
    if rsi is None:
        return 0.0

    if rsi > RSI_UPPER_BOUND:
        boundary_distance = rsi - RSI_UPPER_BOUND
    elif rsi < RSI_LOWER_BOUND:
        boundary_distance = RSI_LOWER_BOUND - rsi
    else:
        return 0.0

    return (boundary_distance / 30) + 1


def evaluate_ticker(ticker, signals):
    """
    Compute all three severities for a single ticker and return the one
    with the highest value, but only if it actually reached the 1.0
    trigger point. Returns None if nothing triggered for this ticker.

    Returns a dict like:
    {"metric": "rsi", "severity": 1.4} or None
    """
    candidates = {
        "return_1d": severity_1d(signals["pct_change_1d"], signals["atr_pct"]),
        "return_5d": severity_5d(signals["pct_change_5d"], signals["atr_pct"]),
        "rsi": severity_rsi(signals["rsi"]),
    }

    # Keep only metrics that actually crossed the trigger threshold
    triggered = {m: s for m, s in candidates.items() if s >= 1.0}

    if not triggered:
        return None

    # Pick the metric with the highest severity as the representative one
    # for this ticker — this is also the metric used as the cooldown key.
    best_metric = max(triggered, key=triggered.get)
    return {"metric": best_metric, "severity": triggered[best_metric]}


def consolidate_correlated_groups(triggered, correlation_groups):
    """
    Merge triggered tickers that belong to the same correlation group
    (e.g. VOO and IVV) into a single consolidated alert entry, using the
    highest severity among them as the group's representative severity.

    `triggered` is a dict like {"VOO": {"metric": "return_1d", "severity": 1.4},
                                 "IVV": {"metric": "return_1d", "severity": 1.3}, ...}

    Returns a list of alert entries, each either a single ticker or a
    consolidated group.
    """
    consolidated = []
    already_grouped = set()

    for group in correlation_groups:
        triggered_in_group = [t for t in group if t in triggered]

        if not triggered_in_group:
            continue

        # Only consolidate if 2+ members of the group actually triggered.
        # If only one member triggered, it's handled below as an individual alert.
        if len(triggered_in_group) >= 2:
            best_ticker = max(triggered_in_group, key=lambda t: triggered[t]["severity"])
            consolidated.append({
                "tickers": triggered_in_group,
                "metric": triggered[best_ticker]["metric"],
                "severity": triggered[best_ticker]["severity"],
            })
            already_grouped.update(triggered_in_group)

    # Any triggered ticker not part of a consolidated group becomes its own
    # individual alert entry.
    for ticker, result in triggered.items():
        if ticker not in already_grouped:
            consolidated.append({
                "tickers": [ticker],
                "metric": result["metric"],
                "severity": result["severity"],
            })

    return consolidated


def generate_alerts(watchlist, technical_results, fundamental_results,
                     state_manager, correlation_groups):
    """
    Main orchestrator. Combines everything and returns the final list of
    alerts to actually send this run, already filtered through the cooldown
    logic in state_manager, and enriched with context (fundamentals,
    52-week range, golden/death cross) for llm_client.py and email_sender.py.
    """
    # Step 1: evaluate each ticker independently
    triggered = {}
    for ticker, signals in technical_results.items():
        result = evaluate_ticker(ticker, signals)
        if result is not None:
            triggered[ticker] = result

    if not triggered:
        return []

    # Step 2: consolidate correlated tickers (e.g. VOO + IVV) into single entries
    consolidated = consolidate_correlated_groups(triggered, correlation_groups)

    # Step 3: apply cooldown per entry, and attach context for the ones that pass
    final_alerts = []
    for entry in consolidated:
        # For cooldown purposes, use the first ticker in the group as the key
        # representative — VOO and IVV move together, so their cooldown state
        # can reasonably share a single trigger point.
        cooldown_key_ticker = entry["tickers"][0]

        if not state_manager.should_alert(cooldown_key_ticker, entry["metric"], entry["severity"]):
            continue

        alert = {
            "tickers": entry["tickers"],
            "metric": entry["metric"],
            "severity": entry["severity"],
            "technical_context": {t: technical_results[t] for t in entry["tickers"]},
            "fundamental_context": {
                t: fundamental_results[t]
                for t in entry["tickers"]
                if t in fundamental_results
            },
        }
        final_alerts.append(alert)

        # Record immediately so a second consolidated group sharing a ticker
        # (shouldn't normally happen, but defensive) doesn't double-fire.
        state_manager.record_alert(cooldown_key_ticker, entry["metric"], entry["severity"])

    return final_alerts