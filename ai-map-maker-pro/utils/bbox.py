"""Bounding-box math. Bboxes are dicts: {south, west, north, east} in WGS84."""
from __future__ import annotations

from typing import Dict, Tuple


BBox = Dict[str, float]  # keys: south, west, north, east


def normalize(bbox) -> BBox:
    """Accept dict, tuple, or list and normalize to canonical dict form."""
    if isinstance(bbox, dict):
        return {
            "south": float(bbox.get("south", bbox.get("s", 0.0))),
            "west":  float(bbox.get("west",  bbox.get("w", 0.0))),
            "north": float(bbox.get("north", bbox.get("n", 0.0))),
            "east":  float(bbox.get("east",  bbox.get("e", 0.0))),
        }
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        # Nominatim returns [south, north, west, east] as strings
        try:
            s, n, w, e = (float(x) for x in bbox)
            return {"south": s, "north": n, "west": w, "east": e}
        except (TypeError, ValueError):
            pass
    raise ValueError(f"Cannot parse bbox: {bbox!r}")


def expand(bbox: BBox, factor: float = 1.5) -> BBox:
    """Expand bbox around its center by factor (1.5 = 50% bigger)."""
    b = normalize(bbox)
    lat_pad = (b["north"] - b["south"]) * (factor - 1) / 2
    lon_pad = (b["east"]  - b["west"])  * (factor - 1) / 2
    return {
        "south": max(-90.0,  b["south"] - lat_pad),
        "north": min( 90.0,  b["north"] + lat_pad),
        "west":  max(-180.0, b["west"]  - lon_pad),
        "east":  min( 180.0, b["east"]  + lon_pad),
    }


def to_overpass_bbox(bbox: BBox) -> str:
    """Format for Overpass QL: (south,west,north,east)."""
    b = normalize(bbox)
    return f"{b['south']},{b['west']},{b['north']},{b['east']}"


def to_subset_sql(bbox: BBox, buffer_pct: float = 0.20) -> str:
    """
    Build an OGR SQL WHERE clause for QgsVectorLayer.setSubsetString()
    that keeps features intersecting the expanded bbox.

    Uses xmin(geometry) / xmax(geometry) etc. which OGR supports for most
    vector providers.
    """
    b = expand(bbox, 1.0 + buffer_pct)
    return (
        f"xmax(geometry) >= {b['west']} AND xmin(geometry) <= {b['east']} "
        f"AND ymax(geometry) >= {b['south']} AND ymin(geometry) <= {b['north']}"
    )


def dimensions(bbox: BBox) -> Tuple[float, float]:
    """Return (width_deg, height_deg)."""
    b = normalize(bbox)
    return b["east"] - b["west"], b["north"] - b["south"]


def center(bbox: BBox) -> Tuple[float, float]:
    b = normalize(bbox)
    return (b["west"] + b["east"]) / 2, (b["south"] + b["north"]) / 2


def is_valid(bbox: BBox) -> bool:
    try:
        b = normalize(bbox)
        return (b["south"] < b["north"]
                and b["west"] < b["east"]
                and -90 <= b["south"] <= 90
                and -90 <= b["north"] <= 90
                and -180 <= b["west"] <= 180
                and -180 <= b["east"] <= 180)
    except Exception:
        return False
