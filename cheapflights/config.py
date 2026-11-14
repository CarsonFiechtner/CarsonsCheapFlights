"""Settings, read from environment variables (or a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Tiny .env loader so we don't need python-dotenv. Real env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _list(name: str, default: str) -> list[str]:
    return [x.strip().upper() for x in os.environ.get(name, default).split(",") if x.strip()]


@dataclass
class Config:
    travelpayouts_token: str = ""
    origins: list[str] = field(default_factory=lambda: ["SFO", "SJC"])
    months_ahead: int = 10
    currency: str = "usd"
    db_path: str = "data/prices.sqlite"

    # Deal rule: a fare is a deal when ALL of these hold.
    min_pct_off: float = 0.40        # at least 40% below the route's median
    min_dollars_off: float = 200.0   # and at least $200 below it (filters out cheap short hops)
    min_samples: int = 8             # and we've seen the route enough to trust the median...
    min_history_days: int = 7        # ...on at least this many different days
    mistake_pct_off: float = 0.70    # 70%+ off gets flagged as a likely mistake fare
    max_stops: int = 2               # ignore itineraries with more stops each way
    history_days: int = 90           # how far back the "normal price" looks
    raw_keep_days: int = 365         # delete individual prices older than this (monthly summaries are kept forever)
    history_dir: str = "data/history"  # monthly summary CSVs; on GitHub this is on the `history` branch
    realert_days: int = 14           # don't re-send the same deal for this long...
    realert_drop: float = 0.10       # ...unless the price dropped another 10%

    # Email (any SMTP server; Gmail works with an app password).
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    alert_to: str = ""

    def origin_label(self) -> str:
        """All origins are treated as one home area, e.g. 'SFO/SJC'."""
        return "/".join(self.origins)

    @classmethod
    def from_env(cls) -> "Config":
        _load_dotenv()
        e = os.environ.get
        c = cls()
        c.travelpayouts_token = e("TRAVELPAYOUTS_TOKEN", "")
        c.origins = _list("ORIGINS", "SFO,SJC")
        c.months_ahead = int(e("MONTHS_AHEAD", c.months_ahead))
        c.db_path = e("DB_PATH", c.db_path)
        c.min_pct_off = float(e("MIN_PCT_OFF", c.min_pct_off))
        c.min_dollars_off = float(e("MIN_DOLLARS_OFF", c.min_dollars_off))
        c.min_samples = int(e("MIN_SAMPLES", c.min_samples))
        c.min_history_days = int(e("MIN_HISTORY_DAYS", c.min_history_days))
        c.mistake_pct_off = float(e("MISTAKE_PCT_OFF", c.mistake_pct_off))
        c.max_stops = int(e("MAX_STOPS", c.max_stops))
        c.history_days = int(e("HISTORY_DAYS", c.history_days))
        c.raw_keep_days = int(e("RAW_KEEP_DAYS", c.raw_keep_days))
        c.history_dir = e("HISTORY_DIR", c.history_dir)
        c.smtp_host = e("SMTP_HOST", c.smtp_host)
        c.smtp_port = int(e("SMTP_PORT", c.smtp_port))
        c.smtp_user = e("SMTP_USER", "")
        c.smtp_password = e("SMTP_PASSWORD", "")
        c.alert_to = e("ALERT_TO", "") or c.smtp_user
        return c
