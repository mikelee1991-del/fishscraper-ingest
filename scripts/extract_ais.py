#!/usr/bin/env python3
"""
Extract Marine Cadastre daily AIS broadcasts for LA-area charter vessels.

Uses DuckDB + httpfs to stream-filter remote .csv.zst files (1-minute NAIS
sample). Only rows matching the SoCal bbox and the fleet allowlist are kept.

The allowlist is ``deploy/accepted_names.json`` (accepted_names,
mmsi_allowlist, mmsi_to_report_boat) unioned with the MMSI maps in
``scripts/config.py``. Extract refuses to run when that union is empty so
this job cannot publish the national Cadastre firehose.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from config import (  # noqa: E402
    ACCEPTED_NAME_DENYLIST,
    AIS_BASE_URL_TMPL,
    AIS_BBOX,
    AIS_FILENAME,
    DATA_PROCESSED,
    MMSI_ALLOWLIST,
    MMSI_DENYLIST,
    MMSI_TO_REPORT_BOAT,
    PILOT_AIS_END,
    PILOT_AIS_START,
)

NAME_NORMALIZE_RE = re.compile(r"[^A-Z0-9]+")
DEFAULT_ACCEPTED = ROOT / "deploy" / "accepted_names.json"


def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def normalize_name(name: str) -> str:
    return NAME_NORMALIZE_RE.sub("", (name or "").upper())


def _coerce_mmsi(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _accepted_names_from_payload(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise SystemExit("accepted names JSON must be an object")
    raw = payload.get("accepted_names") or {}
    if not isinstance(raw, dict):
        raise SystemExit("accepted_names must be an object")
    return {str(key): str(value) for key, value in raw.items()}


def _mmsi_maps_from_payload(payload: dict) -> tuple[set[int], dict[int, str]]:
    allow: set[int] = set()
    names: dict[int, str] = {}
    raw_allow = payload.get("mmsi_allowlist") or []
    if isinstance(raw_allow, list):
        for item in raw_allow:
            mmsi = _coerce_mmsi(item)
            if mmsi is not None:
                allow.add(mmsi)
    raw_names = payload.get("mmsi_to_report_boat") or {}
    if isinstance(raw_names, dict):
        for key, boat in raw_names.items():
            mmsi = _coerce_mmsi(key)
            if mmsi is None:
                continue
            names[mmsi] = str(boat)
            allow.add(mmsi)
    return allow, names


def allowlist_is_empty(
    accepted: dict[str, str],
    mmsi_allow: set[int],
    mmsi_names: dict[int, str],
) -> bool:
    """True when neither a vessel name nor an MMSI would keep a row."""
    return not accepted and not mmsi_allow and not mmsi_names


def require_fleet_filter(
    accepted: dict[str, str],
    mmsi_allow: set[int],
    mmsi_names: dict[int, str],
) -> None:
    if allowlist_is_empty(accepted, mmsi_allow, mmsi_names):
        print(
            "Refusing to extract: fleet allowlist is empty. "
            "Union deploy/accepted_names.json (accepted_names, mmsi_allowlist, "
            "mmsi_to_report_boat) with scripts/config.py MMSI maps before running. "
            "An empty allowlist would publish SoCal or national Cadastre traffic.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def load_fleet_filter(path: Path) -> tuple[dict[str, str], set[int], dict[int, str]]:
    """Union accepted_names.json with scripts/config.py MMSI maps.

    JSON boat names overlay config.py when both name the same MMSI. MMSIs are
    unioned so a boat listed in only one file is still extracted. Denied names
    and MMSIs are removed after the union.
    """
    if not path.exists():
        raise SystemExit(f"accepted names file not found: {path}")
    payload = json.loads(path.read_text())
    accepted = _accepted_names_from_payload(payload)
    extra_allow, extra_names = _mmsi_maps_from_payload(payload if isinstance(payload, dict) else {})

    mmsi_allow = set(MMSI_ALLOWLIST) | set(MMSI_TO_REPORT_BOAT) | extra_allow | set(extra_names)
    mmsi_names = dict(MMSI_TO_REPORT_BOAT)
    mmsi_names.update(extra_names)

    for denied in ACCEPTED_NAME_DENYLIST:
        accepted.pop(normalize_name(denied), None)
    for denied in MMSI_DENYLIST:
        mmsi_allow.discard(int(denied))
        mmsi_names.pop(int(denied), None)

    require_fleet_filter(accepted, mmsi_allow, mmsi_names)
    print(
        f"fleet filter from {path} + scripts/config.py: "
        f"{len(accepted)} accepted names, {len(mmsi_allow | set(mmsi_names))} MMSIs",
        flush=True,
    )
    return accepted, mmsi_allow, mmsi_names


def _duckdb_http():
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    return con


def extract_day(
    day: date,
    accepted: dict[str, str],
    mmsi_allow: set[int],
    mmsi_names: dict[int, str],
    out_dir: Path,
    force: bool,
    con=None,
    *,
    replace_aisstream: bool = False,
) -> dict:
    out_path = out_dir / f"ais_{day.isoformat()}.parquet"
    allow_mmsis = sorted(set(mmsi_allow) | set(mmsi_names))
    if allowlist_is_empty(accepted, set(allow_mmsis), mmsi_names):
        return {
            "date": day.isoformat(),
            "rows": 0,
            "path": None,
            "skipped": False,
            "error": "empty fleet allowlist",
        }

    own_con = con is None
    if own_con:
        con = _duckdb_http()
    assert con is not None
    try:
        if out_path.exists() and not force:
            from ais_day_source import classify_day_source, should_replace_with_cadastre

            src = classify_day_source(out_path)
            if replace_aisstream and should_replace_with_cadastre(src):
                print(
                    f"[info] {day.isoformat()}: replacing {src} day with Cadastre",
                    flush=True,
                )
            else:
                n = con.execute(
                    f"SELECT count(*) FROM read_parquet('{out_path.as_posix()}')"
                ).fetchone()[0]
                return {
                    "date": day.isoformat(),
                    "rows": int(n),
                    "path": str(out_path),
                    "skipped": True,
                }

        url = (
            f"{AIS_BASE_URL_TMPL.format(year=day.year)}/"
            f"{AIS_FILENAME.format(date=day.isoformat())}"
        )
        bbox = AIS_BBOX
        allow_sql = ",".join(str(int(m)) for m in allow_mmsis) if allow_mmsis else "-1"
        # Bbox prefilter only. Fleet names/MMSIs are applied in pandas below;
        # unfiltered bbox rows are never written.
        q = f"""
        SELECT
          mmsi,
          base_date_time,
          longitude,
          latitude,
          sog,
          cog,
          heading,
          vessel_name,
          call_sign,
          vessel_type,
          status,
          length,
          width,
          draft,
          transceiver
        FROM read_csv('{url}', compression='zstd', parallel=true, ignore_errors=true)
        WHERE longitude BETWEEN {bbox['min_lon']} AND {bbox['max_lon']}
          AND latitude BETWEEN {bbox['min_lat']} AND {bbox['max_lat']}
          AND (
            vessel_name IS NOT NULL
            OR mmsi IN ({allow_sql})
          )
        """
        try:
            df = con.execute(q).fetchdf()
        except Exception as exc:
            msg = str(exc)
            print(f"[warn] {day.isoformat()}: AIS fetch failed: {msg[:180]}", file=sys.stderr)
            return {
                "date": day.isoformat(),
                "rows": 0,
                "path": None,
                "skipped": False,
                "error": msg[:300],
            }
        if df.empty:
            if out_path.exists():
                out_path.unlink()
            return {"date": day.isoformat(), "rows": 0, "path": None, "skipped": False}

        norms = df["vessel_name"].fillna("").map(normalize_name)
        name_hit = norms.isin(set(accepted.keys()))
        mmsi_hit = df["mmsi"].isin(set(allow_mmsis))
        fleet_df = df.loc[name_hit | mmsi_hit].copy()
        fleet_df["vessel_name_norm"] = norms.loc[fleet_df.index].values
        report_by_mmsi = {int(k): v for k, v in mmsi_names.items()}
        fleet_df["report_boat_name"] = [
            report_by_mmsi.get(int(m)) or accepted.get(n)
            for m, n in zip(fleet_df["mmsi"], fleet_df["vessel_name_norm"])
        ]
        fleet_df["date"] = day.isoformat()
        fleet_df["ais_source"] = "cadastre"
        if fleet_df.empty:
            if out_path.exists():
                out_path.unlink()
            return {"date": day.isoformat(), "rows": 0, "path": None, "skipped": False}

        fleet_df.to_parquet(out_path, index=False)
        return {
            "date": day.isoformat(),
            "rows": int(len(fleet_df)),
            "path": str(out_path),
            "skipped": False,
        }
    finally:
        if own_con:
            con.close()


def rebuild_mmsi_registry(
    ais_dir: Path,
    out_path: Path,
    accepted: dict[str, str],
) -> "object":
    import duckdb
    import pandas as pd

    files = sorted(ais_dir.glob("ais_*.parquet"))
    if not files:
        return pd.DataFrame()
    con = duckdb.connect()
    glob = (ais_dir / "ais_*.parquet").as_posix()
    df = con.execute(
        f"""
        SELECT mmsi,
               any_value(vessel_name) AS vessel_name,
               any_value(vessel_name_norm) AS vessel_name_norm,
               any_value(report_boat_name) AS report_boat_name,
               any_value(call_sign) AS call_sign,
               count(*) AS n_points,
               min(base_date_time) AS first_seen,
               max(base_date_time) AS last_seen
        FROM read_parquet('{glob}')
        GROUP BY mmsi
        ORDER BY n_points DESC
        """
    ).fetchdf()

    matched = []
    for _, r in df.iterrows():
        norm = r["vessel_name_norm"] or normalize_name(r["vessel_name"])
        report_name = r["report_boat_name"] or accepted.get(norm)
        matched.append(
            {
                "mmsi": int(r["mmsi"]),
                "ais_vessel_name": r["vessel_name"],
                "vessel_name_norm": norm,
                "call_sign": r["call_sign"],
                "report_boat_names": [report_name] if report_name else [],
                "n_points": int(r["n_points"]),
                "first_seen": str(r["first_seen"]),
                "last_seen": str(r["last_seen"]),
                "match_confidence": "high" if report_name else "unmatched_ais_only",
            }
        )
    reg = pd.DataFrame(matched)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    reg.to_json(out_path, orient="records", indent=2)
    return reg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=PILOT_AIS_START)
    ap.add_argument("--end", default=PILOT_AIS_END)
    ap.add_argument(
        "--accepted-names",
        type=Path,
        default=DEFAULT_ACCEPTED,
        help="JSON with accepted_names, mmsi_allowlist, and mmsi_to_report_boat",
    )
    ap.add_argument("--out-dir", type=Path, default=DATA_PROCESSED / "ais_daily")
    ap.add_argument("--force", action="store_true", help="Re-download/filter even if parquet exists")
    ap.add_argument(
        "--replace-aisstream",
        action="store_true",
        help="Force re-extract days that are aisstream-only or mixed (Cadastre preferred)",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=6,
        help="Parallel day downloads (each worker uses its own DuckDB connection)",
    )
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    accepted, mmsi_allow, mmsi_names = load_fleet_filter(args.accepted_names)
    days = list(daterange(start, end))
    print(f"Days to process: {len(days)} (workers={args.workers})")

    summary: list[dict] = []
    print_lock = Lock()
    workers = max(1, int(args.workers))

    def _job(day: date) -> dict:
        info = extract_day(
            day,
            accepted,
            mmsi_allow,
            mmsi_names,
            args.out_dir,
            force=args.force,
            replace_aisstream=args.replace_aisstream,
        )
        with print_lock:
            err = f" err={info['error'][:60]}" if info.get("error") else ""
            print(
                f"{info['date']}: rows={info['rows']} skipped={info.get('skipped')}{err}",
                flush=True,
            )
        return info

    if workers == 1:
        summary = [_job(day) for day in days]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_job, day): day for day in days}
            by_date = {}
            for fut in as_completed(futs):
                info = fut.result()
                by_date[info["date"]] = info
        summary = [by_date[d.isoformat()] for d in days]

    reg_path = DATA_PROCESSED / "vessel_mmsi_registry.json"
    rebuild_mmsi_registry(args.out_dir, reg_path, accepted)
    summary_path = DATA_PROCESSED / "ais_extract_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    n_ok = sum(1 for s in summary if s.get("rows", 0) > 0 or s.get("skipped"))
    n_err = sum(1 for s in summary if s.get("error"))
    print(f"Registry -> {reg_path}")
    print(f"Summary -> {summary_path} (ok/skip days={n_ok}, errors={n_err})")


if __name__ == "__main__":
    main()
