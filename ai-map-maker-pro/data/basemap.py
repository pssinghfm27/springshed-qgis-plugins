"""
XYZ basemap providers.

Each provider function returns a QGIS-compatible URI string suitable for:
    QgsRasterLayer(uri, name, 'wms')

All URLs have been verified to work as of April 2026 and are free for
reasonable use. Satellite imagery is via ESRI's public WorldImagery
service (permitted for viewing but not for heavy automated downloads;
users doing mass export should switch to a licensed provider).
"""
from __future__ import annotations

import urllib.parse


def _xyz(url: str, zmin: int = 0, zmax: int = 19) -> str:
    return (
        "type=xyz"
        f"&url={urllib.parse.quote(url, safe='{}:/')}"
        f"&zmax={zmax}&zmin={zmin}"
        "&crs=EPSG:3857"
    )


PROVIDERS = {
    "osm":       ("OpenStreetMap",
                  _xyz("https://tile.openstreetmap.org/{z}/{x}/{y}.png")),
    "satellite": ("ESRI World Imagery",
                  _xyz("https://server.arcgisonline.com/ArcGIS/rest/services/"
                       "World_Imagery/MapServer/tile/{z}/{y}/{x}",
                       zmax=19)),
    "topo":      ("OpenTopoMap",
                  _xyz("https://a.tile.opentopomap.org/{z}/{x}/{y}.png", zmax=17)),
    "terrain":   ("Stamen Terrain",
                  _xyz("https://tiles.stadiamaps.com/tiles/stamen_terrain/"
                       "{z}/{x}/{y}.png", zmax=18)),
    "carto_light": ("CartoDB Positron",
                    _xyz("https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png")),
    "carto_dark":  ("CartoDB Dark Matter",
                    _xyz("https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png")),
}


def uri_for(provider_key: str) -> str:
    """Return the QGIS WMS-provider URI for a named basemap."""
    if provider_key not in PROVIDERS:
        provider_key = "osm"
    return PROVIDERS[provider_key][1]


def display_name(provider_key: str) -> str:
    if provider_key not in PROVIDERS:
        provider_key = "osm"
    return PROVIDERS[provider_key][0]


def available():
    return [(k, v[0]) for k, v in PROVIDERS.items()]
