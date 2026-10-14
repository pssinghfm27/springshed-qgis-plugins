# -*- coding: utf-8 -*-
"""Analysis engine for CHIRAG-Springs QGIS plugin — mathematical processing,
file parsing, coordinate transforms, styling, GSI verification, and recharge
zone prediction."""

import csv
import math
import os
import re
import xml.etree.ElementTree as ET
from collections import OrderedDict

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

from qgis.core import (
    QgsVectorLayer,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMarkerSymbol,
    QgsSimpleMarkerSymbolLayer,
    QgsProperty,
    QgsCategorizedSymbolRenderer,
    QgsRendererCategory,
    QgsSymbol,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsFillSymbol,
    QgsSimpleFillSymbolLayer,
    QgsRuleBasedRenderer,
    QgsSingleSymbolRenderer,
    QgsEllipseSymbolLayer,
    QgsMarkerLineSymbolLayer,
    QgsFontMarkerSymbolLayer,
    QgsSimpleLineSymbolLayer,
)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor, QFont

# Compatibility shim: the angle property constant moved across QGIS versions
try:
    from qgis.core import QgsSymbolLayer as _QSL
    _PROP_ANGLE = _QSL.Property.Angle          # QGIS >= 3.26
except AttributeError:
    try:
        _PROP_ANGLE = _QSL.PropertyAngle        # QGIS 3.18–3.24
    except AttributeError:
        _PROP_ANGLE = 0                          # ultimate fallback (Sketchy but functional)


# ---------------------------------------------------------------------------
#  1. FILE PARSING
# ---------------------------------------------------------------------------

def parse_survey_file(file_path):
    """Parse CSV or Excel survey file.  Auto-detect format by extension.

    Returns ``(list_of_row_dicts, list_of_column_names)``.

    For Excel the function tries every sheet and picks the first one whose
    header row contains at least one recognisable latitude-like and one
    longitude-like column name.
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Survey file not found: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()

    if ext == '.csv':
        return _parse_csv(file_path)
    elif ext in ('.xlsx', '.xls'):
        return _parse_excel_survey(file_path)
    else:
        raise ValueError(f"Unsupported survey file format: {ext}")


def _parse_csv(file_path):
    """Internal helper — parse a CSV survey file."""
    rows = []
    columns = []
    try:
        with open(file_path, 'r', newline='', encoding='utf-8-sig') as fh:
            reader = csv.DictReader(fh)
            columns = list(reader.fieldnames) if reader.fieldnames else []
            for row in reader:
                rows.append(dict(row))
    except UnicodeDecodeError:
        with open(file_path, 'r', newline='', encoding='latin-1') as fh:
            reader = csv.DictReader(fh)
            columns = list(reader.fieldnames) if reader.fieldnames else []
            for row in reader:
                rows.append(dict(row))
    except Exception as exc:
        raise IOError(f"Failed to parse CSV '{file_path}': {exc}")
    return rows, columns


def _looks_like_survey_header(header_list):
    """Return *True* if *header_list* contains at least one latitude-like
    and one longitude-like column name."""
    lat_aliases = {'latitude', 'lat', 'y'}
    lon_aliases = {'longitude', 'lon', 'long', 'x'}
    lower = {h.strip().lower() for h in header_list if isinstance(h, str)}
    has_lat = bool(lower & lat_aliases)
    has_lon = bool(lower & lon_aliases)
    return has_lat and has_lon


def _parse_excel_survey(file_path):
    """Internal helper — parse an Excel survey file (tries every sheet)."""
    if not HAS_OPENPYXL:
        raise ImportError(
            "The 'openpyxl' library is required to read Excel files.  "
            "Install it with:  pip install openpyxl"
        )

    try:
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    except Exception as exc:
        raise IOError(f"Failed to open Excel workbook '{file_path}': {exc}")

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        all_rows_raw = list(ws.iter_rows(values_only=True))
        if not all_rows_raw:
            continue

        # First non-empty row is treated as header
        header_row_idx = 0
        for idx, row in enumerate(all_rows_raw):
            cells = [str(c).strip() if c is not None else '' for c in row]
            if any(cells):
                header_row_idx = idx
                break

        header = [str(c).strip() if c is not None else f'col_{i}'
                  for i, c in enumerate(all_rows_raw[header_row_idx])]

        if not _looks_like_survey_header(header):
            continue

        rows = []
        for row in all_rows_raw[header_row_idx + 1:]:
            vals = [c for c in row]
            d = OrderedDict()
            for i, col_name in enumerate(header):
                d[col_name] = vals[i] if i < len(vals) else None
            # skip entirely empty rows
            if all(v is None or str(v).strip() == '' for v in d.values()):
                continue
            rows.append(d)

        wb.close()
        return rows, header

    wb.close()
    raise ValueError(
        f"No sheet in '{file_path}' has recognisable Latitude/Longitude columns."
    )


def parse_intervention_file(file_path):
    """Parse intervention Excel file.

    Returns ``(list_of_row_dicts, list_of_column_names)``.

    Section-header rows (where the *S.No* cell is not numeric) are silently
    skipped — this handles the emoji-decorated headers in rows 2, 16, 24.
    """
    if not HAS_OPENPYXL:
        raise ImportError(
            "The 'openpyxl' library is required to read Excel files.  "
            "Install it with:  pip install openpyxl"
        )

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Intervention file not found: {file_path}")

    try:
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    except Exception as exc:
        raise IOError(f"Failed to open Excel workbook '{file_path}': {exc}")

    # Prefer 'Sheet2' if present; otherwise use the first sheet
    if 'Sheet2' in wb.sheetnames:
        ws = wb['Sheet2']
    else:
        ws = wb[wb.sheetnames[0]]

    all_rows_raw = list(ws.iter_rows(values_only=True))
    wb.close()

    if not all_rows_raw:
        return [], []

    # Locate the header row — the first row that contains 'S.No' (case-insensitive)
    header = None
    header_idx = 0
    for idx, row in enumerate(all_rows_raw):
        cells = [str(c).strip() if c is not None else '' for c in row]
        for cell in cells:
            if cell.lower().replace('.', '').replace(' ', '') in ('sno', 'serialno', 'sno'):
                header = cells
                header_idx = idx
                break
            if cell.lower() in ('s.no', 's. no', 's.no.'):
                header = cells
                header_idx = idx
                break
        if header is not None:
            break

    # Fallback: just use first row as header
    if header is None:
        header = [str(c).strip() if c is not None else f'col_{i}'
                  for i, c in enumerate(all_rows_raw[0])]
        header_idx = 0

    # Clean header names (remove empty trailing columns)
    header = [h if h else f'col_{i}' for i, h in enumerate(header)]

    # Detect which column index represents "S.No"
    sno_col_idx = None
    for i, h in enumerate(header):
        if h.lower().replace('.', '').replace(' ', '') in ('sno', 'serialno', 'sno'):
            sno_col_idx = i
            break
        if h.lower() in ('s.no', 's. no', 's.no.'):
            sno_col_idx = i
            break

    rows = []
    for row in all_rows_raw[header_idx + 1:]:
        vals = [c for c in row]

        # Skip rows where S.No is not numeric (section headers with emoji etc.)
        if sno_col_idx is not None and sno_col_idx < len(vals):
            sno_val = vals[sno_col_idx]
            if sno_val is None:
                continue
            try:
                float(sno_val)
            except (ValueError, TypeError):
                continue

        d = OrderedDict()
        for i, col_name in enumerate(header):
            d[col_name] = vals[i] if i < len(vals) else None
        # Skip entirely empty rows
        if all(v is None or str(v).strip() == '' for v in d.values()):
            continue
        rows.append(d)

    return rows, header


def parse_kml_file(file_path):
    """Parse KML file extracting Point placemarks with extended data.

    Returns a list of dicts with keys matching the KML schema fields
    (``Latitude``, ``Longitude``, ``Elevation``, ``Code``, ``Rock``,
    ``Strike N``, ``Strike S``, ``Dip Direct``, ``Dip Amount``, ``Remark``).

    Uses *xml.etree.ElementTree* — no external KML libraries required.
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"KML file not found: {file_path}")

    KML_NS = '{http://www.opengis.net/kml/2.2}'

    try:
        tree = ET.parse(file_path)
    except ET.ParseError as exc:
        raise IOError(f"Failed to parse KML '{file_path}': {exc}")

    root = tree.getroot()

    # Handle files with or without namespace
    ns = ''
    if root.tag.startswith('{'):
        ns = root.tag.split('}')[0] + '}'
    else:
        ns = ''

    results = []

    for placemark in root.iter(f'{ns}Placemark'):
        point = placemark.find(f'{ns}Point')
        if point is None:
            continue

        coords_el = point.find(f'{ns}coordinates')
        if coords_el is None or not coords_el.text:
            continue

        coords_text = coords_el.text.strip()
        parts = coords_text.split(',')
        if len(parts) < 2:
            continue

        lon = _safe_float(parts[0].strip())
        lat = _safe_float(parts[1].strip())
        elev = _safe_float(parts[2].strip()) if len(parts) > 2 else 0.0

        record = OrderedDict()
        record['Latitude'] = lat
        record['Longitude'] = lon
        record['Elevation'] = elev

        # Extended data — two common KML patterns
        ext_data = placemark.find(f'{ns}ExtendedData')
        if ext_data is not None:
            # Pattern 1: <SchemaData><SimpleData name="...">
            for sd in ext_data.iter(f'{ns}SimpleData'):
                name = sd.get('name', '')
                value = sd.text if sd.text else ''
                record[name] = value

            # Pattern 2: <Data name="..."><value>
            for data_el in ext_data.iter(f'{ns}Data'):
                name = data_el.get('name', '')
                value_el = data_el.find(f'{ns}value')
                value = value_el.text if (value_el is not None and value_el.text) else ''
                record[name] = value

        # If Latitude/Longitude were not overridden by ExtendedData, keep
        # the values we parsed from <coordinates>
        if 'Latitude' not in record or record['Latitude'] in (None, ''):
            record['Latitude'] = lat
        if 'Longitude' not in record or record['Longitude'] in (None, ''):
            record['Longitude'] = lon

        results.append(record)

    return results


# ---------------------------------------------------------------------------
#  2. COLUMN DETECTION
# ---------------------------------------------------------------------------

# Mapping of semantic role -> list of candidate column names (lower-cased).
_COLUMN_ALIASES = {
    'latitude':       ['latitude', 'lat', 'y'],
    'longitude':      ['longitude', 'lon', 'long', 'x'],
    'strike':         ['strike_north_deg', 'strike n', 'strike', 'strike_n'],
    'strike_south':   ['strike_south_deg', 'strike s', 'strike_s'],
    'dip_direction':  ['dip_direction_deg', 'dip direction', 'dip dir',
                       'dip direct', 'dip_dir', 'dip_direction'],
    'dip_amount':     ['dip_amount_deg', 'dip amount', 'dip', 'dip_amount'],
    'elevation':      ['elevation_m', 'elevation', 'elev', 'altitude',
                       'elevation (m)', 'height'],
    'code':           ['code_key', 'code', 'code_label'],
    'rock_type':      ['rock_type', 'rock', 'lithology', 'rock type'],
    'remarks':        ['remarks', 'remark', 'notes', 'note', 'comment'],
    'serial_no':      ['serial_no', 's.no', 'sno', 's. no', 'sr', 'sr.',
                       'serial', 'serial no'],
    'project_name':   ['project_name', 'project', 'project name'],
    'village':        ['village', 'location'],
    'district':       ['district'],
    'state':          ['state'],
    'surveyor':       ['surveyor', 'observer', 'recorded_by', 'recorded by'],
    'spring_type':    ['spring_type_key', 'spring_type_label', 'spring type',
                       'spring_type'],
}


def detect_columns(column_names):
    """Fuzzy-match *column_names* to semantic roles.

    Returns a dict mapping each semantic role to the **actual** column name
    string from *column_names*, or ``None`` if no match was found.
    """
    result = {role: None for role in _COLUMN_ALIASES}

    # Normalised lookup: lower-case stripped name -> original name
    norm_map = {}
    for cn in column_names:
        key = str(cn).strip().lower()
        norm_map[key] = cn

    for role, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in norm_map:
                result[role] = norm_map[alias]
                break

    return result


# ---------------------------------------------------------------------------
#  3. COORDINATE TRANSFORMS
# ---------------------------------------------------------------------------

def get_utm_epsg(longitude, latitude=29.0):
    """Calculate the UTM EPSG code for a given *longitude*.

    The UTM zone number is ``floor((longitude + 180) / 6) + 1``.
    Northern hemisphere → ``32600 + zone``, southern → ``32700 + zone``.
    The *latitude* parameter defaults to 29° (India) and is only used for
    hemisphere selection.

    Returns an ``int`` EPSG code.
    """
    try:
        zone = int(math.floor((float(longitude) + 180.0) / 6.0)) + 1
        zone = max(1, min(zone, 60))
        if float(latitude) >= 0:
            return 32600 + zone
        else:
            return 32700 + zone
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid longitude/latitude for UTM: {exc}")


def transform_point(lat, lon, source_epsg, target_epsg):
    """Transform a single point between CRS.

    Uses ``QgsCoordinateTransform``.  Returns ``(x, y)`` in the *target_epsg*
    coordinate system.
    """
    try:
        src_crs = QgsCoordinateReferenceSystem(f'EPSG:{source_epsg}')
        dst_crs = QgsCoordinateReferenceSystem(f'EPSG:{target_epsg}')
        xform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
        pt = xform.transform(QgsPointXY(float(lon), float(lat)))
        return (pt.x(), pt.y())
    except Exception as exc:
        raise RuntimeError(
            f"Coordinate transform failed ({source_epsg}->{target_epsg}) "
            f"for ({lat}, {lon}): {exc}"
        )


def transform_point_back(x, y, source_epsg, target_epsg):
    """Transform projected coordinates back to geographic.

    Returns ``(lat, lon)`` — note the order: latitude first, longitude second.
    """
    try:
        src_crs = QgsCoordinateReferenceSystem(f'EPSG:{source_epsg}')
        dst_crs = QgsCoordinateReferenceSystem(f'EPSG:{target_epsg}')
        xform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
        pt = xform.transform(QgsPointXY(float(x), float(y)))
        # In EPSG:4326 x=lon, y=lat
        return (pt.y(), pt.x())
    except Exception as exc:
        raise RuntimeError(
            f"Reverse coordinate transform failed ({source_epsg}->{target_epsg}) "
            f"for ({x}, {y}): {exc}"
        )


# ---------------------------------------------------------------------------
#  4. T-SYMBOL STYLING ENGINE
# ---------------------------------------------------------------------------

def create_structural_layer(rows, column_map, layer_name='Structural_Survey'):
    """Create an in-memory ``QgsVectorLayer`` (Point, EPSG:4326) from parsed
    survey *rows*.

    All original attributes are added as layer fields.  Returns the
    ``QgsVectorLayer``.
    """
    lat_col = column_map.get('latitude')
    lon_col = column_map.get('longitude')
    if not lat_col or not lon_col:
        raise ValueError(
            "Cannot create structural layer: latitude and/or longitude "
            "columns not detected."
        )

    # Determine the complete set of field names from the first row
    if not rows:
        raise ValueError("No data rows to create structural layer from.")

    field_names = list(rows[0].keys())

    # Build URI
    uri = "Point?crs=epsg:4326"
    layer = QgsVectorLayer(uri, layer_name, "memory")
    provider = layer.dataProvider()

    # Add fields
    qgs_fields = []
    for fn in field_names:
        qgs_fields.append(QgsField(fn, QVariant.String))
    provider.addAttributes(qgs_fields)
    layer.updateFields()

    # Add features
    features = []
    for row in rows:
        lat = _safe_float(row.get(lat_col))
        lon = _safe_float(row.get(lon_col))
        if lat is None or lon is None:
            continue

        feat = QgsFeature(layer.fields())
        feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
        for fn in field_names:
            val = row.get(fn)
            feat.setAttribute(fn, str(val) if val is not None else '')
        features.append(feat)

    provider.addFeatures(features)
    layer.updateExtents()
    return layer


def apply_strike_dip_styling(layer, column_map):
    """Apply the geological T-symbol styling to *layer*.

    The T-symbol is built from two ``QgsSimpleMarkerSymbolLayer`` sub-layers
    (shape = ``Line``):

    * **Strike bar** — the horizontal top of the T, rotated to the strike
      azimuth.
    * **Dip tick** — the shorter vertical stem, rotated to the dip-direction
      azimuth and offset slightly from the point toward the dip side.

    Labelling shows the dip-amount value near each feature.
    """
    strike_col = column_map.get('strike')
    dip_dir_col = column_map.get('dip_direction')
    dip_amt_col = column_map.get('dip_amount')

    if not strike_col:
        raise ValueError("Strike column not detected — cannot style T-symbols.")
    if not dip_dir_col:
        raise ValueError(
            "Dip-direction column not detected — cannot style T-symbols."
        )

    from qgis.PyQt.QtCore import QPointF

    # --- Build strike bar symbol layer (long thin line rotated to strike) ---
    strike_sym = QgsSimpleMarkerSymbolLayer()
    strike_sym.setShape(QgsSimpleMarkerSymbolLayer.Line)
    strike_sym.setSize(20.0)        # length of the bar in mm (bigger)
    strike_sym.setStrokeWidth(0.8)  # line thickness
    strike_sym.setColor(QColor(0, 0, 0))
    strike_sym.setStrokeColor(QColor(0, 0, 0))
    # Data-defined rotation from strike column
    strike_sym.setDataDefinedProperty(
        _PROP_ANGLE,
        QgsProperty.fromField(strike_col)
    )

    # --- Build dip tick symbol layer (shorter line rotated to dip direction) ---
    dip_sym = QgsSimpleMarkerSymbolLayer()
    dip_sym.setShape(QgsSimpleMarkerSymbolLayer.Line)
    dip_sym.setSize(10.0)           # shorter tick (bigger)
    dip_sym.setStrokeWidth(0.8)
    dip_sym.setColor(QColor(0, 0, 0))
    dip_sym.setStrokeColor(QColor(0, 0, 0))
    # Offset the tick toward the dip side
    dip_sym.setOffset(QPointF(0, -5.0))
    dip_sym.setDataDefinedProperty(
        _PROP_ANGLE,
        QgsProperty.fromField(dip_dir_col)
    )

    # --- Assemble the marker symbol ---
    marker = QgsMarkerSymbol()
    # Replace default sub-symbol with the strike bar
    marker.changeSymbolLayer(0, strike_sym)
    # Add the dip tick as a second sub-symbol
    marker.appendSymbolLayer(dip_sym.clone())

    renderer = QgsSingleSymbolRenderer(marker)
    layer.setRenderer(renderer)

    # --- Multi-line label: Strike / Dip Dir / Dip Amount ---
    # Build an expression that shows all three values
    label_parts = []
    label_parts.append(f"'S: ' || \"{strike_col}\" || '°'")
    label_parts.append(f"'DD: ' || \"{dip_dir_col}\" || '°'")
    if dip_amt_col:
        label_parts.append(f"'DA: ' || \"{dip_amt_col}\" || '°'")
    label_expr = " || '\\n' || ".join(label_parts)

    label_settings = QgsPalLayerSettings()
    label_settings.fieldName = label_expr
    label_settings.isExpression = True
    label_settings.enabled = True
    try:
        from qgis.core import Qgis
        label_settings.placement = Qgis.LabelPlacement.OverPoint
    except (AttributeError, ImportError):
        label_settings.placement = QgsPalLayerSettings.OverPoint

    text_format = QgsTextFormat()
    font = QFont('Arial', 7)
    font.setBold(True)
    text_format.setFont(font)
    text_format.setSize(7)
    text_format.setColor(QColor(30, 30, 30))

    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(1.2)
    buffer_settings.setColor(QColor(255, 255, 255, 200))
    text_format.setBuffer(buffer_settings)

    label_settings.setFormat(text_format)

    # Offset the label away from the symbol so it doesn't overlap
    label_settings.xOffset = 4.0
    label_settings.yOffset = 4.0

    labeling = QgsVectorLayerSimpleLabeling(label_settings)
    layer.setLabeling(labeling)
    layer.setLabelsEnabled(True)

    layer.triggerRepaint()


# ---------------------------------------------------------------------------
#  5. INTERVENTION SYMBOLOGY ENGINE
# ---------------------------------------------------------------------------

# Ordered list of (pattern, key) tuples — first match wins.
_INTERVENTION_PATTERNS = [
    (re.compile(r'\bRWHS\b|Rain\s*Water\s*Harvest', re.IGNORECASE), 'RWHS'),
    (re.compile(r'\bPPT\b|Percolation\s*Pit', re.IGNORECASE), 'PPT'),
    (re.compile(r'\bDRP\b|Dip\s*Recharge\s*Pit|Recharge\s*Pit', re.IGNORECASE), 'DRP'),
    (re.compile(r'\bCCT\b|Continuous\s*Contour', re.IGNORECASE), 'CCT'),
    (re.compile(r'\bCT\b|Counter\s*Trench|Contour\s*Trench', re.IGNORECASE), 'CT'),
    (re.compile(r'\bLBCD\b|Loose\s*Boulder|Check\s*Dam', re.IGNORECASE), 'LBCD'),
    (re.compile(r'\bGabion\b', re.IGNORECASE), 'GABION'),
    (re.compile(r'Recharge\s*Shaft', re.IGNORECASE), 'SHAFT'),
    (re.compile(r'Farm\s*Pond', re.IGNORECASE), 'POND'),
    (re.compile(r'Spring\s*Platform|Platform|\bNali\b', re.IGNORECASE), 'PLATFORM'),
    (re.compile(r'Plantation|Site\s*Clean', re.IGNORECASE), 'PLANTATION'),
]


def classify_intervention(activity_text):
    """Parse *activity_text* and return a primary category key.

    Uses regex / keyword matching against an ordered priority list.
    Returns one of: ``'PPT'``, ``'CT'``, ``'DRP'``, ``'RWHS'``, ``'LBCD'``,
    ``'CCT'``, ``'GABION'``, ``'SHAFT'``, ``'POND'``, ``'PLATFORM'``,
    ``'PLANTATION'``, or ``'OTHER'``.
    """
    if not activity_text or not isinstance(activity_text, str):
        return 'OTHER'
    for pattern, key in _INTERVENTION_PATTERNS:
        if pattern.search(activity_text):
            return key
    return 'OTHER'


def create_intervention_layer(rows, column_map, layer_name='Interventions'):
    """Create an in-memory ``QgsVectorLayer`` (Point, EPSG:4326) from
    intervention *rows*.

    An extra ``intervention_type`` attribute column is added with classified
    values derived from the activity-proposed text.

    Returns the ``QgsVectorLayer``.
    """
    lat_col = column_map.get('latitude')
    lon_col = column_map.get('longitude')
    if not lat_col or not lon_col:
        raise ValueError(
            "Cannot create intervention layer: latitude and/or longitude "
            "columns not detected."
        )

    if not rows:
        raise ValueError("No data rows to create intervention layer from.")

    field_names = list(rows[0].keys())

    uri = "Point?crs=epsg:4326"
    layer = QgsVectorLayer(uri, layer_name, "memory")
    provider = layer.dataProvider()

    # Add original fields + synthetic "intervention_type"
    qgs_fields = []
    for fn in field_names:
        qgs_fields.append(QgsField(fn, QVariant.String))
    qgs_fields.append(QgsField('intervention_type', QVariant.String))
    provider.addAttributes(qgs_fields)
    layer.updateFields()

    # Detect the "Activity Proposed" column
    activity_col = None
    for fn in field_names:
        if 'activity' in fn.lower() or 'proposed' in fn.lower():
            activity_col = fn
            break

    features = []
    for row in rows:
        lat = _safe_float(row.get(lat_col))
        lon = _safe_float(row.get(lon_col))
        if lat is None or lon is None:
            continue

        feat = QgsFeature(layer.fields())
        feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
        for fn in field_names:
            val = row.get(fn)
            feat.setAttribute(fn, str(val) if val is not None else '')

        # Classify
        activity_text = str(row.get(activity_col, '')) if activity_col else ''
        itype = classify_intervention(activity_text)
        feat.setAttribute('intervention_type', itype)
        features.append(feat)

    provider.addFeatures(features)
    layer.updateExtents()
    return layer


def _make_simple_marker(shape, size_mm, fill_color, stroke_color=QColor(0, 0, 0),
                        stroke_width=0.4):
    """Helper — create a ``QgsSimpleMarkerSymbolLayer`` with given parameters."""
    sym = QgsSimpleMarkerSymbolLayer()
    sym.setShape(shape)
    sym.setSize(size_mm)
    sym.setColor(fill_color)
    sym.setStrokeColor(stroke_color)
    sym.setStrokeWidth(stroke_width)
    return sym


def _marker_symbol_from_layer(sym_layer):
    """Wrap a single symbol layer in a ``QgsMarkerSymbol``."""
    marker = QgsMarkerSymbol()
    marker.changeSymbolLayer(0, sym_layer)
    return marker


def apply_intervention_styling(layer):
    """Apply categorised symbology to the intervention *layer* on the
    ``intervention_type`` field.

    Each category gets a distinct shape, size, and colour.
    """
    # Shape constants
    CIRCLE = QgsSimpleMarkerSymbolLayer.Circle
    TRIANGLE = QgsSimpleMarkerSymbolLayer.Triangle
    DIAMOND = QgsSimpleMarkerSymbolLayer.Diamond
    SQUARE = QgsSimpleMarkerSymbolLayer.Square
    STAR = QgsSimpleMarkerSymbolLayer.Star

    categories = []

    # PPT — brown circle
    sym_ppt = _marker_symbol_from_layer(
        _make_simple_marker(CIRCLE, 6.0, QColor('#8B4513'))
    )
    categories.append(QgsRendererCategory('PPT', sym_ppt, 'Percolation Pit'))

    # CT — forest-green triangle
    sym_ct = _marker_symbol_from_layer(
        _make_simple_marker(TRIANGLE, 6.0, QColor('#228B22'))
    )
    categories.append(QgsRendererCategory('CT', sym_ct, 'Counter Trench'))

    # DRP — royal-blue circle (larger)
    drp_outer = _make_simple_marker(CIRCLE, 7.0, QColor('#4169E1'))
    drp_inner = _make_simple_marker(CIRCLE, 4.0, QColor('#FFFFFF'),
                                    stroke_color=QColor('#4169E1'), stroke_width=0.6)
    sym_drp = QgsMarkerSymbol()
    sym_drp.changeSymbolLayer(0, drp_outer)
    sym_drp.appendSymbolLayer(drp_inner.clone())
    categories.append(QgsRendererCategory('DRP', sym_drp, 'Dip Recharge Pit'))

    # RWHS — dark-cyan diamond
    sym_rwhs = _marker_symbol_from_layer(
        _make_simple_marker(DIAMOND, 7.0, QColor('#00CED1'))
    )
    categories.append(QgsRendererCategory('RWHS', sym_rwhs, 'Rain Water Harvesting'))

    # LBCD — grey square with cross
    lbcd_base = _make_simple_marker(SQUARE, 7.0, QColor('#808080'))
    lbcd_cross = _make_simple_marker(QgsSimpleMarkerSymbolLayer.Cross, 5.0,
                                     QColor('#000000'), stroke_width=0.6)
    sym_lbcd = QgsMarkerSymbol()
    sym_lbcd.changeSymbolLayer(0, lbcd_base)
    sym_lbcd.appendSymbolLayer(lbcd_cross.clone())
    categories.append(QgsRendererCategory('LBCD', sym_lbcd, 'Loose Boulder Check Dam'))

    # CCT — dark-green triangle, dashed outline (we approximate with thinner stroke)
    cct_sym = _make_simple_marker(TRIANGLE, 6.0, QColor('#006400'),
                                  stroke_color=QColor(0, 0, 0), stroke_width=0.3)
    sym_cct = _marker_symbol_from_layer(cct_sym)
    categories.append(QgsRendererCategory('CCT', sym_cct, 'Continuous Contour Trench'))

    # GABION — dim-grey square with cross pattern
    gab_base = _make_simple_marker(SQUARE, 8.0, QColor('#696969'))
    gab_cross = _make_simple_marker(QgsSimpleMarkerSymbolLayer.Cross, 6.0,
                                    QColor('#000000'), stroke_width=0.5)
    sym_gab = QgsMarkerSymbol()
    sym_gab.changeSymbolLayer(0, gab_base)
    sym_gab.appendSymbolLayer(gab_cross.clone())
    categories.append(QgsRendererCategory('GABION', sym_gab, 'Gabion'))

    # SHAFT — medium-blue circle, thick outline
    shaft_sym = _make_simple_marker(CIRCLE, 8.0, QColor('#0000CD'),
                                    stroke_color=QColor(0, 0, 0), stroke_width=1.0)
    sym_shaft = _marker_symbol_from_layer(shaft_sym)
    categories.append(QgsRendererCategory('SHAFT', sym_shaft, 'Recharge Shaft'))

    # POND — dodger-blue circle, wide outline
    pond_sym = _make_simple_marker(CIRCLE, 9.0, QColor('#1E90FF'),
                                   stroke_color=QColor(0, 0, 0), stroke_width=1.2)
    sym_pond = _marker_symbol_from_layer(pond_sym)
    categories.append(QgsRendererCategory('POND', sym_pond, 'Farm Pond'))

    # PLATFORM — teal star
    plat_sym = _make_simple_marker(STAR, 8.0, QColor('#008080'))
    sym_plat = _marker_symbol_from_layer(plat_sym)
    categories.append(QgsRendererCategory('PLATFORM', sym_plat, 'Spring Platform'))

    # PLANTATION — lime-green triangle
    plant_sym = _make_simple_marker(TRIANGLE, 6.0, QColor('#32CD32'),
                                    stroke_color=QColor('#32CD32'))
    sym_plant = _marker_symbol_from_layer(plant_sym)
    categories.append(QgsRendererCategory('PLANTATION', sym_plant, 'Plantation'))

    # OTHER — light-grey circle
    other_sym = _make_simple_marker(CIRCLE, 5.0, QColor('#D3D3D3'))
    sym_other = _marker_symbol_from_layer(other_sym)
    categories.append(QgsRendererCategory('OTHER', sym_other, 'Other'))

    renderer = QgsCategorizedSymbolRenderer('intervention_type', categories)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


# ---------------------------------------------------------------------------
#  6. GSI CROSS-CHECK ENGINE
# ---------------------------------------------------------------------------

def verify_against_gsi(field_rows, column_map, gsi_baseline, threshold_deg=15.0):
    """Cross-reference field data against a GSI regional baseline.

    Parameters
    ----------
    field_rows : list[dict]
        Parsed survey rows.
    column_map : dict
        Semantic column mapping (from ``detect_columns``).
    gsi_baseline : dict
        ``{'strike': float, 'dip_direction': float, 'dip_amount': float (opt)}``.
    threshold_deg : float
        Maximum acceptable angular deviation in degrees.

    Returns
    -------
    list[dict]
        One entry per plane row (code in ``{'BP', 'FP', 'FR'}``), each
        containing deviation details and a ``flagged`` boolean.
    """
    code_col = column_map.get('code')
    strike_col = column_map.get('strike')
    dip_dir_col = column_map.get('dip_direction')
    lat_col = column_map.get('latitude')
    lon_col = column_map.get('longitude')
    sno_col = column_map.get('serial_no')

    if not strike_col or not dip_dir_col:
        raise ValueError(
            "Strike and/or dip-direction columns not detected — "
            "cannot perform GSI verification."
        )

    gsi_strike = float(gsi_baseline.get('strike', 0))
    gsi_dip_dir = float(gsi_baseline.get('dip_direction', 0))

    plane_codes = {'BP', 'FP', 'FR', 'bp', 'fp', 'fr',
                   'Bedding Plane', 'Folding Plane', 'Fracture'}

    results = []
    for idx, row in enumerate(field_rows):
        code_val = str(row.get(code_col, '')).strip() if code_col else ''
        if code_val not in plane_codes:
            continue

        field_strike = _safe_float(row.get(strike_col))
        field_dip_dir = _safe_float(row.get(dip_dir_col))
        if field_strike is None or field_dip_dir is None:
            continue

        strike_dev = angular_difference(field_strike, gsi_strike)
        dip_dir_dev = angular_difference(field_dip_dir, gsi_dip_dir)

        reasons = []
        flagged = False
        if strike_dev > threshold_deg:
            flagged = True
            reasons.append(
                f"Strike deviation {strike_dev:.1f}° > {threshold_deg:.1f}° threshold"
            )
        if dip_dir_dev > threshold_deg:
            flagged = True
            reasons.append(
                f"Dip-dir deviation {dip_dir_dev:.1f}° > {threshold_deg:.1f}° threshold"
            )

        lat_val = _safe_float(row.get(lat_col)) if lat_col else None
        lon_val = _safe_float(row.get(lon_col)) if lon_col else None
        sno_val = row.get(sno_col, idx) if sno_col else idx

        results.append({
            'row_index': idx,
            'serial_no': sno_val,
            'latitude': lat_val,
            'longitude': lon_val,
            'code': code_val,
            'field_strike': field_strike,
            'field_dip_dir': field_dip_dir,
            'gsi_strike': gsi_strike,
            'gsi_dip_dir': gsi_dip_dir,
            'strike_deviation': strike_dev,
            'dip_dir_deviation': dip_dir_dev,
            'flagged': flagged,
            'reason': '; '.join(reasons) if reasons else 'Within threshold',
        })

    return results


# ---------------------------------------------------------------------------
#  7. RECHARGE ZONE PREDICTION
# ---------------------------------------------------------------------------

def circular_mean(angles_deg):
    """Compute circular (vector) mean of *angles_deg* (in degrees).

    Returns a value in the range [0, 360).
    """
    if not angles_deg:
        return 0.0
    sin_sum = sum(math.sin(math.radians(a)) for a in angles_deg)
    cos_sum = sum(math.cos(math.radians(a)) for a in angles_deg)
    mean_rad = math.atan2(sin_sum, cos_sum)
    mean_deg = math.degrees(mean_rad) % 360.0
    return mean_deg


def angular_difference(a, b):
    """Compute smallest angular difference between two bearings (degrees).

    Returns a value in [0, 180].
    """
    diff = abs(float(a) - float(b)) % 360.0
    if diff > 180.0:
        diff = 360.0 - diff
    return diff


def compute_recharge_zones(survey_rows, column_map):
    """Compute predicted recharge-zone wedge polygons for each spring.

    Algorithm
    ---------
    1. Separate spring rows (``code == 'SP'`` or ``'Spring'``) from plane rows
       (``code in {'BP', 'FP', 'FR'}``).
    2. If no springs are found, use the centroid of all points as the spring
       location.
    3. Compute the circular mean of all plane dip-directions.
    4. Compute the arithmetic mean of all plane dip-amounts.
    5. ``up_dip_dir = (mean_dip_dir + 180) % 360``.
    6. Radius *R* (metres) scaled inversely by dip-amount:
       - dip > 45°: R = 150 m
       - 15° ≤ dip ≤ 45°: R = 150 + (45 − dip) × 15  → 150‥600 m
       - dip < 15°: R = 600 m
    7. For each spring, a 60° wedge (±30° around the up-dip vector) is drawn
       in UTM, then transformed back to EPSG:4326.
    8. The wedge has 18 arc segments for smooth curvature.

    Returns a list of result dicts.
    """
    code_col = column_map.get('code')
    lat_col = column_map.get('latitude')
    lon_col = column_map.get('longitude')
    dip_dir_col = column_map.get('dip_direction')
    dip_amt_col = column_map.get('dip_amount')

    if not lat_col or not lon_col:
        raise ValueError(
            "Latitude/longitude columns not detected — cannot compute "
            "recharge zones."
        )

    # ---- classify rows ----
    spring_codes = {'SP', 'sp', 'Spring', 'spring', 'DEP', 'dep',
                    'Depression Spring'}
    plane_codes = {'BP', 'FP', 'FR', 'bp', 'fp', 'fr',
                   'Bedding Plane', 'Folding Plane', 'Fracture'}

    springs = []
    planes = []
    all_lats = []
    all_lons = []

    for row in survey_rows:
        lat = _safe_float(row.get(lat_col))
        lon = _safe_float(row.get(lon_col))
        if lat is None or lon is None:
            continue
        all_lats.append(lat)
        all_lons.append(lon)

        code_val = str(row.get(code_col, '')).strip() if code_col else ''

        if code_val in spring_codes:
            springs.append({'lat': lat, 'lon': lon})
        if code_val in plane_codes:
            dip_dir = _safe_float(row.get(dip_dir_col)) if dip_dir_col else None
            dip_amt = _safe_float(row.get(dip_amt_col)) if dip_amt_col else None
            if dip_dir is not None:
                planes.append({'dip_dir': dip_dir, 'dip_amt': dip_amt})

    # Fallback: no springs → centroid
    if not springs and all_lats:
        centroid_lat = sum(all_lats) / len(all_lats)
        centroid_lon = sum(all_lons) / len(all_lons)
        springs.append({'lat': centroid_lat, 'lon': centroid_lon})

    if not springs:
        return []

    # ---- compute dip stats ----
    if planes:
        dip_dirs = [p['dip_dir'] for p in planes]
        dip_amts = [p['dip_amt'] for p in planes if p['dip_amt'] is not None]
        mean_dip_dir = circular_mean(dip_dirs)
        mean_dip_amt = sum(dip_amts) / len(dip_amts) if dip_amts else 30.0
    else:
        mean_dip_dir = 0.0
        mean_dip_amt = 30.0

    up_dip_dir = (mean_dip_dir + 180.0) % 360.0

    # ---- radius ----
    if mean_dip_amt > 45.0:
        radius_m = 150.0
    elif mean_dip_amt < 15.0:
        radius_m = 600.0
    else:
        radius_m = 150.0 + (45.0 - mean_dip_amt) * 15.0

    # ---- generate wedges ----
    results = []
    for sp in springs:
        try:
            wedge_coords = _generate_wedge(
                sp['lat'], sp['lon'], up_dip_dir, radius_m, half_angle=30.0,
                n_segments=18
            )
        except Exception:
            wedge_coords = []

        results.append({
            'spring_lat': sp['lat'],
            'spring_lon': sp['lon'],
            'mean_dip_dir': mean_dip_dir,
            'up_dip_dir': up_dip_dir,
            'mean_dip_amount': mean_dip_amt,
            'radius_m': radius_m,
            'polygon_coords': wedge_coords,
            'plane_count': len(planes),
        })

    return results


def _generate_wedge(lat, lon, center_bearing, radius_m, half_angle=30.0,
                    n_segments=18):
    """Generate a wedge (pie-slice) polygon in geographic coordinates.

    The wedge is centred on ``(lat, lon)``, fans out from
    ``center_bearing - half_angle`` to ``center_bearing + half_angle`` with
    a radius of ``radius_m``.

    Computation is done in UTM then transformed back to EPSG:4326.

    Returns a list of ``(lat, lon)`` tuples forming a closed polygon.
    """
    utm_epsg = get_utm_epsg(lon, lat)
    cx, cy = transform_point(lat, lon, 4326, utm_epsg)

    start_angle = center_bearing - half_angle
    end_angle = center_bearing + half_angle
    step = (end_angle - start_angle) / n_segments

    # Build polygon: apex -> arc points -> apex (closed)
    coords_utm = [(cx, cy)]  # apex at the spring
    for i in range(n_segments + 1):
        bearing_deg = start_angle + i * step
        bearing_rad = math.radians(bearing_deg)
        # In maths: north = +Y, east = +X; bearing clockwise from north
        dx = radius_m * math.sin(bearing_rad)
        dy = radius_m * math.cos(bearing_rad)
        coords_utm.append((cx + dx, cy + dy))
    coords_utm.append((cx, cy))  # close the polygon

    # Transform back to geographic
    coords_geo = []
    for (ux, uy) in coords_utm:
        try:
            lat_out, lon_out = transform_point_back(ux, uy, utm_epsg, 4326)
            coords_geo.append((lat_out, lon_out))
        except Exception:
            continue

    return coords_geo


def create_recharge_layer(zone_results, layer_name='Predicted_Recharge_Zones'):
    """Create a ``QgsVectorLayer`` (Polygon, EPSG:4326) from recharge-zone
    results.

    Styled with semi-transparent green fill (rgba 0, 180, 0, 80) and a red
    outline.

    Returns the ``QgsVectorLayer``.
    """
    uri = "Polygon?crs=epsg:4326"
    layer = QgsVectorLayer(uri, layer_name, "memory")
    provider = layer.dataProvider()

    fields = [
        QgsField('spring_lat', QVariant.Double),
        QgsField('spring_lon', QVariant.Double),
        QgsField('up_dip_dir', QVariant.Double),
        QgsField('mean_dip_amount', QVariant.Double),
        QgsField('radius_m', QVariant.Double),
        QgsField('plane_count', QVariant.Int),
    ]
    provider.addAttributes(fields)
    layer.updateFields()

    features = []
    for zr in zone_results:
        coords = zr.get('polygon_coords', [])
        if len(coords) < 4:
            continue  # need at least a triangle + closing vertex

        # QgsPointXY expects (x=lon, y=lat)
        ring = [QgsPointXY(lon, lat) for (lat, lon) in coords]
        polygon = QgsGeometry.fromPolygonXY([ring])

        feat = QgsFeature(layer.fields())
        feat.setGeometry(polygon)
        feat.setAttribute('spring_lat', zr['spring_lat'])
        feat.setAttribute('spring_lon', zr['spring_lon'])
        feat.setAttribute('up_dip_dir', zr['up_dip_dir'])
        feat.setAttribute('mean_dip_amount', zr['mean_dip_amount'])
        feat.setAttribute('radius_m', zr['radius_m'])
        feat.setAttribute('plane_count', zr['plane_count'])
        features.append(feat)

    provider.addFeatures(features)
    layer.updateExtents()

    # ---- Style: semi-transparent green fill, red outline ----
    fill_sym = QgsSimpleFillSymbolLayer()
    fill_sym.setColor(QColor(0, 180, 0, 80))
    fill_sym.setStrokeColor(QColor(255, 0, 0))
    fill_sym.setStrokeWidth(0.6)

    fill_symbol = QgsFillSymbol()
    fill_symbol.changeSymbolLayer(0, fill_sym)

    renderer = QgsSingleSymbolRenderer(fill_symbol)
    layer.setRenderer(renderer)
    layer.triggerRepaint()

    return layer


# ---------------------------------------------------------------------------
#  8. KML INTERVENTION LOADING
# ---------------------------------------------------------------------------

def load_kml_as_layer(file_path, layer_name='KML_Interventions'):
    """Load a KML file as a QGIS vector layer via the OGR provider.

    Returns the ``QgsVectorLayer`` or ``None`` on failure.
    """
    if not os.path.isfile(file_path):
        return None

    try:
        layer = QgsVectorLayer(file_path, layer_name, 'ogr')
        if not layer.isValid():
            return None
        return layer
    except Exception:
        return None


# ---------------------------------------------------------------------------
#  9. GOOGLE EARTH KML EXPORT
# ---------------------------------------------------------------------------

def export_structural_kml(rows, column_map, output_path):
    """Export structural survey data to a Google Earth KML file with
    T-symbol line geometries and labelled placemarks.

    For each survey point, the KML contains:
    * A **Point placemark** with an info balloon showing all attributes
      and a label displaying Strike / Dip Dir / Dip Amount.
    * A **strike-bar LineString** — a short line centred on the point,
      oriented along the strike azimuth.
    * A **dip-tick LineString** — a shorter line from the point toward
      the dip direction.

    Features are colour-coded:
    * Springs (SP) — cyan
    * Bedding Plane (BP) — red
    * Fracture (FR) — orange
    * Folding Plane (FP) — purple
    * Other — white

    Parameters
    ----------
    rows : list[dict]
        Parsed survey rows.
    column_map : dict
        Semantic column mapping from ``detect_columns``.
    output_path : str
        Destination ``.kml`` file path.

    Returns
    -------
    int
        Number of placemarks written.
    """
    lat_col = column_map.get('latitude')
    lon_col = column_map.get('longitude')
    strike_col = column_map.get('strike')
    dip_dir_col = column_map.get('dip_direction')
    dip_amt_col = column_map.get('dip_amount')
    code_col = column_map.get('code')
    elev_col = column_map.get('elevation')
    rock_col = column_map.get('rock_type')
    remarks_col = column_map.get('remarks')
    village_col = column_map.get('village')
    serial_col = column_map.get('serial_no')

    if not lat_col or not lon_col:
        raise ValueError("Latitude/longitude columns not detected.")

    # --- Colour map (ABGR format for KML) ---
    CODE_COLORS = {
        'SP': 'ffffff00',   # cyan
        'BP': 'ff0000ff',   # red
        'FR': 'ff00a5ff',   # orange
        'FP': 'ffff00ff',   # purple
        'DP': 'ff00ff00',   # green
    }
    DEFAULT_COLOR = 'ffffffff'  # white

    # T-symbol arm length in degrees (approx 30m at equator ≈ 0.00027°)
    STRIKE_HALF_LEN = 0.00035   # half-length of strike bar
    DIP_TICK_LEN = 0.00020      # length of dip tick

    def _bearing_endpoint(lat, lon, bearing_deg, distance_deg):
        """Approximate endpoint given a bearing and distance in degrees."""
        bearing_rad = math.radians(bearing_deg)
        dlat = distance_deg * math.cos(bearing_rad)
        dlon = distance_deg * math.sin(bearing_rad) / max(math.cos(math.radians(lat)), 0.01)
        return (lat + dlat, lon + dlon)

    # ---- Build KML ----
    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<kml xmlns="http://www.opengis.net/kml/2.2">')
    lines.append('<Document>')
    lines.append('  <name>CHIRAG-Springs Structural Survey</name>')
    lines.append('  <description>Exported from CHIRAG-Springs QGIS Plugin</description>')

    # ---- Styles ----
    for code, color in CODE_COLORS.items():
        lines.append(f'  <Style id="style_{code}">')
        lines.append(f'    <IconStyle>')
        lines.append(f'      <color>{color}</color>')
        lines.append(f'      <scale>1.0</scale>')
        lines.append(f'      <Icon><href>http://maps.google.com/mapfiles/kml/shapes/target.png</href></Icon>')
        lines.append(f'    </IconStyle>')
        lines.append(f'    <LabelStyle>')
        lines.append(f'      <color>{color}</color>')
        lines.append(f'      <scale>0.8</scale>')
        lines.append(f'    </LabelStyle>')
        lines.append(f'  </Style>')

    lines.append(f'  <Style id="style_DEFAULT">')
    lines.append(f'    <IconStyle>')
    lines.append(f'      <color>{DEFAULT_COLOR}</color>')
    lines.append(f'      <scale>0.9</scale>')
    lines.append(f'      <Icon><href>http://maps.google.com/mapfiles/kml/shapes/target.png</href></Icon>')
    lines.append(f'    </IconStyle>')
    lines.append(f'    <LabelStyle><color>{DEFAULT_COLOR}</color><scale>0.7</scale></LabelStyle>')
    lines.append(f'  </Style>')

    # Line styles for T-symbol arms
    for code, color in CODE_COLORS.items():
        lines.append(f'  <Style id="line_{code}">')
        lines.append(f'    <LineStyle><color>{color}</color><width>3</width></LineStyle>')
        lines.append(f'  </Style>')
    lines.append(f'  <Style id="line_DEFAULT">')
    lines.append(f'    <LineStyle><color>{DEFAULT_COLOR}</color><width>2</width></LineStyle>')
    lines.append(f'  </Style>')

    # ---- Folders ----
    lines.append('  <Folder>')
    lines.append('    <name>Survey Points</name>')
    lines.append('    <open>1</open>')

    count = 0
    for idx, row in enumerate(rows):
        lat = _safe_float(row.get(lat_col))
        lon = _safe_float(row.get(lon_col))
        if lat is None or lon is None:
            continue

        strike = _safe_float(row.get(strike_col)) if strike_col else None
        dip_dir = _safe_float(row.get(dip_dir_col)) if dip_dir_col else None
        dip_amt = _safe_float(row.get(dip_amt_col)) if dip_amt_col else None
        elev = _safe_float(row.get(elev_col)) if elev_col else 0
        code_val = str(row.get(code_col, '')).strip().upper() if code_col else ''
        rock = str(row.get(rock_col, '')) if rock_col else ''
        remark = str(row.get(remarks_col, '')) if remarks_col else ''
        village = str(row.get(village_col, '')) if village_col else ''
        sno = str(row.get(serial_col, idx + 1)) if serial_col else str(idx + 1)

        style_id = code_val if code_val in CODE_COLORS else 'DEFAULT'

        # Label text
        label_parts = [f"#{sno}"]
        if strike is not None:
            label_parts.append(f"S:{strike:.0f}°")
        if dip_dir is not None:
            label_parts.append(f"DD:{dip_dir:.0f}°")
        if dip_amt is not None:
            label_parts.append(f"DA:{dip_amt:.0f}°")
        label_text = ' '.join(label_parts)

        # Description HTML
        desc_lines = ['<![CDATA[<table style="font-family:Arial;font-size:12px;">']
        desc_lines.append(f'<tr><td><b>Serial No</b></td><td>{sno}</td></tr>')
        desc_lines.append(f'<tr><td><b>Code</b></td><td>{code_val}</td></tr>')
        if village:
            desc_lines.append(f'<tr><td><b>Village</b></td><td>{village}</td></tr>')
        desc_lines.append(f'<tr><td><b>Latitude</b></td><td>{lat:.6f}</td></tr>')
        desc_lines.append(f'<tr><td><b>Longitude</b></td><td>{lon:.6f}</td></tr>')
        if elev:
            desc_lines.append(f'<tr><td><b>Elevation (m)</b></td><td>{elev:.1f}</td></tr>')
        if strike is not None:
            desc_lines.append(f'<tr><td><b>Strike (°)</b></td><td>{strike:.2f}</td></tr>')
        if dip_dir is not None:
            desc_lines.append(f'<tr><td><b>Dip Direction (°)</b></td><td>{dip_dir:.2f}</td></tr>')
        if dip_amt is not None:
            desc_lines.append(f'<tr><td><b>Dip Amount (°)</b></td><td>{dip_amt:.2f}</td></tr>')
        if rock:
            desc_lines.append(f'<tr><td><b>Rock Type</b></td><td>{rock}</td></tr>')
        if remark:
            desc_lines.append(f'<tr><td><b>Remarks</b></td><td>{remark}</td></tr>')
        desc_lines.append('</table>]]>')
        description = '\n'.join(desc_lines)

        # ---- Point Placemark ----
        lines.append('    <Placemark>')
        lines.append(f'      <name>{_xml_escape(label_text)}</name>')
        lines.append(f'      <description>{description}</description>')
        lines.append(f'      <styleUrl>#style_{style_id}</styleUrl>')
        lines.append(f'      <Point><coordinates>{lon},{lat},{elev or 0}</coordinates></Point>')
        lines.append('    </Placemark>')

        # ---- Strike Bar LineString ----
        if strike is not None:
            p1 = _bearing_endpoint(lat, lon, strike, STRIKE_HALF_LEN)
            p2 = _bearing_endpoint(lat, lon, (strike + 180) % 360, STRIKE_HALF_LEN)
            lines.append('    <Placemark>')
            lines.append(f'      <name>Strike {strike:.0f}°</name>')
            lines.append(f'      <styleUrl>#line_{style_id}</styleUrl>')
            lines.append(f'      <LineString><tessellate>1</tessellate><coordinates>')
            lines.append(f'        {p1[1]:.8f},{p1[0]:.8f},0 {p2[1]:.8f},{p2[0]:.8f},0')
            lines.append(f'      </coordinates></LineString>')
            lines.append('    </Placemark>')

        # ---- Dip Tick LineString ----
        if dip_dir is not None:
            dip_end = _bearing_endpoint(lat, lon, dip_dir, DIP_TICK_LEN)
            lines.append('    <Placemark>')
            lines.append(f'      <name>Dip {dip_amt:.0f}° → {dip_dir:.0f}°</name>' if dip_amt else f'      <name>Dip → {dip_dir:.0f}°</name>')
            lines.append(f'      <styleUrl>#line_{style_id}</styleUrl>')
            lines.append(f'      <LineString><tessellate>1</tessellate><coordinates>')
            lines.append(f'        {lon:.8f},{lat:.8f},0 {dip_end[1]:.8f},{dip_end[0]:.8f},0')
            lines.append(f'      </coordinates></LineString>')
            lines.append('    </Placemark>')

        count += 1

    lines.append('  </Folder>')
    lines.append('</Document>')
    lines.append('</kml>')

    kml_text = '\n'.join(lines)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(kml_text)

    return count


def _xml_escape(text):
    """Escape XML special characters in *text*."""
    if not text:
        return ''
    return (str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;'))


# ---------------------------------------------------------------------------
#  INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _safe_float(value):
    """Attempt to convert *value* to ``float``.  Returns ``None`` on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
