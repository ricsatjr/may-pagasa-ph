#!/usr/bin/env python3
"""
alias_tool.py
=============
Interactive CLI for resolving unrecognised location names flagged by PAGASA
extractors and updating locations.json accordingly.

Reads:  unresolved_locations.json  (sidecar written by extractors)
Writes: locations.json             (adds aliases, provinces, or extra locations)
        unresolved_locations.json  (updates status of reviewed entries)

Both files must be in the same directory as this script.

Usage:
    python alias_tool.py              # review all pending entries
    python alias_tool.py --source hro # review only entries from the hro extractor
    python alias_tool.py --list       # list all entries without resolving
    python alias_tool.py --reset      # reset all skipped entries back to pending
"""

import json
import os
import sys
import argparse
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE            = os.path.dirname(os.path.abspath(__file__))
_LOCATIONS_PATH  = os.path.join(_HERE, "locations.json")
_UNRESOLVED_PATH = os.path.join(_HERE, "unresolved_locations.json")


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------

def _load_locations() -> dict:
    """Load locations.json."""
    if not os.path.exists(_LOCATIONS_PATH):
        raise FileNotFoundError(f"locations.json not found at {_LOCATIONS_PATH}")
    with open(_LOCATIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_locations(data: dict) -> None:
    """Write locations.json with consistent formatting."""
    with open(_LOCATIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _load_sidecar() -> dict:
    """Load unresolved_locations.json, or return empty structure if absent."""
    if not os.path.exists(_UNRESOLVED_PATH):
        return {"unresolved": []}
    with open(_UNRESOLVED_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_sidecar(data: dict) -> None:
    """Write unresolved_locations.json."""
    with open(_UNRESOLVED_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Lookup helpers (in-memory, avoids importing location_reference at runtime
# so the tool works even during a reload cycle)
# ---------------------------------------------------------------------------

def _region_names(locations: dict) -> list:
    """Return list of all primary region names."""
    return [r["name"] for r in locations.get("regions", [])]


def _province_names(locations: dict) -> list:
    """Return flat list of all province names."""
    return [p for r in locations.get("regions", []) for p in r.get("provinces", [])]


def _find_region(locations: dict, name: str) -> Optional[dict]:
    """Return the region dict whose name or alias matches (case-insensitive)."""
    name_lower = name.strip().lower()
    for region in locations.get("regions", []):
        if region["name"].lower() == name_lower:
            return region
        if any(a.lower() == name_lower for a in region.get("aliases", [])):
            return region
    return None


def _find_province_region(locations: dict, province: str) -> Optional[str]:
    """Return the region name that contains a province, or None."""
    p_lower = province.strip().lower()
    for region in locations.get("regions", []):
        if any(p.lower() == p_lower for p in region.get("provinces", [])):
            return region["name"]
    return None


# ---------------------------------------------------------------------------
# Resolution actions
# ---------------------------------------------------------------------------

def _add_alias_to_region(locations: dict, raw: str, region_name: str) -> bool:
    """
    Add raw as an alias of the named region.
    Returns False if region not found.
    """
    region = _find_region(locations, region_name)
    if not region:
        return False
    if raw not in region.get("aliases", []):
        region.setdefault("aliases", []).append(raw)
    return True


def _add_alias_to_province(locations: dict, raw: str, province_name: str) -> bool:
    """
    Add raw as an alias of the named province.
    Provinces don't have an aliases list in the current schema — this adds the
    raw name as an alias entry on the parent region's province list by inserting
    a province alias dict. Since the current schema stores provinces as plain
    strings, we upgrade only the target province to a dict form:
        {"name": "Cotabato", "aliases": ["North Cotabato"]}
    Returns False if province not found.
    """
    p_lower = province_name.strip().lower()
    for region in locations.get("regions", []):
        provinces = region.get("provinces", [])
        for i, p in enumerate(provinces):
            # Province may be a plain string or an upgraded dict
            if isinstance(p, dict):
                if p["name"].lower() == p_lower:
                    if raw not in p.get("aliases", []):
                        p.setdefault("aliases", []).append(raw)
                    return True
            else:
                if p.lower() == p_lower:
                    # Upgrade to dict form
                    provinces[i] = {"name": p, "aliases": [raw]}
                    return True
    return False


def _add_province_to_region(locations: dict, province: str, region_name: str) -> bool:
    """
    Add a new province name to the named region's province list.
    Returns False if region not found or province already exists.
    """
    region = _find_region(locations, region_name)
    if not region:
        return False
    provinces = region.setdefault("provinces", [])
    # Check for duplicates (handle both str and dict forms)
    existing = [
        (p["name"] if isinstance(p, dict) else p).lower()
        for p in provinces
    ]
    if province.lower() in existing:
        print(f"  Province '{province}' already exists in {region['name']}.")
        return False
    provinces.append(province)
    return True


def _add_extra_location(locations: dict, name: str) -> bool:
    """
    Add name to extra_locations if not already present.
    Returns False if already present.
    """
    extras = locations.setdefault("extra_locations", [])
    if name in extras:
        print(f"  '{name}' is already in extra_locations.")
        return False
    extras.append(name)
    return True


# ---------------------------------------------------------------------------
# Interactive resolution loop
# ---------------------------------------------------------------------------

def _prompt(question: str, valid: list) -> str:
    """Prompt until a valid response is given."""
    valid_lower = [v.lower() for v in valid]
    while True:
        answer = input(f"  {question} [{'/'.join(valid)}]: ").strip()
        if answer.lower() in valid_lower:
            return answer.lower()
        print(f"  Please enter one of: {', '.join(valid)}")


def _search_locations(locations: dict, query: str) -> None:
    """Print regions and provinces that contain the query string."""
    q = query.strip().lower()
    print()
    matches = []
    for region in locations.get("regions", []):
        if q in region["name"].lower() or any(q in a.lower() for a in region.get("aliases", [])):
            matches.append(f"  [region]   {region['name']}  (aliases: {region.get('aliases', [])})")
        for p in region.get("provinces", []):
            pname = p["name"] if isinstance(p, dict) else p
            if q in pname.lower():
                matches.append(f"  [province] {pname}  → {region['name']}")
    for loc in locations.get("extra_locations", []):
        if q in loc.lower():
            matches.append(f"  [extra]    {loc}")
    if matches:
        for m in matches:
            print(m)
    else:
        print(f"  No matches for '{query}'.")
    print()


def _resolve_entry(entry: dict, locations: dict) -> str:
    """
    Interactively resolve one unresolved entry.

    Returns the new status: 'mapped', 'skipped', or 'pending' (if action failed).
    """
    raw    = entry["raw"]
    source = entry["source"]

    print(f"\n{'─' * 60}")
    print(f"  Raw name : {raw!r}")
    print(f"  Source   : {source}")
    print(f"  Seen     : {entry['times_seen']} time(s)  "
          f"(first: {entry['first_seen'][:10]})")
    print()
    print("  Actions:")
    print("    a  add as alias of an existing region")
    print("    p  add as alias of an existing province")
    print("    n  add as new province under a region")
    print("    e  add to extra_locations")
    print("    s  skip (will appear again next run)")
    print("    x  skip permanently (won't appear again)")
    print("    ?  search locations.json")
    print()

    while True:
        action = input("  Choice: ").strip().lower()

        if action == "?":
            query = input("  Search term: ").strip()
            _search_locations(locations, query)
            continue

        elif action == "a":
            # Alias of an existing region
            region_name = input("  Map to region (primary name or alias): ").strip()
            if not region_name:
                continue
            region = _find_region(locations, region_name)
            if not region:
                print(f"  Region '{region_name}' not found. Try '?' to search.")
                continue
            _add_alias_to_region(locations, raw, region["name"])
            print(f"  \u2713 Added '{raw}' as alias of {region['name']}")
            return "mapped"

        elif action == "p":
            # Alias of an existing province
            province_name = input("  Map to province: ").strip()
            if not province_name:
                continue
            parent = _find_province_region(locations, province_name)
            if not parent:
                print(f"  Province '{province_name}' not found. Try '?' to search.")
                continue
            _add_alias_to_province(locations, raw, province_name)
            print(f"  \u2713 Added '{raw}' as alias of {province_name} ({parent})")
            return "mapped"

        elif action == "n":
            # New province under a region
            region_name = input("  Add under which region: ").strip()
            if not region_name:
                continue
            region = _find_region(locations, region_name)
            if not region:
                print(f"  Region '{region_name}' not found. Try '?' to search.")
                continue
            # Use the raw name as the province name, or let developer rename it
            prov_name = input(f"  Province name to store (Enter to use '{raw}'): ").strip()
            if not prov_name:
                prov_name = raw
            if _add_province_to_region(locations, prov_name, region["name"]):
                # If raw differs from prov_name, also add raw as an alias
                if prov_name.lower() != raw.lower():
                    _add_alias_to_province(locations, raw, prov_name)
                print(f"  \u2713 Added '{prov_name}' to {region['name']}")
            return "mapped"

        elif action == "e":
            # Extra location
            loc_name = input(f"  Name to store in extra_locations (Enter to use '{raw}'): ").strip()
            if not loc_name:
                loc_name = raw
            if _add_extra_location(locations, loc_name):
                print(f"  \u2713 Added '{loc_name}' to extra_locations")
            return "mapped"

        elif action == "s":
            print(f"  Skipped (will reappear next run).")
            return "pending"   # leave as pending so it resurfaces

        elif action == "x":
            print(f"  Permanently skipped.")
            return "skipped"

        else:
            print("  Invalid choice. Enter a, p, n, e, s, x, or ?")


# ---------------------------------------------------------------------------
# Listing mode
# ---------------------------------------------------------------------------

def _list_entries(sidecar: dict, source_filter: Optional[str]) -> None:
    """Print all sidecar entries in a readable table."""
    entries = sidecar.get("unresolved", [])
    if source_filter:
        entries = [e for e in entries if e["source"] == source_filter]

    if not entries:
        print("No entries found.")
        return

    print(f"\n{'Raw name':<30} {'Source':<8} {'Status':<10} {'Seen':>4}  {'First seen'}")
    print("─" * 72)
    for e in entries:
        print(f"  {e['raw']:<28} {e['source']:<8} {e['status']:<10} "
              f"{e['times_seen']:>4}  {e['first_seen'][:10]}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Resolve unrecognised PAGASA location names and update locations.json."
    )
    ap.add_argument(
        "--source", default=None,
        help="Filter entries by extractor source (e.g. hro, tcws)",
    )
    ap.add_argument(
        "--list", action="store_true",
        help="List all sidecar entries without resolving",
    )
    ap.add_argument(
        "--reset", action="store_true",
        help="Reset all 'skipped' entries back to 'pending'",
    )
    args = ap.parse_args()

    # Load files
    locations = _load_locations()
    sidecar   = _load_sidecar()

    # ── List mode ──
    if args.list:
        _list_entries(sidecar, args.source)
        return

    # ── Reset mode ──
    if args.reset:
        count = 0
        for e in sidecar["unresolved"]:
            if e["status"] == "skipped":
                e["status"] = "pending"
                count += 1
        _save_sidecar(sidecar)
        print(f"Reset {count} skipped entry/entries to pending.")
        return

    # ── Interactive resolution mode ──
    pending = [
        e for e in sidecar["unresolved"]
        if e["status"] == "pending"
        and (args.source is None or e["source"] == args.source)
    ]

    if not pending:
        filter_msg = f" for source '{args.source}'" if args.source else ""
        print(f"No pending entries{filter_msg}.")
        return

    print(f"\n{len(pending)} pending location(s) to review.\n")
    resolved = skipped = 0

    for i, entry in enumerate(pending, start=1):
        print(f"[{i}/{len(pending)}]", end="")
        new_status = _resolve_entry(entry, locations)

        # Update status in sidecar
        for e in sidecar["unresolved"]:
            if (e["raw"].lower() == entry["raw"].lower()
                    and e["source"] == entry["source"]):
                e["status"] = new_status
                break

        if new_status == "mapped":
            resolved += 1
            # Save after each successful resolution so progress isn't lost
            _save_locations(locations)
            _save_sidecar(sidecar)
        elif new_status == "skipped":
            skipped += 1
            _save_sidecar(sidecar)

    print(f"\n{'─' * 60}")
    print(f"Done.  Resolved: {resolved}  |  Skipped: {skipped}  |  "
          f"Deferred (s): {len(pending) - resolved - skipped}")
    print(f"locations.json updated: {_LOCATIONS_PATH}")
    print(f"Sidecar updated:        {_UNRESOLVED_PATH}")


if __name__ == "__main__":
    main()
