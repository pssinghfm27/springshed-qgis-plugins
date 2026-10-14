"""
Excel (.xlsx / .xlsm / .xls) loader.

Strategy:
  1. For .xlsx/.xlsm: parse with openpyxl (ships with modern QGIS on all
     platforms as part of the Processing plugin's dependencies).
  2. For .xls: try xlrd. If unavailable, raise a clear message telling the
     user to re-save as .xlsx.
  3. Convert the active sheet (or a caller-selected sheet) into a list of
     dicts, then delegate to csv_loader's coordinate detection by writing
     a temporary CSV.

Why the roundtrip through CSV? Keeps coordinate detection, DMS parsing,
and WKT parsing logic in ONE place (csv_loader) rather than duplicating.
"""
from __future__ import annotations

import csv
import os
import tempfile
from typing import Dict, List, Optional

from ..utils import logging as log
from . import csv_loader


def list_sheets(path: str) -> List[str]:
    """Return the sheet names available in an Excel file."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        try:
            import openpyxl  # type: ignore
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            return list(wb.sheetnames)
        except ImportError:
            raise RuntimeError(
                "openpyxl is required to read .xlsx files. Install it with:\n"
                "  python -m pip install openpyxl --break-system-packages"
            )
    if ext == ".xls":
        try:
            import xlrd  # type: ignore
            wb = xlrd.open_workbook(path)
            return wb.sheet_names()
        except ImportError:
            raise RuntimeError(
                "xlrd is required to read legacy .xls files. "
                "Consider re-saving the file as .xlsx in Excel."
            )
    raise ValueError(f"Not an Excel file: {path}")


def load(path: str, sheet_name: Optional[str] = None) -> Dict:
    """Load the given sheet (or the first sheet) from an Excel workbook."""
    rows = _rows_from_excel(path, sheet_name)
    if not rows:
        raise ValueError(f"Excel file appears empty: {path}")

    header = rows[0]
    data_rows = rows[1:]
    csv_path = _write_temp_csv(header, data_rows)

    # Delegate to CSV loader for coordinate detection etc.
    result = csv_loader.load(csv_path)
    base = os.path.splitext(os.path.basename(path))[0]
    result["layer_name"] = f"{base} [{sheet_name or header and 'sheet1'}]"
    log.info(f"Excel loaded: {path} (sheet={sheet_name}) -> {len(data_rows)} rows")
    try:
        os.remove(csv_path)
    except OSError:
        pass
    return result


# ---------------------------------------------------------------------------

def _rows_from_excel(path: str, sheet_name: Optional[str]) -> List[List]:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        import openpyxl  # type: ignore
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb[wb.sheetnames[0]]
        rows = []
        for r in ws.iter_rows(values_only=True):
            rows.append(["" if c is None else str(c) for c in r])
        return rows

    if ext == ".xls":
        import xlrd  # type: ignore
        wb = xlrd.open_workbook(path)
        ws = wb.sheet_by_name(sheet_name) if sheet_name else wb.sheet_by_index(0)
        rows = []
        for r in range(ws.nrows):
            row = []
            for c in range(ws.ncols):
                v = ws.cell_value(r, c)
                row.append("" if v is None else str(v))
            rows.append(row)
        return rows

    raise ValueError(f"Not an Excel file: {path}")


def _write_temp_csv(header: List[str], rows: List[List]) -> str:
    fd, path = tempfile.mkstemp(prefix="excel_import_", suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return path
