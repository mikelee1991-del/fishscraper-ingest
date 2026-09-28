#!/usr/bin/env python3
"""Extract refuses an empty fleet allowlist and unions JSON with config MMSIs."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import MMSI_ALLOWLIST, MMSI_DENYLIST, MMSI_TO_REPORT_BOAT  # noqa: E402
from extract_ais import (  # noqa: E402
    allowlist_is_empty,
    load_fleet_filter,
    require_fleet_filter,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    assert allowlist_is_empty({}, set(), {}) is True
    try:
        require_fleet_filter({}, set(), {})
    except SystemExit as exc:
        assert exc.code == 2, exc.code
    else:
        raise AssertionError("empty allowlist should refuse")

    try:
        load_fleet_filter(Path("/tmp/does-not-exist-accepted-names.json"))
    except SystemExit as exc:
        assert exc.code == 1 or exc.code is None or isinstance(exc.code, str) or exc.code == 2
    else:
        raise AssertionError("missing accepted_names.json should refuse")

    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "accepted_names.json"
        empty.write_text(
            json.dumps(
                {
                    "accepted_names": {},
                    "mmsi_allowlist": [],
                    "mmsi_to_report_boat": {},
                }
            )
        )
        # Config MMSI maps are non-empty, so the union is still a fleet filter.
        accepted, allow, names = load_fleet_filter(empty)
        assert accepted == {}
        assert 367175860 in allow  # Independence, config only
        assert names[366855060] == "New Del Mar"
        assert not allowlist_is_empty(accepted, allow, names)

        poisoned = Path(tmp) / "poison.json"
        poisoned.write_text(
            json.dumps(
                {
                    "accepted_names": {
                        "REDONDO": "Not A Charter",
                        "CUSTOMBOAT": "Custom Boat",
                    },
                    "mmsi_allowlist": [366760710, 367000111],
                    "mmsi_to_report_boat": {"366855060": "Renamed Del Mar"},
                }
            )
        )
        accepted, allow, names = load_fleet_filter(poisoned)
        assert "REDONDO" not in accepted
        assert accepted["CUSTOMBOAT"] == "Custom Boat"
        assert 366760710 not in allow  # MMSI_DENYLIST tug
        assert 366760710 not in names
        assert 367000111 in allow
        assert names[366855060] == "Renamed Del Mar"
        assert 367175860 in allow

    checked = json.loads((ROOT / "deploy" / "accepted_names.json").read_text())
    assert checked["n_names"] == len(checked["accepted_names"]) == 120
    accepted, allow, names = load_fleet_filter(ROOT / "deploy" / "accepted_names.json")
    assert accepted["FREEDOM"] == "Freedom"
    assert "REDONDO" not in accepted
    expected = (
        set(MMSI_ALLOWLIST)
        | set(MMSI_TO_REPORT_BOAT)
        | {int(m) for m in checked["mmsi_allowlist"]}
        | {int(k) for k in checked["mmsi_to_report_boat"]}
    ) - set(MMSI_DENYLIST)
    assert allow == expected
    assert 367175860 in allow  # Independence is config-only
    assert 338068929 in allow  # Dreamer is on the JSON list
    assert 366760710 not in allow
    assert not allowlist_is_empty(accepted, allow, names)

    print("test_fleet_allowlist: ok")


if __name__ == "__main__":
    main()
