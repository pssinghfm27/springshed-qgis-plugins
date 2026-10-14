"""Rock Bed Visualizer - QGIS plugin entry point."""


def classFactory(iface):
    from .plugin import RockBedVisualizerPlugin
    return RockBedVisualizerPlugin(iface)
