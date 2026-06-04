"""
location_reference.py
=====================
Philippine administrative location reference for PAGASA weather data extractors.

Loads region, province, alias, and extra-location data from locations.json
(same directory) and exposes lookup functions and an unresolved-location logger.

Public API
----------
is_known(name)               → bool       True if name is in the reference data
canonical(name)              → str        Primary name for a known location, else original
region_of(province)          → str        Region name for a province, else ""
provinces_of(region)         → list[str]  Province list for a region name or alias
all_regions()                → list[str]  All primary region names
all_provinces()              → list[str]  All province names across all regions
log_unresolved(name, source)             Write unrecognised name to sidecar file

The sidecar file (unresolved_locations.json, same directory) is shared across
all PAGASA extractors. Each extractor identifies itself via the 'source' argument
(e.g. "hro", "tcws").
"""

import json
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List

# ---------------------------------------------------------------------------
# Paths — resolved relative to this file so imports work from any working dir
# ---------------------------------------------------------------------------

_HERE            = os.path.dirname(os.path.abspath(__file__))
_LOCATIONS_PATH  = os.path.join(_HERE, "locations.json")
_UNRESOLVED_PATH = os.path.join(_HERE, "unresolved_locations.json")

_PST = timezone(timedelta(hours=8))   # Philippine Standard Time (UTC+8)


# ---------------------------------------------------------------------------
# Load and index locations.json
# ---------------------------------------------------------------------------

def _load() -> dict:
    """Load locations.json; raise FileNotFoundError with a helpful message if absent."""
    if not os.path.exists(_LOCATIONS_PATH):
        raise FileNotFoundError(
            f"locations.json not found at {_LOCATIONS_PATH}. "
            "Ensure it is in the same directory as location_reference.py."
        )
    with open(_LOCATIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _build_lookups(data: dict):
    """
    Build fast lowercase lookup structures from loaded JSON.

    Returns:
        province_to_region : {province_lower: region_name}
        all_known_names    : {any_name_lower: canonical_name}
                             covers region names, aliases, provinces, extra locations
    """
    province_to_region: Dict[str, str] = {}
    all_known_names:    Dict[str, str] = {}

    for region in data.get("regions", []):
        rname = region["name"]

        # Primary region name
        all_known_names[rname.lower()] = rname

        # All aliases resolve to primary region name
        for alias in region.get("aliases", []):
            all_known_names[alias.lower()] = rname

        # Provinces map to their region; also registered as known names
        for province in region.get("provinces", []):
            province_to_region[province.lower()] = rname
            all_known_names[province.lower()]     = province

    # Extra locations: recognised but not mapped to a region
    for loc in data.get("extra_locations", []):
        all_known_names[loc.lower()] = loc

    return province_to_region, all_known_names


# Load once at import time
_DATA               = _load()
_REGIONS_LIST       = _DATA.get("regions", [])
_PROVINCE_TO_REGION, _ALL_KNOWN_NAMES = _build_lookups(_DATA)


# ---------------------------------------------------------------------------
# Public lookup functions
# ---------------------------------------------------------------------------

def is_known(name: str) -> bool:
    """Return True if name (case-insensitive) exists in the reference data."""
    return name.strip().lower() in _ALL_KNOWN_NAMES


def canonical(name: str) -> str:
    """
    Return the canonical name for a known location.
    Returns the original string unchanged if not found.
    """
    return _ALL_KNOWN_NAMES.get(name.strip().lower(), name.strip())


def region_of(province_name: str) -> str:
    """
    Return the region name for a given province name (case-insensitive).
    Returns empty string if not found.
    """
    return _PROVINCE_TO_REGION.get(province_name.strip().lower(), "")


def provinces_of(region_name: str) -> List[str]:
    """
    Return the province list for a region name or alias (case-insensitive).
    Returns empty list if not found.
    """
    canon = canonical(region_name)
    for region in _REGIONS_LIST:
        if region["name"] == canon:
            return region.get("provinces", [])
    return []


def all_regions() -> List[str]:
    """Return list of all primary region names."""
    return [r["name"] for r in _REGIONS_LIST]


def all_provinces() -> List[str]:
    """Return flat list of all province names across all regions."""
    return [p for r in _REGIONS_LIST for p in r.get("provinces", [])]


# ---------------------------------------------------------------------------
# Unresolved location logger
# ---------------------------------------------------------------------------

def log_unresolved(name: str, source: str) -> None:
    """
    Log an unrecognised location name to the shared sidecar file.

    If the name+source pair already exists (any status), increments times_seen
    and updates last_seen. New entries are added with status="pending".
    Resolved or skipped entries are not re-opened — only times_seen is updated.

    Args:
        name:   Raw location string as it appeared in the bulletin.
        source: Extractor identifier, e.g. "hro", "tcws".
    """
    sidecar = {"unresolved": []}
    if os.path.exists(_UNRESOLVED_PATH):
        with open(_UNRESOLVED_PATH, encoding="utf-8") as f:
            sidecar = json.load(f)

    now = datetime.now(tz=_PST).strftime("%Y-%m-%dT%H:%M:%S+08:00")

    existing = next(
        (e for e in sidecar["unresolved"]
         if e["raw"].lower() == name.lower() and e["source"] == source),
        None,
    )

    if existing:
        existing["times_seen"] += 1
        existing["last_seen"]   = now
    else:
        sidecar["unresolved"].append({
            "raw":        name,
            "source":     source,
            "first_seen": now,
            "last_seen":  now,
            "times_seen": 1,
            "status":     "pending",
        })

    with open(_UNRESOLVED_PATH, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Quick self-test (run directly: python location_reference.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        ("Ilocos Norte",          True,  "Ilocos Region"),
        ("Benguet",               True,  "Cordillera Administrative Region"),
        ("CALABARZON",            True,  ""),
        ("Region IV-A",           True,  ""),
        ("Metro Manila",          True,  ""),
        ("Babuyan Islands",       True,  ""),
        ("Sulu",                  True,  "Zamboanga Peninsula"),
        ("Negros Occidental",     True,  "Negros Island Region"),
        ("Negros Oriental",       True,  "Negros Island Region"),
        ("Siquijor",              True,  "Negros Island Region"),
        ("Maguindanao del Norte", True,  "BARMM"),
        ("some unknown place",    False, ""),
        ("barmm",                 True,  ""),
        ("cordillera",            True,  ""),
        ("cagayan valley",        True,  ""),
    ]

    print(f"{'Name':<35} {'Known':>5}  {'Region'}")
    print("-" * 65)
    all_pass = True
    for name, expect_known, expect_region in tests:
        known = is_known(name)
        reg   = region_of(name)
        ok    = (known == expect_known) and (expect_region == "" or reg == expect_region)
        flag  = "\u2713" if ok else "\u2717"
        if not ok:
            all_pass = False
        print(f"{flag} {name:<35} {str(known):>5}  {reg}")

    print()
    print("Provinces of CAR:       ", provinces_of("CAR"))
    print("Provinces of NIR:       ", provinces_of("Negros Island Region"))
    print("Provinces of Region IX: ", provinces_of("Region IX"))
    print()

    # Test log_unresolved
    log_unresolved("North Cotabato", "hro")
    log_unresolved("North Cotabato", "hro")   # second call — should increment times_seen
    log_unresolved("Maguindanao",    "tcws")
    with open(_UNRESOLVED_PATH, encoding="utf-8") as f:
        sidecar = json.load(f)
    print("Sidecar entries after test:")
    for e in sidecar["unresolved"]:
        print(f"  {e['raw']!r:25} source={e['source']}  "
              f"times_seen={e['times_seen']}  status={e['status']}")
    print()
    print("All tests passed." if all_pass else "SOME TESTS FAILED.")
