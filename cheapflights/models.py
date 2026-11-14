from __future__ import annotations

from dataclasses import dataclass


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


@dataclass
class Deal:
    fare: Fare
    baseline: float     # median price for the route
    p25: float          # 25th percentile for the route
    samples: int
    is_mistake: bool

    @property
    def pct_off(self) -> float:
        return 1 - self.fare.price / self.baseline

    @property
    def dollars_off(self) -> float:
        return self.baseline - self.fare.price
