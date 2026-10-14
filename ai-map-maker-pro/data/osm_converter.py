"""
OpenStreetMap → GeoJSON converter.

This fixes bug 3.8 from the handoff doc. The prior converter only handled
'node' and 'way' element types and silently dropped 'relation' elements,
which is why forests, rivers and admin boundaries showed 0 features.

This version:
- Handles relations (multipolygons) by assembling member ways into
  MultiPolygon geometries using the 'outer' / 'inner' roles.
- Handles relations of type 'route' / 'waterway' by producing MultiLineStrings.
- Uses `out geom;` output from Overpass, which embeds full coordinate lists
  in each way/relation member, so we don't need to resolve node IDs.
- Preserves all tags as feature properties.
"""
from __future__ import annotations

from typing import Dict, List, Optional


def overpass_to_geojson(overpass_json: Dict) -> Dict:
    """
    Convert the JSON response from Overpass API (with `out geom;`) into
    a GeoJSON FeatureCollection.
    """
    elements = overpass_json.get("elements", []) or []
    features: List[Dict] = []

    for el in elements:
        typ = el.get("type")
        if typ == "node":
            feat = _node_to_feature(el)
        elif typ == "way":
            feat = _way_to_feature(el)
        elif typ == "relation":
            feat = _relation_to_feature(el)
        else:
            feat = None

        if feat is not None:
            features.append(feat)

    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# Element converters
# ---------------------------------------------------------------------------

def _node_to_feature(node: Dict) -> Optional[Dict]:
    lat = node.get("lat")
    lon = node.get("lon")
    if lat is None or lon is None:
        return None
    return {
        "type": "Feature",
        "id": f"node/{node.get('id')}",
        "properties": _props(node),
        "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
    }


def _way_to_feature(way: Dict) -> Optional[Dict]:
    geom_nodes = way.get("geometry", []) or []
    if len(geom_nodes) < 2:
        return None
    coords = [[float(p["lon"]), float(p["lat"])] for p in geom_nodes
              if p.get("lat") is not None and p.get("lon") is not None]
    if len(coords) < 2:
        return None

    # A way is a polygon when it's closed AND tagged as an area.
    is_closed = coords[0] == coords[-1]
    tags = way.get("tags", {}) or {}
    area_tags = {"building", "landuse", "natural", "leisure", "amenity"}
    is_area = is_closed and (any(t in tags for t in area_tags) or tags.get("area") == "yes")

    if is_area:
        geometry = {"type": "Polygon", "coordinates": [coords]}
    else:
        geometry = {"type": "LineString", "coordinates": coords}

    return {
        "type": "Feature",
        "id": f"way/{way.get('id')}",
        "properties": _props(way),
        "geometry": geometry,
    }


def _relation_to_feature(rel: Dict) -> Optional[Dict]:
    """
    Convert an OSM relation into a (Multi)Polygon or MultiLineString feature.

    Multipolygon relations (type=multipolygon or type=boundary) -> MultiPolygon
    Route/waterway relations -> MultiLineString
    Other relations -> GeometryCollection or skipped
    """
    tags = rel.get("tags", {}) or {}
    rel_type = tags.get("type", "")
    members = rel.get("members", []) or []

    if rel_type in ("multipolygon", "boundary"):
        polys = _assemble_multipolygon(members)
        if not polys:
            return None
        if len(polys) == 1:
            geometry = {"type": "Polygon", "coordinates": polys[0]}
        else:
            geometry = {"type": "MultiPolygon", "coordinates": [[r] if isinstance(r[0][0], (int, float))
                                                                   else r for r in polys]}
            # Actually normalize: each polygon is a list of rings
            geometry = {"type": "MultiPolygon",
                        "coordinates": [list(p) for p in polys]}
    elif rel_type in ("route", "waterway"):
        lines = _assemble_multilinestring(members)
        if not lines:
            return None
        geometry = {"type": "MultiLineString", "coordinates": lines}
    else:
        # Unknown relation type — try multipolygon heuristic, else skip
        polys = _assemble_multipolygon(members)
        if polys:
            geometry = {"type": "MultiPolygon", "coordinates": [list(p) for p in polys]}
        else:
            return None

    return {
        "type": "Feature",
        "id": f"relation/{rel.get('id')}",
        "properties": _props(rel),
        "geometry": geometry,
    }


# ---------------------------------------------------------------------------
# Geometry assembly
# ---------------------------------------------------------------------------

def _assemble_multipolygon(members: List[Dict]) -> List[List[List[List[float]]]]:
    """
    From relation members, build a list of polygons.
    Each polygon is [outer_ring, inner_ring1, inner_ring2, ...].
    Rings are lists of [lon, lat] pairs.

    Ways are stitched together where their endpoints meet, so fragmented
    outer rings still form a closed loop.
    """
    outer_ways, inner_ways = [], []
    for m in members:
        if m.get("type") != "way":
            continue
        geom = m.get("geometry", []) or []
        if len(geom) < 2:
            continue
        coords = [[float(p["lon"]), float(p["lat"])] for p in geom
                  if p.get("lat") is not None and p.get("lon") is not None]
        if len(coords) < 2:
            continue
        role = m.get("role", "outer")
        if role == "inner":
            inner_ways.append(coords)
        else:
            outer_ways.append(coords)

    outer_rings = _stitch_rings(outer_ways)
    inner_rings = _stitch_rings(inner_ways)

    if not outer_rings:
        return []

    # Every outer ring is a polygon; all inner rings go into the first
    # outer ring that contains them (cheap test: shared bbox overlap).
    polys: List[List[List[List[float]]]] = []
    for outer in outer_rings:
        rings = [outer]
        for inner in inner_rings:
            if _bbox_contains(_ring_bbox(outer), _ring_bbox(inner)):
                rings.append(inner)
        polys.append(rings)
    return polys


def _assemble_multilinestring(members: List[Dict]) -> List[List[List[float]]]:
    """Extract all way geometries from relation members as line strings."""
    lines = []
    for m in members:
        if m.get("type") != "way":
            continue
        geom = m.get("geometry", []) or []
        coords = [[float(p["lon"]), float(p["lat"])] for p in geom
                  if p.get("lat") is not None and p.get("lon") is not None]
        if len(coords) >= 2:
            lines.append(coords)
    return lines


def _stitch_rings(ways: List[List[List[float]]]) -> List[List[List[float]]]:
    """
    Greedy way-stitching: repeatedly find a way whose endpoint matches
    the current ring's end, and append it. When the ring closes, start a new one.
    """
    if not ways:
        return []
    remaining = [list(w) for w in ways]
    rings = []

    while remaining:
        current = remaining.pop(0)
        while current[0] != current[-1] and remaining:
            end = current[-1]
            best_idx, reverse = None, False
            for i, w in enumerate(remaining):
                if w[0] == end:
                    best_idx, reverse = i, False
                    break
                if w[-1] == end:
                    best_idx, reverse = i, True
                    break
            if best_idx is None:
                break  # open ring (dangling) — keep as-is
            nxt = remaining.pop(best_idx)
            if reverse:
                nxt = nxt[::-1]
            current.extend(nxt[1:])  # skip shared endpoint

        if len(current) >= 4 and current[0] == current[-1]:
            rings.append(current)
        elif len(current) >= 3:
            # Force-close ring that almost closes
            current.append(current[0])
            rings.append(current)
    return rings


def _ring_bbox(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_contains(outer, inner):
    return (outer[0] <= inner[0] and outer[1] <= inner[1]
            and outer[2] >= inner[2] and outer[3] >= inner[3])


def _props(el: Dict) -> Dict:
    """Combine tags + OSM id/type as GeoJSON properties."""
    tags = el.get("tags", {}) or {}
    props = dict(tags)
    props["@id"] = el.get("id")
    props["@type"] = el.get("type")
    return props
