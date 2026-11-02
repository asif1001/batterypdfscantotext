"""Render a PDF to page images. 300 dpi is the production default."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def _zoom_for(page, dpi: int) -> float:
    requested = dpi / 72
    try:
        images = page.get_image_info()
    except Exception:
        return requested
    if len(images) != 1 or page.rect.width <= 0:
        return requested
    native = images[0].get("width", 0) / page.rect.width * 72
    if native < 72:
        return requested
    return min(requested, native / 72)


def render_pdf(path: str | Path, dpi: int = 300) -> list[np.ndarray]:
    import fitz

    document = fitz.open(path)
    pages: list[np.ndarray] = []
    try:
        for page in document:
            # A scanned page is one photo. Rendering above that photo's own dpi only blurs the QR.
            zoom = _zoom_for(page, dpi)
            matrix = fitz.Matrix(zoom, zoom)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
            if pixmap.n == 4:
                image = cv2.cvtColor(image, cv2.COLOR_RGBA2BGR)
            else:
                image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            pages.append(image.copy())
    finally:
        document.close()
    return pages
