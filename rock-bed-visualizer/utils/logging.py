"""Log helper that wraps QgsMessageLog with a consistent tag."""
from __future__ import annotations

TAG = "RockBed"


def _emit(msg: str, level: str) -> None:
    try:
        from qgis.core import QgsMessageLog, Qgis
        lv = {
            "info":    Qgis.Info,
            "warn":    Qgis.Warning,
            "crit":    Qgis.Critical,
            "success": Qgis.Success,
        }.get(level, Qgis.Info)
        QgsMessageLog.logMessage(str(msg), TAG, lv)
    except Exception:
        # Fallback for unit tests outside QGIS
        print(f"[{TAG}:{level}] {msg}")


def info(msg: str)    -> None: _emit(msg, "info")
def warn(msg: str)    -> None: _emit(msg, "warn")
def error(msg: str)   -> None: _emit(msg, "crit")
def success(msg: str) -> None: _emit(msg, "success")
