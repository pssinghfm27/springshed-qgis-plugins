"""
Input validators.

Each function returns an error-message string if validation fails, or
None if the input is OK. The dialog collects all messages and shows
them in a single dialog so the user sees everything that's wrong, not
the first thing.

Design rule: error messages must tell the user WHAT to do next, not
just what went wrong.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

from . import math_plane


def validate_angles(strike_deg: float, dip_dir_deg: float,
                    dip_amount_deg: float) -> Optional[str]:
    """Check that strike/dip angles are in valid ranges and non-degenerate."""
    if not (0.0 <= strike_deg <= 360.0):
        return (f"Strike direction {strike_deg}° is outside 0–360°. "
                "Enter a compass bearing clockwise from north.")
    if not (0.0 <= dip_dir_deg <= 360.0):
        return (f"Dip direction {dip_dir_deg}° is outside 0–360°. "
                "Enter a compass bearing clockwise from north.")
    if not (0.0 <= dip_amount_deg <= 90.0):
        return (f"Dip amount {dip_amount_deg}° is outside 0–90°. "
                "Dip amount is the angle from horizontal, so it cannot exceed 90°.")
    if dip_amount_deg <= 0.001:
        return ("Dip amount is 0° — the bed is horizontal and the plane is "
                "trivially flat. Set a non-zero dip amount to see variation.")
    if math_plane.is_degenerate(strike_deg, dip_dir_deg, dip_amount_deg):
        return (f"Strike direction ({strike_deg}°) and dip direction "
                f"({dip_dir_deg}°) are equal or exactly opposite. This is a "
                "geometrically impossible bed — strike and dip must be "
                "perpendicular in map view. Change one of the values by 90°.")
    return None


def validate_spacing(spacing: float, polygon_geom) -> Optional[str]:
    """Check that grid spacing is sane vs the polygon size."""
    if spacing <= 0:
        return "Grid spacing must be positive."
    if polygon_geom is None or polygon_geom.isEmpty():
        return "Boundary polygon is empty."
    bbox = polygon_geom.boundingBox()
    width  = bbox.xMaximum() - bbox.xMinimum()
    height = bbox.yMaximum() - bbox.yMinimum()
    if width <= 0 or height <= 0:
        return "Boundary polygon has zero area."

    # Guard against a spacing so fine that the grid would be gigantic.
    n_cells = (width / spacing) * (height / spacing)
    if n_cells > 500_000:
        return (f"Spacing {spacing} would produce ~{int(n_cells):,} candidate "
                "points, which is too many. Increase the spacing (try "
                f"{int(math.sqrt(n_cells / 10_000) * spacing)} or larger) "
                "or shrink the boundary polygon.")

    # And against a spacing so coarse that < 10 points would land inside.
    if n_cells < 10:
        return (f"Spacing {spacing} is too coarse for a polygon this small — "
                "fewer than 10 points would be generated. Reduce the spacing.")
    return None


def validate_crs_projected(crs) -> Optional[str]:
    """
    The paper computes distances in EPSG:3857 (metres). Anything geographic
    (lat/lon in degrees) will produce garbage because a "degree" is not a
    unit of distance. Warn the user.
    """
    try:
        is_geographic = crs.isGeographic()
    except Exception:
        return None
    if is_geographic:
        return (f"The boundary polygon's CRS ({crs.authid()}) is geographic "
                "(lat/lon in degrees). The strike/dip formula computes "
                "distances in the polygon's CRS units, so a geographic CRS "
                "will give nonsensical z values. Reproject the polygon to a "
                "projected CRS first (UTM zone or EPSG:3857).")
    return None


def validate_origin_inside(origin_xy: Tuple[float, float],
                           polygon_geom,
                           tolerate_outside: bool = False) -> Optional[str]:
    """
    Warn if the origin point is outside the boundary polygon. The paper's
    method extrapolates the plane through the origin regardless, but the
    further the polygon is from the origin, the larger the z values grow.
    """
    from qgis.core import QgsGeometry, QgsPointXY

    ox, oy = origin_xy
    origin_geom = QgsGeometry.fromPointXY(QgsPointXY(ox, oy))
    if polygon_geom.contains(origin_geom):
        return None
    if tolerate_outside:
        return None
    distance = polygon_geom.distance(origin_geom)
    return (f"The origin point is {distance:.1f} units outside the boundary "
            "polygon. The plane will still be computed, but z values at "
            "polygon edges may be very large because they are extrapolated. "
            "Move the origin inside the polygon for best results, or confirm "
            "the extrapolation if that's what you want.")


def validate_dem_covers(dem_layer, x: float, y: float,
                        source_crs=None) -> Optional[str]:
    """Check that the DEM's extent contains the origin point."""
    from . import dem_sampler
    if not dem_sampler.dem_covers_point(dem_layer, x, y, source_crs):
        return ("The DEM does not cover the origin point. Load a DEM whose "
                "extent includes the origin, or move the origin inside the "
                "DEM's area.")
    return None


def validate_attribute_row(strike, dip_dir, dip_amount,
                           row_number: int) -> Optional[str]:
    """
    Validate a single row from the batch-mode attribute table. Any field
    that's missing or out of range yields a per-row message the caller
    can collect into one dialog.
    """
    try:
        s = float(strike)
        a = float(dip_dir)
        b = float(dip_amount)
    except (TypeError, ValueError):
        return (f"Row {row_number}: strike / dip_direction / dip_amount "
                "must be numeric. Skipping this row.")
    msg = validate_angles(s, a, b)
    if msg:
        return f"Row {row_number}: {msg}"
    return None
