"""
Elevation / DEM data sources.

Fixes bug 3.5 from the handoff. The old plugin used an OpenTopography CDN
URL that had been retired. Two working sources here:

1. AWS Open Data "terrain tiles" (Mapzen/Terrarium-encoded PNG tiles).
   Free, no account needed, hosted by AWS as a public dataset. We use
   this as a QGIS XYZ raster layer, which is what QGIS natively supports
   for tile-based terrain.

2. OpenTopography "Global DEM" API. This DOES still work at
   /API/globaldem — it's only the old /otCatalog CDN that's gone.
   Requires a free API key. Returns a bbox-cropped GeoTIFF that can be
   loaded as a true raster DEM (suitable for contours, slope, hillshade
   with the QGIS Raster Terrain Analysis plugin — core in QGIS 4).

For springshed / hydrological work the OpenTopography SRTM 30m is the
right choice when a key is configured. Without a key we fall back to
the AWS terrain tiles so the plugin still works out of the box.
"""
from __future__ import annotations

import os
import tempfile
import urllib.parse
import urllib.request
from typing import Dict, Optional

from ..utils import bbox as bboxlib, cache, logging as log


# ---- AWS Terrain Tiles ----------------------------------------------------

AWS_TERRAIN_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def aws_terrain_xyz_uri() -> str:
    """Return a QGIS-compatible XYZ URI for the AWS Terrain Tiles dataset.

    Terrarium encoding stores elevation in RGB channels:
      elevation_m = (R*256 + G + B/256) - 32768

    QGIS renders this as an image by default. For true DEM analysis
    (contours, hillshade, slope) use `opentopography_dem()` below to get
    a GeoTIFF.
    """
    return (
        "type=xyz"
        f"&url={urllib.parse.quote(AWS_TERRAIN_URL, safe='{}:/')}"
        "&zmax=15&zmin=0"
        "&crs=EPSG:3857"
    )


# ---- OpenTopography Global DEM API ---------------------------------------

OPENTOPO_URL = "https://portal.opentopography.org/API/globaldem"


def opentopography_dem(bbox: Dict, dem_type: str = "SRTMGL1",
                       api_key: Optional[str] = None,
                       output_format: str = "GTiff") -> str:
    """
    Download a bbox-cropped DEM GeoTIFF from OpenTopography.

    Parameters
    ----------
    bbox : dict
        south / west / north / east in WGS84.
    dem_type : str
        One of:
          - "SRTMGL1"   (SRTM 1-arc-second / ~30m — good for India)
          - "SRTMGL3"   (SRTM 3-arc-second / ~90m — wider coverage)
          - "AW3D30"    (ALOS World 3D 30m — best accuracy in mountainous terrain)
          - "COP30"     (Copernicus GLO-30 30m — modern, good for Himalayas)
          - "COP90"     (Copernicus GLO-90 90m)
    api_key : str
        Free OpenTopography API key. Required as of 2022.
        Register at https://portal.opentopography.org/myopentopo

    Returns
    -------
    Path to a cached GeoTIFF file.
    """
    if not api_key:
        raise ValueError(
            "OpenTopography requires a free API key. "
            "Register at https://portal.opentopography.org/myopentopo "
            "and paste it into the plugin Settings dialog."
        )

    b = bboxlib.normalize(bbox)

    # Cache key derived from (dem_type, bbox) — reuse if already downloaded.
    key = f"{dem_type}_{b['south']:.4f}_{b['west']:.4f}_{b['north']:.4f}_{b['east']:.4f}"
    cache_path = cache.cache_path("opentopo", key, ".tif")
    if cache.is_fresh(cache_path, ttl_seconds=86400 * 90):
        log.info(f"OpenTopography DEM cache hit for {key}")
        return cache_path

    params = {
        "demtype": dem_type,
        "south": b["south"],
        "north": b["north"],
        "west":  b["west"],
        "east":  b["east"],
        "outputFormat": output_format,
        "API_Key": api_key,
    }
    url = f"{OPENTOPO_URL}?{urllib.parse.urlencode(params)}"
    log.info(f"OpenTopography request: demtype={dem_type}  bbox=({b['south']:.3f},{b['west']:.3f},"
             f"{b['north']:.3f},{b['east']:.3f})")

    req = urllib.request.Request(
        url, headers={"User-Agent": "ClaudeMapperPro/2.0 (QGIS plugin)"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = resp.read()

    # OpenTopography returns the GeoTIFF directly on success; errors come
    # back as small JSON/XML error bodies. Any response under ~2KB is almost
    # certainly an error.
    if len(data) < 2000:
        body_preview = data[:500].decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenTopography returned an error: {body_preview}")

    cache.write_bytes(cache_path, data)
    log.info(f"OpenTopography DEM saved: {cache_path} ({len(data)/1024:.0f} KB)")
    return cache_path
