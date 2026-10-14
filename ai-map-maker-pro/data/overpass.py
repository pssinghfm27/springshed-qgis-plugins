"""
OpenStreetMap Overpass client.

This replaces the broken fetcher from v1. Key fixes:
- Every query includes BOTH way AND relation so forests, rivers, admin
  boundaries render correctly (was the main cause of 0-feature layers).
- Uses `out geom;` so we get full coordinate lists per member and don't
  need to resolve node IDs separately.
- Automatic bbox expansion retry when a query returns 0 features.
- Configurable timeout (120s default, up from 60).
- Logs the exact Overpass QL sent so users can debug in overpass-turbo.eu.
- Presets for road, water, forest, building, amenity, landuse, railway, admin.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

from ..utils import bbox as bboxlib, logging as log
from . import osm_converter

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]


# Presets map a friendly "osm_type" to the (selector, element_types) pairs
# we want to fetch. Each preset returns ways AND relations to catch all
# geometries regardless of how they're tagged.
_PRESETS: Dict[str, List[Tuple[str, Tuple[str, ...]]]] = {
    "road": [
        ("way['highway']", ("way",)),
    ],
    "railway": [
        ("way['railway']", ("way",)),
        ("relation['route'='train']", ("relation",)),
    ],
    "water": [
        ("way['natural'='water']",       ("way",)),
        ("relation['natural'='water']",  ("relation",)),
        ("way['waterway']",              ("way",)),
        ("relation['waterway']",         ("relation",)),
    ],
    "forest": [
        ("way['natural'='wood']",        ("way",)),
        ("relation['natural'='wood']",   ("relation",)),
        ("way['landuse'='forest']",      ("way",)),
        ("relation['landuse'='forest']", ("relation",)),
    ],
    "building": [
        ("way['building']", ("way",)),
    ],
    "amenity": [
        ("node['amenity']", ("node",)),
        ("way['amenity']",  ("way",)),
    ],
    "landuse": [
        ("way['landuse']",      ("way",)),
        ("relation['landuse']", ("relation",)),
    ],
    "admin": [
        ("relation['boundary'='administrative']", ("relation",)),
    ],
    "place": [
        ("node['place']", ("node",)),
    ],
}


def build_query(osm_type: str, bbox: Dict, custom_filter: Optional[Dict] = None,
                timeout: int = 120) -> str:
    """
    Build Overpass QL for a given osm_type + bbox.

    If custom_filter is provided (e.g. {'natural': 'wood'}), a single
    targeted query is built from that instead of using a preset.
    """
    bbox_str = bboxlib.to_overpass_bbox(bbox)
    selectors = []

    if custom_filter:
        k, v = next(iter(custom_filter.items()))
        for element in ("node", "way", "relation"):
            selectors.append(f'  {element}["{k}"="{v}"]({bbox_str});')
    else:
        preset = _PRESETS.get(osm_type)
        if not preset:
            raise ValueError(f"Unknown osm_type: {osm_type}")
        for selector, elements in preset:
            # preset entries like "way['highway']" already contain the selector.
            # We need to insert the bbox: "way['highway'](bbox);"
            selectors.append(f"  {selector}({bbox_str});")

    selector_block = "\n".join(selectors)
    return (
        f"[out:json][timeout:{timeout}];\n"
        f"(\n{selector_block}\n);\n"
        f"out body geom;"
    )


def fetch(osm_type: str, bbox: Dict, custom_filter: Optional[Dict] = None,
          timeout: int = 120, auto_expand: bool = True,
          max_expansions: int = 2) -> Dict:
    """
    Fetch OSM data for the given osm_type + bbox.

    Returns
    -------
    GeoJSON FeatureCollection dict. May have 0 features if the area has no data.

    Behavior
    --------
    - Tries each endpoint in OVERPASS_ENDPOINTS until one succeeds.
    - If 0 features returned AND auto_expand=True, retries with a 50% larger
      bbox (up to max_expansions times).
    """
    current_bbox = bbox
    for expansion in range(max_expansions + 1):
        query = build_query(osm_type, current_bbox, custom_filter, timeout)
        log.info(f"Overpass query ({osm_type}, expansion={expansion}):\n{query}")

        raw = _post_overpass(query, timeout=timeout)
        if raw is None:
            log.warn(f"All Overpass endpoints failed for {osm_type}")
            return {"type": "FeatureCollection", "features": []}

        gj = osm_converter.overpass_to_geojson(raw)
        n = len(gj["features"])
        log.info(f"Overpass {osm_type}: got {n} features")

        if n > 0 or not auto_expand or expansion == max_expansions:
            return gj

        log.warn(f"0 features for {osm_type}; expanding bbox by 50% and retrying")
        current_bbox = bboxlib.expand(current_bbox, 1.5)

    return {"type": "FeatureCollection", "features": []}


def fetch_to_file(osm_type: str, bbox: Dict,
                  custom_filter: Optional[Dict] = None,
                  timeout: int = 120, auto_expand: bool = True) -> Optional[str]:
    """
    Like fetch(), but writes the GeoJSON to a temp file and returns the path.
    Returns None if 0 features (so the caller can skip layer creation).
    """
    gj = fetch(osm_type, bbox, custom_filter, timeout, auto_expand)
    if not gj["features"]:
        return None
    fd, path = tempfile.mkstemp(prefix=f"osm_{osm_type}_", suffix=".geojson")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(gj, f)
    return path


# ---------------------------------------------------------------------------

def _post_overpass(query: str, timeout: int) -> Optional[Dict]:
    """POST the query to each endpoint in turn until one succeeds."""
    payload = urllib.parse.urlencode({"data": query}).encode("utf-8")
    headers = {
        "User-Agent": "ClaudeMapperPro/2.0 (QGIS plugin)",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    last_err = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            req = urllib.request.Request(endpoint, data=payload,
                                         headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except Exception as e:
            last_err = e
            log.warn(f"Overpass endpoint {endpoint} failed: {e}")
            time.sleep(1)
    log.error(f"All Overpass endpoints failed. Last error: {last_err}")
    return None
