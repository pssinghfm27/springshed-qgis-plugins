"""
Main dialog for Claude AI Map Maker Pro.

Tabs:
  1. Prompt  — type a request, Claude plans + downloads + styles the map.
  2. Upload  — drop CSV/Excel/KML/KMZ to add as layers (with auto-detected coords).
  3. Scan    — upload a PDF or image of a map; Claude Vision extracts features
               and can (a) recreate a similar map and/or (b) georeference the
               scanned page as a raster layer.
  4. Export  — build a print layout with legend + scale bar + north arrow
               and export to PDF/PNG.

Thread safety: every QgsVectorLayer / QgsRasterLayer / QgsProject call is
performed on the main thread. Workers only do network + file I/O.
"""
from __future__ import annotations

import os
import traceback
from typing import Dict, Optional

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget, QLabel,
    QPushButton, QPlainTextEdit, QTextBrowser, QLineEdit, QComboBox,
    QFileDialog, QMessageBox, QProgressBar, QCheckBox, QSplitter,
    QListWidget, QListWidgetItem, QGroupBox, QFormLayout, QSpinBox,
    QDoubleSpinBox,
)
from qgis.PyQt.QtCore import Qt

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsProject, QgsRasterLayer,
    QgsRectangle, QgsVectorLayer,
)

from ..compat import C
from .. import settings_store as S
from ..claude.client import ClaudeClient, ClaudeError
from ..data import geocoder, strike_dip
from ..io_loaders import sniff, load_to_layer_inputs
from ..styling import symbology, labels as style_labels, layout as layout_builder, georeferencer
from ..utils import bbox as bboxlib, logging as log
from ..workers.thread_workers import PlanWorker, DownloadWorker, VisionWorker
from .settings_dialog import SettingsDialog


class ClaudeMapDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("Claude AI Map Maker Pro")
        self.resize(960, 720)

        self.current_plan: Optional[Dict] = None
        self.current_bbox: Optional[Dict] = None
        self.current_scan: Optional[Dict] = None
        self._active_worker = None  # keep QThread alive

        self._build_ui()
        self._update_status_bar()

    # =====================================================================
    # UI construction
    # =====================================================================

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # Top row: model selector + settings shortcut
        top = QHBoxLayout()
        top.addWidget(QLabel("<b>Model:</b>"))
        self.model_combo = QComboBox()
        for key, label in S.AVAILABLE_MODELS:
            self.model_combo.addItem(label, key)
        self._select_in_combo(self.model_combo, S.get_model())
        self.model_combo.currentIndexChanged.connect(
            lambda _: S.set_model(self.model_combo.currentData()))
        top.addWidget(self.model_combo, 2)

        self.settings_btn = QPushButton("⚙  Settings")
        self.settings_btn.clicked.connect(self._open_settings)
        top.addWidget(self.settings_btn)
        root.addLayout(top)

        # Tab widget
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_prompt_tab(),     "💬  Prompt")
        self.tabs.addTab(self._build_upload_tab(),     "📂  Upload file")
        self.tabs.addTab(self._build_scan_tab(),       "🔍  Scan map")
        self.tabs.addTab(self._build_rockbeds_tab(),   "🪨  Rock beds")
        self.tabs.addTab(self._build_export_tab(),     "📤  Export")
        root.addWidget(self.tabs, 1)

        # Status / progress
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

    # ---- Tab 1: Prompt --------------------------------------------------

    def _build_prompt_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "<b>Describe the map you want.</b> Examples:<br>"
            "• <i>Forest cover and spring locations in Bageshwar district, Uttarakhand</i><br>"
            "• <i>Road network of New Delhi with hospitals highlighted</i><br>"
            "• <i>River basins and elevation for Uttarakhand with hillshade</i>"
        ))

        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText(
            "e.g. Show me terrain, rivers and forests in Bageshwar, Uttarakhand, "
            "with village points and a topographic basemap."
        )
        self.prompt_edit.setMinimumHeight(120)
        v.addWidget(self.prompt_edit)

        hb = QHBoxLayout()
        self.generate_btn = QPushButton("✨  Generate map")
        self.generate_btn.setStyleSheet(
            "QPushButton { background:#2563EB; color:white; font-weight:600; "
            "padding:8px 16px; border-radius:4px; } "
            "QPushButton:hover { background:#1D4ED8; }"
        )
        self.generate_btn.clicked.connect(self._on_generate)
        hb.addWidget(self.generate_btn)

        self.refine_btn = QPushButton("🔧  Refine last plan")
        self.refine_btn.setEnabled(False)
        self.refine_btn.clicked.connect(self._on_refine)
        hb.addWidget(self.refine_btn)

        hb.addStretch()
        v.addLayout(hb)

        # Plan preview
        v.addWidget(QLabel("<b>Current plan:</b>"))
        self.plan_view = QTextBrowser()
        self.plan_view.setOpenExternalLinks(True)
        v.addWidget(self.plan_view, 1)

        return w

    # ---- Tab 2: File upload ---------------------------------------------

    def _build_upload_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "<b>Add data from a file.</b> Supports CSV, TSV, Excel (xlsx/xls), "
            "KML, KMZ. Coordinate columns (lat/lon/WKT) are auto-detected."
        ))

        hb = QHBoxLayout()
        self.upload_path = QLineEdit()
        self.upload_path.setReadOnly(True)
        self.upload_path.setPlaceholderText("No file selected")
        hb.addWidget(self.upload_path, 1)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._pick_upload_file)
        hb.addWidget(browse_btn)
        v.addLayout(hb)

        self.upload_layer_name = QLineEdit()
        self.upload_layer_name.setPlaceholderText("Layer name (auto from filename)")
        v.addWidget(self.upload_layer_name)

        self.upload_btn = QPushButton("📥  Add to map")
        self.upload_btn.clicked.connect(self._on_upload_add)
        v.addWidget(self.upload_btn)

        self.upload_info = QTextBrowser()
        self.upload_info.setMaximumHeight(160)
        v.addWidget(self.upload_info, 1)

        return w

    # ---- Tab 3: Map scanning --------------------------------------------

    def _build_scan_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "<b>Scan a map from a PDF or image.</b> Claude Vision will extract "
            "the map type, visible features, legend, and approximate location, "
            "then optionally recreate a similar map in QGIS AND/OR georeference "
            "the scanned image as a raster overlay."
        ))

        hb = QHBoxLayout()
        self.scan_path = QLineEdit()
        self.scan_path.setReadOnly(True)
        self.scan_path.setPlaceholderText("Pick a PDF or image with a map")
        hb.addWidget(self.scan_path, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._pick_scan_file)
        hb.addWidget(browse_btn)
        v.addLayout(hb)

        page_row = QHBoxLayout()
        page_row.addWidget(QLabel("PDF page:"))
        self.scan_page = QSpinBox()
        self.scan_page.setMinimum(1)
        self.scan_page.setMaximum(50)
        self.scan_page.setValue(1)
        page_row.addWidget(self.scan_page)
        page_row.addStretch()
        v.addLayout(page_row)

        self.scan_hint = QLineEdit()
        self.scan_hint.setPlaceholderText(
            "Optional hint: e.g. 'This is a geological map of Uttarakhand'"
        )
        v.addWidget(self.scan_hint)

        hb2 = QHBoxLayout()
        self.scan_btn = QPushButton("🔍  Analyze with Claude Vision")
        self.scan_btn.clicked.connect(self._on_scan)
        hb2.addWidget(self.scan_btn)
        hb2.addStretch()
        v.addLayout(hb2)

        # After-scan actions
        opts = QGroupBox("After scanning:")
        opts_layout = QVBoxLayout(opts)
        self.opt_recreate = QCheckBox("Recreate a similar map in QGIS (using extracted features)")
        self.opt_recreate.setChecked(True)
        self.opt_georef = QCheckBox(
            "Georeference the scanned image and add it as a raster overlay")
        self.opt_georef.setChecked(True)
        opts_layout.addWidget(self.opt_recreate)
        opts_layout.addWidget(self.opt_georef)
        v.addWidget(opts)

        self.apply_scan_btn = QPushButton("✅  Apply scan results")
        self.apply_scan_btn.clicked.connect(self._on_apply_scan)
        self.apply_scan_btn.setEnabled(False)
        v.addWidget(self.apply_scan_btn)

        self.scan_view = QTextBrowser()
        v.addWidget(self.scan_view, 1)

        return w

    # ---- Tab 4: Rock beds (strike/dip projection) -----------------------

    def _build_rockbeds_tab(self) -> QWidget:
        """
        Project a rock bed as a 3D plane through a spring/observation point
        using the strike/dip formula from the Peepalkhola paper.

        Inputs:
          - Origin point layer (spring) — uses the first selected feature,
            or the layer's first feature if nothing is selected.
          - Boundary polygon layer — the plane is evaluated only inside this.
          - DEM raster — used to read the origin's ground elevation.
          - Strike direction θ (deg), dip direction α (deg), dip amount β (deg).
          - Grid spacing (in CRS units — metres for UTM / 3857).

        Output: a new point layer with `z_abs` = ground elevation at origin
        + plane offset at that point. Ready for 3D visualisation via QGIS
        native 3D view or the qgis2threejs plugin.
        """
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "<b>Project a rock-bed plane from a spring/observation point.</b> "
            "Uses the strike/dip formula from the Peepalkhola paper. Output is "
            "a point grid with computed z-values, ready for 3D visualisation."
        ))

        # ---- Inputs ----
        form = QFormLayout()

        self.rb_origin_combo = QComboBox()
        self.rb_origin_combo.setToolTip(
            "Point layer containing the origin (typically the spring). "
            "If the layer has a selection, the first selected feature is used; "
            "otherwise the layer's first feature is used."
        )
        form.addRow("Origin point layer:", self.rb_origin_combo)

        self.rb_boundary_combo = QComboBox()
        self.rb_boundary_combo.setToolTip(
            "Polygon layer defining where to evaluate the plane. The first "
            "selected feature (or first feature if none) is used."
        )
        form.addRow("Boundary polygon layer:", self.rb_boundary_combo)

        self.rb_dem_combo = QComboBox()
        self.rb_dem_combo.setToolTip(
            "DEM raster used to read the ground elevation at the origin."
        )
        form.addRow("DEM raster:", self.rb_dem_combo)

        # Refresh button to populate the combos from the current project
        refresh_btn = QPushButton("↻  Refresh layer lists")
        refresh_btn.clicked.connect(self._refresh_rb_layers)
        form.addRow("", refresh_btn)

        # Angles
        angles = QHBoxLayout()
        self.rb_strike = QDoubleSpinBox()
        self.rb_strike.setRange(0, 360); self.rb_strike.setDecimals(1)
        self.rb_strike.setSuffix("°"); self.rb_strike.setValue(70.0)
        angles.addWidget(QLabel("Strike:")); angles.addWidget(self.rb_strike)

        self.rb_dip_dir = QDoubleSpinBox()
        self.rb_dip_dir.setRange(0, 360); self.rb_dip_dir.setDecimals(1)
        self.rb_dip_dir.setSuffix("°"); self.rb_dip_dir.setValue(160.0)
        angles.addWidget(QLabel("Dip dir:")); angles.addWidget(self.rb_dip_dir)

        self.rb_dip_amt = QDoubleSpinBox()
        self.rb_dip_amt.setRange(0, 90); self.rb_dip_amt.setDecimals(1)
        self.rb_dip_amt.setSuffix("°"); self.rb_dip_amt.setValue(40.0)
        angles.addWidget(QLabel("Dip amt:")); angles.addWidget(self.rb_dip_amt)
        angles.addStretch()
        form.addRow("Bed orientation:", _wrap_layout(angles))

        # Grid spacing
        self.rb_spacing = QDoubleSpinBox()
        self.rb_spacing.setRange(0.1, 10000.0); self.rb_spacing.setDecimals(2)
        self.rb_spacing.setValue(3.0); self.rb_spacing.setSuffix(" units")
        self.rb_spacing.setToolTip(
            "Grid spacing in the boundary polygon's CRS units. 3 metres is "
            "typical for local 3857 / UTM work; use larger spacing for bigger "
            "areas so you don't generate millions of points."
        )
        form.addRow("Grid spacing:", self.rb_spacing)

        # Output layer name
        self.rb_output_name = QLineEdit()
        self.rb_output_name.setPlaceholderText("Rock bed plane")
        self.rb_output_name.setText("Rock bed plane")
        form.addRow("Output layer name:", self.rb_output_name)

        v.addLayout(form)

        # Help note on convention
        note = QLabel(
            "<i>Convention: strike and dip direction are measured clockwise "
            "from north. If the generated plane tilts the wrong way, add 180° "
            "to the dip direction — the Peepalkhola paper treats dip direction "
            "as the up-dip azimuth.</i>"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#6B7280; font-size:11px;")
        v.addWidget(note)

        # Action
        hb = QHBoxLayout()
        self.rb_build_btn = QPushButton("🪨  Build rock-bed plane")
        self.rb_build_btn.setStyleSheet(
            "QPushButton { background:#7C3AED; color:white; font-weight:600; "
            "padding:8px 16px; border-radius:4px; } "
            "QPushButton:hover { background:#6D28D9; }"
        )
        self.rb_build_btn.clicked.connect(self._on_build_rock_bed)
        hb.addWidget(self.rb_build_btn)
        hb.addStretch()
        v.addLayout(hb)

        self.rb_info = QTextBrowser()
        v.addWidget(self.rb_info, 1)

        # Populate immediately on first view
        self._refresh_rb_layers()
        return w

    # -- Rock-beds tab: helpers and action handlers -----------------------

    def _refresh_rb_layers(self) -> None:
        """Populate the three layer combos from layers currently in the project."""
        try:
            from qgis.core import QgsMapLayer, QgsProject, QgsWkbTypes
        except ImportError:
            return

        point_layers, poly_layers, raster_layers = [], [], []
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.type() == QgsMapLayer.VectorLayer:
                gtype = _geom_family(lyr)
                if gtype == "point":
                    point_layers.append(lyr)
                elif gtype == "polygon":
                    poly_layers.append(lyr)
            elif lyr.type() == QgsMapLayer.RasterLayer:
                raster_layers.append(lyr)

        self._fill_layer_combo(self.rb_origin_combo, point_layers)
        self._fill_layer_combo(self.rb_boundary_combo, poly_layers)
        self._fill_layer_combo(self.rb_dem_combo, raster_layers)

    @staticmethod
    def _fill_layer_combo(combo: QComboBox, layers: list) -> None:
        """Refresh a combo with (name, layer-id) pairs without losing selection."""
        prev_id = combo.currentData() if combo.count() else None
        combo.blockSignals(True)
        combo.clear()
        for lyr in layers:
            combo.addItem(lyr.name(), lyr.id())
        if prev_id:
            for i in range(combo.count()):
                if combo.itemData(i) == prev_id:
                    combo.setCurrentIndex(i)
                    break
        combo.blockSignals(False)

    @staticmethod
    def _lookup_layer(combo: QComboBox):
        from qgis.core import QgsProject
        lid = combo.currentData()
        if not lid:
            return None
        return QgsProject.instance().mapLayer(lid)

    def _on_build_rock_bed(self) -> None:
        """Main-thread slot: run the plane computation and add the output layer."""
        try:
            from qgis.core import (
                QgsFeatureRequest, QgsProject, QgsVectorLayer,
            )
        except ImportError:
            QMessageBox.critical(self, "QGIS not available",
                                 "This tab requires QGIS.")
            return

        origin_layer   = self._lookup_layer(self.rb_origin_combo)
        boundary_layer = self._lookup_layer(self.rb_boundary_combo)
        dem_layer      = self._lookup_layer(self.rb_dem_combo)

        if not origin_layer:
            QMessageBox.warning(self, "Missing input",
                                "Pick a point layer for the origin (spring).")
            return
        if not boundary_layer:
            QMessageBox.warning(self, "Missing input",
                                "Pick a polygon layer to bound the plane.")
            return
        if not dem_layer:
            QMessageBox.warning(self, "Missing input",
                                "Pick a DEM raster to sample the origin's elevation.")
            return

        # Pull the origin feature — selected first, else first feature.
        origin_feat = _first_feature(origin_layer)
        if origin_feat is None:
            QMessageBox.warning(self, "No features",
                                f"Layer '{origin_layer.name()}' has no features.")
            return
        origin_geom = origin_feat.geometry()
        if origin_geom is None or origin_geom.isEmpty():
            QMessageBox.warning(self, "Invalid origin",
                                "Origin feature has no geometry.")
            return
        origin_pt = origin_geom.asPoint()
        origin_xy = (origin_pt.x(), origin_pt.y())

        # Pull the boundary polygon
        boundary_feat = _first_feature(boundary_layer)
        if boundary_feat is None:
            QMessageBox.warning(self, "No features",
                                f"Layer '{boundary_layer.name()}' has no features.")
            return
        boundary_geom = boundary_feat.geometry()
        if boundary_geom is None or boundary_geom.isEmpty():
            QMessageBox.warning(self, "Invalid boundary",
                                "Boundary feature has no geometry.")
            return

        # Sample DEM at origin
        origin_elev = strike_dip.sample_dem_at_point(
            dem_layer, origin_xy[0], origin_xy[1],
            source_crs=origin_layer.crs())
        if origin_elev is None:
            QMessageBox.warning(
                self, "DEM sample failed",
                f"Could not sample DEM at ({origin_xy[0]:.2f}, {origin_xy[1]:.2f}). "
                "Check that the DEM covers the origin point."
            )
            return

        # Check CRSes match (or at least are both projected)
        if origin_layer.crs() != boundary_layer.crs():
            reply = QMessageBox.question(
                self, "CRS mismatch",
                f"Origin layer CRS ({origin_layer.crs().authid()}) differs from "
                f"boundary layer CRS ({boundary_layer.crs().authid()}).\n\n"
                "The plane will be computed in the boundary polygon's CRS. "
                "Continue?",
                C.MSG_YES | C.MSG_NO, C.MSG_YES,
            )
            if reply != C.MSG_YES:
                return
            # Reproject origin into boundary CRS
            from qgis.core import QgsCoordinateTransform, QgsPointXY, QgsProject
            tr = QgsCoordinateTransform(origin_layer.crs(), boundary_layer.crs(),
                                         QgsProject.instance())
            rp = tr.transform(QgsPointXY(origin_xy[0], origin_xy[1]))
            origin_xy = (rp.x(), rp.y())

        # Build the grid
        self._set_busy(True, "Computing rock-bed grid…")
        try:
            features = strike_dip.build_rock_bed_grid(
                origin_xy=origin_xy,
                origin_elevation=origin_elev,
                strike_deg=self.rb_strike.value(),
                dip_dir_deg=self.rb_dip_dir.value(),
                dip_amount_deg=self.rb_dip_amt.value(),
                polygon_geom=boundary_geom,
                spacing=self.rb_spacing.value(),
            )
        except ValueError as e:
            self._set_busy(False, "")
            QMessageBox.critical(self, "Cannot build grid", str(e))
            return

        if not features:
            self._set_busy(False, "")
            QMessageBox.warning(self, "Empty grid",
                                "No grid points fell inside the boundary polygon. "
                                "Check that the origin is near/inside the polygon "
                                "and your spacing is appropriate.")
            return

        # Write GeoJSON and add as a layer
        gj_path = strike_dip.write_grid_as_geojson(features)
        name = self.rb_output_name.text().strip() or "Rock bed plane"
        layer = QgsVectorLayer(gj_path, name, "ogr")
        if not layer.isValid():
            self._set_busy(False, "")
            QMessageBox.critical(self, "Invalid layer",
                                 f"Could not load generated grid from {gj_path}")
            return

        # Apply graduated styling by z_abs for immediate visual cue
        z_min = min(f["z_abs"] for f in features)
        z_max = max(f["z_abs"] for f in features)
        z_mid = (z_min + z_max) / 2
        grad_spec = {
            "default_color": "#7C3AED", "stroke_color": None,
            "stroke_width": 0.2, "point_size": 2.4, "opacity": 1.0,
            "label_field": "z_abs", "render_type": "graduated",
            "categories": [
                {"min": z_min,         "max": z_mid,   "color": "#1E40AF", "label": f"{z_min:.0f} – {z_mid:.0f} m"},
                {"min": z_mid,         "max": z_max,   "color": "#EA580C", "label": f"{z_mid:.0f} – {z_max:.0f} m"},
            ],
        }
        symbology.apply_style(layer, grad_spec)
        QgsProject.instance().addMapLayer(layer)

        self._set_busy(False, "")
        self.rb_info.setHtml(
            f"<b>✓ Rock-bed plane built.</b><br>"
            f"Origin: ({origin_xy[0]:.2f}, {origin_xy[1]:.2f}) at "
            f"{origin_elev:.2f} m elevation<br>"
            f"Strike {self.rb_strike.value()}°, dip dir {self.rb_dip_dir.value()}°, "
            f"dip amount {self.rb_dip_amt.value()}°<br>"
            f"Grid points: {len(features):,} (spacing {self.rb_spacing.value()})<br>"
            f"Z range on bed: {z_min:.1f} – {z_max:.1f} m<br><br>"
            f"<i>Next: open <b>View → 3D Map Views</b> and use the DEM as the "
            f"terrain; or use the <b>qgis2threejs</b> plugin and set the "
            f"'<code>z_abs</code>' field as the absolute Z for this layer.</i>"
        )

    # ---- Tab 5: Export --------------------------------------------------

    def _build_export_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "<b>Build a print layout with legend, scale bar and north arrow,</b> "
            "then export to PDF or PNG."
        ))

        form = QFormLayout()
        self.export_title = QLineEdit()
        self.export_title.setPlaceholderText("Map title for the layout")
        form.addRow("Title:", self.export_title)
        v.addLayout(form)

        hb = QHBoxLayout()
        pdf_btn = QPushButton("📄  Export PDF")
        pdf_btn.clicked.connect(lambda: self._on_export("pdf"))
        hb.addWidget(pdf_btn)
        png_btn = QPushButton("🖼  Export PNG (300dpi)")
        png_btn.clicked.connect(lambda: self._on_export("png"))
        hb.addWidget(png_btn)
        hb.addStretch()
        v.addLayout(hb)

        v.addStretch()

        self.export_info = QTextBrowser()
        self.export_info.setMaximumHeight(180)
        v.addWidget(self.export_info)

        return w

    # =====================================================================
    # Settings / status helpers
    # =====================================================================

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self)
        if dlg.exec() == C.DIALOG_ACCEPTED:
            self._select_in_combo(self.model_combo, S.get_model())
            self._update_status_bar()

    def _update_status_bar(self) -> None:
        key_ok = bool(S.get_api_key())
        msg = []
        msg.append("🔑 API key: ✓ set" if key_ok else "🔑 API key: ✗ not set (open Settings)")
        msg.append(f"Model: {S.get_model()}")
        self.status_label.setText("    ".join(msg))

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        self.progress.setVisible(busy)
        for btn in (self.generate_btn, self.refine_btn,
                    self.upload_btn, self.scan_btn, self.apply_scan_btn):
            btn.setEnabled(not busy)
        if msg:
            self.status_label.setText(msg)
        elif not busy:
            self._update_status_bar()

    def _make_client(self) -> Optional[ClaudeClient]:
        key = S.get_api_key()
        if not key:
            QMessageBox.warning(
                self, "API key missing",
                "Open Settings (⚙ top right) and paste your Anthropic API key.\n\n"
                "Free keys: https://console.anthropic.com/settings/keys"
            )
            return None
        return ClaudeClient(
            api_key=key,
            model=S.get_model(),
            use_cache=S.get_bool(S.USE_PROMPT_CACHING, True),
        )

    @staticmethod
    def _select_in_combo(combo: QComboBox, value: str) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    # =====================================================================
    # PROMPT tab actions
    # =====================================================================

    def _on_generate(self) -> None:
        text = self.prompt_edit.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "Empty prompt",
                                    "Describe the map you want to create.")
            return
        client = self._make_client()
        if not client:
            return

        self._set_busy(True, "Asking Claude…")
        self._active_worker = PlanWorker(client, text, prior_plan=None,
                                         model=S.get_model(), parent=self)
        self._active_worker.status.connect(self._on_status)
        self._active_worker.finished_ok.connect(self._on_plan_ready)
        self._active_worker.finished_err.connect(self._on_worker_error)
        self._active_worker.start()

    def _on_refine(self) -> None:
        if not self.current_plan:
            return
        text = self.prompt_edit.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "Empty feedback",
                                    "Type a refinement like "
                                    "'make roads red' or 'add hospitals'.")
            return
        client = self._make_client()
        if not client:
            return
        self._set_busy(True, "Refining plan with Claude…")
        self._active_worker = PlanWorker(
            client, text, prior_plan=self.current_plan,
            model=S.get_model(), parent=self)
        self._active_worker.status.connect(self._on_status)
        self._active_worker.finished_ok.connect(self._on_plan_ready)
        self._active_worker.finished_err.connect(self._on_worker_error)
        self._active_worker.start()

    def _on_plan_ready(self, plan: Dict) -> None:
        self.current_plan = plan
        usage = plan.get("_usage", "")
        self._render_plan(plan, usage)
        self.refine_btn.setEnabled(True)
        self._set_busy(False, "Plan ready. Geocoding location…")

        # Chain: geocode, then download.
        location = plan.get("location", "")
        try:
            geo = geocoder.geocode(location)
        except Exception as e:
            QMessageBox.warning(self, "Geocoding failed",
                                f"Could not resolve '{location}':\n{e}")
            return
        self.current_bbox = geo["bbox"]
        self.export_title.setText(plan.get("map_title", ""))
        self._start_download(plan, self.current_bbox)

    def _render_plan(self, plan: Dict, usage: str = "") -> None:
        rows = []
        rows.append(f"<h3>{self._esc(plan.get('map_title', ''))}</h3>")
        rows.append(f"<p><b>Location:</b> {self._esc(plan.get('location', ''))}</p>")
        if plan.get("notes"):
            rows.append(f"<p><i>{self._esc(plan['notes'])}</i></p>")
        if usage:
            rows.append(f"<p style='color:#6B7280;font-size:11px'>"
                        f"API usage: {self._esc(usage)}</p>")
        rows.append("<table style='border-collapse:collapse;width:100%'>")
        rows.append("<tr style='background:#F3F4F6'><th style='padding:4px;text-align:left'>Layer</th>"
                    "<th style='padding:4px;text-align:left'>Source</th>"
                    "<th style='padding:4px;text-align:left'>Color</th>"
                    "<th style='padding:4px;text-align:left'>z</th></tr>")
        for lyr in plan.get("layers", []):
            color = lyr.get("default_color", "")
            swatch = (f"<span style='display:inline-block;width:14px;height:14px;"
                      f"background:{color};border:1px solid #999'></span>")
            rows.append(f"<tr>"
                        f"<td style='padding:4px'>{self._esc(lyr.get('name',''))}</td>"
                        f"<td style='padding:4px'>{self._esc(lyr.get('source',''))}"
                        f" {self._esc(lyr.get('osm_type') or lyr.get('ne_layer') or '')}</td>"
                        f"<td style='padding:4px'>{swatch} {color}</td>"
                        f"<td style='padding:4px'>{lyr.get('z_order','')}</td></tr>")
        rows.append("</table>")
        self.plan_view.setHtml("".join(rows))

    # =====================================================================
    # Download & layer-add (main thread slots)
    # =====================================================================

    def _start_download(self, plan: Dict, bbox: Dict) -> None:
        self._set_busy(True, "Downloading layers…")
        otopo_key = S.get_value("opentopography_key", "", type_=str) or None

        self._active_worker = DownloadWorker(
            plan=plan, bbox=bbox,
            overpass_timeout=S.get_int(S.OVERPASS_TIMEOUT, 120),
            auto_expand=S.get_bool(S.AUTO_FALLBACK, True),
            opentopo_api_key=otopo_key or None,
            parent=self,
        )
        self._active_worker.status.connect(self._on_status)
        self._active_worker.layer_ready.connect(self._on_layer_ready)
        self._active_worker.layer_skipped.connect(self._on_layer_skipped)
        self._active_worker.finished_ok.connect(self._on_download_done)
        self._active_worker.finished_err.connect(self._on_worker_error)
        self._active_worker.start()

    def _on_status(self, msg: str) -> None:
        self.status_label.setText(msg)

    def _on_layer_skipped(self, lid: str, reason: str) -> None:
        log.warn(f"Layer skipped: {lid} — {reason}")
        self.status_label.setText(f"Skipped {lid}: {reason}")

    def _on_layer_ready(self, layer_id: str, path_or_uri: str, spec: Dict) -> None:
        """Main-thread slot — SAFE to touch QGIS objects here."""
        try:
            kind = spec.get("_kind", "vector")
            provider = spec.get("_provider", "ogr")
            name = spec.get("name") or layer_id

            if kind == "raster":
                layer = QgsRasterLayer(path_or_uri, name, provider)
            else:
                layer = QgsVectorLayer(path_or_uri, name, provider)

            if not layer.isValid():
                log.error(f"Layer {name} invalid. URI: {path_or_uri}")
                return

            # Apply bbox subset for Natural Earth layers (bug 3.6 fix)
            subset = spec.get("_subset_sql")
            if subset and kind == "vector":
                try:
                    layer.setSubsetString(subset)
                except Exception as e:
                    log.warn(f"Subset string rejected for {name}: {e}")

            # Style the layer
            if kind == "vector":
                symbology.apply_style(layer, spec)
                style_labels.apply_labels(layer, spec)
            else:
                try:
                    layer.setOpacity(float(spec.get("opacity", 1.0)))
                except AttributeError:
                    pass

            QgsProject.instance().addMapLayer(layer)
            log.success(f"Added layer: {name} ({layer.featureCount() if kind=='vector' else 'raster'})")
        except Exception as e:
            log.error(f"Failed to add layer {layer_id}: {e}\n{traceback.format_exc()}")

    def _on_download_done(self) -> None:
        # Final step: zoom the canvas to the plan's bbox and fix CRS.
        if self.current_bbox:
            try:
                canvas = self.iface.mapCanvas()
                canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
                b = bboxlib.normalize(self.current_bbox)
                canvas.setExtent(QgsRectangle(b["west"], b["south"], b["east"], b["north"]))
                canvas.refresh()
            except Exception as e:
                log.warn(f"Canvas zoom failed: {e}")
        self._set_busy(False, "Map ready ✓")

    def _on_worker_error(self, msg: str) -> None:
        self._set_busy(False, "")
        QMessageBox.critical(self, "Error", msg)

    # =====================================================================
    # UPLOAD tab actions
    # =====================================================================

    def _pick_upload_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open data file", "",
            "Data files (*.csv *.tsv *.txt *.xlsx *.xlsm *.xls *.kml *.kmz);;"
            "All files (*.*)"
        )
        if path:
            self.upload_path.setText(path)
            base = os.path.splitext(os.path.basename(path))[0]
            self.upload_layer_name.setText(base)

    def _on_upload_add(self) -> None:
        path = self.upload_path.text().strip()
        if not path or not os.path.isfile(path):
            QMessageBox.information(self, "No file",
                                    "Pick a CSV, Excel, KML, or KMZ file first.")
            return
        try:
            spec = load_to_layer_inputs(path)
        except Exception as e:
            QMessageBox.critical(self, "Failed to parse file", str(e))
            return

        name = self.upload_layer_name.text().strip() or spec["layer_name"]
        layer = QgsVectorLayer(spec["source_path"], name, spec["provider"])
        if not layer.isValid():
            QMessageBox.critical(self, "Invalid layer",
                                 f"QGIS could not open:\n{spec['source_path']}")
            return

        # Tasteful default symbology for user-uploaded layers
        default_spec = {
            "default_color": "#EF4444",
            "stroke_color":  "#FFFFFF",
            "stroke_width":  0.6,
            "point_size":    4.0,
            "opacity":       1.0,
            "render_type":   "single",
        }
        symbology.apply_style(layer, default_spec)

        QgsProject.instance().addMapLayer(layer)
        try:
            self.iface.mapCanvas().setExtent(layer.extent())
            self.iface.mapCanvas().refresh()
        except Exception:
            pass

        self.upload_info.setHtml(
            f"<b>Added:</b> {self._esc(name)}<br>"
            f"<b>Source:</b> {self._esc(path)}<br>"
            f"<b>Features:</b> {layer.featureCount()}<br>"
            f"<b>Notes:</b> {self._esc(spec.get('notes',''))}"
        )

    # =====================================================================
    # SCAN tab actions
    # =====================================================================

    def _pick_scan_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open map image/PDF", "",
            "Maps (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.webp *.bmp);;"
            "All files (*.*)"
        )
        if path:
            self.scan_path.setText(path)
            kind = sniff(path)
            self.scan_page.setEnabled(kind == "pdf")

    def _on_scan(self) -> None:
        path = self.scan_path.text().strip()
        if not path or not os.path.isfile(path):
            QMessageBox.information(self, "No file",
                                    "Pick a PDF or image first.")
            return
        client = self._make_client()
        if not client:
            return
        self.current_scan = None
        self.apply_scan_btn.setEnabled(False)

        self._set_busy(True, "Analyzing with Claude Vision…")
        self._active_worker = VisionWorker(
            client=client,
            file_path=path,
            user_hint=self.scan_hint.text().strip(),
            model=S.get_vision_model(),
            page_index=max(0, self.scan_page.value() - 1),
            parent=self,
        )
        self._active_worker.status.connect(self._on_status)
        self._active_worker.finished_ok.connect(self._on_scan_ready)
        self._active_worker.finished_err.connect(self._on_worker_error)
        self._active_worker.start()

    def _on_scan_ready(self, scan: Dict) -> None:
        self.current_scan = scan
        self._set_busy(False, "Scan complete")
        self.apply_scan_btn.setEnabled(True)

        parts = []
        parts.append(f"<h3>Scan results</h3>")
        parts.append(f"<b>Map type:</b> {self._esc(scan.get('map_type','unknown'))}<br>")
        if scan.get("title"):
            parts.append(f"<b>Title:</b> {self._esc(scan['title'])}<br>")
        if scan.get("location"):
            parts.append(f"<b>Location:</b> {self._esc(scan['location'])}<br>")
        bb = scan.get("approximate_bbox")
        if bb:
            parts.append(f"<b>Bbox:</b> "
                         f"S{bb.get('south')}, W{bb.get('west')}, "
                         f"N{bb.get('north')}, E{bb.get('east')}<br>")
        parts.append(f"<b>BBox confidence:</b> {self._esc(scan.get('bbox_confidence','unknown'))}<br>")
        if scan.get("projection_guess"):
            parts.append(f"<b>Projection:</b> {self._esc(scan['projection_guess'])}<br>")

        feats = scan.get("visible_features") or []
        if feats:
            parts.append(f"<b>Visible features:</b> {', '.join(self._esc(f) for f in feats)}<br>")

        gcps = scan.get("ground_control_points") or []
        parts.append(f"<b>GCPs:</b> {len(gcps)}<br>")

        legend = scan.get("legend") or []
        if legend:
            parts.append("<b>Legend:</b><ul>")
            for it in legend:
                sym = it.get("symbol", "")
                lbl = it.get("label", "")
                if sym.startswith("#"):
                    parts.append(f"<li><span style='background:{sym};"
                                 f"display:inline-block;width:14px;height:14px;"
                                 f"border:1px solid #999'></span> {self._esc(lbl)}</li>")
                else:
                    parts.append(f"<li>{self._esc(sym)} — {self._esc(lbl)}</li>")
            parts.append("</ul>")

        rp = scan.get("recreate_plan")
        if rp:
            parts.append(f"<b>Recreate plan:</b> {len(rp.get('layers',[]))} layers "
                         f"({self._esc(rp.get('notes',''))})")

        self.scan_view.setHtml("".join(parts))

    def _on_apply_scan(self) -> None:
        if not self.current_scan:
            return
        scan = self.current_scan

        # Option A: recreate a similar map
        if self.opt_recreate.isChecked() and scan.get("recreate_plan"):
            rp = scan["recreate_plan"]
            self.current_plan = rp
            self.refine_btn.setEnabled(True)
            self._render_plan(rp, usage="(from scan)")
            loc = rp.get("location") or scan.get("location")
            try:
                geo = geocoder.geocode(loc)
                self.current_bbox = geo["bbox"]
            except Exception as e:
                bb = scan.get("approximate_bbox")
                if bb and bboxlib.is_valid(bboxlib.normalize(bb)):
                    self.current_bbox = bboxlib.normalize(bb)
                else:
                    QMessageBox.warning(self, "Location unknown",
                                        f"Could not geocode '{loc}': {e}")
                    return
            self.export_title.setText(rp.get("map_title", ""))
            self._start_download(rp, self.current_bbox)

        # Option B: georeference the scanned image
        if self.opt_georef.isChecked():
            img_path = scan.get("_image_path")
            if not img_path or not os.path.isfile(img_path):
                QMessageBox.warning(self, "No image",
                                    "Scanned image path missing — try re-scanning.")
                return
            self._georef_scanned_image(img_path, scan)

    def _georef_scanned_image(self, image_path: str, scan: Dict) -> None:
        """Write worldfile + optional .points file, then load as raster layer."""
        bb = scan.get("approximate_bbox")
        gcps = scan.get("ground_control_points") or []

        # Prefer bbox georeferencing when the bbox confidence is medium+.
        conf = (scan.get("bbox_confidence") or "").lower()
        used_wld = False
        if bb and bboxlib.is_valid(bboxlib.normalize(bb)) and conf in ("high", "medium"):
            try:
                georeferencer.write_worldfile_for_bbox(image_path, bb)
                used_wld = True
            except Exception as e:
                log.warn(f"World file failed: {e}")

        # Always write a .points file if we have GCPs — useful even when
        # bbox is trusted.
        if len(gcps) >= 2:
            try:
                georeferencer.write_points_file(image_path, gcps)
            except Exception as e:
                log.warn(f"GCP file failed: {e}")

        if used_wld:
            name = f"Scanned: {os.path.basename(image_path)}"
            layer = QgsRasterLayer(image_path, name, "gdal")
            if layer.isValid():
                layer.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
                layer.setOpacity(0.65)
                QgsProject.instance().addMapLayer(layer)
                log.success(f"Georeferenced scan added: {name}")
                self.status_label.setText(
                    "Scanned map overlay added. For precise alignment open the "
                    "QGIS Georeferencer tool and use the auto-generated .points file."
                )
            else:
                QMessageBox.warning(self, "Raster invalid",
                                    f"QGIS could not open {image_path}")
        else:
            QMessageBox.information(
                self, "Georeferencing skipped",
                "BBox confidence was too low to auto-georeference.\n\n"
                "A .points file was written next to the image — open the "
                "Georeferencer plugin (Raster → Georeferencer), load the image, "
                "then Load GCPs from that .points file."
            )

    # =====================================================================
    # EXPORT tab actions
    # =====================================================================

    def _on_export(self, fmt: str) -> None:
        title = self.export_title.text().strip() or "Map"
        project = QgsProject.instance()
        try:
            canvas_extent = self.iface.mapCanvas().extent()
        except Exception:
            canvas_extent = None

        try:
            layout = layout_builder.build_layout(
                project=project, title=title, extent=canvas_extent,
            )
            # Register layout (optional — lets the user edit it afterwards)
            try:
                project.layoutManager().addLayout(layout)
            except Exception:
                pass
        except Exception as e:
            QMessageBox.critical(self, "Layout failed",
                                 f"Could not build print layout:\n{e}")
            return

        if fmt == "pdf":
            out, _ = QFileDialog.getSaveFileName(
                self, "Export PDF", f"{title}.pdf", "PDF (*.pdf)")
            if not out:
                return
            try:
                layout_builder.export_pdf(layout, out)
                self.export_info.setHtml(f"<b>Saved:</b> {self._esc(out)}")
            except Exception as e:
                QMessageBox.critical(self, "Export failed", str(e))
        elif fmt == "png":
            out, _ = QFileDialog.getSaveFileName(
                self, "Export PNG", f"{title}.png", "PNG (*.png)")
            if not out:
                return
            try:
                layout_builder.export_png(layout, out, dpi=300)
                self.export_info.setHtml(f"<b>Saved:</b> {self._esc(out)}")
            except Exception as e:
                QMessageBox.critical(self, "Export failed", str(e))

    # =====================================================================
    # Helpers
    # =====================================================================

    @staticmethod
    def _esc(s) -> str:
        if s is None:
            return ""
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))


# ---------------------------------------------------------------------------
# Module-level helpers (used by the rock-beds tab)
# ---------------------------------------------------------------------------

def _wrap_layout(layout):
    """Wrap a QLayout in a QWidget so it can be placed into a QFormLayout row."""
    w = QWidget()
    w.setLayout(layout)
    return w


def _geom_family(vector_layer) -> str:
    """'point' | 'line' | 'polygon' for a QgsVectorLayer, QGIS-3/4 safe."""
    from ..compat import detect_geometry_type
    return detect_geometry_type(vector_layer)


def _first_feature(layer):
    """
    Return the first selected feature on a QgsVectorLayer, or the layer's
    first feature if nothing is selected. Returns None if the layer is empty.
    """
    try:
        if layer.selectedFeatureCount() > 0:
            for f in layer.selectedFeatures():
                return f
        for f in layer.getFeatures():
            return f
    except Exception:
        pass
    return None
