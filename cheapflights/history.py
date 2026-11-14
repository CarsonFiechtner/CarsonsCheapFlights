"""Long-term price history as quarterly summaries.

Individual prices are deleted after `raw_keep_days`, but before that every
finished calendar quarter (by the day prices were seen) is boiled down to one
row per (origin, destination, departure month) with count, min, 25th
percentile, median and max. These summaries are kept forever: in the
`route_quarterly` table and as one CSV per quarter in `history_dir`. On GitHub
that directory is on the `history` branch, so it survives even if the database
is lost.

Each quarter's CSV is written once, after the quarter ends, and never changes.
"""

from __future__ import annotations

import csv
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS route_quarterly (
    quarter      TEXT NOT NULL,   -- e.g. 2026-Q4
    origin       TEXT NOT NULL,
    destination  TEXT NOT NULL,
    depart_month TEXT NOT NULL,   -- YYYY-MM
    n            INTEGER NOT NULL,
    min_price    REAL NOT NULL,
    p25_price    REAL NOT NULL,
    median_price REAL NOT NULL,
    max_price    REAL NOT NULL,
    PRIMARY KEY (quarter, origin, destination, depart_month)
) WITHOUT ROWID;
"""

FIELDS = ["quarter", "origin", "destination", "depart_month", "n",
          "min_price", "p25_price", "median_price", "max_price"]

# Individual prices must outlive the longest quarter plus some slack, or a
# quarter could be partly deleted before it's summarized.
MIN_RAW_KEEP_DAYS = 100


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def quarter_of(day: date) -> str:
    return f"{day.year}-Q{(day.month - 1) // 3 + 1}"


def quarter_bounds(quarter: str) -> tuple[date, date]:
    year, q = int(quarter[:4]), int(quarter[-1])
    start = date(year, 3 * (q - 1) + 1, 1)
    next_start = date(year + 1, 1, 1) if q == 4 else date(year, 3 * q + 1, 1)
    return start, next_start - timedelta(days=1)


def _summarize(prices: list[float]) -> tuple[int, float, float, float, float]:
    prices = sorted(prices)
    p25 = statistics.quantiles(prices, n=4, method="inclusive")[0] if len(prices) > 1 else prices[0]
    return len(prices), prices[0], round(p25, 2), round(statistics.median(prices), 2), prices[-1]


def rollup(conn: sqlite3.Connection, today: date | None = None) -> list[str]:
    """Summarize every finished quarter that isn't summarized yet. Returns the quarters added."""
    ensure_schema(conn)
    current = quarter_of(today or date.today())
    done = {r[0] for r in conn.execute("SELECT DISTINCT quarter FROM route_quarterly")}
    seen = {quarter_of(date.fromisoformat(r[0])) for r in
            conn.execute("SELECT DISTINCT seen_day FROM price_observations")}
    todo = sorted(q for q in seen - done if q < current)

    for quarter in todo:
        start, end = quarter_bounds(quarter)
        groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        rows = conn.execute(
            "SELECT origin, destination, substr(depart_date, 1, 7), price FROM price_observations"
            " WHERE return_date != '' AND seen_day BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat()),
        )
        for origin, dest, month, price in rows:
            groups[(origin, dest, month)].append(price)
        conn.executemany(
            "INSERT OR IGNORE INTO route_quarterly VALUES (?,?,?,?,?,?,?,?,?)",
            [(quarter, o, d, m, *_summarize(p)) for (o, d, m), p in groups.items()],
        )
    conn.commit()
    return todo


def export(conn: sqlite3.Connection, directory: str) -> list[Path]:
    """Write a CSV for each summarized quarter that doesn't have one yet."""
    ensure_schema(conn)
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for (quarter,) in conn.execute("SELECT DISTINCT quarter FROM route_quarterly ORDER BY quarter").fetchall():
        path = out / f"{quarter}.csv"
        if path.exists():
            continue
        rows = conn.execute(
            f"SELECT {', '.join(FIELDS)} FROM route_quarterly WHERE quarter = ?"
            " ORDER BY origin, destination, depart_month", (quarter,))
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(FIELDS)
            w.writerows(rows)
        written.append(path)
    return written


def load(conn: sqlite3.Connection, directory: str) -> int:
    """Load quarterly CSVs into the database (e.g. after the database was lost). Safe to repeat."""
    ensure_schema(conn)
    count = 0
    for path in sorted(Path(directory).glob("*.csv")):
        with path.open(newline="") as fh:
            rows = [tuple(r[f] for f in FIELDS) for r in csv.DictReader(fh)]
        conn.executemany("INSERT OR IGNORE INTO route_quarterly VALUES (?,?,?,?,?,?,?,?,?)", rows)
        count += len(rows)
    conn.commit()
    return count
