"""
ocr_fallback.py
===============
OCR-based extraction for flattened PAGASA advisory PDFs with no text layer.

Approach:
    1. Rasterize the PDF page to an image (pdf2image / poppler).
    2. Detect table borders using morphological operations (OpenCV).
    3. Extract cell bounding boxes from the detected grid.
    4. OCR each cell individually (Tesseract, single-block mode).
    5. OCR the header region above the table for metadata.
    6. Reconstruct table dicts in the same format as _extract_raw_tables()
       so the existing _parse_table() pipeline can be reused unchanged.

Why cell-level OCR:
    Slicing the image to individual cells before OCR avoids the word-clustering
    and column-assignment problems that plague page-level OCR on tables.
    Each cell is self-contained; wrapped province text is handled naturally.

Public API:
    extract_via_ocr(pdf_path, dpi=300, low_conf_threshold=60)
        -> dict with keys: header_text, tables, warnings

Dependencies:
    System : tesseract-ocr, poppler-utils
    Python : pdf2image, pytesseract, opencv-python-headless
             (pip install in conda env)
"""

import re
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Lazy imports
# ---------------------------------------------------------------------------

def _import_deps():
    """Import all OCR dependencies; raise ImportError with install hint."""
    try:
        import cv2
        import numpy as np
        import pytesseract
        from pdf2image import convert_from_path
        return cv2, np, pytesseract, convert_from_path
    except ImportError as e:
        raise ImportError(
            f"OCR fallback missing dependency: {e}\n"
            "Install with:\n"
            "  pip install pdf2image pytesseract opencv-python-headless\n"
            "  sudo apt install tesseract-ocr poppler-utils"
        )


# ---------------------------------------------------------------------------
# Step 1: Rasterize
# ---------------------------------------------------------------------------

def _rasterize(pdf_path: str, dpi: int = 300):
    """
    Convert the first page of a PDF to a numpy image array (RGB).

    Args:
        pdf_path: Path to the PDF file.
        dpi:      Resolution. 300 DPI balances OCR accuracy and speed.

    Returns:
        numpy ndarray of shape (H, W, 3).
    """
    _, np, _, convert_from_path = _import_deps()
    pages = convert_from_path(pdf_path, dpi=dpi, first_page=1, last_page=1)
    if not pages:
        raise ValueError(f"pdf2image returned no pages for: {pdf_path}")
    return np.array(pages[0])


# ---------------------------------------------------------------------------
# Step 2 & 3: Border detection → cell bounding boxes
# ---------------------------------------------------------------------------

def _detect_cells(
    image,
    min_area:  int = 5_000,
    max_ratio: float = 0.8,
) -> List[Tuple[int, int, int, int]]:
    """
    Detect table cell bounding boxes using morphological line detection.

    Strategy:
        - Binarize (invert so table lines are white on black).
        - Isolate horizontal lines with a wide flat kernel.
        - Isolate vertical lines with a tall thin kernel.
        - Combine → closed contours = cells.
        - Filter by area (exclude noise and full-page contours).
        - Sort top-to-bottom, left-to-right.

    Args:
        image:     RGB numpy array (from _rasterize).
        min_area:  Minimum cell area in pixels (excludes noise).
        max_ratio: Maximum cell area as fraction of page (excludes full table).

    Returns:
        List of (x, y, w, h) tuples sorted top-to-bottom, left-to-right.
    """
    cv2, np, _, _ = _import_deps()

    gray   = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1]

    # Kernel length: 60px at 300 DPI ≈ 5mm — long enough to detect table lines
    # without picking up text strokes (which are typically < 20px wide)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (60, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 60))

    h_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, h_kernel)
    v_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, v_kernel)

    # Combine and dilate slightly to close minor gaps at intersections
    lines_mask = cv2.add(h_lines, v_lines)
    close_k    = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    lines_mask = cv2.dilate(lines_mask, close_k, iterations=1)

    contours, _ = cv2.findContours(
        lines_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
    )

    page_area = image.shape[0] * image.shape[1]
    cells     = []

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = w * h
        if (min_area < area < page_area * max_ratio
                and w > 50 and h > 30):
            cells.append((x, y, w, h))

    # Sort: primary = row (y rounded to nearest 30px), secondary = column (x)
    cells.sort(key=lambda c: (round(c[1] / 30) * 30, c[0]))
    return cells


# ---------------------------------------------------------------------------
# Step 4: OCR individual cells
# ---------------------------------------------------------------------------

def _ocr_cell(
    image,
    x: int, y: int, w: int, h: int,
    padding: int = 5,
) -> str:
    """
    OCR a single cell by cropping the image to its bounding box.

    Uses Tesseract PSM 6 (uniform block of text) which works well for
    isolated cell content. Strips trailing whitespace and bullet characters.

    Args:
        image:   Full-page RGB numpy array.
        x,y,w,h: Cell bounding box.
        padding: Pixels to inset from cell border (avoids border noise).

    Returns:
        Cleaned text string (may be empty for dash-only or blank cells).
    """
    _, _, pytesseract, _ = _import_deps()

    H, W = image.shape[:2]
    crop = image[
        max(0, y + padding): min(H, y + h - padding),
        max(0, x + padding): min(W, x + w - padding),
    ]

    if crop.size == 0:
        return ""

    raw = pytesseract.image_to_string(crop, config='--psm 6 --oem 3')
    # Strip whitespace, bullet chars, and lone dashes
    text = raw.strip().replace('\x0c', '')
    text = re.sub(r'^[\s\-–—•]+$', '', text, flags=re.MULTILINE)
    text = '\n'.join(line for line in text.splitlines() if line.strip())
    return text.strip()


# ---------------------------------------------------------------------------
# Step 5: OCR the header region (above the table)
# ---------------------------------------------------------------------------

def _ocr_header(image, table_top_y: int, padding: int = 10) -> str:
    """
    OCR the page region above the table to extract advisory metadata.

    The header contains:
        - Advisory number  ("WEATHER ADVISORY NO. 18")
        - Weather system   ("For: Southwest Monsoon")
        - Issue datetime   ("Issued at: 11:00 AM, 03 June 2026")
        - Final marker     ("FINAL" in advisory title, if present)

    Args:
        image:        Full-page RGB numpy array.
        table_top_y:  Y-coordinate of the topmost table border.
        padding:      Extra pixels above table_top_y to include.

    Returns:
        Raw OCR text of the header region.
    """
    _, _, pytesseract, _ = _import_deps()

    header_crop = image[0: max(0, table_top_y - padding), :]
    if header_crop.size == 0:
        return ""

    text = pytesseract.image_to_string(header_crop, config='--psm 6 --oem 3')
    return text.strip()


# ---------------------------------------------------------------------------
# Step 6: Reconstruct table structure from sorted cells
# ---------------------------------------------------------------------------

def _group_cells_into_rows(
    cells: List[Tuple[int, int, int, int]],
    row_tolerance: int = 20,
) -> List[List[Tuple[int, int, int, int]]]:
    """
    Group cells into rows by their top-y coordinate.

    Cells within row_tolerance pixels of each other vertically are
    considered part of the same row.

    Returns:
        List of rows, each row is a list of (x,y,w,h) sorted left-to-right.
    """
    if not cells:
        return []

    rows:        List[List[Tuple]] = []
    current_row: List[Tuple]       = [cells[0]]
    current_y                      = cells[0][1]

    for cell in cells[1:]:
        if abs(cell[1] - current_y) <= row_tolerance:
            current_row.append(cell)
        else:
            rows.append(sorted(current_row, key=lambda c: c[0]))
            current_row = [cell]
            current_y   = cell[1]

    if current_row:
        rows.append(sorted(current_row, key=lambda c: c[0]))

    return rows


def _reconstruct_tables(
    image,
    cells: List[Tuple[int, int, int, int]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """
    OCR each cell and reconstruct table dicts from the cell grid.

    Each table dict matches the format returned by _extract_raw_tables():
        {title, data (list of rows, each row a list of cell strings),
         rows, columns}

    Multiple tables (e.g. TC + Habagat in the same bulletin) appear as
    separate contiguous groups of rows — identified by the presence of a
    merged single-cell row (the weather system name row).

    Args:
        image: Full-page RGB numpy array.
        cells: Sorted cell bounding boxes from _detect_cells().

    Returns:
        Tuple of (tables, warnings).
    """
    warnings: List[Dict[str, str]] = []

    # Filter out the full-table outline contour — it's detected as a single
    # large cell spanning the entire table but we only want individual cells.
    # Heuristic: a cell is the full-table outline if it is taller than
    # 3× the median cell height AND wider than 80% of page width.
    page_w = image.shape[1]
    if cells:
        heights     = sorted(c[3] for c in cells)
        median_h    = heights[len(heights) // 2]
        cells = [
            c for c in cells
            if not (c[3] > median_h * 3 and c[2] > page_w * 0.8)
        ]

    # Group cells into visual rows
    rows = _group_cells_into_rows(cells)

    if not rows:
        return [], warnings

    # OCR all cells; build a 2D grid of text
    grid: List[List[str]] = []
    for row in rows:
        grid.append([_ocr_cell(image, *cell) for cell in row])

    # Split grid into individual tables.
    # A new table starts at a single-cell row (merged header = weather system name)
    # that contains "heavy rainfall outlook".
    tables: List[Dict[str, Any]] = []
    current_table_rows: List[List[str]] = []
    current_title: str = ""

    _system_re = re.compile(r'heavy\s+rainfall\s+outlook', re.IGNORECASE)
    # Footer sentinel — signals end of table data
    _footer_re  = re.compile(
        r'forecast\s+rainfall\s+ma[yx]|unless\s+significant|prepared\s+by',
        re.IGNORECASE,
    )

    def flush():
        if current_title and len(current_table_rows) > 1:
            tables.append({
                "title":   current_title,
                "data":    current_table_rows,
                "rows":    len(current_table_rows),
                "columns": max(len(r) for r in current_table_rows),
            })

    for row_cells in grid:
        row_text = ' '.join(cell for cell in row_cells if cell)

        # Footer line — stop accumulating
        if _footer_re.search(row_text):
            flush()
            break

        # Single-cell merged row = weather system title
        if len(row_cells) == 1 and _system_re.search(row_cells[0]):
            if current_table_rows:
                flush()
            current_title      = row_cells[0].replace('\n', ' ').strip()
            current_table_rows = [[current_title] + [""] * 4]
            continue

        if not current_title:
            # Not yet inside a table
            continue

        # Warn if row is unexpectedly short (possible missed cell)
        if current_table_rows and len(row_cells) < len(current_table_rows[0]):
            warnings.append({
                "type":   "ocr_short_row",
                "detail": f"Row has {len(row_cells)} cells, expected "
                          f"{len(current_table_rows[0])}; possible missed border",
            })

        current_table_rows.append([' '.join(c.split()) for c in row_cells])

    else:
        flush()

    return tables, warnings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_via_ocr(
    pdf_path:           str,
    dpi:                int = 300,
    low_conf_threshold: int = 60,
) -> Dict[str, Any]:
    """
    Extract all HRO content from a flattened or image-only PDF using OCR.

    Returns a dict:
        header_text  — raw OCR text of the region above the table;
                       feed into existing _extract_advisory_number(),
                       _extract_datetime(), _detect_final() functions.
        tables       — list of raw table dicts, same format as
                       _extract_raw_tables(); feed into _parse_table().
        warnings     — list of {type, detail} dicts for any issues.

    Args:
        pdf_path:           Path to the PDF file.
        dpi:                Rasterization DPI (300 recommended).
        low_conf_threshold: Unused for cell-level OCR (kept for API compat).

    Raises:
        ImportError if any dependency is missing.
        ValueError  if the PDF cannot be rasterized.
    """
    warnings: List[Dict[str, str]] = []

    # ── Rasterize ──
    image = _rasterize(pdf_path, dpi=dpi)

    # ── Detect cells ──
    cells = _detect_cells(image)
    if not cells:
        warnings.append({
            "type":   "ocr_no_cells",
            "detail": "No table cells detected — page may have no visible borders",
        })
        return {"header_text": "", "tables": [], "warnings": warnings}

    # Table top: y of the first wide cell containing 'heavy rainfall outlook'.
    # Using min(y) would include logo/certification boxes at the page top.
    page_width  = image.shape[1]
    table_top_y = None
    for cx, cy, cw, ch in cells:
        if cw > page_width * 0.5:   # wide cell = likely table row
            table_top_y = cy
            break
    if table_top_y is None:
        table_top_y = min(c[1] for c in cells)   # fallback

    # ── OCR header region ──
    header_text = _ocr_header(image, table_top_y)

    # ── Reconstruct tables from cells ──
    tables, tbl_warnings = _reconstruct_tables(image, cells)
    warnings.extend(tbl_warnings)

    if not tables:
        warnings.append({
            "type":   "ocr_no_tables",
            "detail": "Cells detected but no tables could be reconstructed",
        })

    return {
        "header_text": header_text,
        "tables":      tables,
        "warnings":    warnings,
    }


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python ocr_fallback.py <path-to-pdf>")
        sys.exit(1)

    pdf = sys.argv[1]
    print(f"Processing: {pdf}\n")

    result = extract_via_ocr(pdf)

    print("=== HEADER TEXT ===")
    print(result["header_text"])

    print(f"\n=== TABLES ({len(result['tables'])}) ===")
    for i, tbl in enumerate(result["tables"], 1):
        print(f"\nTable {i}: {tbl['title']}")
        print(f"  {tbl['rows']} rows × {tbl['columns']} columns")
        for j, row in enumerate(tbl["data"]):
            print(f"  Row {j}: {[c[:40] for c in row]}")

    if result["warnings"]:
        print(f"\n=== WARNINGS ({len(result['warnings'])}) ===")
        for w in result["warnings"]:
            print(f"  [{w['type']}] {w['detail']}")
