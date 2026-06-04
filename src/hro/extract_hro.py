#!/usr/bin/env python3
# coding: utf-8
"""
extract_hro.py
=======================
Extracts Heavy Rainfall Outlook (HRO) data from PAGASA Weather Advisory PDFs
and outputs a structured JSON file per advisory series.

One series = one JSON file, covering Advisory #1 through the Final Advisory
for a single continuous heavy rainfall event. Advisory numbering resets to #1
at the start of each new series.

Output filename is auto-generated from the series start datetime:
    pagasa-hro-YYYYMMDD_HHMM.json

Usage:
    python pagasa_hro_extractor.py                        # default folders
    python pagasa_hro_extractor.py --src ./pdfs --dst ./output

Known limitations (see series.extraction.issues in output JSON for per-run details):
    - Image-only PDFs (no text layer) cannot be parsed
    - Advisories without a detectable issue datetime are skipped
    - Location modifiers outside the known modifier list are stored as admin_level="raw"
    - Period datetime ranges are inferred (0/24/48H offsets) when header text is ambiguous

Version history:
    2025-07-26  Initial version (valid for advisories from May 22 – Jul 25 2025)
    2025-06-04  Refactored: series metadata, structured locations, datetime ranges,
                edge case logging, dynamic period/category handling
    2025-06-04  Trimmed JSON output: dropped redundant advisory_id, datetime, empty
                warnings/categories; compacted warning detail strings; dropped
                source_file from series-level warning entries
"""

import re
import os
import json
import argparse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber
import location_reference as gazetteer
from location_reference import log_unresolved as _log_unresolved


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Philippine Standard Time offset (+08:00)
PST = timezone(timedelta(hours=8))

# Maps month names to integers; covers full and abbreviated forms
MONTH_MAP: Dict[str, int] = {
    "january": 1,  "jan": 1,
    "february": 2, "feb": 2,
    "march": 3,    "mar": 3,
    "april": 4,    "apr": 4,
    "may": 5,
    "june": 6,     "jun": 6,
    "july": 7,     "jul": 7,
    "august": 8,   "aug": 8,
    "september": 9,"sep": 9,
    "october": 10, "oct": 10,
    "november": 11,"nov": 11,
    "december": 12,"dec": 12,
}

# Rainfall category threshold patterns matched against first column of data rows;
# order matters — more specific patterns first
RAINFALL_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("above_200mm",   re.compile(r'\(?>?\s*200\s*mm\)?',              re.IGNORECASE)),
    ("100_to_200mm",  re.compile(r'\(?\s*100\s*[–\-]\s*200\s*mm\)?', re.IGNORECASE)),
    ("50_to_100mm",   re.compile(r'\(?\s*50\s*[–\-]\s*100\s*mm\)?',  re.IGNORECASE)),
]

# Location modifier prefixes; longer phrases precede shorter to avoid partial matches
# (e.g. "rest of" must be tested before "rest")
MODIFIER_PATTERN = re.compile(
    r'^(rest\s+of|remaining|northern|southern|eastern|western|central|entire|'
    r'coastal|upland|highland|lowland|mountainous|interior|'
    r'north(?:ern)?\s+(?:and\s+)?(?:central)?|'
    r'south(?:ern)?\s+(?:and\s+)?(?:central)?)\s+(.+)$',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Collapse whitespace and strip a string."""
    return re.sub(r'\s+', ' ', text).strip()


def _to_pst(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """Construct a timezone-aware datetime in Philippine Standard Time (UTC+8)."""
    return datetime(year, month, day, hour, minute, tzinfo=PST)


def _iso(dt: datetime) -> str:
    """Return ISO 8601 string with +08:00 suffix."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _is_known_location(name: str) -> bool:
    """
    Return True if name (after stripping any modifier) is recognised by the
    gazetteer. Used only for the unrecognised_location warning; modifiers are
    kept as part of the location string in the output.
    """
    m = MODIFIER_PATTERN.match(name)
    base = _normalise(m.group(2)) if m else name
    return gazetteer.is_known(base)


def _parse_locations(cell_text: str) -> List[str]:
    """
    Parse a table cell into a flat list of location name strings.

    Modifiers (northern, rest of, entire, etc.) are kept as part of the
    string — e.g. "northern Benguet" stays as "northern Benguet".
    Duplicates are removed while preserving order.

    Returns empty list for blank or '-' cells.
    """
    if not cell_text or cell_text.strip() in ('-', ''):
        return []

    text  = _normalise(re.sub(r'\n', ' ', cell_text))
    parts = re.split(r',|\s+and\s+', text)

    seen:      set       = set()
    locations: List[str] = []

    for part in parts:
        part = part.strip()
        if not part or len(part) < 2:
            continue
        if re.fullmatch(r'(and|or|the|of)', part, re.IGNORECASE):
            continue   # stray conjunction surviving the split
        if part not in seen:
            seen.add(part)
            locations.append(part)

    return locations


# ---------------------------------------------------------------------------
# Period datetime parsing
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Core parser class
# ---------------------------------------------------------------------------

class PAGASAHROParser:
    """
    Parses PAGASA Heavy Rainfall Outlook PDF advisories into structured JSON.

    parse_advisory() processes one PDF → one advisory dict.
    batch_parse()    processes a folder → one series JSON file.
    """

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def parse_advisory(self, pdf_path: str) -> Optional[Dict[str, Any]]:
        """
        Parse a single PAGASA advisory PDF.

        Returns a dict with keys: number, is_final, raw_datetime, source_file,
        tables, and (if non-empty) warnings. Returns None if the PDF cannot be
        parsed (image-only, missing datetime, etc.).

        Args:
            pdf_path: Path to the advisory PDF.
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        advisory_warnings: List[Dict[str, str]] = []

        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[0]   # advisories are single-page
            text = page.extract_text() or ""

            if not text.strip():
                return None   # image-only PDF; caller logs the failure

            advisory_num  = self._extract_advisory_number(text)
            datetime_info = self._extract_datetime(text)
            is_final      = self._detect_final(text, os.path.basename(pdf_path))

            # Both anchors are required to build a meaningful record
            if advisory_num == -1 or datetime_info["iso_datetime"] == "Unknown":
                return None

            issue_dt = datetime.fromisoformat(
                datetime_info["iso_datetime"].replace("+08:00", "")
            ).replace(tzinfo=PST)

            raw_tables    = self._extract_raw_tables(page)
            parsed_tables: Dict[str, Any] = {}

            for i, raw_table in enumerate(raw_tables, start=1):
                parsed, tbl_warnings = self._parse_table(raw_table, issue_dt)
                if parsed:
                    parsed_tables[str(i)] = parsed
                for w in tbl_warnings:
                    w["table"] = str(i)
                    advisory_warnings.append(w)

            record: Dict[str, Any] = {
                "number":       advisory_num,
                "is_final":     is_final,
                "raw_datetime": datetime_info["raw_datetime"],
                "tables":       parsed_tables,
            }
            # Omit warnings key entirely when there are none
            if advisory_warnings:
                record["warnings"] = advisory_warnings

            return record

    def batch_parse(
        self,
        src_folder: str,
        dst_folder: str = "hro-jsons",
        processed_folder: str = "hro-pdfs-processed",
        incremental: bool = False,
    ) -> Dict[str, Any]:
        """
        Parse PDFs in src_folder and write one series JSON to dst_folder.

        Full mode (incremental=False):
            Processes all PDFs in src_folder from scratch. Overwrites any
            existing JSON. Use when reprocessing a complete series.

        Incremental mode (incremental=True):
            Loads the existing series JSON (the most recent .json in dst_folder),
            skips PDFs whose original filename already appears in source_log,
            parses only new PDFs, renames and moves each to processed_folder,
            then merges results and rewrites the JSON.

            Rename convention:  YYYYMMDD_HHMM_ADVxxx.pdf
                                YYYYMMDD_HHMM_ADVxxxF.pdf  (final advisory)

        Args:
            src_folder:        Folder containing incoming advisory PDFs.
            dst_folder:        Folder for JSON output.
            processed_folder:  Folder where successfully parsed PDFs are moved
                               (incremental mode only).
            incremental:       If True, run in incremental/append mode.

        Returns:
            The assembled series dict (also written to disk).
        """
        os.makedirs(dst_folder, exist_ok=True)
        os.makedirs(processed_folder, exist_ok=True)

        # --- Load existing series state (incremental) or start fresh ---
        if incremental:
            series = self._load_existing_series(dst_folder)
        else:
            series = {}

        # Extract mutable working state from the loaded series (or initialise)
        advisories:       Dict[str, Any]       = dict(series.get("advisories", {}))
        source_log:       List[Dict[str, str]] = list(series.get("source_log", []))
        series_failures:  List[Dict[str, str]] = list(
            series.get("series", {})
                  .get("extraction", {})
                  .get("issues", {})
                  .get("failed", [])
        )

        # Already-processed original filenames — used to skip in incremental mode
        processed_originals: set = {entry["original"] for entry in source_log}

        pdf_paths = sorted(self._collect_pdfs(src_folder))
        if not pdf_paths:
            print("No PDF files found in", src_folder)
            return series or {}

        # Filter to unprocessed files in incremental mode
        if incremental:
            pending = [
                p for p in pdf_paths
                if os.path.basename(p) not in processed_originals
            ]
            skipped = len(pdf_paths) - len(pending)
            if skipped:
                print(f"Skipping {skipped} already-processed PDF(s).")
        else:
            pending = pdf_paths

        if not pending:
            print("No new PDFs to process.")
            return series

        print(f"Processing {len(pending)} PDF(s) …\n")

        for path in pending:
            original_fname = os.path.basename(path)
            try:
                result = self.parse_advisory(path)

                if result is None:
                    reason = (
                        "image-only PDF"
                        if self._is_image_pdf(path)
                        else "missing advisory number or datetime"
                    )
                    series_failures.append({"source_file": original_fname, "reason": reason})
                    print(f"  ✗ {original_fname}  [{reason}]")
                    continue

                dt_key       = self._extract_datetime_from_raw(result["raw_datetime"])
                canonical    = self._canonical_filename(dt_key, result["number"], result["is_final"])
                result["source_file"] = canonical
                advisories[dt_key]    = result

                # Rename and move to processed_folder (incremental) or record in-place (full)
                if incremental:
                    dest = os.path.join(processed_folder, canonical)
                    os.rename(path, dest)
                    parsed_at = _iso(datetime.now(tz=PST))
                    source_log.append({
                        "original":  original_fname,
                        "processed": canonical,
                        "parsed_at": parsed_at,
                    })
                    print(f"  ✓ {original_fname}  →  {canonical}  [{dt_key}]")
                else:
                    source_log.append({
                        "original":  original_fname,
                        "processed": canonical,
                        "parsed_at": _iso(datetime.now(tz=PST)),
                    })
                    print(f"  ✓ {original_fname}  →  {dt_key}")

            except Exception as exc:
                series_failures.append({"source_file": original_fname, "reason": str(exc)})
                print(f"  ✗ {original_fname}  [exception: {exc}]")

        if not advisories:
            print("\nNo advisories successfully parsed.")
            return {}

        # Recompute series metadata from the full advisory set
        series = self._build_series(advisories, source_log, series_failures)

        # Derive output path from series start datetime
        sorted_keys  = sorted(advisories.keys())
        start_str    = self._dt_to_filename_prefix(sorted_keys[0])
        output_path  = os.path.join(dst_folder, f"pagasa-hro-{start_str}.json")

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(series, f, indent=2, ensure_ascii=False)

        print(f"\nOutput written to: {output_path}")
        self._print_summary(series)
        return series

    # ------------------------------------------------------------------
    # Series assembly helpers
    # ------------------------------------------------------------------

    def _build_series(
        self,
        advisories:      Dict[str, Any],
        source_log:      List[Dict[str, str]],
        series_failures: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """
        Assemble the full series dict from the current advisory set.
        Called after every parse run (full or incremental) to keep
        series metadata consistent with the advisory contents.
        """
        sorted_keys = sorted(advisories.keys())
        final_entry = next(
            (advisories[k] for k in sorted_keys if advisories[k].get("is_final")),
            None,
        )

        # Collect unique weather systems across all advisories
        all_weather_systems: set = set()
        for adv in advisories.values():
            for tbl in adv.get("tables", {}).values():
                ws = tbl.get("weather_system") or tbl.get("name", "")
                if ws:
                    all_weather_systems.add(ws)

        # Only data quality warnings are promoted to the series level
        SERIES_WARNING_TYPES = {"unrecognised_rainfall_category", "unrecognised_location"}
        series_warnings: List[Dict[str, str]] = []
        for dt_key, adv in advisories.items():
            for w in adv.get("warnings", []):
                if w["type"] in SERIES_WARNING_TYPES:
                    entry = dict(w)
                    entry["advisory_datetime"] = dt_key
                    series_warnings.append(entry)

        extraction_issues: Dict[str, Any] = {}
        if series_failures:
            extraction_issues["failed"] = series_failures
        if series_warnings:
            extraction_issues["warnings"] = series_warnings

        series_meta: Dict[str, Any] = {
            "started":          sorted_keys[0],
            "is_final":         final_entry is not None,
            "weather_systems":  sorted(all_weather_systems),
            "total_advisories": len(advisories),
            "extraction":       {"version": "2025-06-04"},
        }
        if extraction_issues:
            series_meta["extraction"]["issues"] = extraction_issues
        if final_entry:
            series_meta["ended"] = final_entry["raw_datetime"]

        return {
            "series":     series_meta,
            "source_log": source_log,          # ordered list of all processed files
            "advisories": dict(sorted(advisories.items())),
        }

    def _load_existing_series(self, dst_folder: str) -> Dict[str, Any]:
        """
        Find and load the most recently modified JSON in dst_folder.
        Returns an empty dict if no JSON file exists yet (first run).
        """
        json_files = sorted(
            [
                os.path.join(dst_folder, f)
                for f in os.listdir(dst_folder)
                if f.endswith(".json")
            ],
            key=os.path.getmtime,
        )
        if not json_files:
            return {}

        path = json_files[-1]   # most recently modified
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        print(f"Loaded existing series: {path}\n")
        return data

    def _canonical_filename(self, iso_datetime: str, number: int, is_final: bool) -> str:
        """
        Build the canonical PDF filename from parsed advisory metadata.

        Format:  YYYYMMDD_HHMM_ADVxxx.pdf
                 YYYYMMDD_HHMM_ADVxxxF.pdf  (final advisory)

        Examples:
            "2025-07-06T11:00:00+08:00", 1,  False → "20250706_1100_ADV001.pdf"
            "2025-07-08T05:00:00+08:00", 8,  True  → "20250708_0500_ADV008F.pdf"
        """
        prefix = self._dt_to_filename_prefix(iso_datetime)
        suffix = "F" if is_final else ""
        return f"{prefix}_ADV{number:03d}{suffix}.pdf"

    @staticmethod
    def _dt_to_filename_prefix(iso_datetime: str) -> str:
        """
        Convert an ISO 8601 datetime string to the YYYYMMDD_HHMM filename prefix.
        Seconds are dropped — PAGASA bulletin times never have non-zero seconds.

        Example: "2025-07-06T11:00:00+08:00" → "20250706_1100"
        """
        # Slice the fixed-format ISO string directly — no datetime parsing needed
        # "2025-07-06T11:00:00+08:00"
        #  0123456789012345
        date_part = iso_datetime[:10].replace("-", "")   # "20250706"
        time_part = iso_datetime[11:16].replace(":", "") # "1100"
        return f"{date_part}_{time_part}"

    # ------------------------------------------------------------------
    # PDF-level extraction helpers
    # ------------------------------------------------------------------

    def _extract_advisory_number(self, text: str) -> int:
        """Extract the advisory sequence number. Returns -1 if not found."""
        m = re.search(r'WEATHER ADVISORY NO\.\s*(\d+)', text, re.IGNORECASE)
        return int(m.group(1)) if m else -1

    def _extract_datetime(self, text: str) -> Dict[str, str]:
        """
        Extract issue datetime from the 'Issued at: ...' line.

        Returns dict with 'raw_datetime' (original string) and 'iso_datetime'
        (PST / +08:00). Both are 'Unknown' if parsing fails.
        """
        pattern = r'Issued at:\s*(\d{1,2}:\d{2}\s*[AP]M),\s*(\d{1,2})\s+(\w+)\s+(\d{4})'
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            return {"raw_datetime": "Unknown", "iso_datetime": "Unknown"}

        time_str, day, month_str, year = m.groups()
        raw = f"{time_str}, {day} {month_str} {year}"

        try:
            time_obj  = datetime.strptime(time_str.strip(), "%I:%M %p")
            month_num = MONTH_MAP.get(month_str.lower(), 0)
            if not month_num:
                raise ValueError(f"Unrecognised month: {month_str}")
            dt = _to_pst(int(year), month_num, int(day), time_obj.hour, time_obj.minute)
            return {"raw_datetime": raw, "iso_datetime": _iso(dt)}
        except Exception:
            return {"raw_datetime": raw, "iso_datetime": "Unknown"}

    def _extract_datetime_from_raw(self, raw_datetime: str) -> str:
        """
        Re-derive the ISO datetime key from a raw_datetime string.
        Used in batch_parse() to avoid storing the ISO string on every advisory record.
        """
        result = self._extract_datetime(f"Issued at: {raw_datetime}")
        return result["iso_datetime"]

    def _detect_final(self, text: str, filename: str) -> bool:
        """
        Return True if the bulletin is the Final Advisory.
        Either condition is sufficient:
          - PDF text contains 'final advisory' (PAGASA's prose closing statement)
          - Original filename contains the 'F' suffix before .pdf (PAGASA's naming
            convention), e.g. Advisory8F.pdf, WxAdv#4F.pdf, ADV008F.pdf
        """
        text_signal     = bool(re.search(r'final\s+advisory', text, re.IGNORECASE))
        filename_signal = bool(re.search(r'F\.pdf$', filename, re.IGNORECASE))
        return text_signal or filename_signal

    def _is_image_pdf(self, pdf_path: str) -> bool:
        """Return True if the PDF has no extractable text layer."""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                return not (pdf.pages[0].extract_text() or "").strip()
        except Exception:
            return False

    def _collect_pdfs(self, folder: str) -> List[str]:
        """Return all PDF file paths in folder (non-recursive)."""
        return [
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(folder, f))
        ]

    # ------------------------------------------------------------------
    # Table extraction
    # ------------------------------------------------------------------

    def _extract_raw_tables(self, page) -> List[Dict[str, Any]]:
        """
        Extract all tables from a pdfplumber page as cleaned dicts.

        Each dict has: title (str), data (list of rows), rows (int), columns (int).
        Tables with fewer than 3 rows (title + header + ≥1 data row) are skipped.
        """
        raw_tables = []
        for tbl in page.find_tables():
            try:
                data = tbl.extract()
                if not data or len(data) < 3:
                    continue

                cleaned = [
                    [str(cell).strip() if cell else "" for cell in row]
                    for row in data
                    if any(cell and str(cell).strip() for cell in row)
                ]

                if len(cleaned) < 3:
                    continue

                raw_tables.append({
                    "title":   _normalise(" ".join(cleaned[0])),
                    "data":    cleaned,
                    "rows":    len(cleaned),
                    "columns": len(cleaned[0]),
                })
            except Exception as exc:
                print(f"    Warning: could not extract a table — {exc}")

        return raw_tables

    # ------------------------------------------------------------------
    # Table parsing
    # ------------------------------------------------------------------

    def _parse_table(
        self,
        raw_table: Dict[str, Any],
        issue_dt: datetime,
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, str]]]:
        """
        Convert one raw table dict into structured HRO data.

        Returns (parsed_dict | None, warnings). None when no forecast periods
        could be extracted.
        """
        warnings: List[Dict[str, str]] = []
        data  = raw_table["data"]
        title = raw_table["title"]

        periods, period_warnings = self._extract_periods(data, issue_dt)
        warnings.extend(period_warnings)

        if not periods:
            return None, warnings

        cat_warnings = self._populate_rainfall_categories(data, periods)
        warnings.extend(cat_warnings)

        # 'name' key for SW Monsoon tables; 'weather_system' for all others
        result: Dict[str, Any] = {"forecast_periods": periods}
        result["name" if "Southwest Monsoon" in title else "weather_system"] = title

        return result, warnings

    def _extract_periods(
        self,
        data: List[List[str]],
        issue_dt: datetime,
    ) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
        """
        Build the forecast_periods dict from the header row (data[1]).

        Reads columns left-to-right from index 1; stops at 'Potential Impacts'.
        Header text is preserved verbatim as 'description' for display purposes;
        no datetime parsing is attempted on it.

        valid_from/valid_to are fixed 24H offsets from the bulletin issue datetime:
            period_1: [ts,      ts+24H]
            period_2: [ts+24H,  ts+48H]
            period_3: [ts+48H,  ts+72H]
        """
        warnings: List[Dict[str, str]] = []
        periods:  Dict[str, Any]       = {}

        header_row = data[1] if len(data) > 1 else []
        col_index  = 0   # counts valid period columns (1-based)

        for raw_col_idx in range(1, len(header_row)):
            cell = _normalise(re.sub(r'\n', ' ', header_row[raw_col_idx]))

            if "Potential Impacts" in cell:
                break
            if not cell:
                continue

            col_index += 1
            periods[f"period_{col_index}"] = {
                "description":         cell,
                "valid_from":          _iso(issue_dt + timedelta(hours=(col_index - 1) * 24)),
                "valid_to":            _iso(issue_dt + timedelta(hours=col_index * 24)),
                "rainfall_categories": {},   # populated by _populate_rainfall_categories
            }

        return periods, warnings

    def _populate_rainfall_categories(
        self,
        data: List[List[str]],
        periods: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        """
        Fill rainfall_categories in each period from data rows (data[2] onwards).

        Column 0 → rainfall threshold label (determines category key).
        Columns 1…N → location text (parsed via _parse_locations()).

        All three standard categories (above_200mm, 100_to_200mm, 50_to_100mm)
        are always present in every period, defaulting to {}. This makes the
        schema uniform regardless of which thresholds the bulletin happens to list.

        Warnings: unrecognised_rainfall_category, unrecognised_location.
        """
        warnings:    List[Dict[str, str]] = []
        period_keys: List[str]            = list(periods.keys())

        # All three standard categories are always present in every period,
        # defaulting to []. Filled in where the PDF lists locations.
        for period_key in period_keys:
            periods[period_key]["rainfall_categories"] = {
                "above_200mm":  [],
                "100_to_200mm": [],
                "50_to_100mm":  [],
            }

        for row in data[2:]:
            if not row or len(row) < 2:
                continue

            threshold_label = row[0].strip()
            category: Optional[str] = None

            for cat_key, pattern in RAINFALL_PATTERNS:
                if pattern.search(threshold_label):
                    category = cat_key
                    break

            if category is None:
                warnings.append({
                    "type":   "unrecognised_rainfall_category",
                    "detail": threshold_label,
                })
                continue

            for col_idx, period_key in enumerate(period_keys, start=1):
                if col_idx >= len(row):
                    break

                locations = _parse_locations(row[col_idx])

                # Warn for names not found in the reference list (modifier
                # stripped internally by _is_known_location before checking)
                for name in locations:
                    if not _is_known_location(name):
                        warnings.append({
                            "type":   "unrecognised_location",
                            "detail": name,
                        })
                        # Log to shared sidecar for alias_tool review
                        _log_unresolved(name, "hro")

                # Extend the list, avoiding duplicates that may arise across
                # multiple parsed rows for the same category
                if locations:
                    existing = periods[period_key]["rainfall_categories"][category]
                    for name in locations:
                        if name not in existing:
                            existing.append(name)

        return warnings

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def _print_summary(self, series: Dict[str, Any]) -> None:
        """Print a human-readable series summary to stdout."""
        meta   = series.get("series", {})
        issues = meta.get("extraction", {}).get("issues", {})

        print("\n" + "=" * 60)
        print("SERIES SUMMARY")
        print("=" * 60)
        print(f"  Started:          {meta.get('started', 'N/A')}")
        print(f"  Ended:            {meta.get('ended', '(series ongoing)')}")
        print(f"  Final advisory:   {'Yes' if meta.get('is_final') else 'No'}")
        print(f"  Total advisories: {meta.get('total_advisories', 0)}")
        print(f"  Weather systems:  {', '.join(meta.get('weather_systems', []))}")
        print(f"  Failed PDFs:      {len(issues.get('failed', []))}")
        print(f"  Warnings:         {len(issues.get('warnings', []))}")
        print("=" * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """
    CLI entry point.

    Full mode (default):
        python pagasa_hro_extractor.py --src ./pdfs --dst ./output

    Incremental mode:
        python pagasa_hro_extractor.py --src ./pdfs --dst ./output --incremental
                                       --processed ./processed
    """
    ap = argparse.ArgumentParser(
        description="Extract PAGASA Heavy Rainfall Outlook data from advisory PDFs."
    )
    ap.add_argument(
        "--src", default="hro-pdfs",
        help="Folder containing incoming advisory PDFs (default: hro-pdfs)",
    )
    ap.add_argument(
        "--dst", default="hro-jsons",
        help="Folder for JSON output (default: hro-jsons)",
    )
    ap.add_argument(
        "--processed", default="hro-pdfs-processed",
        help="Folder for parsed PDFs after rename/move, incremental mode only "
             "(default: hro-pdfs-processed)",
    )
    ap.add_argument(
        "--incremental", action="store_true",
        help="Append new PDFs to an existing series JSON rather than reprocessing all",
    )
    args = ap.parse_args()

    PAGASAHROParser().batch_parse(
        src_folder=args.src,
        dst_folder=args.dst,
        processed_folder=args.processed,
        incremental=args.incremental,
    )


if __name__ == "__main__":
    main()
