"""Claude AI Map Maker Pro - QGIS plugin entry point."""


def classFactory(iface):
    from .plugin import ClaudeMapperPlugin
    return ClaudeMapperPlugin(iface)
