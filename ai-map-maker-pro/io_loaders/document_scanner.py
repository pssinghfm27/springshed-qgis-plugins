"""
Document scanner: turn a PDF/image of a map into a Claude-ready image
and send it to the vision API for analysis.

For PDFs we rasterise one page at a time using:
  1. pdf2image (via Poppler) if installed - cleanest.
  2. PyMuPDF (fitz) as a fallback.
  3. If neither is available, raise a clear error.

The scan result is returned as parsed JSON (dict) via claude.schemas. The
caller can then:
  - recreate a similar map using scan_result["recreate_plan"], OR
  - georeference the scanned page using scan_result["ground_control_points"]
    and scan_result["approximate_bbox"], OR
  - both.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from typing import Dict, List, Optional

from ..claude import prompts, schemas
from ..claude.client import ClaudeClient
from ..utils import logging as log

DEFAULT_DPI = 200  # 200 DPI is enough for Claude vision; higher = slower + bigger


def rasterize_pdf(pdf_path: str, dpi: int = DEFAULT_DPI,
                  max_pages: int = 8) -> List[str]:
    """
    Rasterize up to `max_pages` pages from a PDF to PNG files in a temp dir.
    Returns the list of PNG paths in page order.
    """
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(pdf_path)

    out_dir = tempfile.mkdtemp(prefix="pdf_rasterize_")
    out_paths: List[str] = []

    # Prefer pdf2image
    try:
        from pdf2image import convert_from_path  # type: ignore
        pages = convert_from_path(pdf_path, dpi=dpi, first_page=1,
                                  last_page=max_pages)
        for i, img in enumerate(pages, 1):
            p = os.path.join(out_dir, f"page_{i:03d}.png")
            img.save(p, "PNG")
            out_paths.append(p)
        log.info(f"Rasterized {len(out_paths)} pages of {pdf_path} via pdf2image")
        return out_paths
    except ImportError:
        pass
    except Exception as e:
        log.warn(f"pdf2image failed ({e}); trying PyMuPDF")

    # Fallback: PyMuPDF
    try:
        import fitz  # type: ignore
        doc = fitz.open(pdf_path)
        for i, page in enumerate(doc, 1):
            if i > max_pages:
                break
            zoom = dpi / 72.0
            matrix = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            p = os.path.join(out_dir, f"page_{i:03d}.png")
            pix.save(p)
            out_paths.append(p)
        log.info(f"Rasterized {len(out_paths)} pages of {pdf_path} via PyMuPDF")
        return out_paths
    except ImportError:
        pass

    shutil.rmtree(out_dir, ignore_errors=True)
    raise RuntimeError(
        "Cannot rasterize PDF: install 'pdf2image' (requires Poppler) or "
        "'pymupdf'.\n"
        "  python -m pip install pymupdf --break-system-packages\n"
        "Or convert the PDF page to an image in another tool and upload that."
    )


def scan_image(image_path: str, client: ClaudeClient,
               user_hint: Optional[str] = None,
               model: Optional[str] = None) -> Dict:
    """
    Send an image to Claude vision and return the parsed scan-result dict.
    """
    if not os.path.isfile(image_path):
        raise FileNotFoundError(image_path)

    question = (
        "Analyze this map image. Return the JSON object exactly matching the "
        "schema in the system prompt."
    )
    if user_hint:
        question += f"\n\nUser-provided hint: {user_hint}"

    response = client.analyze_image(
        image_path=image_path,
        user_question=question,
        system_prompt=prompts.MAP_SCAN_SYSTEM,
        model=model,
        max_tokens=4096,
    )
    text = ClaudeClient.extract_text(response)
    result = schemas.parse_scan_result(text)
    # Attach the source image path so callers can georeference it later.
    result["_image_path"] = image_path
    return result


def scan_pdf(pdf_path: str, client: ClaudeClient,
             user_hint: Optional[str] = None,
             model: Optional[str] = None,
             page_index: int = 0,
             dpi: int = DEFAULT_DPI) -> Dict:
    """
    Rasterize one page of a PDF and scan it with Claude vision.
    Default: page_index=0 (first page).
    """
    pages = rasterize_pdf(pdf_path, dpi=dpi, max_pages=page_index + 1)
    if page_index >= len(pages):
        raise IndexError(f"PDF has only {len(pages)} page(s); requested page {page_index + 1}")
    return scan_image(pages[page_index], client, user_hint, model)
