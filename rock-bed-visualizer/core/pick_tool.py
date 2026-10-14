"""
Temporary map tool for picking an origin point by clicking the canvas.

Lifecycle:
  tool = PickPointTool(iface.mapCanvas(), on_picked)
  iface.mapCanvas().setMapTool(tool)
  ...
  # When the user clicks, on_picked(QgsPointXY) runs.
  # After the dialog has what it needs:
  iface.mapCanvas().unsetMapTool(tool)

We subclass QgsMapToolEmitPoint which exists in QGIS 3 and 4 and already
handles all of the canvas CRS logic.
"""
from __future__ import annotations

from typing import Callable

from qgis.gui import QgsMapToolEmitPoint


class PickPointTool(QgsMapToolEmitPoint):
    def __init__(self, canvas, on_picked: Callable):
        super().__init__(canvas)
        self._on_picked = on_picked
        self.canvasClicked.connect(self._handle_click)

    def _handle_click(self, point, button):  # noqa: N802 — Qt signal signature
        # button arg is included by QgsMapToolEmitPoint; we accept any button
        try:
            self._on_picked(point)
        except Exception:
            # Never let an exception out — it'd crash QGIS's canvas.
            pass
