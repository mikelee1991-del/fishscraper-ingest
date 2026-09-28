#!/usr/bin/env python3
"""Classify fleet-filtered daily AIS parquet by provenance.

Cadastre bulk extracts are preferred over aisstream live fill. Days without an
``ais_source`` column are treated as legacy Cadastre (pre-tagging). Unreadable
placeholder files are ``unknown`` (also treated as Cadastre-owned for planning).
"""

from __future__ import annotations

from pathlib import Path

# Cadastre-owned for planning / merge blocking.
CADASTRE_OWNED = frozenset({"cadastre", "unknown"})
# Re-extract these when NOAA has published the day.
REPLACEABLE = frozenset({"aisstream", "mixed"})


def classify_day_source(path: Path) -> str:
    """Return cadastre | aisstream | mixed | unknown | missing."""
    if not path.is_file():
        return "missing"
    try:
        import pyarrow.parquet as pq

        schema_names = set(pq.ParquetFile(path).schema.names)
        if "ais_source" not in schema_names:
            return "unknown"
        table = pq.read_table(path, columns=["ais_source"])
        col = table.column(0)
        values = {
            (v.as_py() or "").strip()
            for v in col
            if v.as_py() is not None and str(v.as_py()).strip() != ""
        }
        values.discard("")
        if not values:
            return "unknown"
        if values == {"aisstream"}:
            return "aisstream"
        if values == {"cadastre"}:
            return "cadastre"
        if "aisstream" in values and "cadastre" in values:
            return "mixed"
        if "aisstream" in values:
            return "aisstream"
        if "cadastre" in values:
            return "cadastre"
        return "unknown"
    except Exception:
        return "unknown"


def is_cadastre_owned(source: str) -> bool:
    return source in CADASTRE_OWNED


def should_replace_with_cadastre(source: str) -> bool:
    return source in REPLACEABLE


def should_block_aisstream_merge(source: str) -> bool:
    """Once Cadastre (or legacy) owns a day, live shards must not overwrite it."""
    return source in CADASTRE_OWNED or source == "mixed"
