"""
Qt binding compatibility shim.

QGIS 3.x runs on PyQt5 (Qt5); QGIS 4.x runs on PyQt6 (Qt6). In PyQt6 most
enums were re-namespaced into sub-classes. We expose a single `C` namespace
of enum aliases so the rest of the plugin stays binding-agnostic.

Rule of thumb: NEVER `import PyQt5.*` or `import PyQt6.*` directly. Always
use `qgis.PyQt.*`, which auto-selects the right binding. QGIS 4's import
hook actively raises ImportError on direct PyQt5 imports, so the classic
"try PyQt6 / except PyQt5" pattern is a bug trap.

Detection works by probing a known PyQt6-only attribute (QFont.Weight).
"""
from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFont, QColor
from qgis.PyQt.QtWidgets import (
    QLineEdit, QDialog, QMessageBox, QDialogButtonBox,
)


IS_PYQT6 = hasattr(QFont, "Weight")


class C:
    """Namespace of Qt-binding-independent enum values."""

    if IS_PYQT6:
        FONT_BOLD        = QFont.Weight.Bold
        FONT_NORMAL      = QFont.Weight.Normal
        ECHO_PASSWORD    = QLineEdit.EchoMode.Password
        ECHO_NORMAL      = QLineEdit.EchoMode.Normal
        ALIGN_CENTER     = Qt.AlignmentFlag.AlignCenter
        ALIGN_LEFT       = Qt.AlignmentFlag.AlignLeft
        ALIGN_RIGHT      = Qt.AlignmentFlag.AlignRight
        ORIENT_H         = Qt.Orientation.Horizontal
        ORIENT_V         = Qt.Orientation.Vertical
        DIALOG_ACCEPTED  = QDialog.DialogCode.Accepted
        DIALOG_REJECTED  = QDialog.DialogCode.Rejected
        MSG_OK           = QMessageBox.StandardButton.Ok
        MSG_CANCEL       = QMessageBox.StandardButton.Cancel
        MSG_YES          = QMessageBox.StandardButton.Yes
        MSG_NO           = QMessageBox.StandardButton.No
        MSG_INFO         = QMessageBox.Icon.Information
        MSG_WARN         = QMessageBox.Icon.Warning
        MSG_CRIT         = QMessageBox.Icon.Critical
        DBB_OK           = QDialogButtonBox.StandardButton.Ok
        DBB_CANCEL       = QDialogButtonBox.StandardButton.Cancel
        DBB_CLOSE        = QDialogButtonBox.StandardButton.Close
    else:
        FONT_BOLD        = QFont.Bold
        FONT_NORMAL      = QFont.Normal
        ECHO_PASSWORD    = QLineEdit.Password
        ECHO_NORMAL      = QLineEdit.Normal
        ALIGN_CENTER     = Qt.AlignCenter
        ALIGN_LEFT       = Qt.AlignLeft
        ALIGN_RIGHT      = Qt.AlignRight
        ORIENT_H         = Qt.Horizontal
        ORIENT_V         = Qt.Vertical
        DIALOG_ACCEPTED  = QDialog.Accepted
        DIALOG_REJECTED  = QDialog.Rejected
        MSG_OK           = QMessageBox.Ok
        MSG_CANCEL       = QMessageBox.Cancel
        MSG_YES          = QMessageBox.Yes
        MSG_NO           = QMessageBox.No
        MSG_INFO         = QMessageBox.Information
        MSG_WARN         = QMessageBox.Warning
        MSG_CRIT         = QMessageBox.Critical
        DBB_OK           = QDialogButtonBox.Ok
        DBB_CANCEL       = QDialogButtonBox.Cancel
        DBB_CLOSE        = QDialogButtonBox.Close


def detect_geometry_type(layer) -> str:
    """Return 'point' | 'line' | 'polygon' for any QgsVectorLayer, QGIS 3/4 safe."""
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
            # WKB codes 1=Point, 2=LineString, 3=Polygon,
            # 4=MultiPoint, 5=MultiLineString, 6=MultiPolygon
            flat = layer.wkbType() % 1000
            if flat in (1, 4):
                return "point"
            if flat in (2, 5):
                return "line"
            return "polygon"
        except Exception:
            return "polygon"
