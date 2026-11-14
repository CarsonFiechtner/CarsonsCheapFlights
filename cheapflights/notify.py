"""Build and send the deal email."""

from __future__ import annotations

import html
import smtplib
import urllib.parse
from email.message import EmailMessage

from .config import Config
from .models import Deal, Fare


def google_flights_url(fare: Fare) -> str:
    q = f"Flights from {fare.origin} to {fare.destination} on {fare.depart_date}"
    if fare.return_date:
        q += f" returning {fare.return_date}"
    return "https://www.google.com/travel/flights?" + urllib.parse.urlencode({"q": q, "curr": "USD"})


def _stops(n: int | None) -> str:
    if n is None:
        return "?"
    return "nonstop" if n == 0 else f"{n} stop{'s' if n > 1 else ''}"


def subject(deals: list[Deal]) -> str:
    top = deals[0]
    prefix = "MISTAKE FARE? " if top.is_mistake else ""
    more = f" (+{len(deals) - 1} more)" if len(deals) > 1 else ""
    return (f"{prefix}{top.fare.origin}→{top.fare.destination} ${top.fare.price:,.0f} "
            f"({top.pct_off:.0%} off){more}")


def render(deals: list[Deal]) -> tuple[str, str]:
    text_lines, rows = [], []
    for d in deals:
        f = d.fare
        url = google_flights_url(f)
        flag = "⚠️ possible mistake fare · " if d.is_mistake else ""
        text_lines.append(
            f"{f.origin} → {f.destination}  ${f.price:,.0f} (usually ~${d.baseline:,.0f}, {d.pct_off:.0%} off)\n"
            f"  {f.depart_date} → {f.return_date} · {f.airline or '?'} · out {_stops(f.stops)}, "
            f"back {_stops(f.return_stops)}\n  {url}\n"
        )
        rows.append(
            f"<tr><td style='padding:8px 12px'><b>{html.escape(f.origin)} → {html.escape(f.destination)}</b><br>"
            f"<span style='color:#666'>{flag}{f.depart_date} → {f.return_date} · {html.escape(f.airline or '?')}"
            f" · out {_stops(f.stops)}, back {_stops(f.return_stops)}</span></td>"
            f"<td style='padding:8px 12px;text-align:right'><b>${f.price:,.0f}</b><br>"
            f"<span style='color:#080'>{d.pct_off:.0%} off ~${d.baseline:,.0f}</span></td>"
            f"<td style='padding:8px 12px'><a href='{html.escape(url)}'>Check on Google Flights</a></td></tr>"
        )
    footer = ("Prices are cached and may have changed. Check them on Google Flights before booking. "
              "'Usually' is the median price seen for this route over the last 90 days.")
    text = "\n".join(text_lines) + "\n" + footer
    body = (f"<table style='border-collapse:collapse;font-family:sans-serif'>{''.join(rows)}</table>"
            f"<p style='color:#888;font-size:12px'>{footer}</p>")
    return text, body


def send(deals: list[Deal], cfg: Config) -> None:
    if not (cfg.smtp_user and cfg.smtp_password and cfg.alert_to):
        raise RuntimeError("SMTP_USER, SMTP_PASSWORD and ALERT_TO must be set to send email")
    msg = EmailMessage()
    msg["Subject"] = subject(deals)
    msg["From"] = cfg.smtp_user
    msg["To"] = cfg.alert_to
    text, body = render(deals)
    msg.set_content(text)
    msg.add_alternative(body, subtype="html")
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as s:
        s.starttls()
        s.login(cfg.smtp_user, cfg.smtp_password)
        s.send_message(msg)
