"""
Regular point grid inside a polygon, with projected-plane z.

The paper has the user run "Vector → Research Tools → Regular Points"
against a boundary polygon, then edit the attribute table to add a `z`
field with a field-calculator expression. We collapse both steps into
one in-process function so the user sees a finished layer.

Grid construction:
  - Iterate the polygon's bounding box at the user-supplied spacing.
  - Keep only points whose geometry `contains()` inside the polygon.
  - For each kept point, compute (x_rel, y_rel) relative to the origin
    in the SAME CRS as the polygon.
  - Compute z_rel via the math module and z_abs = origin_elevation + z_rel.

All geometry manipulation happens through QGIS types. The caller is
responsible for ensuring polygon and origin are already in the same CRS
(the dialog does the reprojection up-front).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from . import math_plane
from ..utils import logging as log


def build_grid(origin_xy: Tuple[float, float],
               origin_elevation: float,
               strike_deg: float,
               dip_dir_deg: float,
               dip_amount_deg: float,
               polygon_geom,
               spacing: float,
               max_points: int = 200_000) -> List[Dict]:
    """
    Walk a grid across the polygon bounding box at `spacing`. Keep the
    points that fall inside the polygon and compute z for each.

    Returns a list of dicts: {x, y, z_rel, z_abs, in_polygon=True}.

    Raises ValueError if:
      - the polygon has zero area,
      - the angles are degenerate,
      - the grid would exceed max_points.
    """
    from qgis.core import QgsGeometry, QgsPointXY

    if polygon_geom is None or polygon_geom.isEmpty():
        raise ValueError("Boundary polygon is empty.")

    if math_plane.is_degenerate(strike_deg, dip_dir_deg, dip_amount_deg):
        raise ValueError(
            f"Bed orientation (strike {strike_deg}°, dip direction "
            f"{dip_dir_deg}°, dip amount {dip_amount_deg}°) is degenerate. "
            "Strike and dip direction must not be equal or exactly opposite, "
            "and dip amount must be > 0°."
        )

    bbox = polygon_geom.boundingBox()
    x_min, x_max = bbox.xMinimum(), bbox.xMaximum()
    y_min, y_max = bbox.yMinimum(), bbox.yMaximum()
    width  = x_max - x_min
    height = y_max - y_min
    if width <= 0 or height <= 0:
        raise ValueError("Boundary polygon has zero area.")

    n_cols = int(math.ceil(width / spacing)) + 1
    n_rows = int(math.ceil(height / spacing)) + 1
    total  = n_cols * n_rows
    if total > max_points:
        raise ValueError(
            f"Grid would contain {total:,} candidate points "
            f"(> max {max_points:,}). Increase spacing or shrink the polygon."
        )

    ox, oy = origin_xy

    features: List[Dict] = []
    y = y_min
    # Iterate rows north-to-south so output ordering is predictable.
    while y <= y_max + 1e-9:
        x = x_min
        while x <= x_max + 1e-9:
            pt = QgsPointXY(x, y)
            if polygon_geom.contains(QgsGeometry.fromPointXY(pt)):
                z_rel = math_plane.plane_z_offset(
                    x - ox, y - oy,
                    strike_deg, dip_dir_deg, dip_amount_deg)
                # Degenerate case is caught above, so z_rel can't be None
                # here — but be defensive anyway.
                if z_rel is not None:
                    features.append({
                        "x": x, "y": y,
                        "z_rel": z_rel,
                        "z_abs": origin_elevation + z_rel,
                    })
            x += spacing
        y += spacing

    log.info(
        f"Grid built: {len(features):,} points inside polygon "
        f"(bbox {width:.1f}×{height:.1f} units, spacing {spacing})"
    )
    return features


def polygon_plane_corners(origin_xy: Tuple[float, float],
                          origin_elevation: float,
                          strike_deg: float,
                          dip_dir_deg: float,
                          dip_amount_deg: float,
                          polygon_geom) -> List[Tuple[float, float, float]]:
    """
    Return the 4 corners of a rectangle that encloses the polygon's
    bounding box, each with a z value sampled from the plane. Used for
    the 3D polygon output option.
    """
    if math_plane.is_degenerate(strike_deg, dip_dir_deg, dip_amount_deg):
        raise ValueError("Degenerate bed orientation — cannot build 3D polygon.")

    bbox = polygon_geom.boundingBox()
    xs = [bbox.xMinimum(), bbox.xMaximum()]
    ys = [bbox.yMinimum(), bbox.yMaximum()]
    ox, oy = origin_xy

    corners = [
        (xs[0], ys[0]),  # SW
        (xs[1], ys[0]),  # SE
        (xs[1], ys[1]),  # NE
        (xs[0], ys[1]),  # NW
    ]
    out: List[Tuple[float, float, float]] = []
    for x, y in corners:
        z_rel = math_plane.plane_z_offset(
            x - ox, y - oy,
            strike_deg, dip_dir_deg, dip_amount_deg) or 0.0
        out.append((x, y, origin_elevation + z_rel))
    return out
