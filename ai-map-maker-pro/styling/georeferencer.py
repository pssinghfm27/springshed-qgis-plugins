"""
Georeferencing for scanned map images.

Two supported paths:

  A) BBOX-ONLY georeferencing.
     If Claude Vision returned approximate_bbox but few/no reliable GCPs,
     we assume the image covers the bbox edge-to-edge in EPSG:4326 and
     write a .wld world file + .prj sidecar. QGIS will then load the
     PNG/JPG as a raster layer aligned to the declared extent.

  B) GCP-BASED georeferencing.
     If Claude returned 3+ ground control points (pixel% + lat/lon),
     we write a .points file in the format QGIS Georeferencer plugin
     uses. The user can then one-click "Apply" to warp the image.

Bbox-only is fast but only accurate if the image really is a plain
north-up map. GCP-based is what you want for anything with rotation,
projection distortion, or an inset.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from ..utils import bbox as bboxlib, logging as log


def _load_pillow():
    """Lazy import of Pillow. Raises a clear message if missing."""
    try:
        from PIL import Image  # type: ignore
        return Image
    except ImportError:
        raise RuntimeError(
            "Pillow is required to georeference scanned images. Install it with:\n"
            "  python -m pip install Pillow --break-system-packages"
        )


def write_worldfile_for_bbox(image_path: str, bbox: Dict) -> str:
    """
    Write a world file (.pgw/.jgw/.wld) and a .prj for a plain north-up
    image that covers the given WGS84 bbox edge-to-edge.

    Returns the world-file path.
    """
    b = bboxlib.normalize(bbox)
    Image = _load_pillow()
    with Image.open(image_path) as im:
        w, h = im.size

    pixel_w = (b["east"]  - b["west"])  / w       # degrees per pixel in X
    pixel_h = (b["north"] - b["south"]) / h

    # World file format (6 lines): pixel_w, 0, 0, -pixel_h, upper-left X, upper-left Y
    wf_lines = [
        f"{pixel_w:.10f}",
        "0.0",
        "0.0",
        f"{-pixel_h:.10f}",
        f"{b['west']:.10f}",
        f"{b['north']:.10f}",
    ]

    ext = os.path.splitext(image_path)[1].lower()
    wf_ext = {
        ".png":  ".pgw",
        ".jpg":  ".jgw",
        ".jpeg": ".jgw",
        ".tif":  ".tfw",
        ".tiff": ".tfw",
    }.get(ext, ".wld")

    wf_path = os.path.splitext(image_path)[0] + wf_ext
    with open(wf_path, "w", encoding="utf-8") as f:
        f.write("\n".join(wf_lines) + "\n")

    prj_path = os.path.splitext(image_path)[0] + ".prj"
    with open(prj_path, "w", encoding="utf-8") as f:
        f.write(_WKT_WGS84)

    log.info(f"Wrote world file {wf_path} for bbox={b}")
    return wf_path


def write_points_file(image_path: str, gcps: List[Dict]) -> Optional[str]:
    """
    Write a .points file compatible with the QGIS Georeferencer plugin.

    Each GCP is {pixel_x_pct, pixel_y_pct, lat, lon, label}.
    pixel_x_pct/y_pct are measured from the TOP-LEFT corner of the image.

    Returns the points-file path, or None if fewer than 2 valid GCPs.
    """
    if len(gcps) < 2:
        return None

    Image = _load_pillow()
    with Image.open(image_path) as im:
        w, h = im.size

    lines = ["mapX,mapY,pixelX,pixelY,enable,dX,dY,residual"]
    count = 0
    for g in gcps:
        try:
            px = float(g["pixel_x_pct"]) / 100.0 * w
            # QGIS Georeferencer uses pixelY with image coords (from top),
            # but stores negative Y (screen down). Convention: negative Y.
            py = -(float(g["pixel_y_pct"]) / 100.0 * h)
            lon = float(g["lon"])
            lat = float(g["lat"])
            lines.append(f"{lon},{lat},{px:.2f},{py:.2f},1,,,0")
            count += 1
        except (KeyError, TypeError, ValueError):
            continue

    if count < 2:
        return None

    pts_path = os.path.splitext(image_path)[0] + ".points"
    with open(pts_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log.info(f"Wrote {count} GCPs to {pts_path}")
    return pts_path


_WKT_WGS84 = (
    'GEOGCS["WGS 84",DATUM["WGS_1984",'
    'SPHEROID["WGS 84",6378137,298.257223563,'
    'AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],'
    'PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
    'AXIS["Latitude",NORTH],AXIS["Longitude",EAST],'
    'AUTHORITY["EPSG","4326"]]'
)
