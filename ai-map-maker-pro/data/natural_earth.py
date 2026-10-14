"""
Natural Earth shapefile downloader with bbox clipping.

Natural Earth ships global data. Loading 4596 world state polygons when
the user only wants one district is slow AND visually useless. This
module wraps the download and exposes a subset-string helper the layer
loader uses to clip the visible features to the area of interest (bug 3.6).

Data is downloaded from the Natural Earth CDN on first use and cached
locally. Cache entries are valid for 90 days.
"""
from __future__ import annotations

import os
import tempfile
import urllib.request
import zipfile
from typing import Dict, Optional

from ..utils import cache, logging as log

CDN_BASE = "https://naciscdn.org/naturalearth"


# Layer catalogue: friendly name -> (scale, category, filename)
# We use the 1:10m scale for detail where available.
_NE_LAYERS = {
    "countries":        ("10m", "cultural", "ne_10m_admin_0_countries"),
    "states":           ("10m", "cultural", "ne_10m_admin_1_states_provinces"),
    "populated_places": ("10m", "cultural", "ne_10m_populated_places"),
    "roads":            ("10m", "cultural", "ne_10m_roads"),
    "rivers":           ("10m", "physical", "ne_10m_rivers_lake_centerlines"),
    "lakes":            ("10m", "physical", "ne_10m_lakes"),
    "coastlines":       ("10m", "physical", "ne_10m_coastline"),
    "ocean":            ("10m", "physical", "ne_10m_ocean"),
    "land":             ("10m", "physical", "ne_10m_land"),
    "rivers_small":     ("10m", "physical", "ne_10m_rivers_lake_centerlines_scale_rank"),
}


def available_layers():
    return list(_NE_LAYERS.keys())


def download(layer_name: str) -> str:
    """
    Download and extract a Natural Earth layer.

    Returns the absolute path to the .shp file.
    Cached under the system temp dir for 90 days.
    """
    if layer_name not in _NE_LAYERS:
        raise ValueError(f"Unknown Natural Earth layer: {layer_name}")

    scale, category, basename = _NE_LAYERS[layer_name]
    cache_dir_ne = os.path.join(cache.cache_dir(), "natural_earth", basename)
    shp_path = os.path.join(cache_dir_ne, basename + ".shp")

    if cache.is_fresh(shp_path, ttl_seconds=86400 * 90):
        log.info(f"Natural Earth cache hit: {layer_name}")
        return shp_path

    os.makedirs(cache_dir_ne, exist_ok=True)
    url = f"{CDN_BASE}/{scale}/{category}/{basename}.zip"
    log.info(f"Downloading Natural Earth {layer_name}: {url}")

    zip_path = os.path.join(cache_dir_ne, basename + ".zip")
    headers = {"User-Agent": "ClaudeMapperPro/2.0 (QGIS plugin)"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp, open(zip_path, "wb") as f:
        f.write(resp.read())

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(cache_dir_ne)
    try:
        os.remove(zip_path)
    except OSError:
        pass

    if not os.path.isfile(shp_path):
        raise RuntimeError(f"Expected {shp_path} after extraction, not found.")

    log.info(f"Natural Earth {layer_name} downloaded and extracted.")
    return shp_path


def bbox_subset_sql(bbox: Dict, buffer_pct: float = 0.20) -> str:
    """
    Build a subset SQL clause that filters a Natural Earth layer to
    only features intersecting bbox (+buffer). Delegates to the bbox util.
    """
    from ..utils import bbox as bboxlib
    return bboxlib.to_subset_sql(bbox, buffer_pct)
