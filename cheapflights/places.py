"""City names for airport/city codes, e.g. NRT -> Tokyo.

Uses Travelpayouts' free data files (no token needed). They're downloaded
about once a month and stored in the database, so a failed download just
means we keep using the names we already have (or show bare codes).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import urllib.request

log = logging.getLogger(__name__)

CITIES_URL = "https://api.travelpayouts.com/data/en/cities.json"
AIRPORTS_URL = "https://api.travelpayouts.com/data/en/airports.json"
REFRESH_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS place_names (
    code       TEXT PRIMARY KEY,   -- IATA city or airport code
    city       TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
) WITHOUT ROWID;
"""


def _english_name(item: dict) -> str | None:
    return (item.get("name_translations") or {}).get("en") or item.get("name")


def parse(cities: list[dict], airports: list[dict]) -> dict[str, str]:
    """Map every city code and airport code to its city's English name."""
    names: dict[str, str] = {}
    for c in cities or []:
        code, name = c.get("code"), _english_name(c)
        if code and name:
            names[code] = name
    for a in airports or []:
        code, city_code = a.get("code"), a.get("city_code")
        if code and code not in names:
            city = names.get(city_code) if city_code else None
            if city:
                names[code] = city
    return names


def _fetch(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def load(conn: sqlite3.Connection, refresh: bool = True) -> dict[str, str]:
    """Code -> city name. Re-downloads the lists if they're missing or a month old."""
    conn.executescript(SCHEMA)
    row = conn.execute(
        "SELECT COUNT(*), julianday('now') - julianday(MIN(updated_at)) FROM place_names").fetchone()
    count, age = row[0], row[1]
    if refresh and (count == 0 or age is None or age > REFRESH_DAYS):
        try:
            names = parse(_fetch(CITIES_URL), _fetch(AIRPORTS_URL))
            if names:
                conn.execute("DELETE FROM place_names")
                conn.executemany("INSERT INTO place_names (code, city) VALUES (?, ?)", names.items())
                conn.commit()
                log.info("downloaded %d place names (e.g. NRT = %s)", len(names), names.get("NRT", "?"))
        except Exception as exc:
            log.warning("couldn't download city names, using %d saved ones: %s", count, exc)
    return {code: city for code, city in conn.execute("SELECT code, city FROM place_names")}


def label(code: str, names: dict[str, str]) -> str:
    """'Tokyo (NRT)', or just 'NRT' if we don't know the city."""
    city = names.get(code)
    return f"{city} ({code})" if city and city.upper() != code else code
