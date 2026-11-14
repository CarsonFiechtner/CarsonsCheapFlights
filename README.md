# Carson's Cheap Flights

A personal fare tracker in the style of Going (formerly Scott's Cheap Flights). It watches round trips
from **SFO and SJC to anywhere**, learns what each destination normally costs, and emails you when a
fare is far below normal.

## How it works

```
every 4h ─▶ Travelpayouts cached fares ─▶ compare to route's 90-day history ─▶ email ─▶ you click
            (free, SFO/SJC → anywhere,     (median + 25th percentile)           digest   Google Flights
             next 10 months)                                                              to confirm
```

1. **Scan**: for each origin and each of the next 10 months, it fetches the cheapest cached round trips
   to every destination from the [Travelpayouts Data API](https://support.travelpayouts.com/hc/en-us/articles/203956163).
   These are prices Aviasales users found recently. They're free, but they can be stale.
2. **Remember**: it keeps the cheapest price per itinerary per day in SQLite (`data/prices.sqlite`) for
   a year, and a monthly summary of every destination forever (see below).
3. **Detect**: SFO and SJC are treated as one home area. A destination's "normal price" is the
   cheapest price from either airport for each itinerary, each day. A fare is a deal when it's
   **≥40% below that median**, **≥$200 below it**,
   **at or below the 25th percentile**, and the destination has **≥8 prices seen on ≥7 different days**. ≥70% off is flagged as a
   possible mistake fare. Itineraries with more than 2 stops each way are skipped.
4. **Email**: you get one email per run with one deal per destination and month: the cheaper airport,
   with the other listed as "also from SJC: $480" when it's also a deal. Each has a pre-filled Google
   Flights link so you can confirm the price before booking. The same destination and month isn't
   re-sent for 14 days unless the price drops another 10%.

**Expect a quiet first week or two.** A destination needs prices from at least 7 different days before it can trigger a deal.

## Setup

1. **Travelpayouts token (free).** Sign up at travelpayouts.com and join the **Aviasales** program.
   You need the **Aviasales Flight Data API** (cached prices), not the Flight Search API (real-time,
   requires approval and real traffic). On the Tools page that's under **Data API**. The token is a 32-character
   string; Travelpayouts shows it in your profile/API settings ("Learn how to access" on the Data
   API card links to the exact steps).
2. **Gmail app password.** Google Account → Security → 2-Step Verification → App passwords.
3. **GitHub secrets.** Repo → Settings → Secrets and variables → Actions → add `TRAVELPAYOUTS_TOKEN`,
   `SMTP_USER`, `SMTP_PASSWORD` and `ALERT_TO`.
4. **Keep it on the default branch.** Scheduled workflows only run from the repo's default branch. Then open Actions →
   "Scan for flight deals" → *Run workflow* to try it. Tick "Also send a sample deal email" to check
   your email settings.

### Running locally instead

```bash
cp .env.example .env    # fill it in
python -m cheapflights test-email   # check email settings
python -m cheapflights scan         # scan and print deals, no email
python -m cheapflights run          # scan and email deals
```

No third-party packages are needed. Run the tests with `pip install pytest && pytest`.

## Where the data lives

| What | Where | Kept for | If it's lost |
|---|---|---|---|
| Individual prices (`data/prices.sqlite`) | GitHub Actions cache, a new copy saved every run | 1 year (`RAW_KEEP_DAYS`) | Restored from the newest daily backup automatically |
| Daily database backup (gzipped) | Workflow artifacts (`prices-backup-*`) | 7 days | n/a |
| Monthly summaries | `history` branch, `monthly/2026-11.csv` etc. | Forever | n/a: it's in git |

Each monthly CSV has one row per origin, destination and departure month, summarizing every price
seen during that calendar month: count, min, 25th percentile, median and max. A month's file is
written once the month ends and never changes, roughly 6 MB a month at most. These files keep long-term trends (seasonality,
year over year) after the individual prices are deleted. Because individual prices are kept for a
year, you can still look at any recent stretch in full detail.

**If the cache is lost** (GitHub only evicts it after 7 days with no runs, or when the repo passes
10 GB of cache, oldest first), the next run restores the newest backup automatically. That loses at
most a day of prices. If the backups have expired too, it starts fresh and re-learns normal prices,
and the monthly summaries are still reloaded from the `history` branch.

GitHub turns off scheduled workflows in public repos after 60 days without activity. The monthly
commit to `history` keeps the repo active. If it ever gets turned off anyway (GitHub emails you
first), re-enable it from the Actions tab.

## Tuning

All thresholds are environment variables (see `.env.example`). Add them to the `env:` block in
`.github/workflows/scan.yml`, or to `.env` when running locally. Too many emails: raise
`MIN_PCT_OFF` or `MIN_DOLLARS_OFF`. Too few: lower them.

## Ideas for later

- Add OAK as a third origin (`ORIGINS=SFO,SJC,OAK`).
- Weekly summary email of the best fares, even when none count as deals.
- Region filters or a destination wishlist with looser thresholds.
