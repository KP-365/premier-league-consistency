"""
Premier League goal scraper — pulls every match and every goal scorer for a given
PL season using ESPN's public (undocumented) endpoints. No API key needed.

Why ESPN? The endpoints are free, key-less, and return clean JSON with a
per-match `details` array that flags goals with `scoringPlay: true`, the team,
the minute, the scorer (and assist), plus penalty/own-goal flags. Way more
accurate than asking an LLM to "remember" who scored.

ESPN season convention: the 2024-25 season is `season.year = 2024`. So pass
`--season 2024` for last year (the most recently completed PL season as of
May 2026).

Outputs (written to --out-dir, default ./output):
  matches.csv  — one row per match (date, teams, score, status, venue)
  goals.csv    — one row per goal (date, teams, scorer, team, minute, pen/og, assist)
  matches.json — same as matches.csv but JSON
  goals.json   — same as goals.csv but JSON
  raw/         — cached raw JSON per event (lets you re-run without re-hitting ESPN)

Usage:
  python premier_league_scraper.py                       # defaults to 2024-25
  python premier_league_scraper.py --season 2024
  python premier_league_scraper.py --season 2023 --out-dir ./pl-2023-24
  python premier_league_scraper.py --start 2024-08-01 --end 2025-06-01

Requires: requests (pip install requests)
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import requests

# --- constants ---------------------------------------------------------------

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard"
SUMMARY_URL = "https://site.web.api.espn.com/apis/site/v2/sports/soccer/eng.1/summary"

# PL season runs roughly mid-August → late May. We pad both sides to be safe.
DEFAULT_SEASON_BOUNDS: dict[int, tuple[date, date]] = {
    2024: (date(2024, 8, 1), date(2025, 6, 1)),  # 2024-25
    2023: (date(2023, 8, 1), date(2024, 6, 1)),  # 2023-24
    2022: (date(2022, 8, 1), date(2023, 6, 1)),  # 2022-23
    2021: (date(2021, 8, 1), date(2022, 6, 1)),  # 2021-22
    2020: (date(2020, 9, 1), date(2021, 6, 1)),  # 2020-21 (COVID-shifted)
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "application/json",
}

log = logging.getLogger("pl-scraper")


# --- data types --------------------------------------------------------------

@dataclass
class Match:
    event_id: str
    date_utc: str       # ISO 8601
    matchday_date: str  # YYYY-MM-DD (UTC)
    home_team: str
    away_team: str
    home_score: int | None
    away_score: int | None
    status: str
    venue: str
    attendance: int | None


@dataclass
class Goal:
    event_id: str
    matchday_date: str
    home_team: str
    away_team: str
    home_score: int | None
    away_score: int | None
    scoring_team: str
    scorer: str
    scorer_id: str
    assist: str = ""
    assist_id: str = ""
    minute: str = ""
    penalty: bool = False
    own_goal: bool = False


# --- http helpers ------------------------------------------------------------

def _get_json(session: requests.Session, url: str, params: dict[str, Any], *, max_retries: int = 5) -> dict[str, Any]:
    """GET with exponential backoff. ESPN occasionally 429s or 5xx's."""
    delay = 1.0
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            r = session.get(url, params=params, headers=HEADERS, timeout=20)
            if r.status_code == 429:
                log.warning("rate_limited", extra={"url": url, "attempt": attempt})
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:  # ValueError = json decode
            last_err = e
            log.warning("request_failed", extra={"url": url, "attempt": attempt, "err": str(e)})
            time.sleep(delay)
            delay = min(delay * 2, 30)
    raise RuntimeError(f"GET {url} failed after {max_retries} attempts: {last_err}")


# --- scoreboard pass ---------------------------------------------------------

def _daterange_chunks(start: date, end: date, days: int = 7) -> Iterable[tuple[date, date]]:
    """Yield (chunk_start, chunk_end) inclusive windows of `days` days."""
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=days - 1), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def fetch_matches(session: requests.Session, start: date, end: date) -> list[Match]:
    """Walk the scoreboard week-by-week and collect every PL match in the window."""
    matches: list[Match] = []
    seen: set[str] = set()
    for chunk_start, chunk_end in _daterange_chunks(start, end, days=7):
        dates_param = f"{chunk_start:%Y%m%d}-{chunk_end:%Y%m%d}"
        log.info("scoreboard_fetch", extra={"range": dates_param})
        data = _get_json(session, SCOREBOARD_URL, {"dates": dates_param, "limit": 100})
        events = data.get("events", [])
        for ev in events:
            try:
                m = _parse_match(ev)
            except Exception as e:
                log.error("parse_match_failed", extra={"event": ev.get("id"), "err": str(e)})
                continue
            if m.event_id in seen:
                continue
            seen.add(m.event_id)
            matches.append(m)
        time.sleep(0.3)  # be polite
    matches.sort(key=lambda m: m.date_utc)
    return matches


def _parse_match(ev: dict[str, Any]) -> Match:
    comp = ev["competitions"][0]
    home = away = None
    for c in comp["competitors"]:
        if c.get("homeAway") == "home":
            home = c
        elif c.get("homeAway") == "away":
            away = c
    if home is None or away is None:
        raise ValueError("missing home/away competitor")

    def _score(c: dict[str, Any]) -> int | None:
        s = c.get("score")
        if s in (None, ""):
            return None
        try:
            return int(s)
        except (TypeError, ValueError):
            return None

    date_utc = ev["date"]
    matchday = date_utc[:10]
    return Match(
        event_id=str(ev["id"]),
        date_utc=date_utc,
        matchday_date=matchday,
        home_team=home["team"]["displayName"],
        away_team=away["team"]["displayName"],
        home_score=_score(home),
        away_score=_score(away),
        status=comp["status"]["type"]["description"],
        venue=(comp.get("venue") or {}).get("fullName", ""),
        attendance=comp.get("attendance"),
    )


# --- summary pass (goal scorers) --------------------------------------------

def fetch_goals_for_match(
    session: requests.Session,
    match: Match,
    cache_dir: Path,
) -> list[Goal]:
    """Hit the per-event summary endpoint and extract every goal."""
    cache_file = cache_dir / f"{match.event_id}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            data = _get_json(session, SUMMARY_URL, {"event": match.event_id})
            cache_file.write_text(json.dumps(data))
    else:
        data = _get_json(session, SUMMARY_URL, {"event": match.event_id})
        cache_file.write_text(json.dumps(data))

    try:
        details = data["header"]["competitions"][0].get("details", [])
    except (KeyError, IndexError):
        log.warning("no_details", extra={"event": match.event_id})
        return []

    # Resolve team IDs → names from the same competition payload.
    team_lookup: dict[str, str] = {}
    for c in data["header"]["competitions"][0].get("competitors", []):
        team_lookup[str(c["team"]["id"])] = c["team"]["displayName"]

    goals: list[Goal] = []
    for d in details:
        if not d.get("scoringPlay"):
            continue
        team_id = str(d.get("team", {}).get("id", ""))
        scoring_team = team_lookup.get(team_id, d.get("team", {}).get("displayName", "?"))
        participants = d.get("participants") or []
        scorer_name = scorer_id = ""
        assist_name = assist_id = ""
        if participants:
            ath = participants[0].get("athlete", {}) or {}
            scorer_name = ath.get("displayName", "")
            scorer_id = str(ath.get("id", ""))
        if len(participants) > 1:
            ath2 = participants[1].get("athlete", {}) or {}
            assist_name = ath2.get("displayName", "")
            assist_id = str(ath2.get("id", ""))
        minute = (d.get("clock") or {}).get("displayValue", "")

        goals.append(
            Goal(
                event_id=match.event_id,
                matchday_date=match.matchday_date,
                home_team=match.home_team,
                away_team=match.away_team,
                home_score=match.home_score,
                away_score=match.away_score,
                scoring_team=scoring_team,
                scorer=scorer_name,
                scorer_id=scorer_id,
                assist=assist_name,
                assist_id=assist_id,
                minute=minute,
                penalty=bool(d.get("penaltyKick")),
                own_goal=bool(d.get("ownGoal")),
            )
        )
    return goals


# --- writers -----------------------------------------------------------------

def write_csv(rows: list[Any], path: Path, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r) if hasattr(r, "__dataclass_fields__") else r)


def write_json(rows: list[Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(r) if hasattr(r, "__dataclass_fields__") else r for r in rows]
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


# --- main --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--season",
        type=int,
        default=2024,
        help="ESPN season start year. 2024 = 2024-25 season (default).",
    )
    p.add_argument("--start", type=str, help="Override start date YYYY-MM-DD")
    p.add_argument("--end", type=str, help="Override end date YYYY-MM-DD")
    p.add_argument("--out-dir", type=Path, default=Path("./output"), help="Output directory")
    p.add_argument("--limit", type=int, default=0, help="Stop after N matches (0 = all)")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.start and args.end:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date()
    else:
        if args.season not in DEFAULT_SEASON_BOUNDS:
            log.error("unknown season %s, provide --start and --end", args.season)
            return 2
        start, end = DEFAULT_SEASON_BOUNDS[args.season]

    out_dir = args.out_dir.expanduser().resolve()
    cache_dir = out_dir / "raw"
    cache_dir.mkdir(parents=True, exist_ok=True)

    log.info("scrape_start", extra={"start": str(start), "end": str(end), "out": str(out_dir)})

    session = requests.Session()
    t0 = time.time()

    matches = fetch_matches(session, start, end)
    log.info("matches_found", extra={"count": len(matches), "elapsed_s": round(time.time() - t0, 1)})

    if args.limit:
        matches = matches[: args.limit]

    all_goals: list[Goal] = []
    for i, m in enumerate(matches, 1):
        if i % 25 == 0 or i == len(matches):
            log.info(
                "summary_progress",
                extra={"done": i, "total": len(matches), "goals_so_far": len(all_goals)},
            )
        # Skip matches that haven't completed — no goal data yet.
        if m.status not in {"Full Time", "FT", "Final"}:
            log.debug("skip_incomplete", extra={"event": m.event_id, "status": m.status})
            continue
        try:
            all_goals.extend(fetch_goals_for_match(session, m, cache_dir))
        except Exception as e:
            log.error("summary_failed", extra={"event": m.event_id, "err": str(e)})
        time.sleep(0.15)

    write_csv(
        matches,
        out_dir / "matches.csv",
        fieldnames=list(Match.__dataclass_fields__.keys()),
    )
    write_csv(
        all_goals,
        out_dir / "goals.csv",
        fieldnames=list(Goal.__dataclass_fields__.keys()),
    )
    write_json(matches, out_dir / "matches.json")
    write_json(all_goals, out_dir / "goals.json")

    elapsed = round(time.time() - t0, 1)
    print()
    print(f"  Matches:        {len(matches)}")
    print(f"  Goals:          {len(all_goals)}")
    print(f"  Output dir:     {out_dir}")
    print(f"  Files written:  matches.csv, goals.csv, matches.json, goals.json")
    print(f"  Raw cache:      {cache_dir} ({sum(1 for _ in cache_dir.glob('*.json'))} files)")
    print(f"  Elapsed:        {elapsed}s")

    # Top scorer leaderboard preview
    if all_goals:
        leaderboard: dict[str, int] = {}
        for g in all_goals:
            if g.own_goal or not g.scorer:
                continue
            leaderboard[g.scorer] = leaderboard.get(g.scorer, 0) + 1
        top = sorted(leaderboard.items(), key=lambda kv: kv[1], reverse=True)[:15]
        print()
        print("  Top scorers:")
        for name, goals in top:
            print(f"    {goals:>3}  {name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
