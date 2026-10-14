"""
Sample a DEM raster's value at a point.

Equivalent to what the Peepalkhola paper calls "Add Values to Points"
from the Processing Toolbox, but done in-process so we don't force a
dependency on that algorithm name or its parameter signature (which
drifts between QGIS versions).

All operations here MUST be called from the main thread.
"""
from __future__ import annotations

from typing import Optional


def sample_dem_elevation(dem_layer, x: float, y: float,
                         source_crs=None) -> Optional[float]:
    """
    Return the elevation at (x, y) in the DEM's first band.

    Parameters
    ----------
    dem_layer : QgsRasterLayer
        Any valid single-band raster. Works with ASTER, SRTM, ALOS,
        AWS terrain tiles etc.
    x, y : float
        Point coordinates.
    source_crs : QgsCoordinateReferenceSystem, optional
        The CRS that (x, y) is expressed in. If different from the DEM's
        CRS, the point is transformed before sampling.

    Returns
    -------
    float | None
        Elevation in the DEM's vertical units (typically metres), or
        None if the sample is outside the raster or the band has no data.
    """
    if dem_layer is None or not dem_layer.isValid():
        return None

    from qgis.core import (
        QgsCoordinateTransform, QgsPointXY, QgsProject, QgsRaster,
    )

    pt = QgsPointXY(float(x), float(y))
    dem_crs = dem_layer.crs()
    if source_crs is not None and source_crs != dem_crs:
        tr = QgsCoordinateTransform(source_crs, dem_crs, QgsProject.instance())
        pt = tr.transform(pt)

    try:
        result = dem_layer.dataProvider().identify(pt, QgsRaster.IdentifyFormatValue)
    except Exception:
        return None
    if not result.isValid():
        return None

    for _band_id, value in (result.results() or {}).items():
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def dem_covers_point(dem_layer, x: float, y: float, source_crs=None) -> bool:
    """
    Cheap bounds check: does the DEM's extent contain the point?
    Useful for pre-flight validation before the caller does real work.
    """
    if dem_layer is None or not dem_layer.isValid():
        return False

    from qgis.core import (
        QgsCoordinateTransform, QgsPointXY, QgsProject,
    )

    pt = QgsPointXY(float(x), float(y))
    dem_crs = dem_layer.crs()
    if source_crs is not None and source_crs != dem_crs:
        tr = QgsCoordinateTransform(source_crs, dem_crs, QgsProject.instance())
        pt = tr.transform(pt)

    extent = dem_layer.extent()
    return (extent.xMinimum() <= pt.x() <= extent.xMaximum()
            and extent.yMinimum() <= pt.y() <= extent.yMaximum())
