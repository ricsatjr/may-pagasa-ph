#!/usr/bin/env python3
"""
PAGASA Rainfall Advisory Scraper
Fetches rainfall advisory text from PAGASA regional forecast pages and
appends new advisories to monthly log files.

Regions: nlprsd, slprsd, ncrprsd, visprsd, minprsd
Schedule: Run at :30 past each release window (2:30, 5:30, 8:30, 11:30 AM/PM PHT)
"""

import os
import re
import time
import datetime
import requests
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL = "https://www.pagasa.dost.gov.ph/regional-forecast/{region}"
REGIONS = ["nlprsd", "slprsd", "ncrprsd", "visprsd", "minprsd"]
OUTPUT_ROOT = "/home/oz/Git/may-pagasa-ph/data/hrw/rawtext"
PHT = ZoneInfo("Asia/Manila")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

SEPARATOR = "=" * 72

# Positive match: active advisory must contain one of these patterns
ACTIVE_ADVISORY_PATTERN = re.compile(
    r"(Rainfall Advisory|Heavy Rainfall Warning)\s+No\.\s*\d+",
    re.IGNORECASE
)


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_page(url: str, retries: int = 3, delay: int = 10) -> str | None:
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            print(f"  [attempt {attempt}/{retries}] Error: {e}")
            if attempt < retries:
                time.sleep(delay)
    return None


# ── Extract ───────────────────────────────────────────────────────────────────

def extract_rainfall_section(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    section = soup.find(id="rainfalls")
    if section is None:
        print("  Warning: id='rainfalls' not found in page.")
        return None

    chunks = []
    for div in section.find_all("div", recursive=False):
        text = div.get_text(separator="\n").strip()
        if text:
            chunks.append(text)

    if not chunks:
        return None

    block = "\n\n".join(chunks)
    block = re.sub(r"\n{3,}", "\n\n", block).strip()

    if not ACTIVE_ADVISORY_PATTERN.search(block):
        return None

    return block


# ── Storage ───────────────────────────────────────────────────────────────────

def get_output_path(region: str, now: datetime.datetime) -> str:
    return os.path.join(OUTPUT_ROOT, region, now.strftime("%Y-%m") + ".txt")


def get_last_advisory(filepath: str) -> str | None:
    """Return the body text of the most recent entry in the file."""
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    # Split on separator lines; last non-empty block is the most recent entry
    parts = re.split(r"={72}", content)
    for part in reversed(parts):
        stripped = part.strip()
        if not stripped:
            continue
        # Strip the timestamp header line
        body_lines = [l for l in stripped.splitlines() if not l.startswith("=== ")]
        body = "\n".join(body_lines).strip()
        if body:
            return body
    return None


def append_entry(filepath: str, timestamp: str, advisory: str) -> None:
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(f"\n{SEPARATOR}\n")
        f.write(f"=== {timestamp} ===\n\n")
        f.write(advisory)
        f.write("\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def process_region(region: str, now: datetime.datetime) -> None:
    url = BASE_URL.format(region=region)
    timestamp = now.strftime("%Y-%m-%d %H:%M PHT")
    print(f"\n[{region.upper()}] {timestamp}")
    print(f"  Fetching {url} ...")

    html = fetch_page(url)
    if html is None:
        print("  Skipping — fetch failed.")
        return

    advisory = extract_rainfall_section(html)
    if advisory is None:
        print("  No active rainfall advisory. Skipping.")
        return

    filepath = get_output_path(region, now)
    last = get_last_advisory(filepath)

    if last is not None and last == advisory:
        print("  Advisory unchanged from last entry. Skipping.")
        return

    append_entry(filepath, timestamp, advisory)
    print(f"  ✓ Appended to {filepath}")


def main() -> None:
    now = datetime.datetime.now(tz=PHT)
    print(f"PAGASA Rainfall Advisory Scraper — {now.strftime('%Y-%m-%d %H:%M %Z')}")
    for region in REGIONS:
        try:
            process_region(region, now)
        except Exception as e:
            print(f"  [ERROR] {region}: {e}")
    print("\nDone.")


if __name__ == "__main__":
    main()
