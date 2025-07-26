#!/usr/bin/env python
# coding: utf-8

# # about this notebook
# 
# This extracts heavy rainfall outlook information from the weather advisory issued by PAGASA: https://www.pagasa.dost.gov.ph/weather/weather-advisory. 
# 
# The output is  json file containing information from one or more weather advisory pdfs. 
# 
# Working versions must document the coverage dates of successfully parsed weather advisory pdfs.
# 
# - version date: 2025-07-26
#   - valid for weather advisories from May 22 to July 25 2025
#   - error if pdf is image file (no text being recognized)
#   - - cases:
#     - - Advisory 7 and 32 of July 15 2025 advisory series
#       - Advisory 1, 4,5,6,7, 10 of July 2 2025 advisory series
#   - error if table has no column for potential impacts (case: Advisory 15,16 of May 29 2025 advisory series)

# # import libraries

# In[1]:


import re
import os
import json
from datetime import datetime
from typing import Dict, List, Any, Optional
import pdfplumber


# # accessory functions

# In[ ]:


def get_advisory_by_datetime(results: Dict[str, Dict[str, Any]], target_datetime: str) -> Optional[Dict[str, Any]]:
    """
    Get specific advisory by datetime

    Args:
        results: Batch parsing results
        target_datetime: ISO datetime string (e.g., "2025-07-23T17:00:00+08:00")

    Returns:
        Advisory data or None if not found
    """
    return results.get(target_datetime)

def filter_advisories_by_date(results: Dict[str, Dict[str, Any]], date: str) -> Dict[str, Dict[str, Any]]:
    """
    Filter advisories by date

    Args:
        results: Batch parsing results
        date: Date string (e.g., "2025-07-23")

    Returns:
        Filtered advisories for the specified date
    """
    return {k: v for k, v in results.items() if k.startswith(date)}

def get_chronological_advisories(results: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Sort advisories chronologically

    Args:
        results: Batch parsing results

    Returns:
        Advisories sorted by datetime
    """
    # Filter out error entries and sort by datetime
    valid_results = {k: v for k, v in results.items() if not k.startswith("ERROR_")}
    return dict(sorted(valid_results.items()))

def get_latest_advisory(results: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Get the most recent advisory

    Args:
        results: Batch parsing results

    Returns:
        Latest advisory data or None if no valid advisories
    """
    valid_results = {k: v for k, v in results.items() if not k.startswith("ERROR_")}
    if not valid_results:
        return None

    latest_datetime = max(valid_results.keys())
    return valid_results[latest_datetime]

def analyze_advisory_sequence(results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Analyze the sequence of advisories for patterns and gaps

    Args:
        results: Batch parsing results

    Returns:
        Analysis summary
    """
    from datetime import datetime, timedelta

    valid_results = {k: v for k, v in results.items() if not k.startswith("ERROR_")}
    if len(valid_results) < 2:
        return {"message": "Need at least 2 advisories for sequence analysis"}

    sorted_datetimes = sorted(valid_results.keys())
    analysis = {
        "total_advisories": len(valid_results),
        "time_span": {
            "start": sorted_datetimes[0],
            "end": sorted_datetimes[-1]
        },
        "advisory_numbers": [valid_results[dt]["number"] for dt in sorted_datetimes],
        "weather_systems": [],
        "time_gaps": []
    }

    # Extract unique weather systems
    weather_systems = set()
    for advisory in valid_results.values():
        for table in advisory.get("tables", {}).values():
            weather_system = table.get("weather_system") or table.get("name", "")
            if weather_system:
                weather_systems.add(weather_system)
    analysis["weather_systems"] = sorted(list(weather_systems))

    # Analyze time gaps between advisories
    for i in range(len(sorted_datetimes) - 1):
        current_time = datetime.fromisoformat(sorted_datetimes[i].replace('+08:00', ''))
        next_time = datetime.fromisoformat(sorted_datetimes[i + 1].replace('+08:00', ''))
        gap = next_time - current_time

        analysis["time_gaps"].append({
            "from": sorted_datetimes[i],
            "to": sorted_datetimes[i + 1],
            "gap_hours": gap.total_seconds() / 3600,
            "gap_description": str(gap)
        })

    return analysis

def demo_datetime_access(results: Dict[str, Dict[str, Any]]):
    """
    Demonstrate different ways to access datetime-keyed results
    """
    print("\n🎯 DATETIME ACCESS EXAMPLES:")

    # Get specific advisory
    sample_datetime = list(results.keys())[0] if results else None
    if sample_datetime and not sample_datetime.startswith("ERROR_"):
        print(f"✅ Access specific advisory:")
        print(f"   results['{sample_datetime}']['number'] = {results[sample_datetime]['number']}")

    # Get latest advisory
    latest = get_latest_advisory(results)
    if latest:
        print(f"✅ Latest advisory: #{latest['number']} at {latest['datetime']}")

    # Filter by date
    if results:
        sample_date = list(results.keys())[0][:10]  # Extract date part
        date_filtered = filter_advisories_by_date(results, sample_date)
        print(f"✅ Advisories on {sample_date}: {len(date_filtered)} found")

    # Chronological order
    chronological = get_chronological_advisories(results)
    print(f"✅ Chronological order: {len(chronological)} advisories sorted by time")

# Quick utility functions for users
def quick_batch_analysis(pdf_files: List[str]):
    """
    Quick batch analysis with datetime keys - useful for Colab users

    Args:
        pdf_files: List of PDF file paths

    Returns:
        Dictionary with datetime keys and analysis summary
    """
    print(f"🚀 Quick batch analysis of {len(pdf_files)} files...")

    results = batch_parse_pdfs_enhanced(pdf_files)
    analysis = analyze_advisory_sequence(results)

    print(f"\n📊 ANALYSIS SUMMARY:")
    print(f"   Processed: {analysis.get('total_advisories', 0)} advisories")
    print(f"   Weather systems: {', '.join(analysis.get('weather_systems', []))}")

    if analysis.get('time_span'):
        print(f"   Time range: {analysis['time_span']['start']} to {analysis['time_span']['end']}")

    return {
        "results": results,
        "analysis": analysis,
        "chronological": get_chronological_advisories(results)
    }

def compare_periods(result: Dict[str, Any]) -> None:
    """
    Compare data across forecast periods for analysis
    """
    print("\n" + "="*70)
    print("FORECAST PERIODS COMPARISON")
    print("="*70)

    for table_num, table_data in result.get("tables", {}).items():
        weather_system = table_data.get("weather_system") or table_data.get("name", "Unknown")
        print(f"\n📊 {weather_system}")

        periods = table_data.get("forecast_periods", {})

        for period_key, period_data in periods.items():
            print(f"\n   {period_key.upper()}: {period_data['description']}")

            rain_cats = period_data.get("rainfall_categories", {})
            for category, locations in rain_cats.items():
                if locations:
                    print(f"     {category}: {len(locations)} locations")
                    if len(locations) <= 3:
                        print(f"       → {', '.join(locations)}")
                    else:
                        print(f"       → {', '.join(locations[:3])}, ... (+{len(locations)-3} more)")


# # Core functions

# In[38]:


class EnhancedPAGASAParser:
    def __init__(self):
        self.month_map = {
            'January': 1, 'February': 2, 'March': 3, 'April': 4,
            'May': 5, 'June': 6, 'July': 7, 'August': 8,
            'September': 9, 'October': 10, 'November': 11, 'December': 12
        }

    def parse_advisory(self, pdf_path: str) -> Dict[str, Any]:
        """
        Parse PAGASA advisory PDF to enhanced JSON format with all forecast periods
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        with pdfplumber.open(pdf_path) as pdf:
            # Extract basic info
            page = pdf.pages[0]  # PAGASA advisories are typically single page
            text = page.extract_text()

            # Extract metadata
            advisory_num = self._extract_advisory_number(text)
            datetime_info = self._extract_datetime(text)

            # Extract tables with full content
            tables = self._extract_full_tables(page)

            # Parse tables into enhanced target format
            parsed_tables = {}
            for i, table in enumerate(tables, 1):
                parsed_table = self._parse_table_to_enhanced_format(table)
                if parsed_table:
                    parsed_tables[i] = parsed_table

            # return None if advisory number or issue date is unknown
            if advisory_num==-1 or datetime_info["raw_datetime"]=="Unknown":
                return None
            else:
                return {
                    "advisory_id": f"ADV-{advisory_num:03d}",
                    "number": advisory_num,
                    "datetime": datetime_info["iso_datetime"],
                    "raw_datetime": datetime_info["raw_datetime"],
                    "tables": parsed_tables
                }

    def _extract_advisory_number(self, text: str) -> int:
        """Extract advisory number"""
        pattern = r'WEATHER ADVISORY NO\.\s*(\d+)'
        match = re.search(pattern, text, re.IGNORECASE)
        return int(match.group(1)) if match else -1

    def _extract_datetime(self, text: str) -> Dict[str, str]:
        """Extract and parse datetime"""
        pattern = r'Issued at:\s*(\d{1,2}:\d{2}\s*[AP]M),\s*(\d{1,2})\s+(\w+)\s+(\d{4})'
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            time_str, day, month, year = match.groups()
            raw_datetime = f"{time_str}, {day} {month} {year}"

            try:
                time_obj = datetime.strptime(time_str.strip(), "%I:%M %p")
                month_num = self.month_map.get(month, 1)
                dt = datetime(int(year), month_num, int(day), time_obj.hour, time_obj.minute)
                iso_datetime = dt.strftime("%Y-%m-%dT%H:%M:%S") + "+08:00"

                return {"raw_datetime": raw_datetime, "iso_datetime": iso_datetime}
            except:
                pass

        return {"raw_datetime": "Unknown", "iso_datetime": "Unknown"}

    def _extract_full_tables(self, page) -> List[Dict[str, Any]]:
        """Extract complete table content"""
        tables = []
        detected_tables = page.find_tables()

        for table in detected_tables:
            try:
                # Extract ALL table data
                table_data = table.extract()

                if table_data and len(table_data) > 2:  # Need header + at least 2 data rows
                    # Clean the data
                    cleaned_data = []
                    for row in table_data:
                        if row and any(cell and str(cell).strip() for cell in row):
                            cleaned_row = [str(cell).strip() if cell else "" for cell in row]
                            cleaned_data.append(cleaned_row)

                    if cleaned_data:
                        # Find table title from first row
                        title = self._extract_table_title(cleaned_data)

                        tables.append({
                            "data": cleaned_data,
                            "title": title,
                            "rows": len(cleaned_data),
                            "columns": len(cleaned_data[0]) if cleaned_data else 0
                        })

            except Exception as e:
                print(f"Error extracting table data: {e}")
                continue

        return tables

    def _extract_table_title(self, table_data: List[List[str]]) -> str:
        """Extract table title from first row"""
        if table_data and table_data[0]:
            first_row = " ".join(table_data[0]).strip()
            return first_row.replace('"','').replace("'","")

        return "no table title"

    def _parse_table_to_enhanced_format(self, table: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse table data into enhanced target JSON format with all periods"""
        table_data = table["data"]
        title = table["title"]

        if len(table_data) < 3:  # Need at least title, header, and one data row
            return None

        # Extract all forecast periods and their data
        forecast_periods = self._extract_all_forecast_periods(table_data)

        if not forecast_periods:
            return None

        # Build result
        result = {
            "forecast_periods": forecast_periods
        }

        # Add weather system key
        if "Southwest Monsoon" in title:
            result["name"] = title
        else:
            result["weather_system"] = title

        return result

    def _extract_all_forecast_periods(self, table_data: List[List[str]]) -> Dict[str, Any]:
        """Extract all forecast periods and their rainfall data"""
        # First, extract period descriptions from header row
        periods_info = self._extract_all_periods(table_data)

        if not periods_info:
            return {}

        # Initialize the structure
        forecast_periods = {}
        for period_key, description in periods_info.items():
            forecast_periods[period_key] = {
                "description": description,
                "rainfall_categories": {
                    "above_200mm": [],
                    "100_to_200mm": [],
                    "50_to_100mm": []
                }
            }

        # Extract rainfall data from table rows
        data_rows = table_data[2:] if len(table_data) > 2 else []


        for row in data_rows:
            if not row or len(row) < 2:
                continue


            # Determine rainfall category from first column
            rainfall_marker = row[0].strip()

            # if re.search(r'\(>200\s*mm\)', rainfall_marker):
            #     category = "above_200mm"
            # elif re.search(r'\(100\s*[–-]\s*200\s*mm\)', rainfall_marker):
            #     category = "100_to_200mm"
            # elif re.search(r'\(50\s*[–-]\s*100\s*mm\)', rainfall_marker):
            #     category = "50_to_100mm"



            if re.search(r'\(?>200\s*mm\)?', rainfall_marker):
                category = "above_200mm"
            elif re.search(r'\(?100\s*[–-]\s*200\s*mm\)?', rainfall_marker):
                category = "100_to_200mm"
            elif re.search(r'\(?50\s*[–-]\s*100\s*mm\)?', rainfall_marker):
                category = "50_to_100mm"
            else:
                print("---------------------CHECK: ", "rainfall categories not detected")
                continue  # Not a rainfall category row

            # Extract locations for each time period column

            for col_idx in range(1, min(len(row), len(periods_info) + 1)):
                period_key = f"period_{col_idx}"

                if period_key in forecast_periods and col_idx < len(row):

                    locations_text = row[col_idx].strip()


                    if locations_text and locations_text != '-':
                        locations = self._parse_locations(locations_text)
                        forecast_periods[period_key]["rainfall_categories"][category] = locations

        return forecast_periods

    def _extract_all_periods(self, table_data: List[List[str]]) -> Dict[str, str]:
        """Extract all time period descriptions from header row"""
        periods = {}

        if len(table_data) > 1:
            header_row = table_data[1]

            # Extract periods from columns 1, 2, 3 (skip column 0 = "Forecast Rainfall")
            for col_idx in range(1, min(len(header_row), 5)):  # Up to 4 columns (skip last "Potential Impacts")
                if col_idx < len(header_row) and header_row[col_idx].strip():
                    period_text = header_row[col_idx].strip()

                    # Skip "Potential Impacts" column
                    if "Potential Impacts" in period_text:
                        break

                    # Clean up the period description
                    period_desc = re.sub(r'\n', ' ', period_text)
                    period_desc = re.sub(r'\s+', ' ', period_desc)

                    if period_desc:
                        periods[f"period_{col_idx}"] = period_desc

        return periods

    def _parse_locations(self, text: str) -> List[str]:
        """Parse location names from text"""


        if not text or text.strip() == '-':
            return []

        # Clean up text
        text = re.sub(r'\n', ' ', text)  # Replace newlines with spaces
        text = re.sub(r'\s+', ' ', text)  # Normalize whitespace
        text = text.strip()

        # Split by common delimiters
        locations = []
        parts = re.split(r',|\sand\s', text)

        for part in parts:

            part = part.strip()
            if part and len(part) > 2 and part != '-':
                # Remove common non-location artifacts
                if not re.search(r'^(and|or|near|in|the|of)$', part, re.IGNORECASE):
                    locations.append(part)

        return locations

def parse_pagasa_pdf_enhanced(pdf_path: str) -> Dict[str, Any]:
    """
    Main function to parse PAGASA PDF to enhanced format with all periods

    Args:
        pdf_path: Path to PAGASA advisory PDF

    Returns:
        Dictionary in enhanced JSON format with all forecast periods
    """
    parser = EnhancedPAGASAParser()
    return parser.parse_advisory(pdf_path)

def batch_parse_pdfs_enhanced(pdf_files: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Parse multiple PDF files with datetime as unique keys

    Args:
        pdf_files: List of PDF file paths

    Returns:
        Dictionary with ISO datetime as key and parsed data as value
    """
    results = {}
    parser = EnhancedPAGASAParser()

    for pdf_file in pdf_files:
        try:
            print(f"Parsing {pdf_file}...")
            result = parser.parse_advisory(pdf_file)

            if result==None:
                print(f'Failed parsing {pdf_file}')
                continue

            # Use datetime as unique key
            datetime_key = result["datetime"]
            result["source_file"] = pdf_file  # Add source filename for reference

            results[datetime_key] = result
            print(f"✅ {pdf_file} → {datetime_key}")

            # Show quick summary
            tables = result.get("tables", {})
            for table_num, table_data in tables.items():
                periods = table_data.get("forecast_periods", {})
                weather_system = table_data.get("weather_system") or table_data.get("name", "Unknown")
                print(f"   Table {table_num}: {weather_system} ({len(periods)} periods)")

        except Exception as e:
            print(f"❌ Error parsing {pdf_file}: {e}")
            # Store error with timestamp if possible, otherwise use filename
            error_key = f"ERROR_{pdf_file}_{datetime.now().isoformat()}"
            results[error_key] = {"error": str(e), "source_file": pdf_file}

    return results

def get_chronological_advisories(results: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Sort advisories chronologically

    Args:
        results: Batch parsing results

    Returns:
        Advisories sorted by datetime
    """
    # Filter out error entries and sort by datetime
    valid_results = {k: v for k, v in results.items() if not k.startswith("ERROR_")}
    return dict(sorted(valid_results.items()))

def export_batch_results(results: Dict[str, Dict[str, Any]], output_file: str = "pagasa-hro.json", dest_folder='hro-jsons'):
    """
    Export batch results to JSON file with datetime keys

    Args:
        results: Batch parsing results
        output_file: Output filename
    """

    results=get_chronological_advisories(results)

    try:
        with open(os.path.join(dest_folder,output_file), 'w') as f:
            json.dump(results, f, indent=2)
        print(f"✅ Batch results exported to: {os.path.join(dest_folder,output_file)}")

        # Show summary
        valid_results = {k: v for k, v in results.items() if not k.startswith("ERROR_")}
        error_count = len(results) - len(valid_results)

        print(f"📊 Summary:")
        print(f"   Successfully parsed: {len(valid_results)} advisories")
        if error_count > 0:
            print(f"   Errors: {error_count}")

        if valid_results:
            sorted_times = sorted(valid_results.keys())
            print(f"   Time range: {sorted_times[0]} to {sorted_times[-1]}")

        return True

    except Exception as e:
        print(f"❌ Error exporting results: {e}")
        return False




def get_pdf_paths_single_folder(folder_path):
    pdf_paths = []
    for filename in os.listdir(folder_path):
        full_path = os.path.join(folder_path, filename)
        if os.path.isfile(full_path) and filename.lower().endswith(".pdf"):
            pdf_paths.append(full_path)
    return pdf_paths



def main(source_folder='hro-pdfs',dest_folder='hro-jsons'):


    pdf_files = get_pdf_paths_single_folder(source_folder)

    if not pdf_files:
        print("No PDF files found")
        return

    # Parse all files with datetime keys
    print(f"\n🚀 Starting batch processing of {len(pdf_files)} files...")
    results = batch_parse_pdfs_enhanced(pdf_files)

    # Export results
    if results:
        success = export_batch_results(results, "pagasa-hro.json")

if __name__ == "__main__":
     main()


# In[ ]:




