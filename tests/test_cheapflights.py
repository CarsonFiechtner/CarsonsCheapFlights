from datetime import date, timedelta

import pytest

from cheapflights import db, detector, history, notify, places, travelpayouts
from cheapflights.config import Config
from cheapflights.models import Fare


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def fare(price, dest="NRT", depart="2027-02-10", ret="2027-02-20", stops=0, origin="SFO"):
    return Fare(origin, dest, depart, ret, price, stops=stops, return_stops=stops)


@pytest.fixture
def conn():
    return db.connect(":memory:")


@pytest.fixture
def cfg():
    return Config()


def seed_history(conn, prices, dest="NRT", month="2027-03"):
    # Spread prices across distinct week-long itineraries and days, like real scans.
    for i, p in enumerate(prices):
        dep = date.fromisoformat(f"{month}-{(i % 28) + 1:02d}")
        db.insert_fares(conn, [fare(p, dest=dest, depart=dep.isoformat(), ret=(dep + timedelta(days=7)).isoformat())],
                        seen_day=days_ago(i % 60))


def test_parse_response_handles_good_and_bad_items():
    payload = {"success": True, "currency": "usd", "data": [
        {"origin": "SFO", "destination": "NRT", "price": 512, "airline": "ZG",
         "departure_at": "2027-02-10T11:20:00-08:00", "return_at": "2027-02-20T17:00:00+09:00",
         "transfers": 0, "return_transfers": 1},
        {"origin": "SFO", "destination": "LHR"},  # malformed: no price
    ]}
    fares = travelpayouts.parse_response(payload, "SFO", "usd")
    assert len(fares) == 1
    f = fares[0]
    assert (f.destination, f.depart_date, f.return_date, f.price, f.return_stops) == \
        ("NRT", "2027-02-10", "2027-02-20", 512.0, 1)


def test_month_strings_wraps_year():
    assert travelpayouts.month_strings(3, date(2026, 11, 14)) == ["2026-11", "2026-12", "2027-01"]


def test_insert_keeps_lowest_price_per_day(conn):
    db.insert_fares(conn, [fare(900)])
    db.insert_fares(conn, [fare(700)])
    db.insert_fares(conn, [fare(800)])
    assert db.route_prices(conn, "SFO", "NRT", 90) == [700]


def test_prune_drops_old_rows(conn):
    db.insert_fares(conn, [fare(900)], seen_day=days_ago(200))
    db.insert_fares(conn, [fare(800, depart="2027-02-11")])
    assert db.prune(conn, 120) == 1
    assert db.route_prices(conn, "SFO", "NRT", 365) == [800]


def test_big_drop_is_a_deal(conn, cfg):
    seed_history(conn, [950, 1000, 1050, 980, 1020, 990, 1010, 1100, 970, 1030])
    deals = detector.find_deals(conn, [fare(480)], cfg)
    assert len(deals) == 1
    assert deals[0].baseline == pytest.approx(1005)
    assert not deals[0].is_mistake


def test_small_drop_is_not_a_deal(conn, cfg):
    seed_history(conn, [950, 1000, 1050, 980, 1020, 990, 1010, 1100, 970, 1030])
    assert detector.find_deals(conn, [fare(800)], cfg) == []


def test_cheap_route_needs_minimum_dollar_savings(conn, cfg):
    seed_history(conn, [200] * 10, dest="LAX")
    # 45% off but only $90 saved -> not worth an email
    assert detector.find_deals(conn, [fare(110, dest="LAX")], cfg) == []


def test_single_scan_is_not_enough_history(conn, cfg):
    # Lots of prices, but all seen on one day (one scan): not a baseline yet.
    for i in range(20):
        db.insert_fares(conn, [fare(1000, depart=f"2027-03-{i + 1:02d}", ret=f"2027-03-{i + 8:02d}")])
    assert detector.find_deals(conn, [fare(300)], cfg) == []


def test_not_enough_history(conn, cfg):
    seed_history(conn, [1000, 1000, 1000])
    assert detector.find_deals(conn, [fare(300)], cfg) == []


def test_mistake_fare_flagged_and_sorted_first(conn, cfg):
    seed_history(conn, [1000] * 10)
    seed_history(conn, [1500] * 10, dest="CDG")
    deals = detector.find_deals(conn, [fare(450), fare(250, dest="CDG")], cfg)
    assert [d.fare.destination for d in deals] == ["CDG", "NRT"]
    assert deals[0].is_mistake


def test_too_many_stops_and_one_way_ignored(conn, cfg):
    seed_history(conn, [1000] * 10)
    assert detector.find_deals(conn, [fare(300, stops=3), fare(300, ret=None)], cfg) == []


def test_one_deal_per_route_month_keeps_cheapest(conn, cfg):
    seed_history(conn, [1000] * 10)
    deals = detector.find_deals(conn, [fare(500, depart="2027-02-03"), fare(450, depart="2027-02-17")], cfg)
    assert len(deals) == 1 and deals[0].fare.price == 450


def test_no_repeat_alert_unless_price_drops(conn, cfg):
    seed_history(conn, [1000] * 10)
    db.record_alert(conn, "SFO/SJC", "NRT", "2027-02", 500, 1000)
    assert detector.find_deals(conn, [fare(480)], cfg) == []          # only 4% lower
    assert len(detector.find_deals(conn, [fare(440)], cfg)) == 1      # 12% lower


def test_email_rendering():
    f = fare(389)
    url = notify.google_flights_url(f)
    assert url.startswith("https://www.google.com/travel/flights?q=")
    assert "2027-02-10" in url and "NRT" in url
    from cheapflights.models import Deal
    d = Deal(f, baseline=1000, p25=900, samples=20, is_mistake=False)
    assert notify.subject([d]) == "SFO → NRT $389 (61% off)"
    assert notify.subject([d], {"NRT": "Tokyo"}) == "SFO → Tokyo (NRT) $389 (61% off)"
    text, body = notify.render([d])
    assert "$389" in text and "Check on Google Flights" in body


# --- SFO and SJC as one home area -------------------------------------------

# --- seasonality, trip length, problem alerts ----------------------------------

def test_same_month_baseline_beats_all_months(conn, cfg):
    # Summer is expensive, winter cheap. A $500 February fare is normal for
    # February, even though it's half the all-months median.
    seed_history(conn, [1400] * 20, month="2027-07")
    seed_history(conn, [520, 540, 500, 560, 530, 510, 550, 545, 525, 535], month="2027-02")
    assert detector.find_deals(conn, [fare(500)], cfg) == []
    deals = detector.find_deals(conn, [fare(250)], cfg)  # half of February's normal
    assert len(deals) == 1 and deals[0].baseline_month == "2027-02"
    text, _ = notify.render(deals)
    assert "usually ~$532 for Feb trips" in text


def test_falls_back_to_all_months_without_month_history(conn, cfg):
    seed_history(conn, [1000] * 10, month="2027-03")
    deals = detector.find_deals(conn, [fare(450)], cfg)  # February fare, only March history
    assert len(deals) == 1 and deals[0].baseline_month is None


def test_price_cap(conn, cfg):
    seed_history(conn, [2000] * 10, dest="CDG")
    assert detector.find_deals(conn, [fare(550, dest="CDG")], cfg) == []   # 72% off but over $500
    assert len(detector.find_deals(conn, [fare(500, dest="CDG")], cfg)) == 1
    # Expensive fares still count toward the normal price, so they aren't dropped from history.
    assert 2000 in db.route_prices(conn, ["SFO"], "CDG", 90)


def test_trip_length_limits(conn, cfg):
    seed_history(conn, [1000] * 10)
    too_short = fare(300, depart="2027-02-10", ret="2027-02-11")
    too_long = fare(300, depart="2027-02-01", ret="2027-03-15")
    assert detector.find_deals(conn, [too_short, too_long], cfg) == []
    # Odd-length trips don't count toward the normal price either.
    db.insert_fares(conn, [fare(5, depart="2027-03-01", ret="2027-03-02")], seen_day=days_ago(1))
    assert 5 not in db.route_prices(conn, ["SFO"], "NRT", 90, nights=cfg.nights)


def test_problem_email_at_most_daily(conn):
    assert db.claim_problem_email(conn, "2026-11-14")
    assert not db.claim_problem_email(conn, "2026-11-14")
    assert db.claim_problem_email(conn, "2026-11-15")


def test_zero_fares_raises_and_reports(conn, cfg, monkeypatch):
    from cheapflights import __main__ as cli
    cfg.travelpayouts_token = "x"
    monkeypatch.setattr(travelpayouts, "fetch_cheapest_anywhere", lambda *a, **k: [])
    with pytest.raises(cli.TrackerProblem, match="0 fares"):
        cli.scan(conn, cfg)

    def denied(*a, **k):
        raise OSError("HTTP Error 401: Unauthorized")
    monkeypatch.setattr(travelpayouts, "fetch_cheapest_anywhere", denied)
    with pytest.raises(cli.TrackerProblem, match="401"):
        cli.scan(conn, cfg)

    sent = []
    monkeypatch.setattr(notify, "send_problem", lambda cfg, problem: sent.append(problem))
    cli.report_problem(conn, cfg, "broken")
    cli.report_problem(conn, cfg, "still broken")  # same day: no second email
    assert sent == ["broken"]


def test_baseline_pools_origins_taking_cheapest_per_itinerary(conn):
    db.insert_fares(conn, [fare(900), fare(700, origin="SJC"), fare(650, origin="OAK")])
    assert db.route_prices(conn, ["SFO", "SJC"], "NRT", 90) == [700]


def test_same_deal_from_both_airports_is_one_alert(conn, cfg):
    seed_history(conn, [1000] * 10)
    deals = detector.find_deals(conn, [fare(450), fare(480, origin="SJC", depart="2027-02-12")], cfg)
    assert len(deals) == 1
    assert deals[0].fare.origin == "SFO"
    assert [a.origin for a in deals[0].alternatives] == ["SJC"]
    text, body = notify.render(deals)
    assert "also from SJC: $480" in text and "also from SJC" in body


def test_sjc_only_deal_still_found(conn, cfg):
    seed_history(conn, [1000] * 10)
    deals = detector.find_deals(conn, [fare(900), fare(400, origin="SJC")], cfg)
    assert [(d.fare.origin, d.alternatives) for d in deals] == [("SJC", [])]


def test_alert_dedupe_covers_both_airports(conn, cfg):
    seed_history(conn, [1000] * 10)
    db.record_alert(conn, cfg.origin_label(), "NRT", "2027-02", 450, 1000)
    assert detector.find_deals(conn, [fare(440, origin="SJC")], cfg) == []


# --- long-term monthly history ----------------------------------------------

def test_month_helpers():
    assert history.month_of(date(2026, 11, 14)) == "2026-11"
    assert history.month_bounds("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))
    assert history.month_bounds("2028-02") == (date(2028, 2, 1), date(2028, 2, 29))


def test_monthly_rollup_export_and_reload(conn, tmp_path):
    # Pretend "today" is 2026-12-15: November is finished, December isn't.
    for i, (day, p) in enumerate([("2026-11-01", 400), ("2026-11-10", 500),
                                  ("2026-11-20", 600), ("2026-11-30", 700)]):
        db.insert_fares(conn, [fare(p, depart=f"2027-02-{i + 1:02d}")], seen_day=day)
    db.insert_fares(conn, [fare(100, depart="2027-02-20")], seen_day="2026-12-05")  # unfinished month
    today = date(2026, 12, 15)

    assert history.rollup(conn, today) == ["2026-11"]
    assert history.rollup(conn, today) == []  # already done
    row = conn.execute("SELECT * FROM route_monthly").fetchone()
    assert (row["n"], row["min_price"], row["median_price"], row["max_price"]) == (4, 400, 550, 700)

    files = history.export(conn, str(tmp_path))
    assert [f.name for f in files] == ["2026-11.csv"]
    assert history.export(conn, str(tmp_path)) == []  # written once

    fresh = db.connect(":memory:")  # e.g. the cache was wiped
    assert history.load(fresh, str(tmp_path)) == 1
    assert fresh.execute("SELECT median_price FROM route_monthly").fetchone()[0] == 550


# --- city names ---------------------------------------------------------------

def test_place_names_from_cities_and_airports():
    cities = [{"code": "TYO", "name": "Tokyo", "name_translations": {"en": "Tokyo"}},
              {"code": "LAS", "name": "Las Vegas"}]
    airports = [{"code": "NRT", "city_code": "TYO"}, {"code": "HND", "city_code": "TYO"},
                {"code": "LAS", "city_code": "LAS"}, {"code": "XXX", "city_code": "NOPE"}]
    names = places.parse(cities, airports)
    assert names["NRT"] == names["HND"] == names["TYO"] == "Tokyo"
    assert names["LAS"] == "Las Vegas"
    assert "XXX" not in names
    assert places.label("NRT", names) == "Tokyo (NRT)"
    assert places.label("XXX", names) == "XXX"


def test_place_names_cached_and_download_failure_is_harmless(conn, monkeypatch):
    def boom(url):
        raise OSError("offline")
    monkeypatch.setattr(places, "_fetch", boom)
    assert places.load(conn) == {}  # nothing saved yet, download failed: bare codes

    monkeypatch.setattr(places, "_fetch", lambda url: [{"code": "NRT", "name": "Tokyo"}] if "cities" in url else [])
    assert places.load(conn) == {"NRT": "Tokyo"}

    monkeypatch.setattr(places, "_fetch", boom)
    assert places.load(conn) == {"NRT": "Tokyo"}  # fresh enough: no download attempted, saved names used


def test_email_body_shows_city_names():
    d = __import__("cheapflights.models", fromlist=["Deal"]).Deal(
        fare(389), baseline=1000, p25=900, samples=20, is_mistake=False)
    text, body = notify.render([d], {"NRT": "Tokyo"})
    assert "SFO → Tokyo (NRT)" in text and "Tokyo (NRT)" in body
