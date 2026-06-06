# Premier League Goal Scraper

Pulls every Premier League match and every goal scorer for a season using
ESPN's public (undocumented) endpoints

## Quick start

```bash
pip install requests
python premier_league_scraper.py            # defaults to 2024-25 season
```

Output lands in `./output/`:

| File          | Description |
|---------------|-------------|
| `matches.csv` | One row per match: date, teams, score, status, venue, attendance |
| `goals.csv`   | One row per goal: scorer, scoring team, minute, penalty, own goal, assist |
| `matches.json`, `goals.json` | Same data, JSON format |
| `raw/<id>.json` | Cached ESPN response per match. Delete to force a re-fetch. |

## Options

```bash
# Different season (ESPN's convention: 2024 = 2024-25 season)
python premier_league_scraper.py --season 2023        # 2023-24
python premier_league_scraper.py --season 2022        # 2022-23

# Custom date range
python premier_league_scraper.py --start 2024-08-01 --end 2025-06-01

# Custom output folder
python premier_league_scraper.py --out-dir ./pl-2024-25

# Smoke test with first N matches
python premier_league_scraper.py --limit 5
```

## Verified output (2024-25 season)

- **380** matches (20 teams × 38 matchdays)
- **1,115** goals across the season
- **69** penalties, **33** own goals, **804** with a recorded assist
- Top scorer: **Mohamed Salah (29)** — matches the published Golden Boot

## How it works

1. Sweeps the ESPN scoreboard endpoint (`/sports/soccer/eng.1/scoreboard`) week
   by week to enumerate every match in the season.
2. For each match, hits the summary endpoint (`/sports/soccer/eng.1/summary?event=<id>`)
   and reads `header.competitions[0].details[]` — every entry with
   `scoringPlay: true` is a goal, with the scorer in `participants[0].athlete`,
   the assister (when present) in `participants[1].athlete`, plus `penaltyKick`
   and `ownGoal` flags.
3. Caches each summary response on disk so re-runs are free.

## Notes

- ESPN's endpoints are unofficial — they can change without notice. If a future
  run breaks, inspect a single cached file in `raw/` to see the new shape.
- The script is polite: ~0.15s between summary calls, exponential backoff on
  429/5xx. A full season takes ~2.5 minutes from cold cache.
- Re-runs use the cache, so they finish in under 30 seconds.
