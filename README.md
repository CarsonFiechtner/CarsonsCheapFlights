# Carson's Cheap Flights

A personal fare tracker in the style of Going (formerly Scott's Cheap Flights). It watches round trips
from **SFO and SJC to anywhere**, learns what each route normally costs, and emails you when a fare is
far below normal.

## How it works

```
every 4h ─▶ Travelpayouts cached fares ─▶ compare to route's 90-day history ─▶ email ─▶ you click
            (free, SFO/SJC → anywhere,     (median + 25th percentile)           digest   Google Flights
             next 10 months)                                                              to confirm
```

1. **Scan**: for each origin and each of the next 10 months, it fetches the cheapest cached round trips
   to every destination from the [Travelpayouts Data API](https://support.travelpayouts.com/hc/en-us/articles/203956163).
   These are prices Aviasales users found recently. They're free, but they can be stale.
2. **Remember**: it keeps the cheapest price per itinerary per day in SQLite (`data/prices.sqlite`).
3. **Detect**: a fare is a deal when it's **≥40% below the route's median**, **≥$150 below it**,
   **at or below the 25th percentile**, and the route has **≥8 data points**. ≥70% off is flagged as a
   possible mistake fare. Itineraries with more than 2 stops each way are skipped.
4. **Email**: you get one email per run with the best deal per route and month, each with a
   pre-filled Google Flights link so you can confirm the price before booking. The same deal isn't
   re-sent for 14 days unless the price drops another 10%.

**Expect a quiet first week or two.** Most routes need about 8 data points before they can trigger a deal.

## Setup

1. **Travelpayouts token (free).** Sign up at travelpayouts.com, join the Aviasales program, and copy
   your API token from your profile.
2. **Gmail app password.** Google Account → Security → 2-Step Verification → App passwords.
3. **GitHub secrets.** Repo → Settings → Secrets and variables → Actions → add `TRAVELPAYOUTS_TOKEN`,
   `SMTP_USER`, `SMTP_PASSWORD` and `ALERT_TO`.
4. **Merge to the default branch.** Scheduled workflows only run from there. Then open Actions →
   "Scan for flight deals" → *Run workflow* to try it.

### Running locally instead

```bash
cp .env.example .env    # fill it in
python -m cheapflights test-email   # check email settings
python -m cheapflights scan         # scan and print deals, no email
python -m cheapflights run          # scan and email deals
```

No third-party packages are needed. Run the tests with `pip install pytest && pytest`.

## Where the price history lives

On GitHub Actions the SQLite file is kept in the Actions cache: each run restores the newest copy and
saves a new one. A copy is also uploaded as a workflow artifact weekly and on every manual run, kept
for 30 days. If the cache is ever lost, download that artifact into `data/prices.sqlite`. Otherwise
the tracker simply re-learns prices over a week or two.

## Tuning

All thresholds are environment variables (see `.env.example`). Add them to the `env:` block in
`.github/workflows/scan.yml`, or to `.env` when running locally. Too many emails: raise
`MIN_PCT_OFF` or `MIN_DOLLARS_OFF`. Too few: lower them.

## Ideas for later

- Add OAK as a third origin (`ORIGINS=SFO,SJC,OAK`).
- Treat "same destination city, any SF Bay airport" as one deal.
- Weekly summary email of the best fares, even when none count as deals.
- Region filters or a destination wishlist with looser thresholds.
