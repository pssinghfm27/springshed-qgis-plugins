"""Simple filesystem cache under the system temp folder."""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from typing import Optional


def cache_dir() -> str:
    d = os.path.join(tempfile.gettempdir(), "claude_qgis_pro_cache")
    os.makedirs(d, exist_ok=True)
    return d


def cache_path(namespace: str, key: str, suffix: str = "") -> str:
    """Deterministic path inside the cache dir for a given (namespace, key)."""
    h = hashlib.sha1(f"{namespace}::{key}".encode("utf-8")).hexdigest()[:20]
    ns_dir = os.path.join(cache_dir(), namespace)
    os.makedirs(ns_dir, exist_ok=True)
    return os.path.join(ns_dir, h + suffix)


def is_fresh(path: str, ttl_seconds: int = 86400 * 7) -> bool:
    """Check if a cached file exists and is less than ttl_seconds old."""
    if not os.path.exists(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age < ttl_seconds


def read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def write_text(path: str, data: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)


def write_bytes(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def clear(namespace: Optional[str] = None) -> int:
    """Remove cached files. Returns count deleted."""
    import shutil
    target = os.path.join(cache_dir(), namespace) if namespace else cache_dir()
    if not os.path.isdir(target):
        return 0
    count = sum(1 for _, _, files in os.walk(target) for _ in files)
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, exist_ok=True)
    return count
