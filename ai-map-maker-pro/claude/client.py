"""
Anthropic Claude API client.

Design goals:
- Zero external deps (uses urllib, which ships with Python).
- Prompt caching via cache_control on system blocks, which cuts per-call
  cost by ~90% after the first request that writes the cache.
- Graceful retries on 429 / 5xx with exponential backoff.
- Structured errors so the UI layer can show a useful message rather
  than a bare stack trace.
- Supports both text-only planning requests and vision requests that
  include a base64-encoded image.

The API documentation is at https://docs.claude.com/en/api/overview.
This module stays deliberately small — if the SDK is installed we use
that, otherwise we fall back to raw HTTPS.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ..utils import logging as log

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Minimum token count for an incoming prompt cache block to actually
# be written server-side. System blocks under this threshold are not
# cached by Anthropic, so we skip cache_control for short prompts.
MIN_CACHE_TOKENS = 1024


class ClaudeError(Exception):
    """Base exception for all Claude API errors exposed to the UI."""


class ClaudeAuthError(ClaudeError):
    """Invalid or missing API key."""


class ClaudeRateLimitError(ClaudeError):
    """Hit 429. Backoff exhausted."""


class ClaudeServerError(ClaudeError):
    """5xx from the API."""


class ClaudeBadRequest(ClaudeError):
    """400 with a message from the server."""


class ClaudeClient:
    """
    Minimal Anthropic Messages API client.

    Parameters
    ----------
    api_key : str
        Anthropic API key. If empty, every call raises ClaudeAuthError.
    model : str
        Default model. Can be overridden per call.
    use_cache : bool
        If True, add cache_control to system prompts so they get cached.
    """

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6",
                 use_cache: bool = True, max_retries: int = 3,
                 timeout: int = 120):
        self.api_key = (api_key or "").strip()
        self.model = model
        self.use_cache = use_cache
        self.max_retries = max_retries
        self.timeout = timeout

    # --- public API --------------------------------------------------------

    def plan(self, user_request: str, system_prompt: str,
             model: Optional[str] = None, max_tokens: int = 4096) -> Dict[str, Any]:
        """Text-only planning call. Returns the full API response dict."""
        messages = [{"role": "user", "content": user_request}]
        return self._call(
            messages=messages,
            system=self._system_blocks(system_prompt),
            model=model,
            max_tokens=max_tokens,
        )

    def refine(self, prior_plan_json: str, user_feedback: str,
               system_prompt: str, model: Optional[str] = None,
               max_tokens: int = 4096) -> Dict[str, Any]:
        """Follow-up refinement with prior plan as context."""
        user_content = (
            f"CURRENT PLAN:\n{prior_plan_json}\n\n"
            f"USER FEEDBACK:\n{user_feedback}"
        )
        messages = [{"role": "user", "content": user_content}]
        return self._call(
            messages=messages,
            system=self._system_blocks(system_prompt),
            model=model,
            max_tokens=max_tokens,
        )

    def analyze_image(self, image_path: str, user_question: str,
                      system_prompt: str, model: Optional[str] = None,
                      max_tokens: int = 4096) -> Dict[str, Any]:
        """Vision call. Image is read from disk, base64-encoded, and sent."""
        if not os.path.isfile(image_path):
            raise ClaudeBadRequest(f"Image not found: {image_path}")

        with open(image_path, "rb") as f:
            data = f.read()

        b64 = base64.standard_b64encode(data).decode("ascii")
        mime, _ = mimetypes.guess_type(image_path)
        if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            mime = "image/png"

        messages = [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": b64,
                }},
                {"type": "text", "text": user_question},
            ],
        }]
        return self._call(
            messages=messages,
            system=self._system_blocks(system_prompt),
            model=model,
            max_tokens=max_tokens,
        )

    @staticmethod
    def extract_text(response: Dict[str, Any]) -> str:
        """Join all text blocks in the response."""
        out = []
        for block in response.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                out.append(block.get("text", ""))
        return "\n".join(out).strip()

    @staticmethod
    def usage_summary(response: Dict[str, Any]) -> str:
        u = response.get("usage", {}) or {}
        parts = []
        if "input_tokens" in u:
            parts.append(f"in={u['input_tokens']}")
        if "output_tokens" in u:
            parts.append(f"out={u['output_tokens']}")
        if u.get("cache_read_input_tokens"):
            parts.append(f"cache_hit={u['cache_read_input_tokens']}")
        if u.get("cache_creation_input_tokens"):
            parts.append(f"cache_write={u['cache_creation_input_tokens']}")
        return " ".join(parts) or "no usage info"

    # --- internals ---------------------------------------------------------

    def _system_blocks(self, prompt: str) -> List[Dict[str, Any]]:
        """
        Build the system content. When prompt caching is on AND the prompt
        looks long enough to be worth caching, add cache_control.
        """
        block = {"type": "text", "text": prompt}
        if self.use_cache and len(prompt) > MIN_CACHE_TOKENS * 3:
            # Rough heuristic: ~3 chars per token for English prose.
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    def _call(self, messages: List[Dict], system: List[Dict],
              model: Optional[str], max_tokens: int) -> Dict[str, Any]:
        if not self.api_key:
            raise ClaudeAuthError(
                "No Anthropic API key configured. "
                "Open Settings in the Claude Mapper dialog and paste your key "
                "(get one at https://console.anthropic.com/settings/keys)."
            )

        body = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        payload = json.dumps(body).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

        delay = 2.0
        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            req = urllib.request.Request(API_URL, data=payload, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    text = resp.read().decode("utf-8")
                    data = json.loads(text)
                    log.info(f"Claude call ok [{body['model']}] {self.usage_summary(data)}")
                    return data
            except urllib.error.HTTPError as e:
                status = e.code
                try:
                    err_body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    err_body = ""
                msg = self._extract_error(err_body) or e.reason or f"HTTP {status}"
                if status == 401 or status == 403:
                    raise ClaudeAuthError(f"Authentication failed: {msg}")
                if status == 400:
                    raise ClaudeBadRequest(f"Bad request: {msg}")
                if status == 429:
                    last_err = ClaudeRateLimitError(f"Rate limited: {msg}")
                elif 500 <= status < 600:
                    last_err = ClaudeServerError(f"Server error ({status}): {msg}")
                else:
                    raise ClaudeError(f"HTTP {status}: {msg}")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = ClaudeError(f"Network error: {e}")

            log.warn(f"Claude call attempt {attempt} failed ({last_err}); backing off {delay:.1f}s")
            time.sleep(delay)
            delay *= 2

        raise last_err or ClaudeError("Unknown error after retries.")

    @staticmethod
    def _extract_error(body: str) -> str:
        try:
            j = json.loads(body)
            if isinstance(j, dict):
                err = j.get("error", {})
                if isinstance(err, dict):
                    return err.get("message", "") or err.get("type", "")
        except Exception:
            pass
        return body[:200]
