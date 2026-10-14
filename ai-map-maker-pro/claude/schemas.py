"""
Lightweight validation for Claude-returned JSON. We don't use pydantic
(avoiding extra deps for a QGIS plugin) — these are hand-rolled validators
that normalize loose input into canonical plan dicts.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple


_VALID_SOURCES = {"osm", "natural_earth", "aws_terrain", "opentopo_dem", "xyz_basemap"}
_VALID_RENDERS = {"single", "categorized", "graduated", "dem_hillshade"}


class PlanError(ValueError):
    """Raised when a Claude response cannot be parsed into a valid plan."""


def _strip_code_fences(text: str) -> str:
    """Remove ```json fences if Claude added them despite instructions."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text


def _extract_json_object(text: str) -> str:
    """Pull the first balanced {...} block out of mixed text."""
    text = _strip_code_fences(text)
    start = text.find("{")
    if start < 0:
        raise PlanError("No JSON object found in response.")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise PlanError("Unbalanced JSON in response.")


def parse_map_plan(response_text: str) -> Dict[str, Any]:
    """Parse + normalize a Claude map-plan response."""
    raw = _extract_json_object(response_text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise PlanError(f"Invalid JSON: {e}") from e

    if not isinstance(data, dict):
        raise PlanError("Plan root must be an object.")

    plan = {
        "location":  _get_str(data, "location", required=True),
        "map_title": _get_str(data, "map_title", default="Untitled Map"),
        "notes":     _get_str(data, "notes", default=""),
        "layers":    _normalize_layers(data.get("layers")),
    }
    if not plan["layers"]:
        raise PlanError("Plan has no layers.")
    return plan


def parse_scan_result(response_text: str) -> Dict[str, Any]:
    """Parse the JSON returned by the vision scanner."""
    raw = _extract_json_object(response_text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise PlanError(f"Invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise PlanError("Scan result must be an object.")

    result = {
        "map_type":  _get_str(data, "map_type", default="other"),
        "title":     data.get("title"),
        "location":  data.get("location"),
        "approximate_bbox": data.get("approximate_bbox"),
        "bbox_confidence":  _get_str(data, "bbox_confidence", default="unknown"),
        "projection_guess": data.get("projection_guess"),
        "legend":            _as_list(data.get("legend")),
        "visible_features":  _as_list(data.get("visible_features")),
        "ground_control_points": _normalize_gcps(data.get("ground_control_points")),
        "strike_dip_observations": _normalize_strike_dips(data.get("strike_dip_observations")),
        "recreate_plan": None,
    }
    rp = data.get("recreate_plan")
    if isinstance(rp, dict) and rp.get("layers"):
        result["recreate_plan"] = {
            "location": result["location"] or "Scanned map",
            "map_title": _get_str(rp, "map_title", default=result["title"] or "Recreated Map"),
            "notes":     _get_str(rp, "notes", default=""),
            "layers":    _normalize_layers(rp.get("layers")),
        }
    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _get_str(d: dict, key: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    v = d.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise PlanError(f"Missing required field: {key}")
        return default
    return str(v).strip()


def _as_list(v) -> List:
    return v if isinstance(v, list) else []


def _normalize_layers(raw) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        src = str(item.get("source", "")).lower()
        if src not in _VALID_SOURCES:
            continue
        layer = {
            "id":            str(item.get("id") or f"layer_{i}"),
            "name":          str(item.get("name") or f"Layer {i+1}"),
            "source":        src,
            "osm_type":      item.get("osm_type"),
            "osm_filter":    item.get("osm_filter") if isinstance(item.get("osm_filter"), dict) else None,
            "ne_layer":      item.get("ne_layer"),
            "xyz_provider":  item.get("xyz_provider"),
            "render_type":   item.get("render_type", "single")
                             if item.get("render_type") in _VALID_RENDERS else "single",
            "default_color": _norm_color(item.get("default_color"), "#5B8DEF"),
            "stroke_color":  _norm_color(item.get("stroke_color"), None),
            "stroke_width":  _norm_float(item.get("stroke_width"), 0.6),
            "point_size":    _norm_float(item.get("point_size"), 3.0),
            "opacity":       max(0.0, min(1.0, _norm_float(item.get("opacity"), 1.0))),
            "label_field":   item.get("label_field"),
            "z_order":       int(item.get("z_order", i + 1)),
            "categories":    _as_list(item.get("categories")),
        }
        out.append(layer)
    out.sort(key=lambda x: x["z_order"])
    return out


def _normalize_gcps(raw) -> List[Dict[str, Any]]:
    gcps = []
    for p in _as_list(raw):
        if not isinstance(p, dict):
            continue
        try:
            gcps.append({
                "pixel_x_pct": float(p["pixel_x_pct"]),
                "pixel_y_pct": float(p["pixel_y_pct"]),
                "lat": float(p["lat"]),
                "lon": float(p["lon"]),
                "label": str(p.get("label", "")),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return gcps


def _normalize_strike_dips(raw) -> List[Dict[str, Any]]:
    """
    Normalize strike/dip observations extracted by Claude Vision from a
    geological map or survey document. Skips any entry without the three
    core numeric fields.
    """
    out = []
    for p in _as_list(raw):
        if not isinstance(p, dict):
            continue
        try:
            out.append({
                "lat":             float(p["lat"]),
                "lon":             float(p["lon"]),
                "strike_deg":      float(p["strike_deg"]) % 360,
                "dip_dir_deg":     float(p["dip_dir_deg"]) % 360,
                "dip_amount_deg":  max(0.0, min(90.0, float(p["dip_amount_deg"]))),
                "rock_type":       (p.get("rock_type") or None),
                "notes":           (p.get("notes") or None),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _norm_color(v, default):
    if not v:
        return default
    s = str(v).strip()
    if _HEX_RE.match(s):
        if not s.startswith("#"):
            s = "#" + s
        # Expand #abc -> #aabbcc
        if len(s) == 4:
            s = "#" + "".join(c * 2 for c in s[1:])
        return s.upper()
    return default


def _norm_float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
