from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Fare:
    origin: str
    destination: str
    depart_date: str            # YYYY-MM-DD
    return_date: str | None     # YYYY-MM-DD or None for one-way
    price: float
    currency: str = "usd"
    airline: str | None = None
    stops: int | None = None
    return_stops: int | None = None
    source: str = "travelpayouts"

    @property
    def depart_month(self) -> str:
        return self.depart_date[:7]

    @property
    def nights(self) -> int | None:
        if not self.return_date:
            return None
        return (date.fromisoformat(self.return_date) - date.fromisoformat(self.depart_date)).days


@dataclass
class Deal:
    fare: Fare          # the cheapest fare found, from whichever home airport
    baseline: float     # median best-home-area price for this destination
    p25: float          # 25th percentile of the same
    samples: int
    is_mistake: bool
    # Cheapest fare from each *other* home airport for the same destination and
    # month, when it's also a deal (e.g. "also from SJC: $420").
    alternatives: list[Fare] = field(default_factory=list)
    # The departure month the baseline covers (e.g. "2027-02"), or None when there
    # wasn't enough data for that month and all months were used instead.
    baseline_month: str | None = None

    @property
    def pct_off(self) -> float:
        return 1 - self.fare.price / self.baseline

    @property
    def dollars_off(self) -> float:
        return self.baseline - self.fare.price
