#!/usr/bin/env python3
"""
build_display.py
================
Reads the current HRO series JSON and location reference data, then writes
display/site/index.html with the data embedded as JavaScript variables.

This allows display/site/index.html to be opened directly from the filesystem
(file://) without requiring a local HTTP server.

Called automatically by extract_hro.py after every successful parse, or
manually:
    python src/utils/build_display.py

Usage:
    python build_display.py
    python build_display.py --json  data/hro/jsons/current_event/pagasa-hro-*.json
    python build_display.py --past  data/hro/jsons/past_events/pagasa-hro-*.json
"""

import os
import sys
import json
import glob
import argparse
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Paths — resolved relative to this file
# ---------------------------------------------------------------------------

_HERE         = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT    = os.path.abspath(os.path.join(_HERE, "..", ".."))
_DISPLAY_DIR  = os.path.join(_REPO_ROOT, "display", "site")
_OUTPUT_HTML  = os.path.join(_DISPLAY_DIR, "index.html")
_LOCATIONS    = os.path.join(_HERE, "locations.json")

PST = timezone(timedelta(hours=8))


def _load_locations() -> dict:
    """Load locations.json for region/province structure."""
    with open(_LOCATIONS, encoding="utf-8") as f:
        return json.load(f)


def _find_current_json(jsons_root: str) -> str | None:
    """Find the series JSON in current_event/. Returns path or None."""
    pattern = os.path.join(jsons_root, "current_event", "pagasa-hro-*.json")
    files = [f for f in glob.glob(pattern) if "fetch_state" not in f]
    return files[0] if files else None


def _find_past_json(jsons_root: str) -> str | None:
    """Find the most recent series JSON in past_events/."""
    pattern = os.path.join(jsons_root, "past_events", "pagasa-hro-*.json")
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def build(jsons_root: str, explicit_json: str = None) -> None:
    """
    Main build routine.

    Loads the current or most recent series JSON and location data,
    then writes display/site/index.html with both embedded.

    Args:
        jsons_root:    Root of jsons/ directory (contains current_event/ and past_events/)
        explicit_json: If provided, use this JSON path directly.
    """
    # ── Load HRO data ──
    is_past = False
    if explicit_json:
        json_path = explicit_json
    else:
        json_path = _find_current_json(jsons_root)
        if not json_path:
            json_path = _find_past_json(jsons_root)
            is_past = True if json_path else False

    hro_data = {}
    if json_path and os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as f:
            hro_data = json.load(f)
        print(f"Loaded: {json_path}")
    else:
        print("No series JSON found — building empty state page.")

    # ── Load location reference ──
    locations = _load_locations()

    # ── Write HTML ──
    os.makedirs(_DISPLAY_DIR, exist_ok=True)
    html = _render_html(hro_data, locations, is_past)
    with open(_OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Built: {_OUTPUT_HTML}")


def _render_html(hro_data: dict, locations: dict, is_past: bool) -> str:
    """Render the full HTML string with embedded JSON data."""

    hro_json   = json.dumps(hro_data,  ensure_ascii=False, separators=(',', ':'))
    loc_json   = json.dumps(locations, ensure_ascii=False, separators=(',', ':'))
    is_past_js = "true" if is_past else "false"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PAGASA Weather Advisory Viewer</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap" rel="stylesheet">
<style>
/* ── Reset & Base ── */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

:root {{
  --bg:          #0f1117;
  --surface:     #181c25;
  --surface2:    #1e2330;
  --border:      #2a3040;
  --text:        #d4dbe8;
  --text-dim:    #6b7a94;
  --text-bright: #eef2f8;
  --accent:      #4a9eff;
  --accent-dim:  #1a3a5c;

  --red:         #e53935;
  --red-bg:      #3b1010;
  --red-text:    #ff8a80;
  --orange:      #e65100;
  --orange-bg:   #3b1f08;
  --orange-text: #ffab40;
  --yellow:      #f9a825;
  --yellow-bg:   #2e2400;
  --yellow-text: #ffe57f;

  --font-sans:   'IBM Plex Sans', sans-serif;
  --font-mono:   'IBM Plex Mono', monospace;
  --radius:      6px;
  --radius-lg:   10px;
}}

html, body {{
  height: 100%;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-sans);
  font-size: 14px;
  line-height: 1.6;
}}

/* ── Layout ── */
.shell {{
  min-height: 100vh;
  display: grid;
  grid-template-rows: auto 1fr auto;
}}

header {{
  border-bottom: 1px solid var(--border);
  padding: 18px 28px;
  display: flex;
  align-items: center;
  gap: 14px;
  background: var(--surface);
}}

.logo-mark {{
  width: 36px; height: 36px;
  background: var(--accent);
  border-radius: 8px;
  display: grid; place-items: center;
  font-family: var(--font-mono);
  font-weight: 600;
  font-size: 13px;
  color: #fff;
  letter-spacing: -0.5px;
  flex-shrink: 0;
}}

.header-text h1 {{
  font-size: 15px;
  font-weight: 600;
  color: var(--text-bright);
  letter-spacing: 0.02em;
}}
.header-text p {{
  font-size: 11px;
  color: var(--text-dim);
  font-family: var(--font-mono);
}}

main {{
  padding: 28px;
  max-width: 1100px;
  width: 100%;
  margin: 0 auto;
}}

footer {{
  border-top: 1px solid var(--border);
  padding: 12px 28px;
  font-size: 11px;
  color: var(--text-dim);
  font-family: var(--font-mono);
  background: var(--surface);
}}

/* ── Location Selector ── */
.selector-row {{
  display: flex;
  gap: 12px;
  margin-bottom: 28px;
  flex-wrap: wrap;
  align-items: flex-end;
}}

.selector-group {{
  display: flex;
  flex-direction: column;
  gap: 5px;
}}

.selector-group label {{
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--text-dim);
  font-family: var(--font-mono);
}}

select {{
  background: var(--surface2);
  border: 1px solid var(--border);
  color: var(--text-bright);
  padding: 8px 12px;
  border-radius: var(--radius);
  font-family: var(--font-sans);
  font-size: 13px;
  min-width: 200px;
  cursor: pointer;
  appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%236b7a94' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 10px center;
  padding-right: 30px;
  transition: border-color 0.15s;
}}

select:focus {{
  outline: none;
  border-color: var(--accent);
}}

select option {{ background: var(--surface2); }}

/* ── Past Event Banner ── */
.past-banner {{
  background: var(--surface2);
  border: 1px solid var(--border);
  border-left: 3px solid var(--text-dim);
  border-radius: var(--radius);
  padding: 10px 14px;
  margin-bottom: 20px;
  font-size: 12px;
  color: var(--text-dim);
  font-family: var(--font-mono);
  display: flex;
  align-items: center;
  gap: 8px;
}}

.past-banner::before {{
  content: "⚠";
  font-size: 14px;
}}

/* ── No Advisory ── */
.no-advisory {{
  text-align: center;
  padding: 60px 20px;
  color: var(--text-dim);
}}
.no-advisory .icon {{ font-size: 36px; margin-bottom: 12px; }}
.no-advisory h2 {{ font-size: 16px; font-weight: 400; margin-bottom: 6px; color: var(--text); }}
.no-advisory p {{ font-size: 12px; font-family: var(--font-mono); }}

/* ── Advisory Timeline ── */
.timeline {{ display: flex; flex-direction: column; gap: 10px; }}

.advisory-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  transition: border-color 0.15s;
}}

.advisory-card.is-final {{ border-color: #2a3a4a; opacity: 0.8; }}

.card-header {{
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  cursor: pointer;
  user-select: none;
  transition: background 0.1s;
}}

.card-header:hover {{ background: var(--surface2); }}

.adv-number {{
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
  background: var(--accent-dim);
  padding: 2px 8px;
  border-radius: 4px;
  white-space: nowrap;
  flex-shrink: 0;
}}

.adv-datetime {{
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-dim);
  flex-shrink: 0;
}}

.adv-systems {{
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  flex: 1;
}}

.system-tag {{
  font-size: 10px;
  padding: 2px 7px;
  border-radius: 3px;
  background: var(--surface2);
  border: 1px solid var(--border);
  color: var(--text-dim);
  font-family: var(--font-mono);
  white-space: nowrap;
}}

.final-badge {{
  font-size: 10px;
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--text-dim);
  border: 1px solid var(--border);
  padding: 2px 7px;
  border-radius: 3px;
  letter-spacing: 0.05em;
  flex-shrink: 0;
}}

.toggle-icon {{
  color: var(--text-dim);
  font-size: 12px;
  flex-shrink: 0;
  transition: transform 0.2s;
}}

.card-header.open .toggle-icon {{ transform: rotate(180deg); }}

/* ── Card Body ── */
.card-body {{
  display: none;
  border-top: 1px solid var(--border);
  padding: 16px;
  flex-direction: column;
  gap: 20px;
}}

.card-body.open {{ display: flex; }}

/* ── Location note when nothing matches ── */
.no-match {{
  font-size: 12px;
  color: var(--text-dim);
  font-family: var(--font-mono);
  padding: 8px 0;
}}

/* ── Table System Section ── */
.system-section {{ display: flex; flex-direction: column; gap: 8px; }}

.system-label {{
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-dim);
  font-family: var(--font-mono);
  font-weight: 600;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--border);
}}

/* ── Data Table ── */
.hro-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}}

.hro-table th {{
  text-align: left;
  padding: 8px 10px;
  font-family: var(--font-mono);
  font-weight: 600;
  font-size: 10px;
  color: var(--text-dim);
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}}

.hro-table th .period-dates {{
  display: block;
  font-size: 10px;
  color: var(--text-dim);
  font-weight: 400;
  margin-top: 2px;
  white-space: nowrap;
}}

.hro-table td {{
  padding: 7px 10px;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}}

.hro-table tr:last-child td {{ border-bottom: none; }}

.hro-table tr:hover td {{ background: var(--surface2); }}

.province-cell {{
  font-weight: 400;
  color: var(--text-bright);
  white-space: nowrap;
}}

.period-cell {{
  text-align: center;
}}

.dash {{ color: var(--text-dim); font-family: var(--font-mono); }}

/* ── Chips ── */
.chips {{ display: flex; flex-wrap: wrap; gap: 4px; justify-content: center; }}

.chip {{
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  white-space: nowrap;
  line-height: 1.6;
}}

.chip-above200  {{ background: var(--red-bg);    color: var(--red-text);    border: 1px solid var(--red); }}
.chip-100to200  {{ background: var(--orange-bg); color: var(--orange-text); border: 1px solid var(--orange); }}
.chip-50to100   {{ background: var(--yellow-bg); color: var(--yellow-text); border: 1px solid var(--yellow); }}

/* ── Updated at ── */
.updated-at {{
  font-size: 10px;
  color: var(--text-dim);
  font-family: var(--font-mono);
  margin-bottom: 16px;
}}

/* ── Animations ── */
@keyframes fadeIn {{
  from {{ opacity: 0; transform: translateY(6px); }}
  to   {{ opacity: 1; transform: translateY(0); }}
}}

.advisory-card {{
  animation: fadeIn 0.2s ease both;
}}
</style>
</head>
<body>
<div class="shell">

<header>
  <div class="logo-mark">PA</div>
  <div class="header-text">
    <h1>PAGASA Weather Advisory Viewer</h1>
    <p>Heavy Rainfall Outlook — location filter</p>
  </div>
</header>

<main>
  <div class="selector-row">
    <div class="selector-group">
      <label>Region</label>
      <select id="sel-region" onchange="onRegionChange()">
        <option value="">— Select region —</option>
      </select>
    </div>
    <div class="selector-group" id="province-group" style="display:none">
      <label>Province</label>
      <select id="sel-province" onchange="render()">
        <option value="">All provinces</option>
      </select>
    </div>
  </div>

  <div id="output"></div>
</main>

<footer>
  PAGASA Heavy Rainfall Outlook data · Built from source JSON · Open locally via browser
</footer>

</div>

<script>
// ── Embedded data ──────────────────────────────────────────────
const HRO_DATA     = {hro_json};
const LOCATIONS    = {loc_json};
const IS_PAST      = {is_past_js};

// ── Helpers ────────────────────────────────────────────────────

function fmtDt(iso) {{
  // Format ISO datetime as "Jun 4, 5PM" or "Dec 31 2025, 11PM"
  // Year shown only when different from current year or when crossing year boundary
  const d = new Date(iso);
  const now = new Date();
  const mo = d.toLocaleString('en-PH', {{ month: 'short', timeZone: 'Asia/Manila' }});
  const day = d.toLocaleString('en-PH', {{ day: 'numeric', timeZone: 'Asia/Manila' }});
  const hr = d.toLocaleString('en-PH', {{ hour: 'numeric', hour12: true, timeZone: 'Asia/Manila' }})
              .replace(':00', '').replace(' ', '');
  const yr = d.getFullYear();
  const showYear = yr !== now.getFullYear();
  return showYear ? `${{mo}} ${{day}} ${{yr}}, ${{hr}}` : `${{mo}} ${{day}}, ${{hr}}`;
}}

function fmtPeriodHeader(period) {{
  return `${{fmtDt(period.valid_from)}} → ${{fmtDt(period.valid_to)}}`;
}}

function chipClass(cat) {{
  if (cat === 'above_200mm')  return 'chip-above200';
  if (cat === '100_to_200mm') return 'chip-100to200';
  if (cat === '50_to_100mm')  return 'chip-50to100';
  return '';
}}

function chipLabel(cat) {{
  if (cat === 'above_200mm')  return '>200mm';
  if (cat === '100_to_200mm') return '100–200mm';
  if (cat === '50_to_100mm')  return '50–100mm';
  return '';
}}

// Get chip(s) for a location in a period
// Returns array of {{cat, locString}} for matching locations
function getMatches(period, targetProvince, allProvinces) {{
  const cats = ['above_200mm', '100_to_200mm', '50_to_100mm'];
  const results = [];
  for (const cat of cats) {{
    const locs = period.rainfall_categories[cat] || [];
    if (allProvinces) {{
      // Region mode: return per-province chips
      for (const loc of locs) {{
        if (allProvinces.some(p => loc === p || loc.includes(p))) {{
          results.push({{ cat, locString: loc }});
        }}
      }}
    }} else {{
      // Province mode: match this specific province
      const matched = locs.filter(loc => loc === targetProvince || loc.includes(targetProvince));
      for (const loc of matched) {{
        results.push({{ cat, locString: loc }});
      }}
    }}
  }}
  return results;
}}

// ── Location data ──────────────────────────────────────────────

function getRegions() {{
  return LOCATIONS.regions || [];
}}

function getProvincesOf(regionName) {{
  const r = getRegions().find(r => r.name === regionName);
  return r ? (r.provinces || []) : [];
}}

// ── Populate selectors ─────────────────────────────────────────

function populateRegions() {{
  const sel = document.getElementById('sel-region');
  getRegions().forEach(r => {{
    const opt = document.createElement('option');
    opt.value = r.name;
    opt.textContent = r.name;
    sel.appendChild(opt);
  }});
}}

function onRegionChange() {{
  const region = document.getElementById('sel-region').value;
  const provGroup = document.getElementById('province-group');
  const provSel = document.getElementById('sel-province');

  provSel.innerHTML = '<option value="">All provinces</option>';

  if (region) {{
    const provinces = getProvincesOf(region);
    provinces.forEach(p => {{
      const opt = document.createElement('option');
      opt.value = p;
      opt.textContent = p;
      provSel.appendChild(opt);
    }});
    provGroup.style.display = '';
  }} else {{
    provGroup.style.display = 'none';
  }}
  render();
}}

// ── Render ─────────────────────────────────────────────────────

function render() {{
  const region   = document.getElementById('sel-region').value;
  const province = document.getElementById('sel-province').value;
  const out = document.getElementById('output');

  if (!region) {{ out.innerHTML = ''; return; }}

  const advisories = HRO_DATA.advisories || {{}};
  const series     = HRO_DATA.series     || {{}};
  const keys       = Object.keys(advisories).sort().reverse(); // latest first

  if (!keys.length) {{
    out.innerHTML = renderNoAdvisory();
    return;
  }}

  let html = '';

  // Past event banner
  if (IS_PAST) {{
    html += `<div class="past-banner">
      No active advisory — showing last completed series
      (${{fmtDt(series.started)}} to ${{series.ended || '?'}})
    </div>`;
  }}

  // Updated at
  const latestKey = keys[0];
  html += `<div class="updated-at">Latest bulletin: ${{fmtDt(latestKey)}}</div>`;

  // Advisory cards
  html += '<div class="timeline">';
  keys.forEach((key, idx) => {{
    html += renderCard(advisories[key], key, idx === 0, region, province);
  }});
  html += '</div>';

  out.innerHTML = html;
}}

function renderNoAdvisory() {{
  return `<div class="no-advisory">
    <div class="icon">🌤</div>
    <h2>No active Heavy Rainfall Outlook</h2>
    <p>No bulletin is currently being issued for this location.</p>
  </div>`;
}}

function renderCard(adv, key, isLatest, region, province) {{
  const isFinal  = adv.is_final;
  const tables   = adv.tables || {{}};
  const systems  = Object.values(tables).map(t => t.name || t.weather_system || '').filter(Boolean);
  const provinces = province ? null : getProvincesOf(region);

  // Card header
  const systemTags = systems.map(s => `<span class="system-tag">${{s}}</span>`).join('');
  const finalBadge = isFinal ? '<span class="final-badge">FINAL</span>' : '';
  const openClass  = isLatest ? 'open' : '';

  let html = `
  <div class="advisory-card${{isFinal ? ' is-final' : ''}}" style="animation-delay:${{0}}ms">
    <div class="card-header ${{openClass}}" onclick="toggleCard(this)">
      <span class="adv-number">ADV-${{String(adv.number).padStart(3,'0')}}</span>
      <span class="adv-datetime">${{fmtDt(key)}}</span>
      <span class="adv-systems">${{systemTags}}</span>
      ${{finalBadge}}
      <span class="toggle-icon">▾</span>
    </div>
    <div class="card-body ${{openClass}}">`;

  // One section per table (weather system)
  const tableKeys = Object.keys(tables);

  if (tableKeys.length === 0) {{
    // Final advisory with no table
    html += `<div class="no-match">This is the final advisory. No further heavy rainfall outlook issued.</div>`;
  }} else {{
    tableKeys.forEach(tk => {{
      const tbl     = tables[tk];
      const sysName = tbl.name || tbl.weather_system || 'Weather System';
      const periods = Object.values(tbl.forecast_periods || {{}});

      html += `<div class="system-section">
        <div class="system-label">${{sysName}}</div>`;

      if (!periods.length) {{
        html += `<div class="no-match">No forecast periods.</div>`;
      }} else {{
        html += renderTable(periods, region, province, provinces);
      }}

      html += `</div>`;
    }});
  }}

  html += `</div></div>`;
  return html;
}}

function renderTable(periods, region, province, allProvinces) {{
  const showProvinceCol = !province; // region mode: show province column
  const targetProvince  = province || null;
  const rows = showProvinceCol ? allProvinces : [targetProvince];

  let html = `<table class="hro-table"><thead><tr>`;
  if (showProvinceCol) html += `<th>Province</th>`;
  periods.forEach(p => {{
    html += `<th>
      <span class="period-dates">${{fmtPeriodHeader(p)}}</span>
    </th>`;
  }});
  html += `</tr></thead><tbody>`;

  rows.forEach(prov => {{
    html += `<tr>`;
    if (showProvinceCol) html += `<td class="province-cell">${{prov}}</td>`;

    periods.forEach(period => {{
      const matches = getMatches(period, prov, null);
      if (!matches.length) {{
        html += `<td class="period-cell"><span class="dash">—</span></td>`;
      }} else {{
        const chips = matches.map(m =>
          `<span class="chip ${{chipClass(m.cat)}}">${{m.locString}}</span>`
        ).join('');
        html += `<td class="period-cell"><div class="chips">${{chips}}</div></td>`;
      }}
    }});

    html += `</tr>`;
  }});

  html += `</tbody></table>`;
  return html;
}}

function toggleCard(header) {{
  header.classList.toggle('open');
  header.nextElementSibling.classList.toggle('open');
}}

// ── Init ───────────────────────────────────────────────────────
populateRegions();
</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(
        description="Embed HRO JSON data into display/site/index.html."
    )
    ap.add_argument(
        '--jsons', default=None,
        help="Root jsons/ folder (contains current_event/ and past_events/). "
             "Defaults to data/hro/jsons/ relative to repo root.",
    )
    ap.add_argument(
        '--json', default=None,
        help="Explicit JSON file path to use directly.",
    )
    args = ap.parse_args()

    jsons_root = args.jsons or os.path.join(_REPO_ROOT, "data", "hro", "jsons")
    build(jsons_root=jsons_root, explicit_json=args.json)


if __name__ == "__main__":
    main()
