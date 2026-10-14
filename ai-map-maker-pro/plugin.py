"""
Main plugin class. Registers the toolbar icon + Plugins menu entry,
and opens the main dialog when clicked.
"""
from __future__ import annotations

import os

from qgis.PyQt.QtGui import QIcon
# QAction location differs between Qt5 (QtWidgets) and Qt6 (QtGui).
# qgis.PyQt re-exports it from both modules in modern QGIS, but we
# guard against environments where only one path is available.
try:
    from qgis.PyQt.QtGui import QAction
except ImportError:
    from qgis.PyQt.QtWidgets import QAction


class ClaudeMapperPlugin:
    """QGIS plugin entry point class."""

    MENU_NAME = "&Claude AI Map Maker Pro"
    ACTION_TEXT = "Open Claude Map Maker…"

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dialog = None

    # Called by QGIS on plugin load ------------------------------------------

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icons", "icon.svg")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        self.action = QAction(icon, self.ACTION_TEXT, self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(self.MENU_NAME, self.action)

    # Called by QGIS on plugin unload ----------------------------------------

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

    # Show the main dialog ---------------------------------------------------

    def run(self):
        from .dialogs.main_dialog import ClaudeMapDialog
        if self.dialog is None:
            self.dialog = ClaudeMapDialog(self.iface, self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
