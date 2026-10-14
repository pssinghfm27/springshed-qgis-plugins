"""
Convert grid and plane data into QGIS-loadable layer sources.

We write GeoJSON to temp files because:
  - GeoJSON's CRS defaults to EPSG:4326, which we need to match whatever
    CRS the polygon's in — so we transform on the way out.
  - Alternatively we can build QgsVectorLayer('memory', ...) directly;
    that keeps everything in RAM, but memory layers vanish when the
    project is closed without a save. GeoJSON on disk is safer.

This module writes raw files; the dialog turns them into QgsVectorLayer
objects and styles them.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Dict, List, Tuple


def write_points_geojson(features: List[Dict],
                         crs_authid: str,
                         prefix: str = "rockbed_grid_",
                         extra_props: Dict = None) -> str:
    """
    Write a list of {x, y, z_rel, z_abs[, ...]} dicts as a GeoJSON
    FeatureCollection. Returns the temp file path.

    The GeoJSON spec only blesses EPSG:4326 as default — but QGIS happily
    reads GeoJSON in any CRS declared at the collection level via the
    (deprecated but still supported) `crs` member.
    """
    extra_props = extra_props or {}
    geojson = {
        "type": "FeatureCollection",
        "crs": {
            "type": "name",
            "properties": {"name": f"urn:ogc:def:crs:{crs_authid.replace(':','::')}"},
        },
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [f["x"], f["y"]]},
                "properties": {
                    "z_rel": round(f["z_rel"], 4),
                    "z_abs": round(f["z_abs"], 4),
                    **extra_props,
                },
            }
            for f in features
        ],
    }

    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".geojson")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(geojson, f)
    return path


def write_plane_polygon_geojson(corners: List[Tuple[float, float, float]],
                                crs_authid: str,
                                properties: Dict,
                                prefix: str = "rockbed_plane_") -> str:
    """
    Write a 3D polygon feature describing the projected plane as a simple
    quad. `corners` is [(x, y, z), ...] with 4 entries.

    We use a PolygonZ geometry (GeoJSON permits 3D coordinates). QGIS
    treats these as Polygon25D layers — they render fine in 3D view.
    """
    if len(corners) != 4:
        raise ValueError("Plane polygon needs exactly 4 corners.")

    ring = [[x, y, z] for (x, y, z) in corners]
    ring.append(ring[0])  # close the ring

    geojson = {
        "type": "FeatureCollection",
        "crs": {
            "type": "name",
            "properties": {"name": f"urn:ogc:def:crs:{crs_authid.replace(':','::')}"},
        },
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": properties,
        }],
    }

    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".geojson")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(geojson, f)
    return path
