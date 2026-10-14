"""QSettings-backed persistent settings for the plugin."""
from __future__ import annotations

from qgis.PyQt.QtCore import QSettings


_PREFIX = "ClaudeMapperPro/"


DEFAULT_MODEL = "claude-sonnet-4-6"
AVAILABLE_MODELS = [
    ("claude-haiku-4-5",  "Haiku 4.5 — fastest, cheapest (simple maps)"),
    ("claude-sonnet-4-6", "Sonnet 4.6 — balanced (recommended default)"),
    ("claude-opus-4-7",   "Opus 4.7 — highest quality (complex maps, vision)"),
    ("claude-opus-4-6",   "Opus 4.6 — previous flagship (fallback)"),
]

# Vision tasks benefit from Opus quality; defaulting high pays off.
DEFAULT_VISION_MODEL = "claude-opus-4-7"


def set_value(key: str, value) -> None:
    QSettings().setValue(_PREFIX + key, value)


def get_value(key: str, default=None, type_=None):
    s = QSettings()
    full = _PREFIX + key
    if type_ is not None:
        return s.value(full, default, type=type_)
    return s.value(full, default)


def get_api_key() -> str:
    return get_value("api_key", "", type_=str) or ""


def set_api_key(key: str) -> None:
    set_value("api_key", key.strip())


def get_model() -> str:
    return get_value("model", DEFAULT_MODEL, type_=str) or DEFAULT_MODEL


def set_model(model: str) -> None:
    set_value("model", model)


def get_vision_model() -> str:
    return get_value("vision_model", DEFAULT_VISION_MODEL, type_=str) or DEFAULT_VISION_MODEL


def set_vision_model(model: str) -> None:
    set_value("vision_model", model)


def get_bool(key: str, default: bool = True) -> bool:
    val = get_value(key, default)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes")
    return bool(val)


def get_int(key: str, default: int) -> int:
    try:
        return int(get_value(key, default))
    except (TypeError, ValueError):
        return default


# Feature toggles — exposed in settings dialog
USE_PROMPT_CACHING = "use_prompt_caching"      # default True
AUTO_FALLBACK = "auto_bbox_fallback"            # retry with expanded bbox
OVERPASS_TIMEOUT = "overpass_timeout"           # seconds, default 120
CACHE_DOWNLOADS = "cache_downloads"             # cache Natural Earth, tiles
