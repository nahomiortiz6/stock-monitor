# main.py
# Entry point executed by GitHub Actions on every scheduled run.
# Ties together market-hours gating, data fetching, alert evaluation,
# LLM interpretation, and email delivery into a single sequential flow.

from dotenv import load_dotenv

load_dotenv()  # No-op in GitHub Actions (no .env file there); loads local secrets when run on your Mac.

import exchange_calendars as xcals
import pandas as pd

from src.state_manager import StateManager
from src.data_fetcher import load_watchlist, load_correlation_groups, fetch_all
from src.fundamental_fetcher import fetch_all_fundamentals
from src.alert_engine import generate_alerts
from src.llm_client import get_interpretation
from src.email_sender import send_alerts_digest


def market_is_open():
    """
    Check whether NYSE is genuinely open at this exact moment in UTC.
    This single check covers weekends, holidays, and the DST timezone gap
    between Mexico (fixed UTC-6) and the US (which observes DST) — the wide
    cron window in monitor.yml fires more often than needed, and this is
    what filters out the runs where nothing should actually happen.
    """
    nyse = xcals.get_calendar("XNYS")
    now_utc = pd.Timestamp.now("UTC")
    return nyse.is_open_at_time(now_utc)


def main():
    if not market_is_open():
        print("Mercado cerrado en este instante. Finalizando sin hacer nada más.")
        return

    state_manager = StateManager()

    try:
        watchlist = load_watchlist()
        correlation_groups = load_correlation_groups()

        print(f"Procesando {len(watchlist)} activos...")
        technical_results = fetch_all(watchlist)
        fundamental_results = fetch_all_fundamentals(watchlist, state_manager)

        alerts = generate_alerts(
            watchlist,
            technical_results,
            fundamental_results,
            state_manager,
            correlation_groups,
        )

        if not alerts:
            print("Ninguna alerta disparada en esta corrida.")
            return

        print(f"{len(alerts)} alerta(s) disparada(s). Generando interpretaciones...")
        alerts_with_interpretations = [
            (alert, get_interpretation(alert, state_manager)) for alert in alerts
        ]

        send_alerts_digest(alerts_with_interpretations)
        print("Correo enviado exitosamente.")

    finally:
        # Always persist state — cache updates and cooldown records made
        # during this run must survive even if something failed midway.
        state_manager.save()


if __name__ == "__main__":
    main()