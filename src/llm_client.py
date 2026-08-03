# llm_client.py
# Calls Claude Haiku 4.5 to generate a plain-language interpretation of a
# triggered alert's technical and fundamental context. This is the only
# place in the system that talks to the Anthropic API — news headlines from
# yfinance bypass the LLM entirely and go straight into the email with links.

import os
from anthropic import Anthropic

# Pinned model ID, not an alias, so behavior never silently shifts if
# Anthropic updates what a short alias like "claude-haiku-4-5" points to.
MODEL_ID = "claude-haiku-4-5-20251001"

MAX_TOKENS = 300


def build_prompt(alert):
    """
    Turn a single alert dict (as produced by alert_engine.generate_alerts)
    into a plain-text prompt describing the situation for Claude to interpret.
    """
    tickers = ", ".join(alert["tickers"])
    metric = alert["metric"]
    severity = round(alert["severity"], 2)

    lines = [
        f"Ticker(s): {tickers}",
        f"Triggered metric: {metric}",
        f"Severity (1.0 = threshold, higher = stronger signal): {severity}",
        "",
        "Technical context:",
    ]

    for ticker, signals in alert["technical_context"].items():
        lines.append(
            f"- {ticker}: 1-day change {signals['pct_change_1d']:.2f}%, "
            f"5-day change {signals['pct_change_5d']:.2f}%, "
            f"RSI {signals['rsi']:.1f}, ATR% {signals['atr_pct']:.2f}, "
            f"52-week range {signals['week52_low']:.2f}-{signals['week52_high']:.2f}, "
            f"Golden Cross: {signals['golden_cross']}, Death Cross: {signals['death_cross']}"
        )

    if alert["fundamental_context"]:
        lines.append("")
        lines.append("Fundamental context:")
        for ticker, funds in alert["fundamental_context"].items():
            lines.append(
                f"- {ticker}: P/E {funds['pe_ratio']}, ROE {funds['roe']}, "
                f"profit margin {funds['profit_margin']}, "
                f"debt/equity {funds['debt_to_equity']}% (as a percentage of equity, "
                f"not a multiplier), EV/EBITDA {funds['ev_to_ebitda']}"
            )

    lines.append("")
    lines.append(
        "Write a short interpretation (3-4 sentences), in Spanish, of what this "
        "signal likely means for a long-term investor. The reader has no formal "
        "background in finance, so briefly explain any technical term you use in "
        "plain words as you go (e.g. instead of just 'RSI sobrecomprado', explain "
        "that it means the price rose very fast recently and may pause or pull "
        "back). Do not give buy/sell advice — only explain what the data suggests "
        "is happening. Write in plain prose only — no Markdown, no headers, no "
        "bullet points, no bold text."
    )

    return "\n".join(lines)


def get_interpretation(alert, state_manager):
    """
    Call Claude Haiku 4.5 to interpret a single alert, and record the call
    in state_manager for monthly cost tracking.

    Returns the plain-text interpretation string, or a fallback message if
    the API call fails for any reason (network issue, rate limit, etc.) —
    a failed LLM call should never prevent the email from being sent with
    the raw data alone.
    """
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = build_prompt(alert)

    try:
        response = client.messages.create(
            model=MODEL_ID,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        state_manager.increment_llm_call_count()
        return response.content[0].text

    except Exception as e:
        print(f"[ERROR] LLM call failed for {alert['tickers']}: {e}")
        return (
            "No se pudo generar una interpretación automática para esta alerta. "
            "Revisa los datos técnicos y fundamentales incluidos arriba."
        )