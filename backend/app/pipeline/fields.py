"""Handwriting zones, empty-field ink test, and the compact strip sent to a vision model."""

from __future__ import annotations

import cv2
import numpy as np

from app.defaults import HANDWRITING_FIELDS
from app.pipeline.decode import crop_zone


def field_ink(card_bgr: np.ndarray, zones: dict[str, list[float]]) -> dict[str, bool]:
    result = {}
    for name in HANDWRITING_FIELDS:
        box = zones.get(name)
        if not box:
            result[name] = False
            continue
        crop = crop_zone(card_bgr, box)
        result[name] = has_ink(crop)
    return result


def has_ink(crop_bgr: np.ndarray, min_pixels: int = 45) -> bool:
    """True when the crop contains handwriting, ignoring a dotted baseline and pale paper."""
    if crop_bgr is None or crop_bgr.size == 0:
        return False
    gray = crop_bgr if crop_bgr.ndim == 2 else cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    ink = gray < 150
    height, width = ink.shape[:2]
    if width == 0 or height == 0:
        return False
    row_counts = ink.sum(axis=1)
    # A printed or dotted line is a thin row that runs across most of the crop.
    line_rows = row_counts > width * 0.45
    ink = ink.copy()
    ink[line_rows] = False
    # Drop isolated specks.
    kernel = np.ones((2, 2), np.uint8)
    cleaned = cv2.morphologyEx(ink.astype(np.uint8) * 255, cv2.MORPH_OPEN, kernel)
    return int(cv2.countNonZero(cleaned)) >= min_pixels


def build_field_strip(
    card_bgr: np.ndarray,
    zones: dict[str, list[float]],
    ink: dict[str, bool],
    max_width: int = 1000,
) -> tuple[np.ndarray | None, list[str]]:
    """One greyscale vertical strip of the fields that actually contain ink."""
    names = [name for name in HANDWRITING_FIELDS if ink.get(name)]
    if not names:
        return None, []
    rows: list[np.ndarray] = []
    for name in names:
        crop = crop_zone(card_bgr, zones[name])
        gray = crop if crop.ndim == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = _fit_width(gray, max_width)
        label = np.full((32, gray.shape[1]), 255, np.uint8)
        cv2.putText(label, name, (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, 0, 1, cv2.LINE_AA)
        rows.append(label)
        rows.append(gray)
        rows.append(np.full((8, gray.shape[1]), 255, np.uint8))
    return np.vstack(rows), names


def _fit_width(image: np.ndarray, max_width: int) -> np.ndarray:
    height, width = image.shape[:2]
    if width > max_width:
        scale = max_width / width
        image = cv2.resize(image, (max_width, max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
        width = max_width
        height = image.shape[0]
    if width < max_width:
        padded = np.full((height, max_width), 255, np.uint8)
        padded[:, :width] = image
        return padded
    return image
