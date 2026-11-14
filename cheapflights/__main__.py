"""Command line: `python -m cheapflights run` scans, finds deals and emails them."""

from __future__ import annotations

import argparse
import logging
import sys

from . import db, detector, notify, travelpayouts
from .config import Config

log = logging.getLogger("cheapflights")


def scan(conn, cfg: Config):
    if not cfg.travelpayouts_token:
        sys.exit("TRAVELPAYOUTS_TOKEN is not set")
    fares = []
    for origin in cfg.origins:
        for month in travelpayouts.month_strings(cfg.months_ahead):
            try:
                got = travelpayouts.fetch_cheapest_anywhere(origin, month, cfg.travelpayouts_token, cfg.currency)
            except Exception as exc:
                log.error("scan %s %s failed: %s", origin, month, exc)
                continue
            log.info("%s %s: %d fares", origin, month, len(got))
            fares.extend(got)
    # Score against history *before* adding this scan, so a fare can't make itself look normal.
    deals = detector.find_deals(conn, fares, cfg)
    db.insert_fares(conn, fares)
    db.prune(conn, cfg.history_days + 30)
    return fares, deals


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="cheapflights")
    p.add_argument("command", choices=["run", "scan", "test-email"],
                   help="run: scan + email deals · scan: scan and print deals, no email · "
                        "test-email: send a sample email to check SMTP settings")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.from_env()

    if args.command == "test-email":
        from .models import Deal, Fare
        sample = Fare("SFO", "NRT", "2027-02-10", "2027-02-20", 389, airline="ZG", stops=0, return_stops=0)
        notify.send([Deal(sample, baseline=980, p25=850, samples=40, is_mistake=False)], cfg)
        print(f"Sent a test email to {cfg.alert_to}")
        return

    conn = db.connect(cfg.db_path)
    fares, deals = scan(conn, cfg)
    print(f"Saw {len(fares)} fares, {len(deals)} new deal(s).")
    for d in deals:
        f = d.fare
        print(f"  {f.origin}->{f.destination} {f.depart_date}/{f.return_date} ${f.price:,.0f} "
              f"({d.pct_off:.0%} off ${d.baseline:,.0f}, n={d.samples}){' MISTAKE?' if d.is_mistake else ''}")
    if args.command == "run" and deals:
        notify.send(deals, cfg)
        for d in deals:
            db.record_alert(conn, d.fare.origin, d.fare.destination, d.fare.depart_month,
                            d.fare.price, d.baseline)
        print(f"Emailed {len(deals)} deal(s) to {cfg.alert_to}")


if __name__ == "__main__":
    main()
