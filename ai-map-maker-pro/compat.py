"""
PyQt5 (QGIS 3.x Qt5 builds) vs PyQt6 (QGIS 3.34+ Qt6 / QGIS 4.x) compat shims.

QGIS ships a `qgis.PyQt.*` namespace that transparently re-routes to whichever
Qt the host build was compiled against. We use that exclusively for *imports*.
What we still need to handle is the enum re-namespacing: PyQt6 moved many
enums into nested classes (e.g. `QFont.Bold` -> `QFont.Weight.Bold`).

Detection strategy: probe a known PyQt6-only construct (Qt.AlignmentFlag).
Don't try to import PyQt6 directly — on a Qt5 build that import will fail
*loudly*, and on a Qt6 build importing PyQt5 is forbidden by QGIS's import hook.

Usage:
    from .compat import C
    font.setWeight(C.FONT_BOLD)
"""
from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFont, QColor
from qgis.PyQt.QtWidgets import QLineEdit, QDialog, QMessageBox


# Probe for the PyQt6-style namespaced enum.
# AlignmentFlag is the canonical sentinel — it exists only when Qt is at v6+.
IS_PYQT6 = hasattr(Qt, "AlignmentFlag")


class C:
    """Namespace of compatibility-wrapped enum values."""

    if IS_PYQT6:
        FONT_BOLD = QFont.Weight.Bold
        FONT_NORMAL = QFont.Weight.Normal
        ECHO_PASSWORD = QLineEdit.EchoMode.Password
        ECHO_NORMAL = QLineEdit.EchoMode.Normal
        ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter
        ALIGN_LEFT = Qt.AlignmentFlag.AlignLeft
        ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight
        ALIGN_VCENTER = Qt.AlignmentFlag.AlignVCenter
        ORIENT_H = Qt.Orientation.Horizontal
        ORIENT_V = Qt.Orientation.Vertical
        COLOR_HEX_ARGB = QColor.NameFormat.HexArgb
        COLOR_HEX_RGB = QColor.NameFormat.HexRgb
        DIALOG_ACCEPTED = QDialog.DialogCode.Accepted
        DIALOG_REJECTED = QDialog.DialogCode.Rejected
        MSG_OK = QMessageBox.StandardButton.Ok
        MSG_CANCEL = QMessageBox.StandardButton.Cancel
        MSG_YES = QMessageBox.StandardButton.Yes
        MSG_NO = QMessageBox.StandardButton.No
        MSG_INFO = QMessageBox.Icon.Information
        MSG_WARN = QMessageBox.Icon.Warning
        MSG_CRIT = QMessageBox.Icon.Critical
        SCROLL_AS_NEEDED = Qt.ScrollBarPolicy.ScrollBarAsNeeded
        SCROLL_ALWAYS_OFF = Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    else:
        FONT_BOLD = QFont.Bold
        FONT_NORMAL = QFont.Normal
        ECHO_PASSWORD = QLineEdit.Password
        ECHO_NORMAL = QLineEdit.Normal
        ALIGN_CENTER = Qt.AlignCenter
        ALIGN_LEFT = Qt.AlignLeft
        ALIGN_RIGHT = Qt.AlignRight
        ALIGN_VCENTER = Qt.AlignVCenter
        ORIENT_H = Qt.Horizontal
        ORIENT_V = Qt.Vertical
        COLOR_HEX_ARGB = QColor.HexArgb
        COLOR_HEX_RGB = QColor.HexRgb
        DIALOG_ACCEPTED = QDialog.Accepted
        DIALOG_REJECTED = QDialog.Rejected
        MSG_OK = QMessageBox.Ok
        MSG_CANCEL = QMessageBox.Cancel
        MSG_YES = QMessageBox.Yes
        MSG_NO = QMessageBox.No
        MSG_INFO = QMessageBox.Information
        MSG_WARN = QMessageBox.Warning
        MSG_CRIT = QMessageBox.Critical
        SCROLL_AS_NEEDED = Qt.ScrollBarAsNeeded
        SCROLL_ALWAYS_OFF = Qt.ScrollBarAlwaysOff


def detect_geometry_type(layer) -> str:
    """Return 'point' | 'line' | 'polygon' for any vector layer across QGIS 3/4."""
    try:
        gt = layer.geometryType()
        name = str(gt).lower()
        if "point" in name:
            return "point"
        if "line" in name:
            return "line"
        return "polygon"
    except Exception:
        try:
            # WKB type codes: 1=Point, 2=LineString, 3=Polygon,
            # 4=MultiPoint, 5=MultiLineString, 6=MultiPolygon.
            flat = layer.wkbType() % 1000
            if flat in (1, 4):
                return "point"
            if flat in (2, 5):
                return "line"
            return "polygon"
        except Exception:
            return "polygon"
