#!/usr/bin/env python3
"""plan_extract_window ignores live days past Cadastre and still backfills."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ais_daily_archive import plan_extract_window  # noqa: E402


def _touch(ais_dir: Path, *isos: str) -> None:
    for iso in isos:
        (ais_dir / f"ais_{iso}.parquet").write_bytes(b"")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ais = Path(tmp)
        # Recent Cadastre + live 2026 days (the current Release shape).
        # Unreadable bytes classify as unknown, which planning treats as Cadastre.
        _touch(ais, "2024-11-27", "2025-12-31", "2026-08-18", "2026-08-19")
        start, end, reason = plan_extract_window(
            ais_dir=ais,
            target_end="2025-12-31",
            target_start="2015-01-01",
            max_days=200,
        )
        assert reason == "backfill", reason
        assert start == "2015-01-01", start
        assert end == "2015-07-19", end  # 200 days

        start, end, reason = plan_extract_window(
            ais_dir=ais,
            target_end="2025-12-31",
            target_start="2015-01-01",
            max_days=800,
        )
        assert reason == "backfill", reason
        assert start == "2015-01-01", start
        assert end == "2017-03-10", end  # 800 days: 2015-01-01 + 799 days

        start, end, reason = plan_extract_window(
            ais_dir=ais,
            target_end="2025-12-31",
            target_start="2015-01-01",
            max_days=4000,
        )
        assert reason == "backfill", reason
        assert start == "2015-01-01", start
        assert end == "2024-11-26", end

        empty = Path(tmp) / "empty"
        empty.mkdir()
        start, end, reason = plan_extract_window(
            ais_dir=empty,
            target_end="2025-12-31",
            target_start="2015-01-01",
            max_days=800,
        )
        assert reason == "bootstrap", reason
        assert end == "2025-12-31", end

    # An aisstream-tagged day inside the Cadastre window is re-extracted.
    with tempfile.TemporaryDirectory() as tmp:
        ais = Path(tmp)
        path = ais / "ais_2024-06-01.parquet"
        pq.write_table(pa.table({"ais_source": ["aisstream"]}), path)
        start, end, reason = plan_extract_window(
            ais_dir=ais,
            target_end="2024-06-02",
            target_start="2015-01-01",
            max_days=800,
        )
        assert reason == "replace_aisstream", reason
        assert start == "2024-06-01", start
        assert end == "2024-06-01", end

    print("test_plan_extract_backfill: ok")


if __name__ == "__main__":
    main()
