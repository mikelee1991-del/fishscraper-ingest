#!/usr/bin/env python3
"""Trailing dock-report window is a full scrape with the consumer row shape."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import TARGET_CITIES  # noqa: E402
from scrape_fish_reports import (  # noqa: E402
    WINDOW_ROW_KEYS,
    days_to_scrape,
    parse_day_html,
    publish_window,
    trailing_window,
    window_row_ok,
)

HTML = """
<html><body>
<div class="panel">
  <h2>San Pedro Fish Counts</h2>
  <table><tbody>
    <tr>
      <td><a href="/boats/freedom.php">Freedom</a><br>
          <a href="/landings/22nd.php">22nd Street Landing</a><br>San Pedro</td>
      <td>12 Anglers<br>1/2 Day</td>
      <td>10 Rockfish, 2 Lingcod, 1 Rockfish released</td>
    </tr>
  </tbody></table>
</div>
<div class="panel">
  <h2>San Diego Fish Counts</h2>
  <table><tbody>
    <tr>
      <td><a href="/boats/other.php">Other Boat</a></td>
      <td>4 Anglers</td>
      <td>3 Yellowtail</td>
    </tr>
  </tbody></table>
</div>
</body></html>
"""


def main() -> None:
    hour15 = datetime(2026, 9, 28, 15, 15, tzinfo=timezone.utc)
    start, end, n = trailing_window(hour15)
    assert n == 21, n
    assert end.isoformat() == "2026-09-28", end
    assert start.isoformat() == "2026-09-08", start
    assert (end - start).days + 1 == 21

    # 00:15 UTC is still the previous Pacific evening (PDT, UTC-7).
    early = datetime(2026, 9, 28, 0, 15, tzinfo=timezone.utc)
    start, end, n = trailing_window(early)
    assert n == 2, n
    assert end.isoformat() == "2026-09-27", end
    assert start.isoformat() == "2026-09-26", start

    midday = datetime(2026, 9, 28, 10, 15, tzinfo=timezone.utc)
    start, end, n = trailing_window(midday)
    assert n == 2 and end.isoformat() == "2026-09-28"

    seen = {"2026-09-27"}
    fresh = days_to_scrape(start, end, seen, fresh=True)
    assert [d.isoformat() for d in fresh] == ["2026-09-27", "2026-09-28"]
    resumed = days_to_scrape(start, end, seen, fresh=False)
    assert [d.isoformat() for d in resumed] == ["2026-09-28"]

    day = end
    rows = parse_day_html(HTML, day)
    assert len(rows) == 1, rows
    row = rows[0]
    assert window_row_ok(row)
    assert row["date"] == day.isoformat()
    assert row["city"] == "San Pedro"
    assert row["city"] in TARGET_CITIES
    assert row["boat_name"] == "Freedom"
    assert row["anglers"] == 12
    assert row["total_fish_kept"] == 12
    assert row["species"][0]["species"] == "Rockfish"
    for key in WINDOW_ROW_KEYS:
        assert key in row, key
    assert all(r["city"] != "San Diego" for r in rows)

    other_day = day - timedelta(days=1)
    older = parse_day_html(HTML.replace("San Pedro Fish Counts", "Long Beach Fish Counts"), other_day)
    assert older and older[0]["date"] == other_day.isoformat()
    assert older[0]["city"] == "Long Beach"

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fish_reports_window.jsonl"
        info = publish_window(rows + older, path)
        assert info["n_rows"] == 2
        assert info["n_dates"] == 2
        assert info["release_tag"] == "fish-reports-latest"
        assert path.name == "fish_reports_window.jsonl"
        loaded = [json.loads(line) for line in path.read_text().splitlines()]
        assert [r["date"] for r in loaded] == sorted(r["date"] for r in loaded)
        assert {r["date"] for r in loaded} == {day.isoformat(), other_day.isoformat()}
        # Republish replaces the file; it does not append a delta.
        publish_window(rows, path)
        loaded = [json.loads(line) for line in path.read_text().splitlines()]
        assert len(loaded) == 1
        assert loaded[0]["boat_name"] == "Freedom"

        try:
            publish_window([{"boat_name": "No Date"}], path)
        except SystemExit:
            pass
        else:
            raise AssertionError("row without a date must not publish")
        # Failed publish must not have replaced the good window.
        loaded = [json.loads(line) for line in path.read_text().splitlines()]
        assert loaded[0]["boat_name"] == "Freedom"

    print("test_fish_report_window: ok")


if __name__ == "__main__":
    main()
