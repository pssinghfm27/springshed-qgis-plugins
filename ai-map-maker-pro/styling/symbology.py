"""
Symbology builder for vector layers.

Takes a layer-spec dict (from a Claude map plan or a file-upload default)
and applies the right renderer to a QgsVectorLayer.

All QGIS-type API calls here assume the main thread. Workers must hand
layer objects back via signals before calling apply_style().
"""
from __future__ import annotations

from typing import Dict, Optional

from qgis.core import (
    QgsCategorizedSymbolRenderer, QgsGraduatedSymbolRenderer,
    QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol,
    QgsRendererCategory, QgsRendererRange, QgsSingleSymbolRenderer,
    QgsSymbol, QgsSimpleFillSymbolLayer, QgsSimpleLineSymbolLayer,
    QgsSimpleMarkerSymbolLayer,
)
from qgis.PyQt.QtGui import QColor

from ..compat import detect_geometry_type
from ..utils import logging as log


def apply_style(layer, spec: Dict) -> None:
    """Apply the renderer described by spec to layer."""
    geom = detect_geometry_type(layer)
    render_type = spec.get("render_type", "single")

    if render_type == "categorized" and spec.get("categories"):
        _apply_categorized(layer, spec, geom)
    elif render_type == "graduated" and spec.get("categories"):
        _apply_graduated(layer, spec, geom)
    else:
        _apply_single(layer, spec, geom)

    opacity = float(spec.get("opacity", 1.0))
    try:
        layer.setOpacity(max(0.0, min(1.0, opacity)))
    except AttributeError:
        pass
    try:
        layer.triggerRepaint()
    except Exception:
        pass


# ---------------------------------------------------------------------------

def _apply_single(layer, spec: Dict, geom: str) -> None:
    symbol = _build_symbol(spec, geom)
    renderer = QgsSingleSymbolRenderer(symbol)
    layer.setRenderer(renderer)


def _apply_categorized(layer, spec: Dict, geom: str) -> None:
    field = spec.get("label_field") or _first_str_field(layer) or ""
    if not field:
        log.warn(f"Categorized renderer needs a field name; falling back to single symbol for {layer.name()}")
        return _apply_single(layer, spec, geom)

    cats = []
    for c in spec.get("categories", []):
        val = c.get("value")
        color = c.get("color") or spec.get("default_color", "#5B8DEF")
        label = c.get("label") or str(val)
        sym = _build_symbol({**spec, "default_color": color}, geom)
        cats.append(QgsRendererCategory(val, sym, str(label)))

    renderer = QgsCategorizedSymbolRenderer(field, cats)
    layer.setRenderer(renderer)


def _apply_graduated(layer, spec: Dict, geom: str) -> None:
    field = spec.get("label_field") or _first_num_field(layer) or ""
    if not field:
        return _apply_single(layer, spec, geom)

    ranges = []
    for c in spec.get("categories", []):
        try:
            lo = float(c.get("min", 0))
            hi = float(c.get("max", lo + 1))
        except (TypeError, ValueError):
            continue
        color = c.get("color") or spec.get("default_color", "#5B8DEF")
        label = c.get("label") or f"{lo:g} – {hi:g}"
        sym = _build_symbol({**spec, "default_color": color}, geom)
        ranges.append(QgsRendererRange(lo, hi, sym, str(label)))

    renderer = QgsGraduatedSymbolRenderer(field, ranges)
    layer.setRenderer(renderer)


# ---------------------------------------------------------------------------
# Symbol construction
# ---------------------------------------------------------------------------

def _build_symbol(spec: Dict, geom: str) -> QgsSymbol:
    fill = QColor(spec.get("default_color", "#5B8DEF"))
    stroke_raw = spec.get("stroke_color")
    stroke = QColor(stroke_raw) if stroke_raw else None
    stroke_w = float(spec.get("stroke_width", 0.6))

    if geom == "point":
        sym = QgsMarkerSymbol.createSimple({
            "name":    "circle",
            "color":   _rgba_str(fill),
            "outline_color": _rgba_str(stroke) if stroke else _rgba_str(fill.darker(150)),
            "outline_width": f"{stroke_w * 0.4:.2f}",
            "size":    f"{float(spec.get('point_size', 3.0)):.1f}",
        })
        return sym

    if geom == "line":
        sym = QgsLineSymbol.createSimple({
            "line_color": _rgba_str(fill),
            "line_width": f"{stroke_w:.2f}",
        })
        return sym

    # polygon
    sym = QgsFillSymbol.createSimple({
        "color":         _rgba_str(fill),
        "outline_color": _rgba_str(stroke) if stroke else _rgba_str(fill.darker(150)),
        "outline_width": f"{stroke_w:.2f}",
    })
    return sym


def _rgba_str(c: QColor) -> str:
    return f"{c.red()},{c.green()},{c.blue()},{c.alpha()}"


def _first_str_field(layer) -> Optional[str]:
    try:
        for f in layer.fields():
            if f.typeName().lower().startswith(("string", "text")):
                return f.name()
    except Exception:
        pass
    return None


def _first_num_field(layer) -> Optional[str]:
    try:
        for f in layer.fields():
            if f.typeName().lower() in ("integer", "integer64", "real", "double"):
                return f.name()
    except Exception:
        pass
    return None
