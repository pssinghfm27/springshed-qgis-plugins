"""
Thread-safe worker QThreads.

This is the fix for bug 3.3 from the handoff: *never* touch QGIS layer
objects from a background thread. The workers here do network I/O and
file download only, then emit signals carrying file paths and spec dicts.
The dialog's main-thread slots create the layers.

Three workers:
  - PlanWorker   : call Claude API to generate or refine a map plan.
  - DownloadWorker: iterate a plan's layers and download each source file.
  - VisionWorker : run a document scan (PDF/image -> scan-result dict).
"""
from __future__ import annotations

import json
import os
import tempfile
import traceback
from typing import Dict, List, Optional

from qgis.PyQt.QtCore import QThread, pyqtSignal

from ..claude import prompts, schemas
from ..claude.client import ClaudeClient, ClaudeError
from ..data import basemap, elevation, geocoder, natural_earth, overpass
from ..io_loaders import document_scanner
from ..utils import logging as log


# ---------------------------------------------------------------------------
# Plan / refine worker
# ---------------------------------------------------------------------------

class PlanWorker(QThread):
    """Calls Claude to produce a map plan. Does NOT touch QGIS."""
    finished_ok = pyqtSignal(dict)        # parsed plan dict
    finished_err = pyqtSignal(str)        # error message
    status = pyqtSignal(str)              # progress text

    def __init__(self, client: ClaudeClient, user_text: str,
                 prior_plan: Optional[Dict] = None,
                 model: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.client = client
        self.user_text = user_text
        self.prior_plan = prior_plan
        self.model = model

    def run(self):
        try:
            self.status.emit("Asking Claude to plan the map…")
            if self.prior_plan:
                response = self.client.refine(
                    prior_plan_json=json.dumps(self.prior_plan, ensure_ascii=False),
                    user_feedback=self.user_text,
                    system_prompt=prompts.REFINE_SYSTEM,
                    model=self.model,
                )
            else:
                response = self.client.plan(
                    user_request=self.user_text,
                    system_prompt=prompts.MAP_PLAN_SYSTEM,
                    model=self.model,
                )
            text = ClaudeClient.extract_text(response)
            plan = schemas.parse_map_plan(text)
            plan["_usage"] = self.client.usage_summary(response)
            self.finished_ok.emit(plan)
        except ClaudeError as e:
            self.finished_err.emit(f"Claude API error: {e}")
        except schemas.PlanError as e:
            self.finished_err.emit(f"Plan parse error: {e}")
        except Exception as e:  # noqa: BLE001
            log.error(f"PlanWorker crashed:\n{traceback.format_exc()}")
            self.finished_err.emit(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# Download worker
# ---------------------------------------------------------------------------

class DownloadWorker(QThread):
    """
    Iterates the plan's layers, downloads data, emits per-layer ready signals.

    For each layer we emit layer_ready(layer_id, path_or_uri, spec_dict).
    The main thread then instantiates the QgsMapLayer and adds it.

    Path semantics:
      - For vector sources we emit a filesystem path (.geojson, .shp, .kml).
      - For raster sources we emit the raster provider URI string OR a path.
      - For XYZ basemaps the "path" is the xyz URI string and provider="wms".
    """
    layer_ready = pyqtSignal(str, str, dict)        # layer_id, path_or_uri, spec
    layer_skipped = pyqtSignal(str, str)             # layer_id, reason
    status = pyqtSignal(str)
    finished_ok = pyqtSignal()
    finished_err = pyqtSignal(str)

    def __init__(self, plan: Dict, bbox: Dict,
                 overpass_timeout: int = 120,
                 auto_expand: bool = True,
                 opentopo_api_key: Optional[str] = None,
                 parent=None):
        super().__init__(parent)
        self.plan = plan
        self.bbox = bbox
        self.overpass_timeout = overpass_timeout
        self.auto_expand = auto_expand
        self.opentopo_api_key = opentopo_api_key

    def run(self):
        try:
            for layer_spec in self.plan.get("layers", []):
                self._handle_layer(layer_spec)
            self.finished_ok.emit()
        except Exception as e:  # noqa: BLE001
            log.error(f"DownloadWorker crashed:\n{traceback.format_exc()}")
            self.finished_err.emit(str(e))

    def _handle_layer(self, spec: Dict) -> None:
        lid = spec["id"]
        source = spec["source"]

        if source == "osm":
            self._osm(lid, spec)
        elif source == "natural_earth":
            self._natural_earth(lid, spec)
        elif source == "xyz_basemap":
            self._xyz(lid, spec)
        elif source == "aws_terrain":
            self._aws_terrain(lid, spec)
        elif source == "opentopo_dem":
            self._opentopo(lid, spec)
        else:
            self.layer_skipped.emit(lid, f"Unknown source: {source}")

    def _osm(self, lid: str, spec: Dict) -> None:
        osm_type = spec.get("osm_type")
        if not osm_type:
            self.layer_skipped.emit(lid, "OSM layer missing osm_type.")
            return
        self.status.emit(f"Fetching OSM ({osm_type})…")
        path = overpass.fetch_to_file(
            osm_type=osm_type,
            bbox=self.bbox,
            custom_filter=spec.get("osm_filter"),
            timeout=self.overpass_timeout,
            auto_expand=self.auto_expand,
        )
        if path is None:
            self.layer_skipped.emit(lid, f"OSM '{osm_type}' returned 0 features (even after expansion).")
            return
        self.layer_ready.emit(lid, path, {**spec, "_provider": "ogr", "_kind": "vector"})

    def _natural_earth(self, lid: str, spec: Dict) -> None:
        ne = spec.get("ne_layer", "countries")
        self.status.emit(f"Fetching Natural Earth ({ne})…")
        try:
            shp_path = natural_earth.download(ne)
        except Exception as e:
            self.layer_skipped.emit(lid, f"Natural Earth '{ne}' failed: {e}")
            return
        subset = natural_earth.bbox_subset_sql(self.bbox, buffer_pct=0.25)
        self.layer_ready.emit(lid, shp_path, {
            **spec, "_provider": "ogr", "_kind": "vector", "_subset_sql": subset
        })

    def _xyz(self, lid: str, spec: Dict) -> None:
        provider = spec.get("xyz_provider") or "osm"
        uri = basemap.uri_for(provider)
        self.layer_ready.emit(lid, uri, {
            **spec, "_provider": "wms", "_kind": "raster"
        })

    def _aws_terrain(self, lid: str, spec: Dict) -> None:
        uri = elevation.aws_terrain_xyz_uri()
        self.layer_ready.emit(lid, uri, {
            **spec, "_provider": "wms", "_kind": "raster"
        })

    def _opentopo(self, lid: str, spec: Dict) -> None:
        if not self.opentopo_api_key:
            self.layer_skipped.emit(
                lid,
                "OpenTopography DEM requires a free API key. "
                "Set it in Settings → API keys, or use 'aws_terrain' as the DEM source."
            )
            return
        dem_type = spec.get("dem_type", "SRTMGL1")
        self.status.emit(f"Downloading {dem_type} DEM from OpenTopography…")
        try:
            path = elevation.opentopography_dem(
                bbox=self.bbox, dem_type=dem_type,
                api_key=self.opentopo_api_key,
            )
        except Exception as e:
            self.layer_skipped.emit(lid, f"OpenTopography failed: {e}")
            return
        self.layer_ready.emit(lid, path, {
            **spec, "_provider": "gdal", "_kind": "raster"
        })


# ---------------------------------------------------------------------------
# Vision / document scan worker
# ---------------------------------------------------------------------------

class VisionWorker(QThread):
    """Run a Claude-vision scan of a PDF or image."""
    finished_ok = pyqtSignal(dict)    # parsed scan result
    finished_err = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, client: ClaudeClient, file_path: str,
                 user_hint: str = "",
                 model: Optional[str] = None,
                 page_index: int = 0,
                 parent=None):
        super().__init__(parent)
        self.client = client
        self.file_path = file_path
        self.user_hint = user_hint
        self.model = model
        self.page_index = page_index

    def run(self):
        try:
            ext = os.path.splitext(self.file_path)[1].lower()
            if ext == ".pdf":
                self.status.emit("Rasterizing PDF page…")
                result = document_scanner.scan_pdf(
                    self.file_path, self.client, self.user_hint,
                    self.model, self.page_index)
            else:
                self.status.emit("Sending image to Claude Vision…")
                result = document_scanner.scan_image(
                    self.file_path, self.client, self.user_hint, self.model)
            self.finished_ok.emit(result)
        except ClaudeError as e:
            self.finished_err.emit(f"Claude Vision error: {e}")
        except Exception as e:  # noqa: BLE001
            log.error(f"VisionWorker crashed:\n{traceback.format_exc()}")
            self.finished_err.emit(f"Vision scan failed: {e}")
