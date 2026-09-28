#!/usr/bin/env python3
"""Public workflows publish the FishScraper PR #93 Release contract and nothing else."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(rel: str) -> str:
    return (ROOT / rel).read_text()


def main() -> None:
    cadastre = _text(".github/workflows/cadastre-extract.yml")
    fish = _text(".github/workflows/fish-reports.yml")
    readme = _text("README.md")

    assert 'cron: "0 15 * * *"' in cadastre
    assert "timeout-minutes: 360" in cadastre
    assert "--workers 6" in cadastre
    assert "--replace-aisstream" in cadastre
    assert "--min-files 3500" in cadastre
    assert "ais-daily-archive" in cadastre
    assert "ais_daily.tar.zst" in cadastre
    assert "max_extract_days" in cadastre
    assert 'default: "800"' in cadastre
    assert "workflow_dispatch" in cadastre
    assert "actions: write" in cadastre
    assert "extract_ais.py" in cadastre
    assert "aisstream-collector" in cadastre

    assert 'cron: "15 0,5,10,15,20 * * *"' in fish
    assert "fish-reports-latest" in fish
    assert "fish_reports_window.jsonl" in fish
    assert "--publish-window" in fish
    assert "workflow_dispatch" in fish
    assert "21" in fish
    assert "scrape_fish_reports.py" in fish

    for name, text in (("cadastre", cadastre), ("fish", fish)):
        assert "CLOUDFLARE" not in text, name
        assert "AISSTREAM_API_KEY" not in text, name
        assert "build_map_data" not in text, name
        assert "detect_stops" not in text, name
        assert "deploy-docs-worker" not in text, name
        assert "contents: write" in text, name

    assert "ais-daily-archive" in readme
    assert "fish-reports-latest" in readme
    assert "aisstream-collector" in readme
    assert "stays private" in readme.lower() or "stays **private**" in readme
    assert "3500" in readme
    assert "accepted_names.json" in readme

    print("test_workflows: ok")


if __name__ == "__main__":
    main()
