#!/usr/bin/env python3
"""Pack / unpack the fleet-filtered ais_daily parquet archive.

The archive is ~0.5–0.7 GB — too large for normal git history (and free Git LFS
bandwidth is too tight for daily CI checkouts). Persist it as a GitHub Release
asset + Actions cache instead; only docs/ outputs are committed.

Release contract (private FishScraper consumes this):
  tag ais-daily-archive / asset ais_daily.tar.zst
Do not pack or upload until at least 3500 daily parquet files exist.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from config import DATA_PROCESSED, PILOT_AIS_END, PILOT_AIS_START  # noqa: E402

DEFAULT_AIS_DIR = DATA_PROCESSED / "ais_daily"
DEFAULT_ARCHIVE = DATA_PROCESSED / "ais_daily.tar.zst"
RELEASE_TAG = "ais-daily-archive"
RELEASE_ASSET = "ais_daily.tar.zst"
# FishScraper restores this asset only when it has at least this many days.
MIN_ARCHIVE_DAYS = 3500


def parse_iso(iso: str) -> date:
    return datetime.strptime(iso, "%Y-%m-%d").date()


def local_ais_dates(ais_dir: Path) -> list[date]:
    dates: list[date] = []
    if not ais_dir.is_dir():
        return dates
    for p in ais_dir.glob("ais_*.parquet"):
        m = re.match(r"ais_(\d{4}-\d{2}-\d{2})\.parquet$", p.name)
        if m:
            dates.append(parse_iso(m.group(1)))
    return sorted(dates)


def local_range(ais_dir: Path) -> tuple[str | None, str | None, int]:
    dates = local_ais_dates(ais_dir)
    if not dates:
        return None, None, 0
    return dates[0].isoformat(), dates[-1].isoformat(), len(dates)


def pack_archive(ais_dir: Path, out_path: Path, min_files: int = 0) -> dict:
    ais_dir = ais_dir.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(ais_dir.glob("ais_*.parquet"))
    if not files:
        raise SystemExit(f"No parquet files under {ais_dir}")
    if min_files and len(files) < min_files:
        start, end, n = local_range(ais_dir)
        print(
            json.dumps(
                {
                    "refused": True,
                    "reason": "narrower_than_min_files",
                    "n_files": n,
                    "min_files": min_files,
                    "start": start,
                    "end": end,
                }
            )
        )
        raise SystemExit(2)

    use_zstd = True
    try:
        import zstandard  # noqa: F401
    except Exception:
        use_zstd = False

    if use_zstd and out_path.suffixes[-2:] != [".tar", ".zst"]:
        out_path = (
            out_path.with_suffix("").with_suffix(".tar.zst")
            if out_path.suffix == ".zst"
            else out_path
        )

    if use_zstd:
        import zstandard as zstd

        tmp = out_path.with_suffix(out_path.suffix + ".partial")
        cctx = zstd.ZstdCompressor(level=6, threads=0)
        with tmp.open("wb") as fh, cctx.stream_writer(fh) as compressor:
            with tarfile.open(fileobj=compressor, mode="w|") as tar:
                for f in files:
                    tar.add(f, arcname=f"ais_daily/{f.name}")
        tmp.replace(out_path)
    else:
        out_path = (
            out_path.with_suffix("").with_suffix(".tar.gz")
            if "zst" in out_path.name
            else out_path
        )
        with tarfile.open(out_path, "w:gz") as tar:
            for f in files:
                tar.add(f, arcname=f"ais_daily/{f.name}")

    start, end, n = local_range(ais_dir)
    meta = {
        "archive": str(out_path),
        "bytes": out_path.stat().st_size,
        "n_files": n,
        "start": start,
        "end": end,
        "release_tag": RELEASE_TAG,
        "release_asset": out_path.name,
    }
    meta_path = out_path.with_suffix(out_path.suffix + ".json")
    if out_path.name.endswith(".tar.zst"):
        meta_path = Path(str(out_path) + ".json")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps(meta, indent=2))
    return meta


def unpack_archive(archive: Path, dest_parent: Path) -> dict:
    """Unpack into dest_parent/ais_daily/ (parquet files).

    Existing days in dest are kept. Members in the archive overwrite the
    same path. Extra days already on disk are not deleted.
    """
    dest_parent.mkdir(parents=True, exist_ok=True)
    ais_dir = dest_parent / "ais_daily"
    ais_dir.mkdir(parents=True, exist_ok=True)

    if archive.name.endswith(".tar.zst") or archive.suffixes[-2:] == [".tar", ".zst"]:
        import zstandard as zstd

        dctx = zstd.ZstdDecompressor()
        with archive.open("rb") as fh, dctx.stream_reader(fh) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                tar.extractall(path=dest_parent, filter="data")
    else:
        with tarfile.open(archive, "r:*") as tar:
            tar.extractall(path=dest_parent, filter="data")

    if not any(ais_dir.glob("ais_*.parquet")):
        for p in dest_parent.glob("ais_*.parquet"):
            p.rename(ais_dir / p.name)

    start, end, n = local_range(ais_dir)
    info = {"ais_dir": str(ais_dir), "n_files": n, "start": start, "end": end}
    print(json.dumps(info, indent=2))
    return info


def _parquet_member(name: str) -> bool:
    return re.match(r"ais_\d{4}-\d{2}-\d{2}\.parquet$", Path(name).name) is not None


def iter_archive_member_names(archive: Path):
    """Yield tar member names. Supports .tar.zst and tarfile's other modes."""
    if archive.name.endswith(".tar.zst") or archive.suffixes[-2:] == [".tar", ".zst"]:
        import zstandard as zstd

        dctx = zstd.ZstdDecompressor()
        with archive.open("rb") as fh, dctx.stream_reader(fh) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                for member in tar:
                    yield member.name
        return
    with tarfile.open(archive, "r:*") as tar:
        for member in tar:
            yield member.name


def count_ais_parquet_members(archive: Path) -> int:
    """Count fleet-day parquet members without extracting the archive."""
    return sum(1 for name in iter_archive_member_names(archive) if _parquet_member(name))


def bump_pilot_ais_end(new_end: str, config_path: Path | None = None) -> bool:
    """Rewrite PILOT_AIS_END in scripts/config.py. Returns True if changed."""
    config_path = config_path or (ROOT / "scripts" / "config.py")
    text = config_path.read_text()
    pattern = r'PILOT_AIS_END = "[0-9]{4}-[0-9]{2}-[0-9]{2}"'
    repl = f'PILOT_AIS_END = "{new_end}"'
    new_text, n = re.subn(pattern, repl, text, count=1)
    if n != 1:
        raise SystemExit(f"Could not rewrite PILOT_AIS_END in {config_path}")
    if new_text == text:
        return False
    config_path.write_text(new_text)
    print(f"Updated PILOT_AIS_END -> {new_end}")
    return True


def plan_extract_window(
    *,
    ais_dir: Path,
    target_end: str,
    target_start: str = PILOT_AIS_START,
    max_days: int = 200,
) -> tuple[str | None, str | None, str]:
    """Return (start, end, reason) for the next extract_ais window, or (None, None, reason).

    Cadastre always wins over aisstream for days NOAA has published
    (``target_end`` = latest Cadastre day). Live aisstream days after that are
    ignored so they cannot block backfill. Local aisstream-only (or mixed) days
    inside the Cadastre window are treated as missing so they get force-replaced.
    """
    from ais_day_source import classify_day_source, is_cadastre_owned, should_replace_with_cadastre

    target_end_d = parse_iso(target_end)
    target_start_d = parse_iso(target_start)
    dates = local_ais_dates(ais_dir)

    def day_path(d: date) -> Path:
        return ais_dir / f"ais_{d.isoformat()}.parquet"

    cadastre_owned: set[date] = set()
    replaceable: set[date] = set()
    for d in dates:
        if not (target_start_d <= d <= target_end_d):
            continue
        src = classify_day_source(day_path(d))
        if is_cadastre_owned(src):
            cadastre_owned.add(d)
        elif should_replace_with_cadastre(src):
            replaceable.add(d)

    if not dates and not cadastre_owned:
        end = target_end_d
        start = max(target_start_d, end - timedelta(days=max_days - 1))
        return start.isoformat(), end.isoformat(), "bootstrap"

    if replaceable:
        first = min(replaceable)
        end = first
        for nxt in sorted(replaceable):
            if nxt > end + timedelta(days=1):
                break
            if (nxt - first).days + 1 > max_days:
                break
            end = nxt
        return first.isoformat(), end.isoformat(), "replace_aisstream"

    cadastre_have = sorted(cadastre_owned)

    if cadastre_have:
        last_cadastre = cadastre_have[-1]
        if last_cadastre < target_end_d:
            start = last_cadastre + timedelta(days=1)
            end = min(target_end_d, start + timedelta(days=max_days - 1))
            return start.isoformat(), end.isoformat(), "forward"
    elif target_start_d <= target_end_d:
        end = target_end_d
        start = max(target_start_d, end - timedelta(days=max_days - 1))
        return start.isoformat(), end.isoformat(), "bootstrap"

    missing: list[date] = []
    d = target_start_d
    while d <= target_end_d:
        if d not in cadastre_owned:
            missing.append(d)
        d += timedelta(days=1)
    if not missing:
        return None, None, "up_to_date"

    start = missing[0]
    end = start
    for nxt in missing[1:]:
        if nxt != end + timedelta(days=1):
            break
        if (nxt - start).days + 1 > max_days:
            break
        end = nxt
    return start.isoformat(), end.isoformat(), "backfill"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_pack = sub.add_parser("pack", help="Create ais_daily.tar.zst from parquet dir")
    p_pack.add_argument("--ais-dir", type=Path, default=DEFAULT_AIS_DIR)
    p_pack.add_argument("--out", type=Path, default=DEFAULT_ARCHIVE)
    p_pack.add_argument(
        "--min-files",
        type=int,
        default=0,
        help=(
            "Refuse to pack if fewer parquet days exist "
            f"(Release uploads pass {MIN_ARCHIVE_DAYS} so a narrow slice cannot clobber history)"
        ),
    )

    p_unpack = sub.add_parser("unpack", help="Extract archive into data/processed/")
    p_unpack.add_argument("--archive", type=Path, required=True)
    p_unpack.add_argument("--dest", type=Path, default=DATA_PROCESSED)

    p_range = sub.add_parser("range", help="Print local ais_daily date coverage")
    p_range.add_argument("--ais-dir", type=Path, default=DEFAULT_AIS_DIR)
    p_range.add_argument("--github-output", type=Path, default=None)

    p_plan = sub.add_parser("plan", help="Plan next extract window")
    p_plan.add_argument("--ais-dir", type=Path, default=DEFAULT_AIS_DIR)
    p_plan.add_argument("--target-end", required=True)
    p_plan.add_argument("--target-start", default=PILOT_AIS_START)
    p_plan.add_argument("--max-days", type=int, default=200)
    p_plan.add_argument("--github-output", type=Path, default=None)

    p_bump = sub.add_parser("bump-config", help="Set PILOT_AIS_END in config.py")
    p_bump.add_argument("--end", required=True)

    p_count = sub.add_parser("count", help="Count ais_YYYY-MM-DD.parquet members in an archive")
    p_count.add_argument("--archive", type=Path, required=True)

    args = ap.parse_args()

    if args.cmd == "pack":
        pack_archive(args.ais_dir, args.out, min_files=args.min_files)
    elif args.cmd == "unpack":
        unpack_archive(args.archive, args.dest)
    elif args.cmd == "range":
        start, end, n = local_range(args.ais_dir)
        print(json.dumps({"start": start, "end": end, "n_files": n}))
        if args.github_output:
            args.github_output.write_text(
                f"local_start={start or ''}\n"
                f"local_end={end or ''}\n"
                f"local_n={n}\n"
            )
    elif args.cmd == "plan":
        start, end, reason = plan_extract_window(
            ais_dir=args.ais_dir,
            target_end=args.target_end,
            target_start=args.target_start,
            max_days=args.max_days,
        )
        print(json.dumps({"start": start, "end": end, "reason": reason, "config_end": PILOT_AIS_END}))
        if args.github_output:
            args.github_output.write_text(
                f"extract_start={start or ''}\n"
                f"extract_end={end or ''}\n"
                f"extract_reason={reason}\n"
                f"need_extract={'true' if start and end else 'false'}\n"
            )
    elif args.cmd == "bump-config":
        changed = bump_pilot_ais_end(args.end)
        print(json.dumps({"changed": changed, "end": args.end}))
    elif args.cmd == "count":
        print(count_ais_parquet_members(args.archive))


if __name__ == "__main__":
    main()
