#!/usr/bin/env python3
"""
fetch_hro.py
============
Downloads new Heavy Rainfall Outlook (HRO) advisory PDFs from the PAGASA
public file directory and optionally triggers the extractor.

Source directory:
    https://pubfiles.pagasa.dost.gov.ph/tamss/weather/weather_advisory/

Workflow:
    1. Read fetch_state.json from jsons/current_event/ to get the latest
       known bulletin datetime (cutoff). If absent, prompt user for a start
       date or default to today.
    2. Fetch the PAGASA directory listing and parse filenames + Last-Modified
       timestamps (directory timestamps are in GMT; converted to PST for
       comparison).
    3. Download only PDFs whose Last-Modified timestamp is later than the
       cutoff datetime.
    4. Save downloaded files to pdfs/new/.
    5. If any files were downloaded (and --no-extract is not set), call
       extract_hro.py --incremental automatically.

Usage:
    python fetch_hro.py                          # fetch and extract
    python fetch_hro.py --no-extract             # fetch only
    python fetch_hro.py --dry-run                # show what would be downloaded
    python fetch_hro.py --src-url <url>          # override PAGASA directory URL
"""

import os
import re
import sys
import json
import argparse
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAGASA_URL  = "https://pubfiles.pagasa.dost.gov.ph/tamss/weather/weather_advisory/"
PST         = timezone(timedelta(hours=8))
GMT         = timezone.utc

# Relative path from this script to extract_hro.py (same directory)
_HERE        = os.path.dirname(os.path.abspath(__file__))
_EXTRACTOR   = os.path.join(_HERE, "extract_hro.py")


# ---------------------------------------------------------------------------
# Directory listing parser
# ---------------------------------------------------------------------------

# Regex to parse Apache directory listing rows.
# Matches: <a href="...pdf">filename.pdf</a>   DD-Mon-YYYY HH:MM   size
# The timestamp is in GMT (server time).
_DIR_ROW_RE = re.compile(
    r'href="([^"]+\.pdf)"[^>]*>([^<]+\.pdf)</a>\s+'
    r'(\d{2}-\w{3}-\d{4}\s+\d{2}:\d{2})',
    re.IGNORECASE,
)


def _fetch_directory(url: str) -> List[Tuple[str, Optional[datetime]]]:
    """
    Fetch the PAGASA directory listing and return parsed PDF entries.

    Parses the raw HTML with a regex rather than an event-driven HTML parser
    because the Apache listing puts filename and timestamp on the same line
    as plain text, not in separate tags.

    Returns list of (filename, upload_datetime_gmt_aware) tuples.
    The timestamp is the server upload time (GMT), not the bulletin issue time.
    Raises urllib.error.URLError on network failure.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "fetch_hro/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    entries: List[Tuple[str, Optional[datetime]]] = []
    for m in _DIR_ROW_RE.finditer(html):
        fname    = m.group(2).strip()
        date_str = m.group(3).strip()
        try:
            dt = datetime.strptime(date_str, "%d-%b-%Y %H:%M").replace(tzinfo=GMT)
            entries.append((fname, dt))
        except ValueError:
            entries.append((fname, None))

    return entries


# ---------------------------------------------------------------------------
# Fetch state helpers
# ---------------------------------------------------------------------------

def _read_fetch_state(current_folder: str) -> Optional[datetime]:
    """
    Read fetch_state.json from current_event/ and return the latest bulletin
    datetime as a timezone-aware PST datetime.
    Returns None if the file does not exist.
    """
    path = os.path.join(current_folder, "fetch_state.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    raw = state.get("latest_bulletin_datetime", "")
    if not raw:
        return None
    # Parse ISO 8601 with +08:00 offset
    dt = datetime.fromisoformat(raw)
    return dt.astimezone(PST)


def _prompt_start_date() -> datetime:
    """
    Prompt the user for a start date when no fetch_state.json exists.
    Defaults to today at 00:00:00 PST if the user presses Enter.
    """
    today = datetime.now(tz=PST).strftime("%Y-%m-%d")
    print("No fetch state found (first run or fresh repo).")
    raw = input(
        f"Enter start date to filter downloads (YYYY-MM-DD), "
        f"or press Enter to use today [{today}]: "
    ).strip()

    if not raw:
        raw = today

    try:
        dt = datetime.strptime(raw, "%Y-%m-%d").replace(
            hour=0, minute=0, second=0, tzinfo=PST
        )
        print(f"Using start date: {dt.strftime('%Y-%m-%d %H:%M PST')}\n")
        return dt
    except ValueError:
        print(f"Invalid date '{raw}'. Using today instead.")
        return datetime.now(tz=PST).replace(hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _already_seen(fname: str, new_folder: str, processed_folder: str, failed_folder: str) -> bool:
    """
    Return True if a PDF filename already exists in new/, processed/, or failed/.
    Prevents re-downloading files that are already in the pipeline.
    """
    for folder in (new_folder, processed_folder, failed_folder):
        if os.path.exists(os.path.join(folder, fname)):
            return True
    return False


def _download(url: str, dest_path: str) -> None:
    """
    Download a single file from url to dest_path.
    Raises urllib.error.URLError on failure.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "fetch_hro/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    with open(dest_path, "wb") as f:
        f.write(data)


# ---------------------------------------------------------------------------
# Main fetch logic
# ---------------------------------------------------------------------------

def fetch(
    src_url:          str,
    new_folder:       str,
    processed_folder: str,
    failed_folder:    str,
    current_folder:   str,
    dry_run:          bool = False,
    no_extract:       bool = False,
    dst_folder:       str  = "",
) -> int:
    """
    Core fetch routine.

    Args:
        src_url:          PAGASA directory URL.
        new_folder:       pdfs/new/ — destination for downloaded PDFs.
        processed_folder: pdfs/processed/ — checked to avoid re-downloading.
        failed_folder:    pdfs/failed/ — checked to avoid re-downloading.
        current_folder:   jsons/current_event/ — contains fetch_state.json.
        dry_run:          If True, print what would be downloaded without downloading.
        no_extract:       If True, skip calling extract_hro.py after download.
        dst_folder:       jsons/ root — passed to extract_hro.py.

    Returns:
        Number of files downloaded (0 on dry run or nothing new).
    """
    # ── Determine cutoff datetime ──
    cutoff = _read_fetch_state(current_folder)
    if cutoff is None:
        cutoff = _prompt_start_date()
    else:
        print(f"Latest known bulletin: {cutoff.strftime('%Y-%m-%d %H:%M PST')}")
        print(f"Fetching advisories issued after this datetime.\n")

    # ── Fetch directory listing ──
    print(f"Fetching directory listing from:\n  {src_url}\n")
    try:
        entries = _fetch_directory(src_url)
    except urllib.error.URLError as e:
        print(f"Error: could not reach PAGASA directory — {e}")
        return 0

    if not entries:
        print("No PDF files found in directory listing.")
        return 0

    # ── Filter by Last-Modified > cutoff ──
    # Directory timestamps are GMT; convert to PST for comparison
    to_download = []
    for fname, last_modified_gmt in entries:
        if last_modified_gmt is None:
            # No timestamp parsed — skip to be safe
            print(f"  [skip] {fname}  (no timestamp found)")
            continue
        last_modified_pst = last_modified_gmt.astimezone(PST)
        if last_modified_pst > cutoff:
            to_download.append((fname, last_modified_pst))

    if not to_download:
        print("No new advisories found.")
        return 0

    print(f"Found {len(to_download)} new advisory file(s):\n")

    # ── Download ──
    downloaded = 0
    for fname, last_modified_pst in sorted(to_download, key=lambda x: x[1]):

        # Skip if already in pipeline
        if _already_seen(fname, new_folder, processed_folder, failed_folder):
            print(f"  [skip] {fname}  (already in pipeline)")
            continue

        ts_str = last_modified_pst.strftime("%Y-%m-%d %H:%M PST")

        if dry_run:
            print(f"  [dry-run] {fname}  ({ts_str})")
            continue

        dest = os.path.join(new_folder, fname)
        try:
            _download(f"{src_url.rstrip('/')}/{fname}", dest)
            print(f"  ✓ {fname}  ({ts_str})")
            downloaded += 1
        except urllib.error.URLError as e:
            print(f"  ✗ {fname}  [download failed: {e}]")

    if dry_run:
        print(f"\n[dry-run] {len(to_download)} file(s) would be downloaded.")
        return 0

    print(f"\n{downloaded} file(s) downloaded to {new_folder}")

    # ── Trigger extractor ──
    if downloaded > 0 and not no_extract:
        print(f"\nTriggering extractor …")
        cmd = [
            sys.executable, _EXTRACTOR,
            "--src",       new_folder,
            "--dst",       dst_folder,
            "--processed", processed_folder,
            "--incremental",
        ]
        print(f"  {' '.join(cmd)}\n")
        subprocess.run(cmd, check=False)

    return downloaded


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Download new PAGASA HRO advisory PDFs and optionally extract them."
    )
    ap.add_argument(
        "--src-url", default=PAGASA_URL,
        help=f"PAGASA directory URL (default: {PAGASA_URL})",
    )
    ap.add_argument(
        "--src", default="data/hro/pdfs/new",
        help="Destination folder for downloaded PDFs (default: data/hro/pdfs/new)",
    )
    ap.add_argument(
        "--processed", default="data/hro/pdfs/processed",
        help="Processed PDFs folder, checked to avoid re-downloads "
             "(default: data/hro/pdfs/processed)",
    )
    ap.add_argument(
        "--dst", default="data/hro/jsons",
        help="JSON output root folder, passed to extractor "
             "(default: data/hro/jsons)",
    )
    ap.add_argument(
        "--no-extract", action="store_true",
        help="Download only — do not trigger extract_hro.py",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be downloaded without downloading anything",
    )
    args = ap.parse_args()

    # Derive sibling folders from --src
    pdfs_root      = os.path.dirname(args.src)   # data/hro/pdfs/
    failed_folder  = os.path.join(pdfs_root, "failed")
    current_folder = os.path.join(args.dst, "current_event")

    # Ensure folders exist
    for folder in (args.src, args.processed, failed_folder, current_folder):
        os.makedirs(folder, exist_ok=True)

    fetch(
        src_url          = args.src_url,
        new_folder       = args.src,
        processed_folder = args.processed,
        failed_folder    = failed_folder,
        current_folder   = current_folder,
        dry_run          = args.dry_run,
        no_extract       = args.no_extract,
        dst_folder       = args.dst,
    )


if __name__ == "__main__":
    main()
