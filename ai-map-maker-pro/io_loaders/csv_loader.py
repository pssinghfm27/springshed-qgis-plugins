"""
CSV / TSV / plain-text loader with coordinate auto-detection.

Recognizes common column name patterns:
  lat / latitude / y / north
  lon / lng / long / longitude / x / east

Also handles:
  - DMS strings ("28°36'50\"N") by parsing to decimal
  - Tab-separated or comma-separated auto-detection
  - A WKT column ("wkt", "geometry", "geom") if present

Produces a GeoJSON FeatureCollection written to a temp .geojson file,
which QGIS can load with the ogr provider — no pandas dependency.
"""
from __future__ import annotations

import csv
import json
import os
import re
import tempfile
from typing import Dict, List, Optional, Tuple

from ..utils import logging as log

_LAT_KEYS = ("latitude", "lat", "y", "north", "northing")
_LON_KEYS = ("longitude", "lon", "lng", "long", "x", "east", "easting")
_WKT_KEYS = ("wkt", "geometry", "geom", "the_geom")


def load(path: str) -> Dict:
    """Load a CSV/TSV file, detect coords, return a layer-input dict."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(4096)
        f.seek(0)
        dialect = _sniff_dialect(sample)
        reader = csv.DictReader(f, dialect=dialect)
        rows = list(reader)
        fieldnames = [fn for fn in (reader.fieldnames or []) if fn]

    if not rows:
        raise ValueError(f"CSV has no data rows: {path}")

    lat_col, lon_col = _find_coord_columns(fieldnames)
    wkt_col = _find_column(fieldnames, _WKT_KEYS)

    features: List[Dict] = []
    if wkt_col:
        for row in rows:
            g = _parse_wkt(row.get(wkt_col) or "")
            if g is not None:
                features.append({
                    "type": "Feature",
                    "geometry": g,
                    "properties": {k: v for k, v in row.items() if k != wkt_col},
                })
    elif lat_col and lon_col:
        for row in rows:
            lat = _parse_coord(row.get(lat_col))
            lon = _parse_coord(row.get(lon_col))
            if lat is None or lon is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {k: v for k, v in row.items()
                               if k not in (lat_col, lon_col)},
            })
    else:
        raise ValueError(
            f"Could not find coordinate columns in {os.path.basename(path)}. "
            f"Expected columns like: lat/latitude and lon/longitude, "
            f"or a single 'wkt'/'geometry' column. "
            f"Got columns: {fieldnames}"
        )

    if not features:
        raise ValueError(f"No valid geometries found in {path}")

    gj = {"type": "FeatureCollection", "features": features}
    out_path = _write_geojson(gj)
    log.info(f"CSV loaded: {len(features)} features from {path}")
    return {
        "kind": "points" if wkt_col is None else "mixed",
        "source_path": out_path,
        "geojson": gj,
        "layer_name": os.path.splitext(os.path.basename(path))[0],
        "provider": "ogr",
        "notes": f"{len(features)} features from CSV",
    }


# ---------------------------------------------------------------------------

def _sniff_dialect(sample: str):
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        return csv.excel  # fallback to plain comma


def _find_column(fieldnames: List[str], candidates: Tuple[str, ...]) -> Optional[str]:
    lowered = {fn.strip().lower(): fn for fn in fieldnames}
    for c in candidates:
        if c in lowered:
            return lowered[c]
    # Partial match (e.g. "pickup_lat")
    for c in candidates:
        for lc, original in lowered.items():
            if c in lc:
                return original
    return None


def _find_coord_columns(fieldnames: List[str]) -> Tuple[Optional[str], Optional[str]]:
    return _find_column(fieldnames, _LAT_KEYS), _find_column(fieldnames, _LON_KEYS)


# Decimal degree or DMS parser
_DMS_RE = re.compile(
    r"^\s*([+-]?\d+(?:\.\d+)?)[°\s:]+"
    r"(?:(\d+(?:\.\d+)?)['\s:]*)?"
    r"(?:(\d+(?:\.\d+)?)[\"]?)?\s*"
    r"([NSEWnsew])?\s*$"
)


def _parse_coord(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    m = _DMS_RE.match(s)
    if m:
        deg = float(m.group(1))
        minutes = float(m.group(2) or 0)
        seconds = float(m.group(3) or 0)
        hemi = (m.group(4) or "").upper()
        val = abs(deg) + minutes / 60 + seconds / 3600
        if deg < 0 or hemi in ("S", "W"):
            val = -val
        return val
    return None


def _parse_wkt(s: str) -> Optional[Dict]:
    """Very small WKT subset parser (POINT, LINESTRING, POLYGON)."""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"^\s*(POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON)\s*\((.+)\)\s*$", s, re.I)
    if not m:
        return None
    typ = m.group(1).upper()
    body = m.group(2).strip()

    def parse_coords(text):
        out = []
        for pair in text.split(","):
            parts = pair.strip().split()
            if len(parts) >= 2:
                out.append([float(parts[0]), float(parts[1])])
        return out

    try:
        if typ == "POINT":
            coords = parse_coords(body)
            return {"type": "Point", "coordinates": coords[0]}
        if typ == "LINESTRING":
            return {"type": "LineString", "coordinates": parse_coords(body)}
        if typ == "POLYGON":
            # strip leading/trailing parens on rings
            rings_text = body.strip().lstrip("(").rstrip(")")
            rings = [parse_coords(r) for r in rings_text.split("),(")]
            return {"type": "Polygon", "coordinates": rings}
    except Exception:
        return None
    return None


def _write_geojson(gj: Dict) -> str:
    fd, path = tempfile.mkstemp(prefix="csv_import_", suffix=".geojson")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(gj, f)
    return path
