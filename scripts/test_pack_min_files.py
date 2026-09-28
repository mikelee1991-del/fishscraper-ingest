#!/usr/bin/env python3
"""pack --min-files 3500 must refuse a narrow slice and must not write the archive."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ais_daily_archive import (  # noqa: E402
    MIN_ARCHIVE_DAYS,
    count_ais_parquet_members,
    pack_archive,
    unpack_archive,
)


def main() -> None:
    assert MIN_ARCHIVE_DAYS == 3500
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        ais = tmp_p / "ais_daily"
        ais.mkdir()
        for iso in ("2015-01-01", "2015-01-02"):
            (ais / f"ais_{iso}.parquet").write_bytes(b"not-a-real-parquet")
        out = tmp_p / "ais_daily.tar.zst"

        try:
            pack_archive(ais, out, min_files=3500)
        except SystemExit as exc:
            assert exc.code == 2, exc.code
        else:
            raise AssertionError("narrow pack should refuse")
        assert not out.exists()

        meta = pack_archive(ais, out, min_files=2)
        assert meta["n_files"] == 2
        assert out.is_file()
        assert count_ais_parquet_members(out) == 2

        dest = tmp_p / "unpacked"
        info = unpack_archive(out, dest)
        assert info["n_files"] == 2
        assert (dest / "ais_daily" / "ais_2015-01-01.parquet").is_file()
        # Extra day already on disk survives unpack.
        (dest / "ais_daily" / "ais_2015-01-03.parquet").write_bytes(b"extra")
        unpack_archive(out, dest)
        assert (dest / "ais_daily" / "ais_2015-01-03.parquet").is_file()

    print("test_pack_min_files: ok")


if __name__ == "__main__":
    main()
