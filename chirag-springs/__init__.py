# -*- coding: utf-8 -*-
"""
CHIRAG-Springs — Comprehensive Hydrogeological Intervention and
Recharge Analysis for Groundwater.

A QGIS 3.x plugin for structural geology field-data visualization,
intervention mapping, GSI cross-verification, and predictive
recharge-zone modeling.
"""


def classFactory(iface):
    """QGIS plugin entry point.

    :param iface: A QgisInterface instance providing the hook into the
        QGIS application at run time.
    :returns: An instance of the ChiragSprings plugin class.
    """
    from .chirag_springs import ChiragSprings
    return ChiragSprings(iface)
