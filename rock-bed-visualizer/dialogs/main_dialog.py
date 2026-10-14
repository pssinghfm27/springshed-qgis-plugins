"""
Main dialog for Rock Bed Visualizer.

Design goals (the mistakes NOT to repeat):

1. Every dropdown refreshes from the live QGIS project, every time the
   dialog is shown or after the user clicks Refresh. Empty combos show
   a helpful placeholder telling the user what kind of layer is missing.

2. The dialog has two MODES selected by radio buttons:
   - Single-bed: enter strike, dip_dir, dip_amount manually. Origin can
     come from a point layer feature or a canvas click.
   - Batch: read strike/dip/origin from an attribute table of a rock
     observation layer; one output plane per row.

3. A "Validate" button runs every validator and shows a single dialog
   listing ALL problems it found, each with an actionable hint.

4. "Build planes" is only enabled after at least one Validate pass. If
   the user edits any field, the enable state is re-checked.

5. All QGIS layer operations happen on the main thread — this dialog's
   slots are all synchronous. No QThreads, no signals-across-threads.

6. Every QMessageBox message is phrased as "here's what's wrong, here's
   how to fix it" rather than "error: <something technical>".
"""
from __future__ import annotations

import os
import traceback
from typing import Dict, List, Optional, Tuple

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QDoubleSpinBox, QSpinBox,
    QRadioButton, QButtonGroup, QGroupBox, QMessageBox, QTextBrowser,
    QFileDialog, QWidget, QCheckBox, QProgressBar,
)

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature,
    QgsField, QgsGeometry, QgsMapLayer, QgsPointXY, QgsProject,
    QgsRasterLayer, QgsRectangle, QgsVectorLayer, QgsWkbTypes,
)

from ..compat import C, detect_geometry_type
from ..core import grid_builder, dem_sampler, math_plane, validators
from ..core import layer_writer
from ..core.pick_tool import PickPointTool
from ..utils import logging as log


PAPER_CAVEATS = (
    "Caveats (from the Peepalkhola paper): this method projects a single "
    "idealised rock-bed plane through the origin using one strike/dip "
    "measurement. It does not account for folds, faults, or any internal "
    "structural variability. Use it as an indicative aid for questions like "
    "'if this is the bed orientation, how far along the terrain could the "
    "recharge area extend?' — not as a definitive geological model."
)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _first_feature(layer):
    """Return the first selected feature, else the first feature. None if empty."""
    if layer is None:
        return None
    try:
        if layer.selectedFeatureCount() > 0:
            for f in layer.selectedFeatures():
                return f
        for f in layer.getFeatures():
            return f
    except Exception:
        pass
    return None


def _transform_point(x: float, y: float,
                     src_crs: QgsCoordinateReferenceSystem,
                     dst_crs: QgsCoordinateReferenceSystem) -> Tuple[float, float]:
    """Transform (x, y) between CRSes, or return unchanged if CRSes match."""
    if src_crs == dst_crs:
        return (x, y)
    tr = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
    pt = tr.transform(QgsPointXY(x, y))
    return (pt.x(), pt.y())


# ===========================================================================
# Dialog
# ===========================================================================

class RockBedDialog(QDialog):

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("Rock Bed Visualizer")
        self.setMinimumWidth(620)
        self.setMinimumHeight(700)

        # State
        self._pick_tool: Optional[PickPointTool] = None
        self._previous_tool = None
        self._validated_ok = False

        self._build_ui()
        self._refresh_layer_combos()
        self._sync_mode_visibility()

        # When the project layers change, refresh combos so stale entries
        # don't linger.
        try:
            QgsProject.instance().layersAdded.connect(self._refresh_layer_combos)
            QgsProject.instance().layersRemoved.connect(self._refresh_layer_combos)
        except Exception:
            pass

    # -----------------------------------------------------------------
    # UI
    # -----------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        intro = QLabel(
            "<b>Project a rock-bed plane</b> through an origin point using "
            "strike/dip observations. Implementation of the method described "
            "in <i>Visualizing Strikes and Dips of Rock Beds in QGIS with "
            "qgis2threejs</i> (CHIRAG / Dotiyalgaon). Works entirely offline."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        root.addWidget(self._build_mode_box())
        root.addWidget(self._build_origin_box())
        self.angles_box = self._build_angles_box()
        root.addWidget(self.angles_box)
        self.batch_box = self._build_batch_box()
        root.addWidget(self.batch_box)
        root.addWidget(self._build_area_box())
        root.addWidget(self._build_dem_box())
        root.addWidget(self._build_output_box())

        # Action buttons
        btn_row = QHBoxLayout()
        self.validate_btn = QPushButton("Validate inputs")
        self.validate_btn.clicked.connect(self._on_validate)
        btn_row.addWidget(self.validate_btn)

        self.build_btn = QPushButton("Build planes")
        self.build_btn.setStyleSheet(
            "QPushButton { background:#7C3AED; color:white; font-weight:600; "
            "padding:8px 16px; border-radius:4px; } "
            "QPushButton:hover { background:#6D28D9; } "
            "QPushButton:disabled { background:#C4B5FD; color:white; }"
        )
        self.build_btn.setEnabled(False)
        self.build_btn.clicked.connect(self._on_build)
        btn_row.addWidget(self.build_btn)

        btn_row.addStretch()

        refresh_btn = QPushButton("↻ Refresh layers")
        refresh_btn.clicked.connect(self._refresh_layer_combos)
        btn_row.addWidget(refresh_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        root.addLayout(btn_row)

        # Status line
        self.status_label = QLabel("Ready. Fill in the form, then click Validate.")
        self.status_label.setStyleSheet("color:#374151;")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        # Caveat footer (from paper, verbatim-ish)
        caveats = QLabel(PAPER_CAVEATS)
        caveats.setWordWrap(True)
        caveats.setStyleSheet(
            "color:#6B7280; font-size:11px; padding:6px; "
            "background:#F9FAFB; border-left:3px solid #D1D5DB;"
        )
        root.addWidget(caveats)

    # ---- Mode selector ---------------------------------------------------

    def _build_mode_box(self) -> QGroupBox:
        box = QGroupBox("Mode")
        h = QHBoxLayout(box)
        self.mode_single = QRadioButton("Single bed (manual angles)")
        self.mode_batch  = QRadioButton("Batch — one plane per row of a rock layer")
        self.mode_single.setChecked(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.mode_single)
        self._mode_group.addButton(self.mode_batch)
        self.mode_single.toggled.connect(self._on_mode_changed)
        h.addWidget(self.mode_single)
        h.addWidget(self.mode_batch)
        h.addStretch()
        return box

    # ---- Origin box ------------------------------------------------------

    def _build_origin_box(self) -> QGroupBox:
        box = QGroupBox("Origin (used in Single-bed mode — or as default in Batch mode)")
        f = QFormLayout(box)

        # Source (layer feature / canvas click)
        src_row = QHBoxLayout()
        self.origin_src_combo = QComboBox()
        self.origin_src_combo.addItem("From point layer feature", "layer")
        self.origin_src_combo.addItem("Pick a point on the canvas",  "canvas")
        self.origin_src_combo.addItem("Manual X / Y entry",            "manual")
        self.origin_src_combo.currentIndexChanged.connect(self._on_origin_source_changed)
        src_row.addWidget(self.origin_src_combo)
        f.addRow("Source:", _wrap_hbox(src_row))

        # Point layer + feature
        self.origin_layer_combo = QComboBox()
        self.origin_layer_combo.currentIndexChanged.connect(self._on_origin_changed)
        f.addRow("Point layer:", self.origin_layer_combo)

        self.origin_feature_info = QLabel("(layer not set)")
        self.origin_feature_info.setStyleSheet("color:#6B7280;")
        f.addRow("Feature:", self.origin_feature_info)

        # Canvas pick button
        self.pick_canvas_btn = QPushButton("Click a point on the QGIS canvas…")
        self.pick_canvas_btn.clicked.connect(self._start_canvas_pick)
        self.pick_canvas_btn.setVisible(False)
        f.addRow("", self.pick_canvas_btn)

        # Manual X / Y
        xy_row = QHBoxLayout()
        self.origin_x_edit = QLineEdit()
        self.origin_x_edit.setPlaceholderText("X (easting)")
        self.origin_x_edit.setValidator(None)  # accept scientific notation etc.
        self.origin_y_edit = QLineEdit()
        self.origin_y_edit.setPlaceholderText("Y (northing)")
        xy_row.addWidget(self.origin_x_edit)
        xy_row.addWidget(QLabel("  "))
        xy_row.addWidget(self.origin_y_edit)
        xy_row.addStretch()
        f.addRow("X / Y:", _wrap_hbox(xy_row))

        # Origin CRS
        self.origin_crs_label = QLabel("(awaiting input)")
        self.origin_crs_label.setStyleSheet("color:#6B7280;")
        f.addRow("Origin CRS:", self.origin_crs_label)

        return box

    # ---- Single-bed angles ------------------------------------------------

    def _build_angles_box(self) -> QGroupBox:
        box = QGroupBox("Bed orientation (Single-bed mode)")
        f = QFormLayout(box)

        self.strike_spin = QDoubleSpinBox()
        self.strike_spin.setRange(0.0, 360.0)
        self.strike_spin.setDecimals(1)
        self.strike_spin.setSuffix("°")
        self.strike_spin.setValue(70.0)
        self.strike_spin.valueChanged.connect(self._invalidate)
        f.addRow("Strike direction:", self.strike_spin)

        self.dip_dir_spin = QDoubleSpinBox()
        self.dip_dir_spin.setRange(0.0, 360.0)
        self.dip_dir_spin.setDecimals(1)
        self.dip_dir_spin.setSuffix("°")
        self.dip_dir_spin.setValue(160.0)
        self.dip_dir_spin.valueChanged.connect(self._invalidate)
        f.addRow("Dip direction:", self.dip_dir_spin)

        self.dip_amt_spin = QDoubleSpinBox()
        self.dip_amt_spin.setRange(0.0, 90.0)
        self.dip_amt_spin.setDecimals(1)
        self.dip_amt_spin.setSuffix("°")
        self.dip_amt_spin.setValue(40.0)
        self.dip_amt_spin.valueChanged.connect(self._invalidate)
        f.addRow("Dip amount:", self.dip_amt_spin)

        note = QLabel(
            "Angles are compass bearings clockwise from north. Dip amount is "
            "the angle from horizontal. If the plane tilts the wrong way in "
            "3D view, add 180° to dip direction — the original paper treats "
            "dip direction as the up-dip azimuth."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#6B7280; font-size:11px;")
        f.addRow("", note)

        return box

    # ---- Batch rock-layer field mapping -----------------------------------

    def _build_batch_box(self) -> QGroupBox:
        box = QGroupBox("Rock observation layer (Batch mode)")
        f = QFormLayout(box)

        self.rock_layer_combo = QComboBox()
        self.rock_layer_combo.currentIndexChanged.connect(self._on_rock_layer_changed)
        f.addRow("Layer (point, one row per observation):", self.rock_layer_combo)

        self.strike_field_combo = QComboBox()
        self.strike_field_combo.currentIndexChanged.connect(self._invalidate)
        f.addRow("Strike field:", self.strike_field_combo)

        self.dip_dir_field_combo = QComboBox()
        self.dip_dir_field_combo.currentIndexChanged.connect(self._invalidate)
        f.addRow("Dip-direction field:", self.dip_dir_field_combo)

        self.dip_amt_field_combo = QComboBox()
        self.dip_amt_field_combo.currentIndexChanged.connect(self._invalidate)
        f.addRow("Dip-amount field:", self.dip_amt_field_combo)

        self.label_field_combo = QComboBox()
        f.addRow("Label field (optional):", self.label_field_combo)

        self.use_row_geom_cb = QCheckBox(
            "Use each row's point geometry as that row's origin "
            "(otherwise use the Origin box above for every row)")
        self.use_row_geom_cb.setChecked(True)
        f.addRow("", self.use_row_geom_cb)

        limit_row = QHBoxLayout()
        self.batch_limit_spin = QSpinBox()
        self.batch_limit_spin.setRange(1, 10_000)
        self.batch_limit_spin.setValue(50)
        self.batch_limit_spin.setSuffix(" rows")
        limit_row.addWidget(self.batch_limit_spin)
        limit_row.addWidget(QLabel("(safety cap)"))
        limit_row.addStretch()
        f.addRow("Max rows to process:", _wrap_hbox(limit_row))

        return box

    # ---- Area box (boundary polygon + grid spacing) ----------------------

    def _build_area_box(self) -> QGroupBox:
        box = QGroupBox("Area & grid")
        f = QFormLayout(box)

        self.boundary_layer_combo = QComboBox()
        self.boundary_layer_combo.currentIndexChanged.connect(self._on_boundary_changed)
        f.addRow("Boundary polygon layer:", self.boundary_layer_combo)

        self.boundary_info = QLabel("(layer not set)")
        self.boundary_info.setStyleSheet("color:#6B7280;")
        f.addRow("Boundary feature:", self.boundary_info)

        self.spacing_spin = QDoubleSpinBox()
        self.spacing_spin.setRange(0.1, 10_000.0)
        self.spacing_spin.setDecimals(2)
        self.spacing_spin.setValue(3.0)
        self.spacing_spin.setSuffix(" units")
        self.spacing_spin.setToolTip(
            "Grid spacing in the boundary polygon's CRS units. For a projected "
            "CRS (UTM, EPSG:3857) this is metres. The Peepalkhola paper used 3 m."
        )
        self.spacing_spin.valueChanged.connect(self._invalidate)
        f.addRow("Grid spacing:", self.spacing_spin)

        return box

    # ---- DEM box ---------------------------------------------------------

    def _build_dem_box(self) -> QGroupBox:
        box = QGroupBox("Elevation (DEM)")
        f = QFormLayout(box)
        self.dem_layer_combo = QComboBox()
        self.dem_layer_combo.currentIndexChanged.connect(self._invalidate)
        f.addRow("DEM raster:", self.dem_layer_combo)
        note = QLabel("Used to read the ground elevation at the origin "
                      "(added as addend to z_rel to produce z_abs).")
        note.setWordWrap(True)
        note.setStyleSheet("color:#6B7280; font-size:11px;")
        f.addRow("", note)
        return box

    # ---- Output box ------------------------------------------------------

    def _build_output_box(self) -> QGroupBox:
        box = QGroupBox("Output")
        f = QFormLayout(box)

        self.output_kind_combo = QComboBox()
        self.output_kind_combo.addItem("Point grid with z attribute (paper's method)", "grid")
        self.output_kind_combo.addItem("3D polygon plane (nicer in native 3D view)",    "polygon")
        self.output_kind_combo.addItem("Both — point grid AND 3D polygon",               "both")
        f.addRow("Output geometry:", self.output_kind_combo)

        self.prefix_edit = QLineEdit()
        self.prefix_edit.setText("Rock bed")
        self.prefix_edit.setPlaceholderText("Layer name prefix")
        f.addRow("Output layer prefix:", self.prefix_edit)

        return box

    # -----------------------------------------------------------------
    # Combo population / layer refresh
    # -----------------------------------------------------------------

    def _refresh_layer_combos(self) -> None:
        """(Re)populate every layer combo from the current QGIS project."""
        point_layers, poly_layers, raster_layers = [], [], []
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.type() == QgsMapLayer.VectorLayer:
                g = detect_geometry_type(lyr)
                if g == "point":
                    point_layers.append(lyr)
                elif g == "polygon":
                    poly_layers.append(lyr)
            elif lyr.type() == QgsMapLayer.RasterLayer:
                raster_layers.append(lyr)

        _fill_layer_combo(self.origin_layer_combo,  point_layers,
                          empty_text="No point layers in project")
        _fill_layer_combo(self.rock_layer_combo,    point_layers,
                          empty_text="No point layers in project")
        _fill_layer_combo(self.boundary_layer_combo, poly_layers,
                          empty_text="No polygon layers in project")
        _fill_layer_combo(self.dem_layer_combo,      raster_layers,
                          empty_text="No raster layers in project")

        # Cascade feature/field updates
        self._on_origin_changed()
        self._on_boundary_changed()
        self._on_rock_layer_changed()

    # -----------------------------------------------------------------
    # Change handlers (wire cascading UI updates + invalidate)
    # -----------------------------------------------------------------

    def _on_mode_changed(self, _checked: bool = False) -> None:
        self._sync_mode_visibility()
        self._invalidate()

    def _sync_mode_visibility(self) -> None:
        is_single = self.mode_single.isChecked()
        self.angles_box.setVisible(is_single)
        self.batch_box.setVisible(not is_single)

    def _on_origin_source_changed(self, _idx: int = -1) -> None:
        src = self.origin_src_combo.currentData()
        self.origin_layer_combo.setEnabled(src == "layer")
        self.origin_feature_info.setEnabled(src == "layer")
        self.pick_canvas_btn.setVisible(src == "canvas")
        self.origin_x_edit.setReadOnly(src == "layer")
        self.origin_y_edit.setReadOnly(src == "layer")
        self._invalidate()

    def _on_origin_changed(self, _idx: int = -1) -> None:
        layer = _lookup_layer(self.origin_layer_combo)
        if layer is None:
            self.origin_feature_info.setText("(layer not set)")
            self.origin_crs_label.setText("(awaiting input)")
            self._invalidate()
            return
        feat = _first_feature(layer)
        if feat is None:
            self.origin_feature_info.setText(
                f"<span style='color:#DC2626'>Layer '{layer.name()}' has no "
                "features. Select a point layer with data.</span>")
        else:
            try:
                pt = feat.geometry().asPoint()
                self.origin_feature_info.setText(
                    f"{_feature_label(feat, layer)}  —  "
                    f"X={pt.x():.4f}, Y={pt.y():.4f}"
                )
                if self.origin_src_combo.currentData() == "layer":
                    self.origin_x_edit.setText(f"{pt.x()}")
                    self.origin_y_edit.setText(f"{pt.y()}")
            except Exception as e:
                self.origin_feature_info.setText(
                    f"<span style='color:#DC2626'>Could not read geometry: {e}</span>")
        # Origin CRS follows the point layer
        try:
            self.origin_crs_label.setText(layer.crs().authid() or "(unknown)")
        except Exception:
            self.origin_crs_label.setText("(unknown)")
        self._invalidate()

    def _on_boundary_changed(self, _idx: int = -1) -> None:
        layer = _lookup_layer(self.boundary_layer_combo)
        if layer is None:
            self.boundary_info.setText("(layer not set)")
            self._invalidate()
            return
        feat = _first_feature(layer)
        if feat is None:
            self.boundary_info.setText(
                f"<span style='color:#DC2626'>Polygon layer '{layer.name()}' "
                "has no features.</span>")
        else:
            extent = feat.geometry().boundingBox()
            area = feat.geometry().area()
            w = extent.xMaximum() - extent.xMinimum()
            h = extent.yMaximum() - extent.yMinimum()
            self.boundary_info.setText(
                f"{_feature_label(feat, layer)}  —  "
                f"bbox {w:.1f}×{h:.1f} units, area {area:.1f}  "
                f"[CRS {layer.crs().authid()}]"
            )
        self._invalidate()

    def _on_rock_layer_changed(self, _idx: int = -1) -> None:
        layer = _lookup_layer(self.rock_layer_combo)
        combos = [self.strike_field_combo, self.dip_dir_field_combo,
                  self.dip_amt_field_combo, self.label_field_combo]
        for c in combos:
            c.blockSignals(True)
            c.clear()
        if layer is None:
            for c in combos:
                c.addItem("(no rock layer selected)")
                c.blockSignals(False)
            return

        fields = [f.name() for f in layer.fields()]
        for c in combos:
            for name in fields:
                c.addItem(name)

        # Heuristic auto-pick of common field names
        _try_select(self.strike_field_combo,  ["strike", "str", "sd", "strike_deg"])
        _try_select(self.dip_dir_field_combo, ["dip_dir", "dip_direction", "dd", "dipdir"])
        _try_select(self.dip_amt_field_combo, ["dip", "dip_amount", "da", "dip_amt"])
        _try_select(self.label_field_combo,   ["name", "label", "id", "rock_id"])

        for c in combos:
            c.blockSignals(False)
        self._invalidate()

    def _invalidate(self, *_args) -> None:
        """
        Called whenever any input changes. The user must re-validate before
        building, so we disable the Build button.
        """
        if self._validated_ok:
            self._validated_ok = False
            self.build_btn.setEnabled(False)
            self.status_label.setText(
                "Inputs changed — run Validate again before building.")

    # -----------------------------------------------------------------
    # Canvas point-pick
    # -----------------------------------------------------------------

    def _start_canvas_pick(self) -> None:
        """
        Temporarily take over the canvas map tool to let the user click
        an origin point. Restores the previous tool when done.
        """
        canvas = self.iface.mapCanvas()
        self._previous_tool = canvas.mapTool()

        def on_pick(point: QgsPointXY) -> None:
            self.origin_x_edit.setText(f"{point.x()}")
            self.origin_y_edit.setText(f"{point.y()}")
            canvas_crs = canvas.mapSettings().destinationCrs()
            self.origin_crs_label.setText(
                canvas_crs.authid() or "(canvas CRS unknown)")
            self.origin_feature_info.setText(
                f"Canvas pick — X={point.x():.4f}, Y={point.y():.4f} "
                f"[{canvas_crs.authid()}]")
            self._restore_map_tool()
            self._invalidate()
            self.raise_()
            self.activateWindow()

        self._pick_tool = PickPointTool(canvas, on_pick)
        canvas.setMapTool(self._pick_tool)

        # Put the dialog out of the way temporarily so the user can see the map
        self.status_label.setText(
            "Click a point on the QGIS canvas to set the origin. "
            "(Press Escape to cancel.)")
        self.showMinimized()

    def _restore_map_tool(self) -> None:
        canvas = self.iface.mapCanvas()
        try:
            if self._previous_tool is not None:
                canvas.setMapTool(self._previous_tool)
            else:
                canvas.unsetMapTool(self._pick_tool)
        except Exception:
            pass
        self._pick_tool = None
        self.showNormal()

    # -----------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------

    def _collect_origin(self, boundary_crs) -> Optional[Tuple[float, float]]:
        """Read the origin XY and transform it into the boundary's CRS."""
        src = self.origin_src_combo.currentData()
        try:
            x = float(self.origin_x_edit.text())
            y = float(self.origin_y_edit.text())
        except ValueError:
            return None

        origin_crs = self._current_origin_crs()
        if origin_crs is None:
            return None

        return _transform_point(x, y, origin_crs, boundary_crs)

    def _current_origin_crs(self) -> Optional[QgsCoordinateReferenceSystem]:
        src = self.origin_src_combo.currentData()
        if src == "layer":
            layer = _lookup_layer(self.origin_layer_combo)
            return layer.crs() if layer is not None else None
        if src == "canvas":
            return self.iface.mapCanvas().mapSettings().destinationCrs()
        if src == "manual":
            # Use the boundary's CRS as the manual entry frame — the user
            # can confirm in the status line.
            layer = _lookup_layer(self.boundary_layer_combo)
            return layer.crs() if layer is not None else None
        return None

    def _on_validate(self) -> None:
        errors: List[str] = []
        warnings: List[str] = []

        # --- Boundary polygon ---
        boundary_layer = _lookup_layer(self.boundary_layer_combo)
        if boundary_layer is None:
            errors.append("• No boundary polygon layer selected. Add a polygon "
                          "layer to the project and pick it.")
        else:
            boundary_feat = _first_feature(boundary_layer)
            if boundary_feat is None:
                errors.append(f"• Boundary layer '{boundary_layer.name()}' has "
                              "no features. Digitise a polygon or load one.")
            else:
                boundary_geom = boundary_feat.geometry()
                crs_msg = validators.validate_crs_projected(boundary_layer.crs())
                if crs_msg:
                    errors.append("• " + crs_msg)
                spc_msg = validators.validate_spacing(self.spacing_spin.value(),
                                                     boundary_geom)
                if spc_msg:
                    errors.append("• " + spc_msg)

        # --- DEM ---
        dem_layer = _lookup_layer(self.dem_layer_combo)
        if dem_layer is None:
            errors.append("• No DEM raster selected. Load an ASTER / SRTM / "
                          "ALOS DEM into the project and pick it.")

        # --- Origin XY ---
        origin_xy = None
        if boundary_layer is not None and _first_feature(boundary_layer) is not None:
            origin_xy = self._collect_origin(boundary_layer.crs())
        if origin_xy is None:
            errors.append("• Origin X/Y could not be read. Pick a point layer "
                          "feature, click the canvas, or type numeric X and Y.")
        else:
            if boundary_layer is not None and _first_feature(boundary_layer) is not None:
                boundary_geom = _first_feature(boundary_layer).geometry()
                msg = validators.validate_origin_inside(origin_xy, boundary_geom)
                if msg:
                    warnings.append("• " + msg)
            if dem_layer is not None and boundary_layer is not None:
                ox, oy = origin_xy
                msg = validators.validate_dem_covers(
                    dem_layer, ox, oy, source_crs=boundary_layer.crs())
                if msg:
                    errors.append("• " + msg)

        # --- Mode-specific validation ---
        if self.mode_single.isChecked():
            msg = validators.validate_angles(
                self.strike_spin.value(),
                self.dip_dir_spin.value(),
                self.dip_amt_spin.value(),
            )
            if msg:
                errors.append("• " + msg)
        else:
            batch_errs = self._validate_batch_mode()
            errors.extend(batch_errs)

        # --- Surface ---
        if errors:
            self._validated_ok = False
            self.build_btn.setEnabled(False)
            self._show_messages("Validation failed", errors, warnings,
                                is_error=True)
            self.status_label.setText(f"Validation failed ({len(errors)} error(s)).")
            return

        self._validated_ok = True
        self.build_btn.setEnabled(True)
        if warnings:
            self._show_messages("Validation passed (with warnings)",
                                [], warnings, is_error=False)
            self.status_label.setText(
                f"Validation passed with {len(warnings)} warning(s). "
                "Click Build planes.")
        else:
            self.status_label.setText("✓ Validation passed. Click Build planes.")

    def _validate_batch_mode(self) -> List[str]:
        errs: List[str] = []
        rock_layer = _lookup_layer(self.rock_layer_combo)
        if rock_layer is None:
            errs.append("• No rock observation layer selected. Pick a point "
                        "layer whose attributes include strike, dip direction "
                        "and dip amount.")
            return errs

        fields = [f.name() for f in rock_layer.fields()]
        for combo, label in [
            (self.strike_field_combo,  "strike"),
            (self.dip_dir_field_combo, "dip direction"),
            (self.dip_amt_field_combo, "dip amount"),
        ]:
            v = combo.currentText()
            if not v or v not in fields:
                errs.append(f"• No valid {label} field selected. Pick the "
                            f"attribute column holding the {label} values.")

        if errs:
            return errs

        # Quick per-row sample: if most rows look bad, flag it upfront.
        bad = 0
        total = 0
        limit = self.batch_limit_spin.value()
        for feat in rock_layer.getFeatures():
            total += 1
            if total > limit:
                break
            msg = validators.validate_attribute_row(
                feat[self.strike_field_combo.currentText()],
                feat[self.dip_dir_field_combo.currentText()],
                feat[self.dip_amt_field_combo.currentText()],
                feat.id(),
            )
            if msg is not None:
                bad += 1
        if total > 0 and bad == total:
            errs.append(f"• Every row in '{rock_layer.name()}' fails validation "
                        "— the selected fields don't contain valid angles. "
                        "Check the field mapping.")
        return errs

    # -----------------------------------------------------------------
    # Build
    # -----------------------------------------------------------------

    def _on_build(self) -> None:
        if not self._validated_ok:
            QMessageBox.warning(self, "Not validated",
                                "Click 'Validate inputs' first.")
            return
        try:
            if self.mode_single.isChecked():
                self._build_single()
            else:
                self._build_batch()
        except Exception as e:
            log.error(f"Build failed: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(
                self, "Build failed",
                f"Something went wrong while building the planes:\n\n{e}\n\n"
                "See Python console → Log Messages → 'RockBed' for full detail."
            )

    # ---- Single-bed build ------------------------------------------------

    def _build_single(self) -> None:
        self._set_busy(True, "Building rock-bed plane…")
        try:
            boundary_layer = _lookup_layer(self.boundary_layer_combo)
            dem_layer      = _lookup_layer(self.dem_layer_combo)
            boundary_feat  = _first_feature(boundary_layer)
            boundary_geom  = boundary_feat.geometry()
            boundary_crs   = boundary_layer.crs()

            origin_xy = self._collect_origin(boundary_crs)
            origin_elev = dem_sampler.sample_dem_elevation(
                dem_layer, origin_xy[0], origin_xy[1], source_crs=boundary_crs)
            if origin_elev is None:
                raise RuntimeError(
                    "Could not read origin elevation from the DEM. "
                    "Check that the DEM covers the origin point.")

            strike  = self.strike_spin.value()
            dip_dir = self.dip_dir_spin.value()
            dip_amt = self.dip_amt_spin.value()
            prefix  = self.prefix_edit.text().strip() or "Rock bed"

            self._emit_plane(
                label=prefix,
                origin_xy=origin_xy,
                origin_elev=origin_elev,
                strike=strike, dip_dir=dip_dir, dip_amt=dip_amt,
                boundary_geom=boundary_geom,
                boundary_crs=boundary_crs,
            )

            self.status_label.setText(
                f"✓ Built rock-bed plane at origin "
                f"({origin_xy[0]:.2f}, {origin_xy[1]:.2f}) @ {origin_elev:.1f} m"
            )
        finally:
            self._set_busy(False)

    # ---- Batch build -----------------------------------------------------

    def _build_batch(self) -> None:
        self._set_busy(True, "Building rock-bed planes (batch)…")
        try:
            rock_layer     = _lookup_layer(self.rock_layer_combo)
            boundary_layer = _lookup_layer(self.boundary_layer_combo)
            dem_layer      = _lookup_layer(self.dem_layer_combo)
            boundary_feat  = _first_feature(boundary_layer)
            boundary_geom  = boundary_feat.geometry()
            boundary_crs   = boundary_layer.crs()

            strike_field  = self.strike_field_combo.currentText()
            dip_dir_field = self.dip_dir_field_combo.currentText()
            dip_amt_field = self.dip_amt_field_combo.currentText()
            label_field   = self.label_field_combo.currentText()
            use_row_geom  = self.use_row_geom_cb.isChecked()
            max_rows      = self.batch_limit_spin.value()

            prefix = self.prefix_edit.text().strip() or "Rock bed"
            rock_crs = rock_layer.crs()

            # Fallback origin (from the top Origin box) if use_row_geom=False
            fallback_origin = None
            if not use_row_geom:
                fallback_origin = self._collect_origin(boundary_crs)

            built = 0
            skipped = 0
            skip_msgs: List[str] = []

            for feat in rock_layer.getFeatures():
                if built + skipped >= max_rows:
                    skip_msgs.append(
                        f"Stopped at {max_rows} rows (safety cap). Increase "
                        "'Max rows to process' to handle more.")
                    break
                fid = feat.id()

                try:
                    strike  = float(feat[strike_field])
                    dip_dir = float(feat[dip_dir_field])
                    dip_amt = float(feat[dip_amt_field])
                except (TypeError, ValueError, KeyError):
                    skipped += 1
                    skip_msgs.append(f"Row {fid}: non-numeric angle values — skipped.")
                    continue

                msg = validators.validate_angles(strike, dip_dir, dip_amt)
                if msg:
                    skipped += 1
                    skip_msgs.append(f"Row {fid}: {msg}")
                    continue

                # Origin
                if use_row_geom:
                    try:
                        pt = feat.geometry().asPoint()
                        row_origin = _transform_point(pt.x(), pt.y(),
                                                      rock_crs, boundary_crs)
                    except Exception:
                        skipped += 1
                        skip_msgs.append(f"Row {fid}: no point geometry — skipped.")
                        continue
                else:
                    if fallback_origin is None:
                        skipped += 1
                        skip_msgs.append(
                            f"Row {fid}: no origin available and the Origin "
                            "box didn't resolve — skipped.")
                        continue
                    row_origin = fallback_origin

                # DEM at origin
                elev = dem_sampler.sample_dem_elevation(
                    dem_layer, row_origin[0], row_origin[1],
                    source_crs=boundary_crs)
                if elev is None:
                    skipped += 1
                    skip_msgs.append(
                        f"Row {fid}: origin outside DEM — skipped.")
                    continue

                # Label
                label = str(feat[label_field]) if label_field and label_field in [
                    f.name() for f in rock_layer.fields()] else f"row_{fid}"
                layer_prefix = f"{prefix} [{label}]"

                try:
                    self._emit_plane(
                        label=layer_prefix,
                        origin_xy=row_origin,
                        origin_elev=elev,
                        strike=strike, dip_dir=dip_dir, dip_amt=dip_amt,
                        boundary_geom=boundary_geom,
                        boundary_crs=boundary_crs,
                        extra_props={
                            "origin_row": fid,
                            "strike_deg":  strike,
                            "dip_dir_deg": dip_dir,
                            "dip_amount_deg": dip_amt,
                        },
                    )
                    built += 1
                except Exception as e:
                    skipped += 1
                    skip_msgs.append(f"Row {fid}: {e}")
                    log.warn(f"Row {fid} build failed: {e}")

            if skip_msgs:
                self._show_messages(
                    "Batch build complete",
                    errors=[],
                    warnings=[f"Built {built} plane(s), skipped {skipped}."]
                             + skip_msgs[:20],
                    is_error=False,
                )
            self.status_label.setText(
                f"✓ Built {built} plane(s); skipped {skipped}.")
        finally:
            self._set_busy(False)

    # ---- Shared plane-emit (writes layer(s) and adds to project) --------

    def _emit_plane(self, label: str,
                    origin_xy: Tuple[float, float],
                    origin_elev: float,
                    strike: float, dip_dir: float, dip_amt: float,
                    boundary_geom,
                    boundary_crs,
                    extra_props: Optional[Dict] = None) -> None:
        """
        Build the requested output geometries for one (origin, angles) and
        add the layers to the QGIS project.
        """
        out_kind = self.output_kind_combo.currentData()
        spacing  = self.spacing_spin.value()
        extra_props = extra_props or {}
        meta = {
            "strike_deg":      strike,
            "dip_dir_deg":     dip_dir,
            "dip_amount_deg":  dip_amt,
            "origin_elev":     round(origin_elev, 3),
            "origin_x":        round(origin_xy[0], 3),
            "origin_y":        round(origin_xy[1], 3),
            **extra_props,
        }

        if out_kind in ("grid", "both"):
            features = grid_builder.build_grid(
                origin_xy=origin_xy,
                origin_elevation=origin_elev,
                strike_deg=strike, dip_dir_deg=dip_dir, dip_amount_deg=dip_amt,
                polygon_geom=boundary_geom,
                spacing=spacing,
            )
            if features:
                path = layer_writer.write_points_geojson(
                    features=features,
                    crs_authid=boundary_crs.authid() or "EPSG:4326",
                    prefix="rockbed_grid_",
                    extra_props=meta,
                )
                layer = QgsVectorLayer(path, f"{label} — grid", "ogr")
                if layer.isValid():
                    _graduate_style_by_z(layer, features)
                    QgsProject.instance().addMapLayer(layer)
                    log.success(f"Added grid layer: {label} "
                                f"({len(features):,} pts)")
                else:
                    log.warn(f"Grid layer invalid: {path}")

        if out_kind in ("polygon", "both"):
            corners = grid_builder.polygon_plane_corners(
                origin_xy=origin_xy,
                origin_elevation=origin_elev,
                strike_deg=strike, dip_dir_deg=dip_dir, dip_amount_deg=dip_amt,
                polygon_geom=boundary_geom,
            )
            path = layer_writer.write_plane_polygon_geojson(
                corners=corners,
                crs_authid=boundary_crs.authid() or "EPSG:4326",
                properties=meta,
                prefix="rockbed_plane_",
            )
            layer = QgsVectorLayer(path, f"{label} — 3D plane", "ogr")
            if layer.isValid():
                _style_plane_polygon(layer)
                QgsProject.instance().addMapLayer(layer)
                log.success(f"Added 3D plane: {label}")
            else:
                log.warn(f"Plane polygon layer invalid: {path}")

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        self.progress.setVisible(busy)
        self.validate_btn.setEnabled(not busy)
        self.build_btn.setEnabled(not busy and self._validated_ok)
        if msg:
            self.status_label.setText(msg)

    def _show_messages(self, title: str, errors: List[str],
                       warnings: List[str], is_error: bool) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(C.MSG_CRIT if is_error else C.MSG_WARN)
        parts = []
        if errors:
            parts.append("<b>Errors (fix these):</b><br>" + "<br>".join(errors))
        if warnings:
            parts.append("<b>Warnings:</b><br>" + "<br>".join(warnings))
        box.setTextFormat(Qt.RichText if hasattr(Qt, "RichText")
                           else Qt.TextFormat.RichText)
        box.setText("<br><br>".join(parts) or "All good.")
        box.exec()

    def closeEvent(self, event):  # noqa: N802 — Qt override
        if self._pick_tool is not None:
            self._restore_map_tool()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _wrap_hbox(layout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    return w


def _fill_layer_combo(combo: QComboBox, layers: list,
                      empty_text: str = "(none)") -> None:
    """Refresh a layer combo while trying to preserve the current selection."""
    prev_id = combo.currentData() if combo.count() else None
    combo.blockSignals(True)
    combo.clear()
    if not layers:
        combo.addItem(empty_text, None)
        combo.setEnabled(False)
    else:
        combo.setEnabled(True)
        for lyr in layers:
            combo.addItem(lyr.name(), lyr.id())
        if prev_id:
            for i in range(combo.count()):
                if combo.itemData(i) == prev_id:
                    combo.setCurrentIndex(i)
                    break
    combo.blockSignals(False)


def _lookup_layer(combo: QComboBox):
    lid = combo.currentData()
    if not lid:
        return None
    return QgsProject.instance().mapLayer(lid)


def _try_select(combo: QComboBox, candidates: List[str]) -> None:
    """Try to pre-select a combo entry matching any of the candidate names (CI)."""
    existing = [combo.itemText(i).lower() for i in range(combo.count())]
    for cand in candidates:
        cand_l = cand.lower()
        # Exact match first
        if cand_l in existing:
            combo.setCurrentIndex(existing.index(cand_l))
            return
        # Substring match
        for i, text in enumerate(existing):
            if cand_l in text:
                combo.setCurrentIndex(i)
                return


def _feature_label(feat, layer) -> str:
    """Try to produce a short display label for a QgsFeature."""
    for candidate in ("name", "id", "label"):
        for f in layer.fields():
            if f.name().lower() == candidate:
                try:
                    return f"{feat[f.name()]}"
                except Exception:
                    pass
    return f"feature #{feat.id()}"


def _graduate_style_by_z(layer, features: List[Dict]) -> None:
    """Apply a quick graduated renderer coloured by z_abs."""
    try:
        from qgis.core import (
            QgsGraduatedSymbolRenderer, QgsMarkerSymbol,
            QgsRendererRange,
        )
        from qgis.PyQt.QtGui import QColor
    except ImportError:
        return

    z_values = [f["z_abs"] for f in features]
    if not z_values:
        return
    z_min, z_max = min(z_values), max(z_values)
    z_mid = (z_min + z_max) / 2

    def symbol(colour_hex: str):
        return QgsMarkerSymbol.createSimple({
            "name": "circle",
            "color": colour_hex,
            "outline_style": "no",
            "size": "2.4",
        })

    ranges = [
        QgsRendererRange(z_min, z_mid, symbol("#1E40AF"),
                         f"{z_min:.1f} – {z_mid:.1f} m"),
        QgsRendererRange(z_mid, z_max, symbol("#EA580C"),
                         f"{z_mid:.1f} – {z_max:.1f} m"),
    ]
    renderer = QgsGraduatedSymbolRenderer("z_abs", ranges)
    layer.setRenderer(renderer)
    try:
        layer.triggerRepaint()
    except Exception:
        pass


def _style_plane_polygon(layer) -> None:
    """Translucent purple fill + solid stroke for the 3D plane polygon."""
    try:
        from qgis.core import QgsFillSymbol, QgsSingleSymbolRenderer
        sym = QgsFillSymbol.createSimple({
            "color": "124,58,237,80",
            "outline_color": "124,58,237,255",
            "outline_width": "0.6",
        })
        layer.setRenderer(QgsSingleSymbolRenderer(sym))
        layer.setOpacity(1.0)
        try:
            layer.triggerRepaint()
        except Exception:
            pass
    except Exception:
        pass
