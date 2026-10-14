"""
Strike / dip rock-bed projection.

Implements the mathematical method from:
  "Visualizing Strikes and Dips of Rock Beds in QGIS with qgis2threejs"
  (Peepalkhola spring case study, CHIRAG / Dotiyalgaon).

Given:
  - An origin point (typically a spring or a rock observation point)
  - The ground elevation at that origin (from DEM)
  - Strike direction θ (degrees clockwise from N)
  - Dip direction α (degrees clockwise from N — the updip direction per
    the original paper's convention; flip by 180° if the bed appears
    inverted)
  - Dip amount β (degrees from horizontal)
  - A boundary polygon in which to generate a point grid
  - A grid spacing

this module builds a point grid inside the polygon and computes the z
(absolute elevation) at each grid point on the projected rock bed plane.

The formula, as derived in the paper, is:

    z_rel = (x'·cosθ·sinβ + y'·sinθ·sinβ)
           ────────────────────────────────────────────────
           (sinα·cosθ·cosβ − sinθ·cosα·cosβ)

    z_abs = z_origin + z_rel

where (x', y') are the grid point's coordinates RELATIVE to the origin,
in the same linear units as the boundary polygon's CRS (metres).

Output is a GeoJSON FeatureCollection in the same CRS as the input
boundary polygon, with a `z` attribute per point. Users can then
visualise this in QGIS's native 3D view or with the qgis2threejs plugin.

Caveats (direct quote from the source paper):
  This method does not account for folds, faults, or any internal
  structural variability within the rock bed. It projects a single
  idealised plane through the origin. Use it as an indicative tool,
  not a definitive geological model.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from typing import Dict, List, Optional, Tuple

from ..utils import logging as log


# ---------------------------------------------------------------------------
# Pure math
# ---------------------------------------------------------------------------

def plane_z_offset(x_rel: float, y_rel: float,
                   strike_deg: float, dip_dir_deg: float,
                   dip_amount_deg: float) -> Optional[float]:
    """
    Height on the projected rock-bed plane at a point (x_rel, y_rel)
    relative to an origin at (0, 0, 0).

    Returns None if the denominator is effectively zero (degenerate
    geometry — e.g. strike direction equals dip direction, which doesn't
    describe a real bed).
    """
    theta = math.radians(strike_deg)
    alpha = math.radians(dip_dir_deg)
    beta  = math.radians(dip_amount_deg)

    sin_beta = math.sin(beta)
    cos_beta = math.cos(beta)
    sin_theta, cos_theta = math.sin(theta), math.cos(theta)
    sin_alpha, cos_alpha = math.sin(alpha), math.cos(alpha)

    numerator   = x_rel * cos_theta * sin_beta + y_rel * sin_theta * sin_beta
    denominator = (sin_alpha * cos_theta * cos_beta
                   - sin_theta * cos_alpha * cos_beta)

    if abs(denominator) < 1e-12:
        return None
    return numerator / denominator


# ---------------------------------------------------------------------------
# Grid generation against a QGIS polygon geometry
# ---------------------------------------------------------------------------

def build_rock_bed_grid(origin_xy: Tuple[float, float],
                        origin_elevation: float,
                        strike_deg: float,
                        dip_dir_deg: float,
                        dip_amount_deg: float,
                        polygon_geom,
                        spacing: float,
                        max_points: int = 200_000) -> List[Dict]:
    """
    Walk a regular grid across the polygon's bounding box and keep the
    points that fall inside the polygon. For each kept point compute the
    absolute z on the projected rock-bed plane.

    Parameters
    ----------
    origin_xy : (x, y)
        Origin coordinates in the same CRS as polygon_geom. Typically
        the spring location.
    origin_elevation : float
        Ground elevation at the origin (sampled from the DEM).
    strike_deg, dip_dir_deg, dip_amount_deg : float
        Bed orientation angles.
    polygon_geom : QgsGeometry
        Boundary polygon — the grid is clipped to this.
    spacing : float
        Grid spacing in the CRS's linear units (metres for EPSG:3857
        and any UTM zone; DEGREES if you pass a geographic polygon —
        you'll get a warning).
    max_points : int
        Safety cap so a tiny spacing on a huge bbox doesn't explode memory.

    Returns
    -------
    A list of feature dicts with 'x', 'y', 'z_rel', 'z_abs' keys.
    """
    from qgis.core import QgsPointXY, QgsGeometry

    ox, oy = origin_xy
    bbox = polygon_geom.boundingBox()
    x_min, y_min, x_max, y_max = (bbox.xMinimum(), bbox.yMinimum(),
                                  bbox.xMaximum(), bbox.yMaximum())

    width  = x_max - x_min
    height = y_max - y_min
    if width <= 0 or height <= 0:
        raise ValueError("Boundary polygon has zero area.")

    # Sanity warning for geographic CRS — the spacing will be in degrees
    # which gives garbage distances at high latitudes. Users should
    # reproject the polygon to UTM or 3857 first.
    if width < 10 and height < 10:
        log.warn(
            f"Polygon bbox is only {width:.4f} × {height:.4f} units wide. "
            "This looks like a geographic CRS — reproject the boundary "
            "polygon to a projected CRS (UTM zone or EPSG:3857) before "
            "running the strike/dip tool. Results below may be nonsensical."
        )

    # Estimate cell count; cap if it's silly
    n_cols = int(math.ceil(width / spacing))
    n_rows = int(math.ceil(height / spacing))
    total = n_cols * n_rows
    if total > max_points:
        raise ValueError(
            f"Grid would contain {total:,} points (> max {max_points:,}). "
            f"Increase spacing or shrink the boundary polygon."
        )

    log.info(
        f"Rock-bed grid: bbox {width:.1f}×{height:.1f} units, "
        f"spacing {spacing}, up to {total:,} candidate points."
    )

    features: List[Dict] = []
    # Step through bbox; keep only points inside the polygon.
    # Iterate rows top-to-bottom to keep with GIS convention.
    y = y_min
    while y <= y_max:
        x = x_min
        while x <= x_max:
            pt = QgsPointXY(x, y)
            if polygon_geom.contains(QgsGeometry.fromPointXY(pt)):
                z_rel = plane_z_offset(x - ox, y - oy,
                                        strike_deg, dip_dir_deg,
                                        dip_amount_deg)
                if z_rel is not None:
                    features.append({
                        "x": x, "y": y,
                        "z_rel": z_rel,
                        "z_abs": origin_elevation + z_rel,
                    })
            x += spacing
        y += spacing

    log.info(f"Rock-bed grid: {len(features):,} points inside polygon.")
    return features


# ---------------------------------------------------------------------------
# DEM sampling (main-thread helper)
# ---------------------------------------------------------------------------

def sample_dem_at_point(dem_layer, x: float, y: float,
                        source_crs=None) -> Optional[float]:
    """
    Sample a DEM raster layer's value at (x, y).

    If source_crs is provided and differs from the DEM's CRS, the point
    is transformed first.

    Returns the elevation in the raster's native units, or None if the
    sample is outside the raster or the provider returns no data.
    """
    from qgis.core import (
        QgsCoordinateTransform, QgsPointXY, QgsProject, QgsRaster,
    )

    if dem_layer is None or not dem_layer.isValid():
        return None

    pt = QgsPointXY(x, y)
    dem_crs = dem_layer.crs()
    if source_crs is not None and source_crs != dem_crs:
        tr = QgsCoordinateTransform(source_crs, dem_crs,
                                     QgsProject.instance())
        pt = tr.transform(pt)

    try:
        result = dem_layer.dataProvider().identify(
            pt, QgsRaster.IdentifyFormatValue)
    except Exception as e:
        log.warn(f"DEM sample failed: {e}")
        return None

    if not result.isValid():
        return None
    values = result.results() or {}
    # The first band's value is the elevation
    for band_id, v in values.items():
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


# ---------------------------------------------------------------------------
# GeoJSON writer
# ---------------------------------------------------------------------------

def write_grid_as_geojson(features: List[Dict],
                          output_dir: Optional[str] = None,
                          prefix: str = "rock_bed_") -> str:
    """
    Write the grid as a GeoJSON FeatureCollection to a temp file and
    return the path. Each feature is a Point with x, y, z_rel, z_abs
    properties. (Geometry remains 2D — QGIS will use the 'z_abs' field
    as a z override when rendering in 3D.)
    """
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [f["x"], f["y"]]},
                "properties": {
                    "z_rel": round(f["z_rel"], 4),
                    "z_abs": round(f["z_abs"], 4),
                },
            }
            for f in features
        ],
    }
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".geojson",
                                 dir=output_dir)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(fc, fh)
    return path


# ---------------------------------------------------------------------------
# Convenience: evaluate formula at single points (for tests / previews)
# ---------------------------------------------------------------------------

def plane_corners(origin_xy: Tuple[float, float],
                  origin_elevation: float,
                  strike_deg: float, dip_dir_deg: float, dip_amount_deg: float,
                  half_size: float = 100.0) -> List[Tuple[float, float, float]]:
    """
    Return the four corners of a square patch centred on the origin, with
    z values on the projected plane. Useful for quick-sanity previews
    and unit tests.
    """
    ox, oy = origin_xy
    corners = [
        (ox - half_size, oy - half_size),
        (ox + half_size, oy - half_size),
        (ox + half_size, oy + half_size),
        (ox - half_size, oy + half_size),
    ]
    out = []
    for x, y in corners:
        z_rel = plane_z_offset(x - ox, y - oy, strike_deg,
                                dip_dir_deg, dip_amount_deg) or 0.0
        out.append((x, y, origin_elevation + z_rel))
    return out
