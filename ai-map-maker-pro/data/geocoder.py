"""
OpenStreetMap Nominatim geocoder.

- Caches results on disk for 30 days (addresses rarely move).
- Respects the 1 req/sec rate limit via a tiny module-level throttle.
- Returns a normalized bbox + display name.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Dict, Optional

from ..utils import bbox as bboxlib, cache, logging as log

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "ClaudeMapperPro/2.0 (QGIS plugin)"

_last_call = 0.0
_lock = threading.Lock()


def geocode(query: str, country: Optional[str] = None,
            use_cache: bool = True) -> Dict:
    """
    Resolve a place name to a bbox and display name.

    Returns
    -------
    {
      "query": str,
      "display_name": str,
      "bbox": {"south": ..., "west": ..., "north": ..., "east": ...},
      "lat": float,
      "lon": float,
      "osm_type": str,
      "importance": float,
    }

    Raises
    ------
    RuntimeError if no result.
    """
    query = (query or "").strip()
    if not query:
        raise ValueError("Empty geocoding query")

    cache_key = f"{query}|{country or ''}"
    cache_file = cache.cache_path("nominatim", cache_key, ".json")

    if use_cache and cache.is_fresh(cache_file, ttl_seconds=86400 * 30):
        raw = cache.read_text(cache_file)
        if raw:
            try:
                log.info(f"Nominatim cache hit for '{query}'")
                return json.loads(raw)
            except json.JSONDecodeError:
                pass  # fall through to live call

    params = {
        "q": query,
        "format": "json",
        "addressdetails": "1",
        "limit": "1",
    }
    if country:
        params["countrycodes"] = country

    url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"

    _throttle()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        results = json.loads(resp.read().decode("utf-8"))

    if not results:
        raise RuntimeError(f"Nominatim found no match for: {query}")

    r = results[0]
    # Nominatim bbox is [south, north, west, east] as strings
    bb = bboxlib.normalize(r["boundingbox"])
    payload = {
        "query": query,
        "display_name": r.get("display_name", query),
        "bbox": bb,
        "lat": float(r["lat"]),
        "lon": float(r["lon"]),
        "osm_type": r.get("osm_type", ""),
        "importance": float(r.get("importance", 0.0)),
    }

    if use_cache:
        cache.write_text(cache_file, json.dumps(payload))
    log.info(f"Geocoded '{query}' -> {payload['display_name']}")
    return payload


def _throttle(min_interval: float = 1.1) -> None:
    """Block until at least min_interval seconds have passed since last call."""
    global _last_call
    with _lock:
        now = time.time()
        wait = min_interval - (now - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
