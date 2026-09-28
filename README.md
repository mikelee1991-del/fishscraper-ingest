# fishscraper-ingest

Public Marine Cadastre extract and dock-report scrape for [FishScraper](https://github.com/mikelee1991-del/FishScraper).

FishScraper stays **private**. This repo is **public** so those HTTP jobs spend public Actions minutes instead of the private-repo quota. It does not rebuild the map and it does not deploy the Cloudflare Worker.

## Three repos

| Repo | Visibility | Owns |
|------|------------|------|
| [aisstream-collector](https://github.com/mikelee1991-del/aisstream-collector) | Public | Live aisstream.io websocket. Release **`aisstream-live`**. |
| **fishscraper-ingest** (this repo) | Public | NOAA Marine Cadastre extract and the trailing dock-report scrape. |
| FishScraper | Private | Download these Releases, merge live shards, rebuild stops/map/Catch, deploy the Worker. |

The live socket stays on aisstream-collector. Do not move it here, and do not add `AISSTREAM_API_KEY`, `CLOUDFLARE_API_TOKEN`, or `CLOUDFLARE_ACCOUNT_ID` to this repo. Cadastre and socalfishreports.com are public HTTP.

## Releases

Private FishScraper (PR #93) downloads these assets and ignores a Cadastre archive with fewer than **3500** days, so a catch-up slice cannot replace 2015+ history.

| Tag | Asset | What it is |
|-----|--------|------------|
| `ais-daily-archive` | `ais_daily.tar.zst` | Fleet-filtered SoCal daily parquet. Packed and uploaded only when the tree has **≥3500** `ais_YYYY-MM-DD.parquet` files. |
| `fish-reports-latest` | `fish_reports_window.jsonl` | Every boat row from a **fresh** trailing scrape (the whole window, not a delta). The consumer replaces those calendar dates. |

Rows are limited to the SoCal bbox and the fleet allowlist (`deploy/accepted_names.json` unioned with the MMSI maps in `scripts/config.py`). Extract **refuses to run** if that allowlist is empty. This repo does not publish the national Cadastre.

## Schedules

| Workflow | Cron (UTC) | What it does |
|----------|------------|----------------|
| Cadastre AIS extract | `0 15 * * *` | Probe Cadastre, restore Actions cache, unpack this repo's Release on top (extra days are kept), extract up to **800** days with **6** workers and `--replace-aisstream`, timeout **360** minutes. Upload `ais-daily-archive` only at ≥3500 days. Until then the chunk stays in the `ais-daily-v2-` cache. |
| Dock fish-report window | `15 0,5,10,15,20 * * *` | Fresh scrape. **2** Pacific days, except the **15:xx UTC** run which uses **21** Pacific days. Uploads the whole window every time. |

Both workflows also have **workflow_dispatch**. Cadastre dispatch input `max_extract_days` defaults to 800. Fish-report dispatch input `days` overrides the 21/2 rule when set.

Cadastre cron is 15:00 UTC, before private `ais-watch` at 15:30 UTC. The dock cron is at :15, before private `aisstream-ingest` at :45.

## Sync the fleet when boats change

On private FishScraper:

```bash
python3 scripts/export_accepted_names.py
```

Copy the export and any MMSI map edits into this repo:

```bash
cp ../FishScraper/deploy/aisstream/accepted_names.json deploy/accepted_names.json
```

Also update `scripts/config.py` when `MMSI_ALLOWLIST`, `MMSI_TO_REPORT_BOAT`, or `MMSI_DENYLIST` change. Extract **unions** the JSON with the config maps. Removing a boat from only one file leaves it in the extract. Copy the same JSON to `aisstream-collector/deploy/accepted_names.json` so the live socket tracks the same fleet.

`accepted_names.json` carries:

- `accepted_names` — normalized AIS name → report boat name
- `mmsi_allowlist` — MMSIs to keep
- `mmsi_to_report_boat` — MMSI → report boat name (JSON wins when both files name the same MMSI)

## One-time seed of `ais-daily-archive`

Public Actions cannot read private FishScraper Releases. The private tag `ais-daily-archive` (updated **2026-09-17**) is the fleet-filtered archive FishScraper already packs:

- asset `ais_daily.tar.zst`
- **425,820,488** bytes
- coverage **2015-01-01 → 2026-09-17**, **4122** days (≥3500)
- sha256 `dae89662ae4803670d38f0c25fd6e3da7df379defa847dd1201ffd54770fa259`

A cloud agent that can see FishScraper metadata still could not download that asset with this repo's credentials. Until Mike copies it, this repo's Cadastre job grows the archive in the Actions cache and does not upload a narrow slice. Private watch/ingest keep using FishScraper's own Release until a public asset has ≥3500 days.

From a machine logged in as **mikelee1991-del** (read on FishScraper, write on this repo):

```bash
gh release download ais-daily-archive \
  -R mikelee1991-del/FishScraper \
  -p ais_daily.tar.zst \
  -D /tmp/ais-seed

shasum -a 256 /tmp/ais-seed/ais_daily.tar.zst
# dae89662ae4803670d38f0c25fd6e3da7df379defa847dd1201ffd54770fa259

python3 scripts/ais_daily_archive.py count --archive /tmp/ais-seed/ais_daily.tar.zst
# 4122

gh release create ais-daily-archive \
  /tmp/ais-seed/ais_daily.tar.zst \
  -R mikelee1991-del/fishscraper-ingest \
  --title "AIS daily parquet archive" \
  --notes "$(cat <<'EOF'
Fleet-filtered SoCal ais_daily parquet archive.
Seeded from private FishScraper tag ais-daily-archive (2026-09-17).
Coverage: 2015-01-01 → 2026-09-17, 4122 days.
Do not replace this asset with a slice under 3500 days.
EOF
)"
```

If the tag already exists, upload over it instead of creating it:

```bash
gh release upload ais-daily-archive /tmp/ais-seed/ais_daily.tar.zst \
  -R mikelee1991-del/fishscraper-ingest \
  --clobber
```

After the seed, the next Cadastre run unpacks those 4122 days, extracts only the missing or aisstream-only Cadastre days, and uploads again only if the tree is still ≥3500 days.

## Actions permissions

Workflows request `contents: write` so they can publish Releases. If an upload fails with a token permission error:

1. This repo → **Settings → Actions → General**
2. **Workflow permissions** → **Read and write permissions**
3. Save

No other secrets are required. Enable Actions if GitHub asks on the first run.

## Local checks

```bash
python3 -m pip install -r requirements.txt
python3 scripts/test_pack_min_files.py
python3 scripts/test_fleet_allowlist.py
python3 scripts/test_fish_report_window.py
python3 scripts/test_plan_extract_backfill.py
python3 scripts/test_workflows.py
```
