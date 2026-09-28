#!/usr/bin/env python3
"""Probe NOAA Marine Cadastre bulk AIS for newly published days/years.

Cheap HEAD + Azure blob list only — does not download multi-hundred-MB daily
CSVs. Intended for the public Cadastre extract workflow.

Writes docs/data/ais_cadastre_status.json and exits 0 always unless --fail-on-new
is set (then exit 2 when new Cadastre coverage appears beyond config).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from config import (  # noqa: E402
    AIS_BASE_URL_TMPL,
    AIS_FILENAME,
    DOCS_DATA,
    PILOT_AIS_END,
    USER_AGENT,
)

BLOB_ACCOUNT = "https://noaaocm.blob.core.windows.net/ais"
LIST_YEARS_URL = (
    f"{BLOB_ACCOUNT}?restype=container&comp=list"
    f"&prefix=csv2/csv&delimiter=/&maxresults=50"
)


def http_request(url: str, method: str = "GET", timeout: int = 45) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            body = resp.read() if method.upper() != "HEAD" else b""
            return int(resp.status), headers, body
    except urllib.error.HTTPError as exc:
        headers = {k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])}
        body = exc.read() if method.upper() != "HEAD" else b""
        return int(exc.code), headers, body


def list_cadastre_years() -> list[int]:
    status, _, body = http_request(LIST_YEARS_URL, method="GET")
    if status != 200:
        raise RuntimeError(f"Azure list failed HTTP {status}")
    text = body.decode("utf-8-sig", errors="replace")
    root = ET.fromstring(text)
    years: list[int] = []
    for node in root.iter():
        if node.tag.endswith("Name") and node.text:
            m = re.search(r"csv2/csv(\d{4})/?$", node.text.strip())
            if m:
                years.append(int(m.group(1)))
    return sorted(set(years))


def day_blob_url(day: date) -> str:
    base = AIS_BASE_URL_TMPL.format(year=day.year)
    return f"{base}/{AIS_FILENAME.format(date=day.isoformat())}"


def probe_day(day: date) -> dict:
    url = day_blob_url(day)
    status, headers, _ = http_request(url, method="HEAD")
    length = headers.get("content-length")
    return {
        "date": day.isoformat(),
        "url": url,
        "http_status": status,
        "bytes": int(length) if length and str(length).isdigit() else None,
        "available": status == 200,
    }


def find_latest_day(year: int, *, today: date, max_steps: int = 400) -> dict | None:
    """Walk backward from min(today, Dec 31 of year) until a 200 HEAD."""
    end = min(today, date(year, 12, 31))
    start = date(year, 1, 1)
    cur = end
    steps = 0
    last_probe = None
    while cur >= start and steps < max_steps:
        last_probe = probe_day(cur)
        if last_probe["available"]:
            return last_probe
        cur -= timedelta(days=1)
        steps += 1
    return last_probe


def build_status(*, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    years = list_cadastre_years()
    max_year = max(years) if years else None
    latest = find_latest_day(max_year, today=today) if max_year else None

    next_year = (max_year + 1) if max_year else today.year
    next_year_published = next_year in years
    next_year_first = probe_day(date(next_year, 1, 1)) if not next_year_published else None

    sentinels: list[dict] = []
    if max_year:
        for d in (
            date(max_year, 12, 31),
            date(max_year, 12, 1),
            date(max_year, 6, 15),
        ):
            if d <= today:
                sentinels.append(probe_day(d))
    if next_year_first:
        sentinels.append(next_year_first)

    latest_day = latest["date"] if latest and latest.get("available") else None
    config_end = PILOT_AIS_END
    ahead_of_config = bool(latest_day and latest_day > config_end)

    if next_year_published or (next_year_first and next_year_first["available"]):
        recommendation = (
            f"Cadastre csv{next_year} is available. This public repo extracts "
            f"from {next_year}-01-01 with --replace-aisstream and publishes Release "
            "ais-daily-archive. Private FishScraper rebuilds the map from that asset."
        )
    elif ahead_of_config and latest_day:
        recommendation = (
            f"Cadastre published through {latest_day} but config PILOT_AIS_END={config_end}. "
            f"This repo extracts {config_end} → {latest_day} with --replace-aisstream."
        )
    elif latest_day and latest_day < config_end:
        nxt = (date.fromisoformat(latest_day) + timedelta(days=1)).isoformat()
        recommendation = (
            f"Cadastre published through {latest_day}; config end is {config_end}. "
            f"Keep aisstream-collector for {nxt}→{config_end}. This repo replaces "
            "aisstream-only days with Cadastre (--replace-aisstream) as NOAA publishes them."
        )
    else:
        recommendation = (
            f"No new Cadastre bulk beyond {config_end}. Live fill stays on "
            "aisstream-collector (Release aisstream-live). This repo publishes "
            "ais-daily-archive only when the fleet-filtered archive has at least 3500 days."
        )

    return {
        "title": "Marine Cadastre AIS availability",
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "checked_day_utc": today.isoformat(),
        "blob_years": years,
        "max_blob_year": max_year,
        "latest_published_day": latest_day,
        "latest_probe": latest,
        "next_year": next_year,
        "next_year_published": next_year_published or bool(next_year_first and next_year_first["available"]),
        "config_ais_window": {"start": "see PILOT_AIS_START", "end": config_end},
        "config_ais_end": config_end,
        "ahead_of_config": ahead_of_config,
        "new_coverage": bool(
            ahead_of_config
            or next_year_published
            or (next_year_first and next_year_first["available"])
        ),
        "sentinel_probes": sentinels,
        "recommendation": recommendation,
        "source": BLOB_ACCOUNT,
    }


def coverage_signature(status: dict) -> str:
    return json.dumps(
        {
            "years": status.get("blob_years"),
            "latest": status.get("latest_published_day"),
            "next_year_published": status.get("next_year_published"),
            "config_end": status.get("config_ais_end"),
        },
        sort_keys=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        type=Path,
        default=DOCS_DATA / "ais_cadastre_status.json",
        help="Status JSON path (not committed; the workflow only needs latest_published_day)",
    )
    ap.add_argument(
        "--prev",
        type=Path,
        default=None,
        help="Previous status JSON for change detection (default: --out if present)",
    )
    ap.add_argument(
        "--fail-on-new",
        action="store_true",
        help="Exit 2 when Cadastre coverage is ahead of PILOT_AIS_END / new year",
    )
    ap.add_argument(
        "--github-output",
        type=Path,
        default=None,
        help="Write key=value outputs for GitHub Actions",
    )
    args = ap.parse_args()

    status = build_status()
    prev_path = args.prev or (args.out if args.out.exists() else None)
    prev = None
    if prev_path and prev_path.exists():
        try:
            prev = json.loads(prev_path.read_text())
        except Exception:
            prev = None

    changed = prev is None or coverage_signature(prev) != coverage_signature(status)
    status["coverage_changed"] = changed
    if prev:
        status["previous_latest_published_day"] = prev.get("latest_published_day")
        status["previous_checked_at"] = prev.get("checked_at")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(status, indent=2) + "\n")
    print(
        f"Wrote {args.out} latest={status.get('latest_published_day')} "
        f"years={status.get('blob_years')} new={status.get('new_coverage')} "
        f"changed={changed}"
    )
    print(status["recommendation"])

    if args.github_output:
        lines = [
            f"latest_published_day={status.get('latest_published_day') or ''}",
            f"new_coverage={'true' if status.get('new_coverage') else 'false'}",
            f"coverage_changed={'true' if changed else 'false'}",
            f"next_year_published={'true' if status.get('next_year_published') else 'false'}",
            f"config_ais_end={status.get('config_ais_end') or ''}",
            f"recommendation={status.get('recommendation') or ''}",
        ]
        args.github_output.parent.mkdir(parents=True, exist_ok=True)
        args.github_output.write_text("\n".join(lines) + "\n")

    if args.fail_on_new and status.get("new_coverage"):
        sys.exit(2)


if __name__ == "__main__":
    main()
