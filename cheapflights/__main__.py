"""Command line: `python -m cheapflights run` scans, finds deals and emails them."""

from __future__ import annotations

import argparse
import logging
import sys

from . import db, detector, history, notify, places, travelpayouts
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
    return fares, deals


def update_history(conn, cfg: Config) -> None:
    """Summarize finished months, save them as CSVs, then drop old individual prices."""
    months = history.rollup(conn)
    files = history.export(conn, cfg.history_dir)
    if months or files:
        log.info("summarized %s; wrote %d CSV file(s) to %s", months, len(files), cfg.history_dir)
    db.prune(conn, max(cfg.raw_keep_days, cfg.history_days, history.MIN_RAW_KEEP_DAYS))


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
        alt = Fare("SJC", "NRT", "2027-02-11", "2027-02-21", 412, airline="ZG", stops=1, return_stops=1)
        names = places.load(db.connect(cfg.db_path))
        notify.send([Deal(sample, baseline=980, p25=850, samples=40, is_mistake=False, alternatives=[alt])],
                    cfg, names)
        print(f"Sent a test email to {cfg.alert_to}")
        return

    conn = db.connect(cfg.db_path)
    # Restore monthly summaries from the CSVs (matters only if the database was lost).
    history.load(conn, cfg.history_dir)
    names = places.load(conn)
    fares, deals = scan(conn, cfg)
    update_history(conn, cfg)
    print(f"Saw {len(fares)} fares, {len(deals)} new deal(s).")
    for d in deals:
        f = d.fare
        print(f"  {f.origin}->{places.label(f.destination, names)} {f.depart_date}/{f.return_date} ${f.price:,.0f} "
              f"({d.pct_off:.0%} off ${d.baseline:,.0f}, n={d.samples}){' MISTAKE?' if d.is_mistake else ''}"
              + "".join(f" | also {a.origin} ${a.price:,.0f}" for a in d.alternatives))
    if args.command == "run" and deals:
        notify.send(deals, cfg, names)
        for d in deals:
            db.record_alert(conn, cfg.origin_label(), d.fare.destination, d.fare.depart_month,
                            d.fare.price, d.baseline)
        print(f"Emailed {len(deals)} deal(s) to {cfg.alert_to}")


if __name__ == "__main__":
    main()
