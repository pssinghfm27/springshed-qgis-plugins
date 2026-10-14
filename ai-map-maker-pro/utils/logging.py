"""Consistent logging to the QGIS Message Log panel."""
from __future__ import annotations

try:
    from qgis.core import QgsMessageLog, Qgis
    _HAS_QGIS = True
except ImportError:  # allow unit testing outside QGIS
    _HAS_QGIS = False

TAG = "ClaudeMapper"


def _level(kind: str):
    if not _HAS_QGIS:
        return 0
    return {
        "info": Qgis.Info,
        "warn": Qgis.Warning,
        "crit": Qgis.Critical,
        "success": Qgis.Success,
    }.get(kind, Qgis.Info)


def info(msg: str) -> None:
    _emit(msg, "info")


def warn(msg: str) -> None:
    _emit(msg, "warn")


def error(msg: str) -> None:
    _emit(msg, "crit")


def success(msg: str) -> None:
    _emit(msg, "success")


def _emit(msg: str, kind: str) -> None:
    if _HAS_QGIS:
        try:
            QgsMessageLog.logMessage(str(msg), TAG, _level(kind))
            return
        except Exception:
            pass
    # Fallback to stderr
    print(f"[{TAG}:{kind}] {msg}")
