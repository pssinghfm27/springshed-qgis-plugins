"""File ingest loaders. Dispatch on file extension."""
from __future__ import annotations

import os
from typing import Dict, Optional

from . import csv_loader, excel_loader, kml_loader


def sniff(file_path: str) -> str:
    """Return the loader kind for a path: 'csv', 'excel', 'kml', 'kmz', 'pdf', 'image', 'unknown'."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".csv", ".tsv", ".txt"):
        return "csv"
    if ext in (".xls", ".xlsx", ".xlsm"):
        return "excel"
    if ext == ".kml":
        return "kml"
    if ext == ".kmz":
        return "kmz"
    if ext == ".pdf":
        return "pdf"
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp", ".gif"):
        return "image"
    return "unknown"


def load_to_layer_inputs(file_path: str) -> Dict:
    """
    Dispatch a file to the right loader and return a dict describing
    how it should be added as a QGIS layer.

    Return shape:
    {
        "kind": "points" | "lines" | "polygons" | "mixed",
        "source_path": "<path QGIS can open>" | None,
        "geojson": {...} | None,
        "layer_name": str,
        "provider": "ogr" | "memory",
        "notes": str,
    }
    """
    kind = sniff(file_path)
    if kind == "csv":
        return csv_loader.load(file_path)
    if kind == "excel":
        return excel_loader.load(file_path)
    if kind == "kml":
        return kml_loader.load_kml(file_path)
    if kind == "kmz":
        return kml_loader.load_kmz(file_path)
    raise ValueError(f"Unsupported file type for layer ingest: {file_path}")
