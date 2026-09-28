#!/usr/bin/env python3
"""Scrape daily boat fish counts from socalfishreports.com for target cities.

The GitHub Release publisher writes a fresh trailing window to
``fish_reports_window.jsonl`` (every row in the window, not a delta and not
a resume against older shards). Private FishScraper replaces those calendar
dates from Release tag ``fish-reports-latest``.

Scheduled window (America/Los_Angeles end date):
  UTC hour 15 → 21 days
  any other hour → 2 days
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from config import (  # noqa: E402
    DATA_RAW,
    FISH_REPORT_URL,
    PILOT_REPORT_END,
    PILOT_REPORT_START,
    REQUEST_SLEEP_SEC,
    TARGET_CITIES,
    USER_AGENT,
)
from trips_io import iter_seen_dates  # noqa: E402

ANGLERS_RE = re.compile(r"(\d+)\s*Anglers?", re.I)
COUNT_RE = re.compile(
    r"(\d+)\s+([A-Za-z][A-Za-z0-9'/\- ]+?)(?=(?:,\s*\d+\s+[A-Za-z])|$)",
    re.I,
)
RELEASED_RE = re.compile(r"\breleased\b", re.I)
UP_TO_RE = re.compile(r"\s*\(up to[^)]*\)", re.I)
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PACIFIC = ZoneInfo("America/Los_Angeles")

# Keys private FishScraper merge_fish_report_window.py relies on, plus the
# fields the yearly shards have always carried.
WINDOW_ROW_KEYS = (
    "date",
    "city",
    "boat_name",
    "anglers",
    "trip_type",
    "dock_totals_raw",
    "species",
    "total_fish_kept",
    "source",
)


def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def trailing_window(
    now: datetime | None = None,
    *,
    short_days: int = 2,
    long_days: int = 21,
    long_hour_utc: int = 15,
) -> tuple[date, date, int]:
    """Return (start, end, n_days) for the Release window.

    ``end`` is the current calendar date in America/Los_Angeles. The 15:00 UTC
    scheduled run (cron minute 15) uses ``long_days``; every other UTC hour
    uses ``short_days``. Both ends are inclusive.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    end = now_utc.astimezone(PACIFIC).date()
    n_days = long_days if now_utc.hour == long_hour_utc else short_days
    n_days = max(1, int(n_days))
    start = end - timedelta(days=n_days - 1)
    return start, end, n_days


def days_to_scrape(
    start: date,
    end: date,
    seen: set[str] | None = None,
    *,
    fresh: bool = False,
) -> list[date]:
    """Dates to fetch. ``fresh`` ignores shards already on disk (full window)."""
    seen = seen or set()
    days = list(daterange(start, end))
    if fresh:
        return days
    return [d for d in days if d.isoformat() not in seen]


def parse_species(text: str) -> list[dict]:
    text = UP_TO_RE.sub("", (text or "").strip())
    if not text:
        return []
    out = []
    for m in COUNT_RE.finditer(text):
        count = int(m.group(1))
        name = m.group(2).strip(" ,")
        released = bool(RELEASED_RE.search(name))
        name = RELEASED_RE.sub("", name).strip(" ,")
        if not name:
            continue
        out.append({"species": name, "count": count, "released": released})
    return out


def parse_day_html(html: str, day: date) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows_out: list[dict] = []
    for panel in soup.select("div.panel"):
        h2 = panel.find("h2")
        if not h2:
            continue
        title = h2.get_text(" ", strip=True)
        if not title.endswith("Fish Counts"):
            continue
        city = title.replace("Fish Counts", "").strip()
        if city not in TARGET_CITIES:
            continue
        for tr in panel.select("table tbody tr"):
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            boat_td, trip_td, totals_td = tds[0], tds[1], tds[2]
            boat_link = boat_td.find("a")
            boat_name = boat_link.get_text(strip=True) if boat_link else ""
            boat_href = boat_link.get("href") if boat_link else None
            landing_link = None
            for a in boat_td.find_all("a"):
                href = a.get("href") or ""
                if "/landings/" in href:
                    landing_link = a
                    break
            landing_name = landing_link.get_text(strip=True) if landing_link else ""
            city_line = ""
            bits = list(boat_td.stripped_strings)
            if bits:
                city_line = bits[-1]
            trip_text = trip_td.get_text("\n", strip=True)
            anglers_m = ANGLERS_RE.search(trip_text)
            anglers = int(anglers_m.group(1)) if anglers_m else None
            trip_lines = [ln.strip() for ln in trip_text.split("\n") if ln.strip()]
            trip_type = trip_lines[1] if len(trip_lines) > 1 else (trip_lines[0] if trip_lines else "")
            if anglers_m and trip_type.startswith(anglers_m.group(0)):
                trip_type = trip_type[len(anglers_m.group(0)) :].strip()
            totals_text = totals_td.get_text(" ", strip=True)
            species = parse_species(totals_text)
            kept = [s for s in species if not s["released"]]
            total_kept = sum(s["count"] for s in kept)
            fish_per_person = (total_kept / anglers) if anglers and anglers > 0 else None
            species_per_person = {
                s["species"]: (s["count"] / anglers) for s in kept if anglers and anglers > 0
            }
            rows_out.append(
                {
                    "date": day.isoformat(),
                    "city": city,
                    "boat_name": boat_name,
                    "boat_url": boat_href,
                    "landing_name": landing_name,
                    "city_line": city_line,
                    "anglers": anglers,
                    "trip_type": trip_type,
                    "dock_totals_raw": totals_text,
                    "species": species,
                    "total_fish_kept": total_kept,
                    "fish_per_person": fish_per_person,
                    "species_per_person": species_per_person,
                    "source": FISH_REPORT_URL + f"?date={day.isoformat()}",
                }
            )
    return rows_out


def window_row_ok(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    if not ISO_DATE_RE.match(str(row.get("date") or "")):
        return False
    if row.get("city") not in TARGET_CITIES:
        return False
    if not isinstance(row.get("species"), list):
        return False
    for key in WINDOW_ROW_KEYS:
        if key not in row:
            return False
    return True


def publish_window(rows: list[dict], path: Path) -> dict:
    """Write every row of a fresh scrape. Overwrites the file (not a delta)."""
    bad = [row for row in rows if not window_row_ok(row)]
    if bad:
        raise SystemExit(
            f"Refusing to publish fish_reports_window.jsonl: {len(bad)} row(s) "
            "missing date/city/species shape"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda row: (str(row["date"]), str(row.get("city") or ""), str(row.get("boat_name") or "")))
    lines = [json.dumps(row, ensure_ascii=False) for row in ordered]
    text = ("\n".join(lines) + "\n") if lines else ""
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_text(text)
    tmp.replace(path)
    dates = sorted({str(row["date"]) for row in ordered})
    info = {
        "path": str(path),
        "n_rows": len(ordered),
        "n_dates": len(dates),
        "dates_present": dates,
        "asset": path.name,
        "release_tag": "fish-reports-latest",
    }
    return info


def fetch_day(session: requests.Session, day: date) -> list[dict]:
    url = f"{FISH_REPORT_URL}?date={day.isoformat()}"
    r = session.get(url, timeout=60)
    r.raise_for_status()
    return parse_day_html(r.text, day)


def scrape_days(days: list[date], *, workers: int) -> tuple[list[dict], list[str]]:
    """Fetch each day. Returns (rows, error dates). Does not write shards."""
    write_lock = Lock()
    rows: list[dict] = []
    errors: list[str] = []
    workers = max(1, int(workers))

    def _job(day: date) -> tuple[str, list[dict], str | None]:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html"})
        try:
            found = fetch_day(session, day)
        except Exception as exc:
            time.sleep(REQUEST_SLEEP_SEC * 3)
            return day.isoformat(), [], str(exc)
        time.sleep(REQUEST_SLEEP_SEC)
        return day.isoformat(), found, None

    def _take(day_iso: str, found: list[dict], err: str | None) -> None:
        with write_lock:
            if err:
                errors.append(day_iso)
                print(f"[warn] {day_iso}: {err}", file=sys.stderr, flush=True)
            else:
                rows.extend(found)
                print(f"{day_iso}: {len(found)} trips", file=sys.stderr, flush=True)

    if workers == 1:
        for day in days:
            _take(*_job(day))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_job, day) for day in days]
            for fut in as_completed(futs):
                _take(*fut.result())
    return rows, errors


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default=None, help="Inclusive start YYYY-MM-DD (default: trailing window or PILOT_REPORT_START)")
    ap.add_argument("--end", default=None)
    ap.add_argument(
        "--days",
        type=int,
        default=None,
        help="Trailing Pacific days ending today. Overrides the UTC-hour 21/2 rule.",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=DATA_RAW / "fish_reports" / "by_year",
        help="Directory for trips_YYYY.jsonl shards (ignored with --publish-window)",
    )
    ap.add_argument(
        "--publish-window",
        type=Path,
        default=None,
        help="Write a fresh full-window JSONL (fish_reports_window.jsonl) and do not resume",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel day scrapes (be polite to socalfishreports.com)",
    )
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    window_start, window_end, _window_n = trailing_window(now)
    if args.days is not None:
        end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else window_end
        n_days = max(1, int(args.days))
        start = (
            datetime.strptime(args.start, "%Y-%m-%d").date()
            if args.start
            else end - timedelta(days=n_days - 1)
        )
    elif args.publish_window is not None:
        start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else window_start
        end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else window_end
        n_days = (end - start).days + 1
    else:
        start = datetime.strptime(args.start or PILOT_REPORT_START, "%Y-%m-%d").date()
        end = datetime.strptime(args.end or PILOT_REPORT_END, "%Y-%m-%d").date()
        n_days = (end - start).days + 1

    fresh = args.publish_window is not None
    if fresh:
        days = days_to_scrape(start, end, seen=set(), fresh=True)
        print(
            f"Fresh window {start.isoformat()} → {end.isoformat()} "
            f"({n_days} days, utc_hour={now.hour}, workers={args.workers})",
            file=sys.stderr,
            flush=True,
        )
        rows, errors = scrape_days(days, workers=args.workers)
        if errors and not rows:
            print(
                f"Refusing to publish an empty window: all {len(errors)} days failed",
                file=sys.stderr,
            )
            sys.exit(1)
        info = publish_window(rows, args.publish_window)
        info.update(
            {
                "requested_start": start.isoformat(),
                "requested_end": end.isoformat(),
                "requested_days": n_days,
                "utc_hour": now.hour,
                "error_days": sorted(errors),
            }
        )
        summary_path = args.publish_window.with_name("window_summary.json")
        summary_path.write_text(json.dumps(info, indent=2) + "\n")
        print(json.dumps(info))
        if errors:
            print(
                f"Published {info['n_rows']} rows but {len(errors)} day(s) failed: {errors}",
                file=sys.stderr,
            )
            sys.exit(1)
        return

    args.out_dir.mkdir(parents=True, exist_ok=True)
    seen_dates = iter_seen_dates(args.out_dir)
    legacy = DATA_RAW / "fish_reports" / "trips.jsonl"
    if legacy.exists():
        seen_dates |= iter_seen_dates(legacy)
    days = days_to_scrape(start, end, seen_dates, fresh=False)
    print(
        f"Days to scrape: {len(days)} (already have {len(seen_dates)}; "
        f"workers={args.workers})"
    )

    write_lock = Lock()
    workers = max(1, int(args.workers))

    def _job(day: date) -> tuple[str, int, str | None]:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html"})
        try:
            rows = fetch_day(session, day)
        except Exception as e:
            time.sleep(REQUEST_SLEEP_SEC * 3)
            return day.isoformat(), 0, str(e)
        shard = args.out_dir / f"trips_{day.year}.jsonl"
        with write_lock:
            with shard.open("a") as out:
                for row in rows:
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
        time.sleep(REQUEST_SLEEP_SEC)
        return day.isoformat(), len(rows), None

    row_counter = {"n": 0}
    err_counter = {"n": 0}

    def _run(day: date) -> None:
        d, n, err = _job(day)
        if err:
            err_counter["n"] += 1
            print(f"[warn] {d}: {err}", file=sys.stderr, flush=True)
        else:
            row_counter["n"] += n
            print(f"{d}: {n} trips", flush=True)

    if workers == 1:
        for day in days:
            _run(day)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_run, day) for day in days]
            for fut in as_completed(futs):
                fut.result()

    print(
        f"Wrote/updated {args.out_dir} (+{row_counter['n']} new rows; "
        f"{err_counter['n']} day errors)"
    )


if __name__ == "__main__":
    main()
