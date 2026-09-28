"""Load / discover fish-report trip JSONL files (yearly shards or single file)."""

from __future__ import annotations

import json
from pathlib import Path


def trip_jsonl_paths(trips_arg: Path) -> list[Path]:
    """Resolve a trips path to one or more JSONL files.

    Accepts:
      - a single *.jsonl file
      - a directory containing trips_YYYY.jsonl (and/or trips.jsonl)
    """
    if trips_arg.is_file():
        return [trips_arg]
    if trips_arg.is_dir():
        yearly = sorted(trips_arg.glob("trips_????.jsonl"))
        if yearly:
            return yearly
        combined = trips_arg / "trips.jsonl"
        if combined.exists():
            return [combined]
    # Default layout used by the archive scrape.
    by_year = trips_arg if trips_arg.name == "by_year" else trips_arg.parent / "by_year"
    if by_year.is_dir():
        yearly = sorted(by_year.glob("trips_????.jsonl"))
        if yearly:
            return yearly
    if trips_arg.exists():
        return [trips_arg]
    return []


def load_trips(trips_arg: Path) -> list[dict]:
    rows: list[dict] = []
    for path in trip_jsonl_paths(trips_arg):
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
    return rows


def iter_seen_dates(trips_arg: Path) -> set[str]:
    seen: set[str] = set()
    for path in trip_jsonl_paths(trips_arg):
        with path.open() as f:
            for line in f:
                try:
                    seen.add(json.loads(line)["date"])
                except Exception:
                    continue
    return seen
