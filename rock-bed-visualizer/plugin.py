"""
QGIS plugin class: registers the toolbar icon + menu entry.
Uses qgis.PyQt exclusively so it works on QGIS 3 (Qt5) and QGIS 4 (Qt6).
"""
from __future__ import annotations

import os

from qgis.PyQt.QtGui import QIcon
try:
    # Qt6 / PyQt6 keeps QAction in QtGui
    from qgis.PyQt.QtGui import QAction
except ImportError:
    # Qt5 / PyQt5 has it under QtWidgets
    from qgis.PyQt.QtWidgets import QAction


class RockBedVisualizerPlugin:

    MENU_NAME = "&Rock Bed Visualizer"
    ACTION_TEXT = "Open Rock Bed Visualizer…"

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dialog = None

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icons", "icon.svg")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        self.action = QAction(icon, self.ACTION_TEXT, self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(self.MENU_NAME, self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu(self.MENU_NAME, self.action)
            self.action = None
        if self.dialog is not None:
            try:
                self.dialog.close()
            except Exception:
                pass
            self.dialog = None

    def run(self):
        from .dialogs.main_dialog import RockBedDialog
        if self.dialog is None:
            self.dialog = RockBedDialog(self.iface, self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
