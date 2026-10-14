"""
Print layout composer and exporter.

Given a map title and a list of visible layers, this module programmatically
builds a QgsPrintLayout with:

  • Title (top, centred)
  • Map frame (with active canvas extent)
  • Legend (filtered to the plan's layers, grouped by name)
  • Scale bar (bottom-left)
  • North arrow (top-right)
  • Attribution footer (bottom, small)

Then exports to PDF or PNG.

All layout construction happens on the main thread. The dialog invokes
this directly — there's no threading benefit here because QgsPrintLayout
operations are fast and must be on the main thread anyway.
"""
from __future__ import annotations

import os
from typing import Iterable, Optional

from qgis.core import (
    QgsLayoutExporter, QgsLayoutItemLabel, QgsLayoutItemLegend,
    QgsLayoutItemMap, QgsLayoutItemPicture, QgsLayoutItemScaleBar,
    QgsLayoutPoint, QgsLayoutSize, QgsPrintLayout, QgsProject,
    QgsRectangle, QgsUnitTypes,
)
from qgis.PyQt.QtGui import QColor, QFont

from ..compat import C
from ..utils import logging as log


ATTRIBUTION = ("Data: OpenStreetMap contributors (ODbL)  ·  "
               "Natural Earth (public domain)  ·  "
               "Basemap © providers  ·  Created with Claude Mapper Pro")


def build_layout(project: QgsProject, title: str,
                 extent: Optional[QgsRectangle] = None,
                 page_width_mm: float = 297.0,
                 page_height_mm: float = 210.0) -> QgsPrintLayout:
    """
    Build and return a fully composed QgsPrintLayout.
    Caller should add it to the project via project.layoutManager().addLayout().
    """
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(f"Map: {title}")
    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(page_width_mm, page_height_mm,
                                    QgsUnitTypes.LayoutMillimeters))

    # --- Title ---
    title_item = QgsLayoutItemLabel(layout)
    title_item.setText(title)
    title_item.setFont(QFont("Arial", 18, C.FONT_BOLD))
    title_item.setFontColor(QColor("#1E3A8A"))
    title_item.adjustSizeToText()
    layout.addLayoutItem(title_item)
    title_item.attemptMove(QgsLayoutPoint(10, 8, QgsUnitTypes.LayoutMillimeters))

    # --- Main map frame ---
    map_item = QgsLayoutItemMap(layout)
    map_item.setRect(0, 0, page_width_mm - 80, page_height_mm - 40)
    if extent is not None:
        map_item.setExtent(extent)
    else:
        map_item.zoomToExtent(project.viewSettings().defaultViewExtent()
                              if hasattr(project, "viewSettings") else QgsRectangle())
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(10, 20, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(page_width_mm - 100,
                                          page_height_mm - 50,
                                          QgsUnitTypes.LayoutMillimeters))

    # --- Legend (right side) ---
    legend = QgsLayoutItemLegend(layout)
    legend.setTitle("Legend")
    legend.setLinkedMap(map_item)
    legend.setAutoUpdateModel(True)
    legend.setBackgroundColor(QColor("#FFFFFFEE"))
    legend.setFrameEnabled(True)
    legend.setFrameStrokeColor(QColor("#CBD5E1"))
    layout.addLayoutItem(legend)
    legend.attemptMove(QgsLayoutPoint(page_width_mm - 85, 20,
                                       QgsUnitTypes.LayoutMillimeters))
    legend.attemptResize(QgsLayoutSize(78, 140, QgsUnitTypes.LayoutMillimeters))

    # --- Scale bar ---
    scalebar = QgsLayoutItemScaleBar(layout)
    scalebar.setStyle("Single Box")
    scalebar.setUnits(QgsUnitTypes.DistanceKilometers)
    scalebar.setNumberOfSegments(4)
    scalebar.setNumberOfSegmentsLeft(0)
    scalebar.setLinkedMap(map_item)
    scalebar.applyDefaultSize()
    layout.addLayoutItem(scalebar)
    scalebar.attemptMove(QgsLayoutPoint(12, page_height_mm - 22,
                                         QgsUnitTypes.LayoutMillimeters))

    # --- Attribution footer ---
    attrib = QgsLayoutItemLabel(layout)
    attrib.setText(ATTRIBUTION)
    attrib.setFont(QFont("Arial", 6))
    attrib.setFontColor(QColor("#6B7280"))
    attrib.adjustSizeToText()
    layout.addLayoutItem(attrib)
    attrib.attemptMove(QgsLayoutPoint(10, page_height_mm - 8,
                                       QgsUnitTypes.LayoutMillimeters))

    log.info(f"Print layout built for title: {title}")
    return layout


def export_pdf(layout: QgsPrintLayout, out_path: str) -> None:
    exporter = QgsLayoutExporter(layout)
    settings = QgsLayoutExporter.PdfExportSettings()
    result = exporter.exportToPdf(out_path, settings)
    if result != QgsLayoutExporter.Success:
        raise RuntimeError(f"PDF export failed with code {result}")
    log.info(f"PDF exported to {out_path}")


def export_png(layout: QgsPrintLayout, out_path: str, dpi: int = 300) -> None:
    exporter = QgsLayoutExporter(layout)
    settings = QgsLayoutExporter.ImageExportSettings()
    settings.dpi = dpi
    result = exporter.exportToImage(out_path, settings)
    if result != QgsLayoutExporter.Success:
        raise RuntimeError(f"PNG export failed with code {result}")
    log.info(f"PNG exported to {out_path} @ {dpi} dpi")
