# -*- coding: utf-8 -*-
"""
CHIRAG-Springs — Hydrogeological Analysis Suite
Main dialog module providing the 3-tab UI for structural survey analysis,
intervention mapping, and predictive analytics with GSI verification.
"""

import os
import traceback

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QGroupBox, QLabel, QLineEdit, QPushButton, QFileDialog,
    QTableWidget, QTableWidgetItem, QFormLayout, QDoubleSpinBox,
    QTextEdit, QMessageBox, QHeaderView, QAbstractItemView,
    QSizePolicy
)
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor, QFont, QIcon
from qgis.core import QgsProject

from .analysis_engine import (
    parse_survey_file, parse_intervention_file, parse_kml_file,
    detect_columns, create_structural_layer, apply_strike_dip_styling,
    classify_intervention, create_intervention_layer, apply_intervention_styling,
    verify_against_gsi, compute_recharge_zones, create_recharge_layer,
    load_kml_as_layer, export_structural_kml
)


class ChiragSpringsDialog(QDialog):
    """Main dialog for the CHIRAG-Springs QGIS plugin."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface

        # ── Shared state ────────────────────────────────────────────
        self._survey_data = None          # list[dict]  – parsed survey rows
        self._survey_columns = None       # list[str]   – raw column names
        self._column_map = None           # dict        – detected column mapping
        self._intervention_data = None    # list[dict]  – parsed intervention rows
        self._intervention_columns = None # list[str]   – raw column names

        # ── Window properties ───────────────────────────────────────
        self.setWindowTitle("CHIRAG-Springs — Hydrogeological Analysis Suite")
        self.setMinimumSize(750, 650)

        # ── Build UI ────────────────────────────────────────────────
        self._setup_ui()

    # ================================================================
    #  UI CONSTRUCTION
    # ================================================================

    def _setup_ui(self):
        """Assemble the top-level layout and tab widget."""
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        self._tabs = QTabWidget()
        root_layout.addWidget(self._tabs)

        # Build each tab
        self._tabs.addTab(self._build_tab_structural(), "📐 Structural Survey")
        self._tabs.addTab(self._build_tab_interventions(), "🔧 Interventions")
        self._tabs.addTab(self._build_tab_analytics(), "🔬 Analytics & Verification")

    # ----------------------------------------------------------------
    #  Helper: styled group box
    # ----------------------------------------------------------------

    @staticmethod
    def _make_group(title):
        """Return a QGroupBox with a bold title."""
        grp = QGroupBox(title)
        font = grp.font()
        font.setBold(True)
        grp.setFont(font)
        return grp

    @staticmethod
    def _body_font():
        """Return a normal-weight font for child widgets inside bold groups."""
        f = QFont()
        f.setBold(False)
        return f

    # ----------------------------------------------------------------
    #  Helper: prominent action button
    # ----------------------------------------------------------------

    @staticmethod
    def _action_button(text):
        btn = QPushButton(text)
        btn.setMinimumHeight(35)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return btn

    # ================================================================
    #  TAB 1 — Structural Hydrogeology & Field Data
    # ================================================================

    def _build_tab_structural(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        body = self._body_font()

        # ── Data Source ─────────────────────────────────────────────
        grp_src = self._make_group("Data Source")
        src_layout = QVBoxLayout(grp_src)
        src_layout.setContentsMargins(10, 10, 10, 10)
        src_layout.setSpacing(8)

        lbl = QLabel("Survey File (CSV / XLS / XLSX):")
        lbl.setFont(body)
        src_layout.addWidget(lbl)

        row = QHBoxLayout()
        row.setSpacing(6)
        self._survey_path_edit = QLineEdit()
        self._survey_path_edit.setReadOnly(True)
        self._survey_path_edit.setFont(body)
        self._survey_path_edit.setPlaceholderText("No file selected")
        row.addWidget(self._survey_path_edit)

        btn_browse_survey = QPushButton("Browse…")
        btn_browse_survey.setFont(body)
        btn_browse_survey.setMinimumHeight(28)
        btn_browse_survey.clicked.connect(self._browse_survey_file)
        row.addWidget(btn_browse_survey)
        src_layout.addLayout(row)
        layout.addWidget(grp_src)

        # ── Detected Columns ───────────────────────────────────────
        grp_cols = self._make_group("Detected Columns")
        cols_form = QFormLayout(grp_cols)
        cols_form.setContentsMargins(10, 10, 10, 10)
        cols_form.setHorizontalSpacing(20)
        cols_form.setVerticalSpacing(6)

        self._det_lat = QLabel("—")
        self._det_lon = QLabel("—")
        self._det_strike = QLabel("—")
        self._det_dip_dir = QLabel("—")
        self._det_dip_amt = QLabel("—")

        for lbl_widget in (self._det_lat, self._det_lon, self._det_strike,
                           self._det_dip_dir, self._det_dip_amt):
            lbl_widget.setFont(body)

        cols_form.addRow(self._form_label("Latitude:", body), self._det_lat)
        cols_form.addRow(self._form_label("Longitude:", body), self._det_lon)
        cols_form.addRow(self._form_label("Strike:", body), self._det_strike)
        cols_form.addRow(self._form_label("Dip Direction:", body), self._det_dip_dir)
        cols_form.addRow(self._form_label("Dip Amount:", body), self._det_dip_amt)
        layout.addWidget(grp_cols)

        # ── Data Preview ───────────────────────────────────────────
        grp_preview = self._make_group("Data Preview")
        preview_layout = QVBoxLayout(grp_preview)
        preview_layout.setContentsMargins(10, 10, 10, 10)
        preview_layout.setSpacing(6)

        self._survey_table = QTableWidget(0, 0)
        self._survey_table.setFont(body)
        self._survey_table.setAlternatingRowColors(True)
        self._survey_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._survey_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._survey_table.setMaximumHeight(200)
        self._survey_table.horizontalHeader().setStretchLastSection(True)
        preview_layout.addWidget(self._survey_table)
        layout.addWidget(grp_preview)

        # ── Action Button ──────────────────────────────────────────
        self._btn_plot_structural = self._action_button("🗺️  Load & Plot Structural Data")
        self._btn_plot_structural.clicked.connect(self._on_plot_structural)
        layout.addWidget(self._btn_plot_structural)

        # ── Google Earth Export Button ─────────────────────────────
        self._btn_export_kml = self._action_button("🌍  Export to Google Earth (KML)")
        self._btn_export_kml.clicked.connect(self._on_export_kml)
        layout.addWidget(self._btn_export_kml)

        layout.addStretch()
        return tab

    # ================================================================
    #  TAB 2 — Intervention Symbology & Mapping
    # ================================================================

    def _build_tab_interventions(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        body = self._body_font()

        # ── Intervention Data Source ────────────────────────────────
        grp_int = self._make_group("Intervention Data Source")
        int_layout = QVBoxLayout(grp_int)
        int_layout.setContentsMargins(10, 10, 10, 10)
        int_layout.setSpacing(8)

        lbl = QLabel("Excel File:")
        lbl.setFont(body)
        int_layout.addWidget(lbl)

        row = QHBoxLayout()
        row.setSpacing(6)
        self._intervention_path_edit = QLineEdit()
        self._intervention_path_edit.setReadOnly(True)
        self._intervention_path_edit.setFont(body)
        self._intervention_path_edit.setPlaceholderText("No file selected")
        row.addWidget(self._intervention_path_edit)

        btn_browse_int = QPushButton("Browse…")
        btn_browse_int.setFont(body)
        btn_browse_int.setMinimumHeight(28)
        btn_browse_int.clicked.connect(self._browse_intervention_file)
        row.addWidget(btn_browse_int)
        int_layout.addLayout(row)
        layout.addWidget(grp_int)

        # ── KML Overlay ────────────────────────────────────────────
        grp_kml = self._make_group("KML Overlay (Optional)")
        kml_layout = QVBoxLayout(grp_kml)
        kml_layout.setContentsMargins(10, 10, 10, 10)
        kml_layout.setSpacing(8)

        lbl_kml = QLabel("KML File:")
        lbl_kml.setFont(body)
        kml_layout.addWidget(lbl_kml)

        row_kml = QHBoxLayout()
        row_kml.setSpacing(6)
        self._kml_path_edit = QLineEdit()
        self._kml_path_edit.setReadOnly(True)
        self._kml_path_edit.setFont(body)
        self._kml_path_edit.setPlaceholderText("No KML selected (optional)")
        row_kml.addWidget(self._kml_path_edit)

        btn_browse_kml = QPushButton("Browse…")
        btn_browse_kml.setFont(body)
        btn_browse_kml.setMinimumHeight(28)
        btn_browse_kml.clicked.connect(self._browse_kml_file)
        row_kml.addWidget(btn_browse_kml)
        kml_layout.addLayout(row_kml)
        layout.addWidget(grp_kml)

        # ── Intervention Preview ───────────────────────────────────
        grp_prev = self._make_group("Intervention Preview")
        prev_layout = QVBoxLayout(grp_prev)
        prev_layout.setContentsMargins(10, 10, 10, 10)
        prev_layout.setSpacing(6)

        self._intervention_table = QTableWidget(0, 0)
        self._intervention_table.setFont(body)
        self._intervention_table.setAlternatingRowColors(True)
        self._intervention_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._intervention_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._intervention_table.setMaximumHeight(250)
        self._intervention_table.horizontalHeader().setStretchLastSection(True)
        prev_layout.addWidget(self._intervention_table)
        layout.addWidget(grp_prev)

        # ── Action Button ──────────────────────────────────────────
        self._btn_map_interventions = self._action_button("🗺️  Map Interventions")
        self._btn_map_interventions.clicked.connect(self._on_map_interventions)
        layout.addWidget(self._btn_map_interventions)

        layout.addStretch()
        return tab

    # ================================================================
    #  TAB 3 — Predictive Analytics & GSI Verification
    # ================================================================

    def _build_tab_analytics(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        body = self._body_font()

        # ── GSI Regional Baseline ──────────────────────────────────
        grp_gsi = self._make_group("GSI Regional Baseline")
        gsi_layout = QVBoxLayout(grp_gsi)
        gsi_layout.setContentsMargins(10, 10, 10, 10)
        gsi_layout.setSpacing(8)

        info_lbl = QLabel("Enter regional structural trend from DTR / GSI data:")
        info_lbl.setFont(body)
        info_lbl.setWordWrap(True)
        gsi_layout.addWidget(info_lbl)

        form = QFormLayout()
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(6)

        self._spin_regional_strike = self._make_spin(0.0, 360.0, 1, 0.0)
        self._spin_regional_dip_dir = self._make_spin(0.0, 360.0, 1, 0.0)
        self._spin_regional_dip_amt = self._make_spin(0.0, 90.0, 1, 0.0)
        self._spin_deviation = self._make_spin(1.0, 90.0, 1, 15.0)

        form.addRow(self._form_label("Regional Strike (°):", body), self._spin_regional_strike)
        form.addRow(self._form_label("Regional Dip Direction (°):", body), self._spin_regional_dip_dir)
        form.addRow(self._form_label("Regional Dip Amount (°):", body), self._spin_regional_dip_amt)
        form.addRow(self._form_label("Deviation Threshold (°):", body), self._spin_deviation)
        gsi_layout.addLayout(form)
        layout.addWidget(grp_gsi)

        # ── GSI Verification Button ────────────────────────────────
        self._btn_verify_gsi = self._action_button("🔍  Run GSI Verification")
        self._btn_verify_gsi.clicked.connect(self._on_verify_gsi)
        layout.addWidget(self._btn_verify_gsi)

        # ── Verification Results ───────────────────────────────────
        grp_results = self._make_group("Verification Results")
        results_layout = QVBoxLayout(grp_results)
        results_layout.setContentsMargins(10, 10, 10, 10)
        results_layout.setSpacing(6)

        self._verification_table = QTableWidget(0, 11)
        self._verification_table.setFont(body)
        self._verification_table.setAlternatingRowColors(True)
        self._verification_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._verification_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._verification_table.setHorizontalHeaderLabels([
            "Row#", "Code", "Lat", "Lon",
            "Field Strike", "Field Dip Dir",
            "GSI Strike", "GSI Dip Dir",
            "Strike Dev", "DipDir Dev", "Status"
        ])
        self._verification_table.horizontalHeader().setStretchLastSection(True)
        self._verification_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        results_layout.addWidget(self._verification_table)

        self._lbl_flag_summary = QLabel("")
        self._lbl_flag_summary.setFont(body)
        self._lbl_flag_summary.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        results_layout.addWidget(self._lbl_flag_summary)
        layout.addWidget(grp_results)

        # ── Recharge Zone Prediction ───────────────────────────────
        grp_recharge = self._make_group("Recharge Zone Prediction")
        rech_layout = QVBoxLayout(grp_recharge)
        rech_layout.setContentsMargins(10, 10, 10, 10)
        rech_layout.setSpacing(8)

        self._btn_recharge = self._action_button("🌊  Generate Predicted Recharge Zones")
        self._btn_recharge.clicked.connect(self._on_generate_recharge)
        rech_layout.addWidget(self._btn_recharge)

        self._recharge_summary = QTextEdit()
        self._recharge_summary.setFont(body)
        self._recharge_summary.setReadOnly(True)
        self._recharge_summary.setPlaceholderText(
            "Prediction summary will appear here after generation."
        )
        self._recharge_summary.setMaximumHeight(160)
        rech_layout.addWidget(self._recharge_summary)
        layout.addWidget(grp_recharge)

        layout.addStretch()
        return tab

    # ================================================================
    #  SMALL UI HELPERS
    # ================================================================

    @staticmethod
    def _form_label(text, font):
        """Return a QLabel suitable for a QFormLayout row label."""
        lbl = QLabel(text)
        lbl.setFont(font)
        return lbl

    @staticmethod
    def _make_spin(minimum, maximum, decimals, default):
        """Return a configured QDoubleSpinBox."""
        spin = QDoubleSpinBox()
        spin.setDecimals(decimals)
        spin.setRange(minimum, maximum)
        spin.setValue(default)
        spin.setSingleStep(1.0)
        spin.setMinimumWidth(100)
        return spin

    # ================================================================
    #  FILE BROWSING SLOTS
    # ================================================================

    def _browse_survey_file(self):
        """Open a file dialog for the structural-survey spreadsheet."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Survey File",
            "",
            "Survey Files (*.csv *.xls *.xlsx);;All Files (*)"
        )
        if not path:
            return

        self._survey_path_edit.setText(path)

        try:
            data, columns = parse_survey_file(path)
            self._survey_data = data
            self._survey_columns = columns

            # Auto-detect columns
            col_map = detect_columns(columns)
            self._column_map = col_map

            self._det_lat.setText(col_map.get("latitude", "—"))
            self._det_lon.setText(col_map.get("longitude", "—"))
            self._det_strike.setText(col_map.get("strike", "—"))
            self._det_dip_dir.setText(col_map.get("dip_direction", "—"))
            self._det_dip_amt.setText(col_map.get("dip_amount", "—"))

            # Populate preview table (first 10 rows)
            self._populate_table(self._survey_table, data, columns, max_rows=10)

            # Visual cue: tint the action button
            self._btn_plot_structural.setStyleSheet(
                "QPushButton { background-color: rgba(76,175,80,60); }"
            )

            self.iface.messageBar().pushSuccess(
                "CHIRAG-Springs",
                f"Loaded {len(data)} survey records from {os.path.basename(path)}"
            )

        except Exception as exc:
            self._survey_data = None
            self._survey_columns = None
            self._column_map = None
            QMessageBox.critical(
                self,
                "Survey File Error",
                f"Failed to parse survey file:\n{exc}\n\n{traceback.format_exc()}"
            )

    def _browse_intervention_file(self):
        """Open a file dialog for the intervention spreadsheet."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Intervention File",
            "",
            "Excel Files (*.xls *.xlsx);;All Files (*)"
        )
        if not path:
            return

        self._intervention_path_edit.setText(path)

        try:
            data, columns = parse_intervention_file(path)
            self._intervention_data = data
            self._intervention_columns = columns

            # Detect columns for intervention data
            self._intervention_column_map = detect_columns(columns)

            # Build preview with auto-classified 'Type' column
            # Find the actual activity column name in the data
            act_col = None
            for c in columns:
                if 'activity' in c.lower() or 'proposed' in c.lower():
                    act_col = c
                    break

            lat_col = self._intervention_column_map.get('latitude')
            lon_col = self._intervention_column_map.get('longitude')

            preview_cols = ["S.No", "Zone", "Lat", "Lon", "Activity", "Type"]
            preview_rows = []
            for row in data:
                activity = str(row.get(act_col, "")) if act_col else ""
                classified = classify_intervention(activity)
                # Try to find S.No and Zone from actual column names
                sno = ""
                zone = ""
                for c in columns:
                    cl = c.lower().strip()
                    if cl in ('s.no', 'sno', 'serial', 'sr', 'sr.no', 'serial_no'):
                        sno = row.get(c, "")
                    if cl == 'zone':
                        zone = row.get(c, "")
                lat_val = row.get(lat_col, "") if lat_col else ""
                lon_val = row.get(lon_col, "") if lon_col else ""
                preview_rows.append({
                    "S.No": sno,
                    "Zone": zone,
                    "Lat": lat_val,
                    "Lon": lon_val,
                    "Activity": activity,
                    "Type": classified,
                })

            self._populate_table(
                self._intervention_table, preview_rows, preview_cols, max_rows=len(preview_rows)
            )

            self.iface.messageBar().pushSuccess(
                "CHIRAG-Springs",
                f"Loaded {len(data)} intervention records from {os.path.basename(path)}"
            )

        except Exception as exc:
            self._intervention_data = None
            self._intervention_columns = None
            QMessageBox.critical(
                self,
                "Intervention File Error",
                f"Failed to parse intervention file:\n{exc}\n\n{traceback.format_exc()}"
            )

    def _browse_kml_file(self):
        """Open a file dialog for the optional KML overlay."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select KML File",
            "",
            "KML Files (*.kml *.kmz);;All Files (*)"
        )
        if path:
            self._kml_path_edit.setText(path)

    # ================================================================
    #  TABLE POPULATION
    # ================================================================

    def _populate_table(self, table_widget, data, columns, max_rows=None):
        """Fill a QTableWidget from a list of dicts.

        Parameters
        ----------
        table_widget : QTableWidget
        data : list[dict]
        columns : list[str]
        max_rows : int or None
        """
        display_data = data if max_rows is None else data[:max_rows]
        n_rows = len(display_data)
        n_cols = len(columns)

        table_widget.setRowCount(n_rows)
        table_widget.setColumnCount(n_cols)
        table_widget.setHorizontalHeaderLabels([str(c) for c in columns])

        for r, row_dict in enumerate(display_data):
            for c, col in enumerate(columns):
                value = row_dict.get(col, "")
                item = QTableWidgetItem(str(value) if value is not None else "")
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table_widget.setItem(r, c, item)

        table_widget.resizeColumnsToContents()

    # ================================================================
    #  ACTION: Plot Structural Data  (Tab 1)
    # ================================================================

    def _on_plot_structural(self):
        """Parse, create layer, style, and add to project."""
        if self._survey_data is None or self._column_map is None:
            QMessageBox.warning(
                self,
                "No Data",
                "Please browse and load a survey file first."
            )
            return

        try:
            layer = create_structural_layer(
                self._survey_data, self._column_map
            )
            apply_strike_dip_styling(layer, self._column_map)

            QgsProject.instance().addMapLayer(layer)

            # Zoom to layer
            canvas = self.iface.mapCanvas()
            extent = layer.extent()
            if not extent.isEmpty():
                extent.scale(1.1)
                canvas.setExtent(extent)
                canvas.refresh()

            self.iface.messageBar().pushSuccess(
                "CHIRAG-Springs",
                f"Structural layer added with {layer.featureCount()} features."
            )
            QMessageBox.information(
                self,
                "Success",
                f"Structural data plotted successfully.\n"
                f"{layer.featureCount()} plane measurements loaded."
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Structural Plot Error",
                f"Failed to create structural layer:\n{exc}\n\n{traceback.format_exc()}"
            )

    # ================================================================
    #  ACTION: Export to Google Earth KML  (Tab 1)
    # ================================================================

    def _on_export_kml(self):
        """Export loaded survey data to a Google Earth KML file."""
        if self._survey_data is None or self._column_map is None:
            QMessageBox.warning(
                self,
                "No Data",
                "Please browse and load a survey file first."
            )
            return

        # Ask where to save
        default_name = os.path.splitext(
            os.path.basename(self._survey_path_edit.text())
        )[0] + '_structural.kml'

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Google Earth KML",
            default_name,
            "KML Files (*.kml);;All Files (*)"
        )
        if not save_path:
            return

        try:
            count = export_structural_kml(
                self._survey_data, self._column_map, save_path
            )
            self.iface.messageBar().pushSuccess(
                "CHIRAG-Springs",
                f"Exported {count} placemarks to {os.path.basename(save_path)}"
            )
            QMessageBox.information(
                self,
                "Google Earth Export",
                f"KML exported successfully!\n\n"
                f"File: {save_path}\n"
                f"Points: {count}\n\n"
                f"Each point includes:\n"
                f"  • Strike bar line\n"
                f"  • Dip tick line\n"
                f"  • Labels with S / DD / DA values\n"
                f"  • Click balloon with all attributes\n\n"
                f"Open this file in Google Earth to view."
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "KML Export Error",
                f"Failed to export KML:\n{exc}\n\n{traceback.format_exc()}"
            )

    # ================================================================
    #  ACTION: Map Interventions  (Tab 2)
    # ================================================================

    def _on_map_interventions(self):
        """Classify, create layer, style, optionally load KML, add to project."""
        if self._intervention_data is None:
            QMessageBox.warning(
                self,
                "No Data",
                "Please browse and load an intervention file first."
            )
            return

        try:
            # Detect columns if not already done
            if not hasattr(self, '_intervention_column_map') or self._intervention_column_map is None:
                self._intervention_column_map = detect_columns(self._intervention_columns)

            layer = create_intervention_layer(
                self._intervention_data, self._intervention_column_map
            )
            apply_intervention_styling(layer)
            QgsProject.instance().addMapLayer(layer)

            layers_added = [layer.name()]

            # Optional KML
            kml_path = self._kml_path_edit.text().strip()
            if kml_path and os.path.isfile(kml_path):
                kml_layer = load_kml_as_layer(kml_path)
                if kml_layer and kml_layer.isValid():
                    QgsProject.instance().addMapLayer(kml_layer)
                    layers_added.append(kml_layer.name())

            # Zoom to intervention layer
            canvas = self.iface.mapCanvas()
            extent = layer.extent()
            if not extent.isEmpty():
                extent.scale(1.1)
                canvas.setExtent(extent)
                canvas.refresh()

            self.iface.messageBar().pushSuccess(
                "CHIRAG-Springs",
                f"Intervention layer(s) added: {', '.join(layers_added)}"
            )
            QMessageBox.information(
                self,
                "Success",
                f"Interventions mapped successfully.\n"
                f"Layers added: {', '.join(layers_added)}\n"
                f"Total features: {layer.featureCount()}"
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Intervention Mapping Error",
                f"Failed to map interventions:\n{exc}\n\n{traceback.format_exc()}"
            )

    # ================================================================
    #  ACTION: GSI Verification  (Tab 3)
    # ================================================================

    def _on_verify_gsi(self):
        """Run GSI verification against loaded survey data."""
        if self._survey_data is None or self._column_map is None:
            QMessageBox.warning(
                self,
                "No Survey Data",
                "Please load structural survey data in the first tab before "
                "running GSI verification."
            )
            return

        try:
            gsi_baseline = {
                "strike": self._spin_regional_strike.value(),
                "dip_direction": self._spin_regional_dip_dir.value(),
                "dip_amount": self._spin_regional_dip_amt.value(),
            }
            threshold = self._spin_deviation.value()

            results = verify_against_gsi(
                self._survey_data, self._column_map, gsi_baseline, threshold
            )

            # Populate verification table
            self._verification_table.setRowCount(len(results))

            flagged_count = 0
            total_count = len(results)

            # Keys matching analysis_engine.verify_against_gsi() output
            header_keys = [
                "row_index", "code", "latitude", "longitude",
                "field_strike", "field_dip_dir",
                "gsi_strike", "gsi_dip_dir",
                "strike_deviation", "dip_dir_deviation", "reason"
            ]

            green_color = QColor(34, 139, 34)    # forest green
            red_color = QColor(220, 20, 60)       # crimson
            bold_font = QFont()
            bold_font.setBold(True)
            normal_font = self._body_font()

            for r, rec in enumerate(results):
                is_flagged = rec.get("flagged", False)
                if is_flagged:
                    flagged_count += 1

                row_color = red_color if is_flagged else green_color
                row_font = bold_font if is_flagged else normal_font

                for c, key in enumerate(header_keys):
                    value = rec.get(key, "")
                    if isinstance(value, float):
                        value = f"{value:.1f}"
                    item = QTableWidgetItem(str(value) if value is not None else "")
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    item.setForeground(row_color)
                    item.setFont(row_font)
                    self._verification_table.setItem(r, c, item)

            self._verification_table.resizeColumnsToContents()

            # Summary label
            if total_count > 0:
                pct = (flagged_count / total_count) * 100.0
            else:
                pct = 0.0

            self._lbl_flag_summary.setText(
                f"{flagged_count} of {total_count} plane measurements flagged "
                f"({pct:.1f}%)"
            )

            self.iface.messageBar().pushSuccess(
                "CHIRAG-Springs",
                f"GSI verification complete — {flagged_count}/{total_count} flagged."
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "GSI Verification Error",
                f"Verification failed:\n{exc}\n\n{traceback.format_exc()}"
            )

    # ================================================================
    #  ACTION: Generate Recharge Zones  (Tab 3)
    # ================================================================

    def _on_generate_recharge(self):
        """Compute and display predicted recharge zones."""
        if self._survey_data is None or self._column_map is None:
            QMessageBox.warning(
                self,
                "No Survey Data",
                "Please load structural survey data in the first tab before "
                "generating recharge zones."
            )
            return

        try:
            zones = compute_recharge_zones(self._survey_data, self._column_map)
            layer = create_recharge_layer(zones)

            QgsProject.instance().addMapLayer(layer)

            # Zoom to recharge layer
            canvas = self.iface.mapCanvas()
            extent = layer.extent()
            if not extent.isEmpty():
                extent.scale(1.1)
                canvas.setExtent(extent)
                canvas.refresh()

            # Build summary text
            summary_lines = []
            summary_lines.append("═══ Recharge Zone Prediction Summary ═══\n")

            if isinstance(zones, list):
                for i, zone in enumerate(zones, start=1):
                    radius = zone.get('radius_m', 0)
                    # Approximate area of a 60° wedge: (angle/360) * pi * r^2
                    import math as _math
                    area_est = (60.0 / 360.0) * _math.pi * radius * radius
                    summary_lines.append(f"── Zone {i} ──")
                    summary_lines.append(
                        f"  Spring Location : "
                        f"({zone.get('spring_lat', 'N/A'):.6f}, {zone.get('spring_lon', 'N/A'):.6f})"
                    )
                    summary_lines.append(
                        f"  Mean Dip (°)    : {zone.get('mean_dip_amount', 'N/A'):.1f}"
                    )
                    summary_lines.append(
                        f"  Up-dip Dir (°)  : {zone.get('up_dip_dir', 'N/A'):.1f}"
                    )
                    summary_lines.append(
                        f"  Radius (m)      : {radius:.0f}"
                    )
                    summary_lines.append(
                        f"  Area Est (m²)   : {area_est:.0f}"
                    )
                    summary_lines.append(
                        f"  Plane count     : {zone.get('plane_count', 'N/A')}"
                    )
                    summary_lines.append("")
            else:
                summary_lines.append(str(zones))

            n_zones = len(zones) if isinstance(zones, list) else 1
            summary_lines.append(f"\nTotal predicted zones: {n_zones}")
            summary_lines.append(f"Layer features: {layer.featureCount()}")

            self._recharge_summary.setPlainText("\n".join(summary_lines))

            self.iface.messageBar().pushSuccess(
                "CHIRAG-Springs",
                f"Recharge zone layer added with {layer.featureCount()} polygons."
            )
            QMessageBox.information(
                self,
                "Recharge Zones",
                f"Predicted recharge zones generated successfully.\n"
                f"{layer.featureCount()} zone polygon(s) added to the project."
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Recharge Zone Error",
                f"Failed to generate recharge zones:\n{exc}\n\n{traceback.format_exc()}"
            )
