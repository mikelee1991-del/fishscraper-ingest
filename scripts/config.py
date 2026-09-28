"""Slim config for public Cadastre extract and dock-report scrape.

Keep the MMSI maps in sync with FishScraper ``scripts/config.py``.
``deploy/accepted_names.json`` is the name list (and a second copy of the
MMSI maps). Extract unions both sources and refuses to run if that
allowlist is empty — this repo must not publish the national Cadastre.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"
DOCS_DATA = ROOT / "docs" / "data"

# Target landings / city sections on socalfishreports.com dock totals.
TARGET_CITIES = {
    "Redondo Beach",
    "San Pedro",
    "Long Beach",
    "Marina Del Rey",
    "Newport Beach",
    "Dana Point",
}

# SoCal coastal bbox used when filtering Marine Cadastre AIS (WGS84).
AIS_BBOX = {
    "min_lon": -119.05,
    "max_lon": -117.45,
    "min_lat": 33.20,
    "max_lat": 34.15,
}

# Marine Cadastre daily AIS (1-minute sample of NAIS broadcasts).
# URL is year-aware: .../csv{YYYY}/ais-YYYY-MM-DD.csv.zst
AIS_BASE_URL_TMPL = "https://noaaocm.blob.core.windows.net/ais/csv2/csv{year}"
AIS_FILENAME = "ais-{date}.csv.zst"  # date = YYYY-MM-DD

FISH_REPORT_URL = "https://www.socalfishreports.com/dock_totals/boats.php"
FISH_REPORT_SOURCE = "https://www.socalfishreports.com/"

# AIS extract window — all published Marine Cadastre daily CSV years.
# The scheduled job plans chunks back to PILOT_AIS_START through the latest
# Cadastre day. PILOT_AIS_END is the last day private FishScraper had packed
# (2026-09-17); the probe compares Cadastre's tip to it. Live days after the
# Cadastre tip stay on aisstream-collector, not this extract.
PILOT_AIS_START = "2015-01-01"
PILOT_AIS_END = "2026-09-17"

# Yearly-shard scrape defaults. The Release publisher uses a trailing window
# instead of this full range (see scrape_fish_reports.trailing_window).
PILOT_REPORT_START = "2005-01-01"
PILOT_REPORT_END = "2026-08-02"

USER_AGENT = (
    "FishScraper-research/0.1 "
    "(+https://github.com/mikelee1991-del/fishscraper-ingest; educational research)"
)
REQUEST_SLEEP_SEC = 0.35

# Normalized AIS vessel_name keys that must never map to a report boat.
ACCEPTED_NAME_DENYLIST = {
    "REDONDO",  # tug / towing vessel; charter AIS broadcast name still unknown
    "MARDIOSA",  # ~96 ft Cabo/San Diego yacht MMSI 368121950; not the LB charter
}

# MMSIs rejected after inspection (same name as a charter, but wrong vessel).
MMSI_DENYLIST = {
    338225409,  # SWEET FREEDOM
    368033280,  # VICTORY recreational
    338189834,  # FREEDOM recreational
    338164131,
    338098628,
    338509385,
    338054072,
    538070070,  # TRITON large yacht (Marshall Islands)
    368215840,  # EL PATRON recreational
    338360469,
    338353534,  # EL DORADO recreational
    338146692,  # DREAMER recreational
    338424198,  # PATRIOT recreational near MDR
    338429048,  # CURRENT — not Dana Wharf charter
    338477409,  # FURY recreational
    366760710,  # REDONDO — Long Beach towing vessel, not the charter
    368121950,  # MARDIOSA — luxury yacht, not the Long Beach charter
}

# Preferred MMSIs when known. Unioned with deploy/accepted_names.json.
MMSI_ALLOWLIST = {
    366855060,  # NEW DEL MAR
    366977270,  # VICTORY
    367621160,  # FREEDOM (San Pedro)
    367550710,  # TRITON (San Pedro sportfisher)
    366977380,  # FREELANCE
    367038000,  # AHRA-AHN
    367034320,  # CITY OF LONG BEACH
    367158550,  # WESTERN PRIDE
    368014440,  # MONTE CARLO
    367655460,  # NATIVE SUN
    367095040,  # ENTERPRISE
    368089620,  # ELDORADO
    367169120,  # TORONADO
    368078070,  # THUNDERBIRD
    366915000,  # EL PATRON
    368269920,  # SPITFIRE
    366849310,  # DANA PRIDE (Dana Point — not San Pedro Pride)
    367175860,  # INDEPENDENCE
    367576030,  # PATRIOT (Newport)
}

# Explicit MMSI → report boat. JSON overlays these names when both exist.
MMSI_TO_REPORT_BOAT = {
    366855060: "New Del Mar",
    366977270: "Victory",
    367621160: "Freedom",
    367550710: "Triton",
    366977380: "Freelance",
    367038000: "Ahra-Ahn",
    367034320: "City of Long Beach",
    367158550: "Western Pride",
    368014440: "Monte Carlo",
    367655460: "Native Sun",
    367095040: "Enterprise",
    368089620: "Eldorado",
    367169120: "Toronado",
    368078070: "Thunderbird",
    366915000: "El Patron",
    368269920: "Spitfire",
    366849310: "Dana Pride",
    368370000: "Apollo",
    338068929: "Dreamer",
    367175860: "Independence",
}
