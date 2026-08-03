# email_sender.py
# Fetches recent news headlines per ticker (bypassing the LLM entirely, as
# decided), builds a single consolidated email body covering every alert
# triggered in this run, and sends it via Gmail SMTP using an app password.

import os
import smtplib
from email.mime.text import MIMEText

import yfinance as yf

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

MAX_NEWS_ITEMS = 3

METRIC_EXPLANATIONS = {
    "rsi": "RSI mide qué tan rápido subió o bajó el precio recientemente. Arriba de 70: subió muy rápido, podría pausar o retroceder. Abajo de 30: bajó muy rápido, podría rebotar.",
    "atr_pct": "ATR% mide qué tanto se mueve normalmente este activo en un día promedio (su volatilidad típica).",
    "pe_ratio": "P/E: cuánto pagan los inversionistas por cada peso/dólar de utilidad de la empresa. Más alto suele significar que el mercado espera más crecimiento futuro, o que la acción está más cara respecto a lo que gana hoy.",
    "roe": "ROE: qué tan eficiente es la empresa generando utilidades con el capital de sus propios accionistas. Más alto generalmente es mejor.",
    "profit_margin": "Margen neto: qué porcentaje de cada venta se convierte en utilidad real para la empresa.",
    "debt_to_equity": "Deuda/Capital: qué porcentaje representa la deuda de la empresa respecto a su capital propio. Más alto significa más apalancamiento (más riesgo financiero).",
    "ev_to_ebitda": "EV/EBITDA: similar al P/E, pero también toma en cuenta la deuda de la empresa. Se usa para comparar empresas de forma más completa.",
}


def fetch_news(ticker, max_items=MAX_NEWS_ITEMS):
    """
    Fetch recent news headlines for a ticker directly from yfinance.
    Returns a list of dicts with title and link only — no LLM involved here,
    the raw headlines and their source links go straight into the email.
    """
    try:
        raw_news = yf.Ticker(ticker).get_news()
    except Exception as e:
        print(f"[WARN] Could not fetch news for {ticker}: {e}")
        return []

    items = []
    for item in raw_news[:max_items]:
        content = item.get("content", {})
        title = content.get("title")
        link = content.get("canonicalUrl", {}).get("url")
        if title and link:
            items.append({"title": title, "link": link})

    return items


def build_alert_section(alert, interpretation):
    """
    Build the plain-text section for a single alert (which may cover one
    ticker or a consolidated correlated group like VOO+IVV).
    """
    tickers_label = " + ".join(alert["tickers"])
    lines = [
        f"{'=' * 50}",
        f"{tickers_label}  (severity: {alert['severity']:.2f}, triggered by: {alert['metric']})",
        f"{'=' * 50}",
        "",
        "Interpretación:",
        interpretation,
        "",
        "Datos técnicos:",
    ]

    metrics_used = set()

    for ticker, signals in alert["technical_context"].items():
        lines.append(
            f"  {ticker}: 1 día {signals['pct_change_1d']:+.2f}%, "
            f"5 días {signals['pct_change_5d']:+.2f}%, RSI {signals['rsi']:.1f}, "
            f"ATR% {signals['atr_pct']:.2f}"
        )
        lines.append(
            f"    Rango de 52 semanas: {signals['week52_low']:.2f} - {signals['week52_high']:.2f}"
        )
        if signals["golden_cross"]:
            lines.append("    Golden Cross detectado")
        if signals["death_cross"]:
            lines.append("    Death Cross detectado")
        metrics_used.update(["rsi", "atr_pct"])

    if alert["fundamental_context"]:
        lines.append("")
        lines.append("Fundamentales:")
        for ticker, funds in alert["fundamental_context"].items():
            lines.append(
                f"  {ticker}: P/E {funds['pe_ratio']}, ROE {funds['roe']}, "
                f"margen neto {funds['profit_margin']}, "
                f"deuda/capital {funds['debt_to_equity']}%, "
                f"EV/EBITDA {funds['ev_to_ebitda']}"
            )
        metrics_used.update(["pe_ratio", "roe", "profit_margin", "debt_to_equity", "ev_to_ebitda"])

    lines.append("")
    lines.append("Qué significa cada métrica:")
    for metric in metrics_used:
        lines.append(f"  - {METRIC_EXPLANATIONS[metric]}")

    news_by_ticker = {t: fetch_news(t) for t in alert["tickers"]}
    if any(news_by_ticker.values()):
        lines.append("")
        lines.append("Noticias recientes:")
        for ticker, news_items in news_by_ticker.items():
            for item in news_items:
                lines.append(f"  [{ticker}] {item['title']}")
                lines.append(f"    {item['link']}")

    lines.append("")
    return "\n".join(lines)


def build_email_body(alerts_with_interpretations):
    """
    Combine every alert triggered in this run into a single consolidated
    email body. alerts_with_interpretations is a list of
    (alert, interpretation) tuples.
    """
    sections = [
        build_alert_section(alert, interpretation)
        for alert, interpretation in alerts_with_interpretations
    ]
    return "\n".join(sections)


def send_email(subject, body):
    """
    Send a plain-text email to yourself via Gmail SMTP, using an app
    password (not your regular Gmail password, and not OAuth — simpler
    for a script that only ever emails its own account).
    """
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = gmail_address

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(gmail_address, gmail_password)
        server.send_message(msg)


def send_alerts_digest(alerts_with_interpretations):
    """
    Main entry point: builds and sends one consolidated email covering
    every alert triggered in this run.
    """
    count = len(alerts_with_interpretations)
    subject = f"Stock Monitor: {count} alert{'s' if count != 1 else ''} triggered"
    body = build_email_body(alerts_with_interpretations)
    send_email(subject, body)