#!/usr/bin/env python3
"""Public workflows: Cadastre and dock Releases, plus aisstream live ingest."""

from __future__ import annotations

import re
import subprocess
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(rel: str) -> str:
    return (ROOT / rel).read_text()


def _heredoc(text: str, marker: str) -> str:
    """Return the dedented body of the first `<< 'MARKER'` heredoc."""
    token = f"<< '{marker}'"
    start = text.index(token)
    lines = text[start:].splitlines()[1:]
    body: list[str] = []
    for line in lines:
        if line.strip() == marker:
            break
        body.append(line)
    else:
        raise AssertionError(f"unterminated heredoc {marker}")
    return textwrap.dedent("\n".join(body) + "\n")


def _check_log_filter(workflow: str) -> None:
    script = _heredoc(workflow, "PY")
    assert "def safe_lines" in script
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "public_log_filter.py"
        path.write_text(script)
        sample = Path(tmp) / "sample.log"
        sample.write_text(
            "\n".join(
                [
                    "Selected 4 MMSIs -> data/processed/vessel_mmsi_registry.json",
                    "Rejected 2 -> data/processed/mmsi_rejected.json",
                    "366977270 'VICTORY' -> ['Victory'] type=30 len=18 score=1000000.0",
                    '{"lat": 33.8455, "lon": -118.3930, "boat_name": "Victory"}',
                    "merged 2026-10-17: 10 rows (dropped 2 dupes) -> data/processed/ais_daily/ais_2026-10-17.parquet",
                    "ais_daily has 4122 days (<3500). Skip rebuild.",
                    "changed=true",
                    "+ private spot row",
                    "Wrote map data -> docs/data",
                ]
            )
            + "\n"
        )
        out = subprocess.check_output(["python3", str(path), str(sample)], text=True)
    kept = out.splitlines()
    assert "Selected 4 MMSIs -> data/processed/vessel_mmsi_registry.json" in kept
    assert "Rejected 2 -> data/processed/mmsi_rejected.json" in kept
    assert any(line.startswith("merged 2026-10-17:") for line in kept)
    assert "changed=true" in kept
    assert "Wrote map data -> docs/data" in kept
    blob = "\n".join(kept)
    assert "366977270" not in blob
    assert "Victory" not in blob
    assert "33.8455" not in blob
    assert "private spot" not in blob


def _check_gh_wrapper(workflow: str) -> None:
    script = _heredoc(workflow, "EOF")
    assert "deploy key is git-over-SSH only" in script
    assert "set -x" not in script
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "gh-real"
        fake.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$GH_FAKE_LOG\"\nexit 0\n"
        )
        fake.chmod(0o755)
        wrap = Path(tmp) / "gh"
        wrap.write_text(script)
        wrap.chmod(0o755)
        log = Path(tmp) / "calls"
        env = {"GH_REAL": str(fake), "GH_FAKE_LOG": str(log), "PATH": "/usr/bin:/bin"}

        listed = subprocess.run(
            [str(wrap), "release", "view", "ais-daily-archive", "-R", "mikelee1991-del/fishscraper-ingest"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert listed.returncode == 0, listed.stderr
        assert "release view ais-daily-archive -R mikelee1991-del/fishscraper-ingest" in log.read_text()

        blocked = subprocess.run(
            [str(wrap), "release", "download", "ais-daily-archive", "-p", "ais_daily.tar.zst", "-D", tmp],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert blocked.returncode == 1
        assert "deploy key is git-over-SSH only" in blocked.stdout
        # The no--R call must not reach the real gh.
        assert log.read_text().count("\n") == 1

        other = subprocess.run(
            [str(wrap), "api", "user"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert other.returncode == 0, other.stderr
        assert "api user" in log.read_text()


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
    assert "actions: write" not in cadastre
    assert "actions/cache" not in cadastre
    assert "ais-daily-v2" not in cadastre
    assert "cache-mode: none" in cadastre
    assert "ais_daily_archive.py unpack" in cadastre
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
    assert "ais-daily-v2" not in readme
    assert "cache-mode: none" in readme
    assert "accepted_names.json" in readme
    assert "FISHSCRAPER_DEPLOY_KEY" in readme
    assert '45 0,5,10,15,20 * * *' in readme
    assert "30 6 * * *" in readme

    ingest = _text(".github/workflows/aisstream-ingest.yml")
    assert 'cron: "45 0,5,10,15,20 * * *"' in ingest
    assert 'cron: "30 6 * * *"' in ingest
    assert "timeout-minutes: 120" in ingest
    assert "workflow_dispatch" in ingest
    assert "scrape_days" in ingest
    assert "private_fish_scrape" in ingest
    assert "force_rebuild" in ingest
    assert 'default: "2"' in ingest
    assert 'default: "false"' in ingest
    assert "repository: mikelee1991-del/FishScraper" in ingest
    assert "ssh-key: ${{ secrets.FISHSCRAPER_DEPLOY_KEY }}" in ingest
    assert "persist-credentials: true" in ingest
    assert "ref: main" in ingest
    assert "INGEST_REPO: mikelee1991-del/fishscraper-ingest" in ingest
    assert "GITHUB_REPOSITORY: mikelee1991-del/FishScraper" in ingest
    assert "contents: read" in ingest
    assert "contents: write" not in ingest
    assert "cache-mode: none" in ingest
    assert "cache-mode: write" not in ingest
    assert "actions/cache" not in ingest
    assert "ais-daily-v2" not in ingest
    assert "group: aisstream-ingest" in ingest
    assert "group: ais-watch" not in ingest
    assert "actions/checkout@v4" in ingest
    assert "restore_ais_daily_release.sh" in ingest
    assert "pull_fish_reports_release.sh" in ingest
    assert "merge_ais_daily.py" in ingest
    assert "refine_registry.py" in ingest
    assert "detect_stops.py" in ingest
    assert "build_map_data.py" in ingest
    assert "build_courses_data.py" in ingest
    assert "<3500" in ingest
    assert 'FISH_REF=main' in ingest
    assert 'git push || git push origin "HEAD:${FISH_REF}"' in ingest
    assert "git reset --soft" in ingest
    assert "git pull --rebase --autostash" in ingest
    assert 'gh release delete-asset "$LIVE_RELEASE" "$(basename "$f")" -R "$LIVE_REPO" --yes || true' in ingest
    assert "deploy-docs-worker" not in ingest
    assert "public_log_filter.py" in ingest

    for banned in (
        "set -x",
        "pull_request",
        "CLOUDFLARE",
        "CLOUDFARE",
        "AISSTREAM_API_KEY",
    ):
        assert banned not in ingest, banned
    secret_refs = set(re.findall(r"secrets\.[A-Z0-9_]+", ingest))
    assert secret_refs == {"secrets.FISHSCRAPER_DEPLOY_KEY", "secrets.GITHUB_TOKEN"}
    assert not re.search(r"\b\d{9}\b", ingest)

    _check_log_filter(ingest)
    _check_gh_wrapper(ingest)

    print("test_workflows: ok")


if __name__ == "__main__":
    main()
