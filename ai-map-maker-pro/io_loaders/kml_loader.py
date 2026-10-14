"""
KML / KMZ loader.

KML is supported natively by the QGIS OGR driver, so the strategy is:
  - .kml  → return the path as-is with provider='ogr'
  - .kmz  → extract the inner .kml file to a temp dir, return that path

QGIS will load all Placemark features. KMZ extraction honours embedded
stylemaps and icon references (though icons inside the .kmz aren't
rendered unless the user extracts the full archive themselves).
"""
from __future__ import annotations

import os
import tempfile
import zipfile
from typing import Dict

from ..utils import logging as log


def load_kml(path: str) -> Dict:
    """Return a layer-input dict for a .kml file. QGIS reads it directly."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    base = os.path.splitext(os.path.basename(path))[0]
    log.info(f"KML ready for load: {path}")
    return {
        "kind": "mixed",
        "source_path": path,
        "geojson": None,
        "layer_name": base,
        "provider": "ogr",
        "notes": "KML — loaded via OGR",
    }


def load_kmz(path: str) -> Dict:
    """Extract the inner KML from a KMZ archive and return its path."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    out_dir = tempfile.mkdtemp(prefix="kmz_extract_")
    with zipfile.ZipFile(path, "r") as zf:
        kml_members = [n for n in zf.namelist() if n.lower().endswith(".kml")]
        if not kml_members:
            raise ValueError(f"No .kml file found inside {path}")
        # Prefer doc.kml if present (KML convention)
        kml_name = next((n for n in kml_members if n.lower().endswith("doc.kml")),
                        kml_members[0])
        zf.extractall(out_dir)
        kml_path = os.path.join(out_dir, kml_name)

    if not os.path.isfile(kml_path):
        raise RuntimeError(f"Extracted KML not found: {kml_path}")

    base = os.path.splitext(os.path.basename(path))[0]
    log.info(f"KMZ extracted: {path} -> {kml_path}")
    return {
        "kind": "mixed",
        "source_path": kml_path,
        "geojson": None,
        "layer_name": base,
        "provider": "ogr",
        "notes": "KMZ — inner KML extracted and loaded via OGR",
    }
