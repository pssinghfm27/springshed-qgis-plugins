"""
Layer labelling helper.

Applies a PAL label configuration when the plan's layer has a
label_field. Uses tasteful defaults: 9pt Arial, buffered, point-around
placement for polygons and parallel for lines.
"""
from __future__ import annotations

from typing import Dict

from qgis.core import (
    QgsPalLayerSettings, QgsTextBufferSettings, QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor, QFont


def apply_labels(layer, spec: Dict) -> None:
    field = spec.get("label_field")
    if not field:
        return
    try:
        fields = [f.name() for f in layer.fields()]
    except Exception:
        return
    if field not in fields:
        return

    settings = QgsPalLayerSettings()
    settings.fieldName = field
    settings.enabled = True

    text_fmt = QgsTextFormat()
    text_fmt.setFont(QFont("Arial", 9))
    text_fmt.setSize(9)
    text_fmt.setColor(QColor("#1F2937"))

    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.8)
    buf.setColor(QColor("#FFFFFF"))
    text_fmt.setBuffer(buf)

    settings.setFormat(text_fmt)

    layer.setLabelsEnabled(True)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    try:
        layer.triggerRepaint()
    except Exception:
        pass
