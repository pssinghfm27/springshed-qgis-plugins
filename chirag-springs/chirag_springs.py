# -*- coding: utf-8 -*-
"""
CHIRAG-Springs — main plugin class.

Registers the plugin in the QGIS Plugins menu and toolbar,
manages the dialog lifecycle.
"""

import os

from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import QSettings, QTranslator, QCoreApplication

from .chirag_springs_dialog import ChiragSpringsDialog


class ChiragSprings:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        """Constructor.

        :param iface: QgisInterface — provides hook into the QGIS application.
        """
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.menu = '&CHIRAG-Springs'
        self.toolbar = self.iface.addToolBar('CHIRAG-Springs')
        self.toolbar.setObjectName('ChiragSpringsToolbar')
        self.dlg = None

    def initGui(self):
        """Create the menu entries and toolbar icons."""
        icon_path = os.path.join(self.plugin_dir, 'icon.png')
        # If icon doesn't exist, use a default QGIS icon
        if not os.path.exists(icon_path):
            icon_path = ':/plugins/chirag_springs/icon.png'  # fallback

        action = QAction(
            QIcon(icon_path),
            'CHIRAG-Springs: Hydrogeological Analysis',
            self.iface.mainWindow()
        )
        action.triggered.connect(self.run)
        action.setEnabled(True)
        action.setStatusTip(
            'Open CHIRAG-Springs for structural geology plotting, '
            'intervention mapping, and recharge zone prediction'
        )

        self.toolbar.addAction(action)
        self.iface.addPluginToMenu(self.menu, action)
        self.actions.append(action)

    def unload(self):
        """Remove the plugin menu items and toolbar icons."""
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)
        # Remove the toolbar
        if self.toolbar:
            del self.toolbar

    def run(self):
        """Run method that creates and shows the dialog."""
        # Create a fresh dialog each time (so state resets cleanly)
        # Or reuse if you want persistence — here we create fresh
        try:
            self.dlg = ChiragSpringsDialog(self.iface, parent=self.iface.mainWindow())
            self.dlg.show()
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(),
                'CHIRAG-Springs Error',
                f'Failed to open CHIRAG-Springs dialog:\n\n{str(e)}'
            )
