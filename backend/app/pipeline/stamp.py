"""Blue warranty stamp (E.K. Kanoo) and round dealer stamp.

Printed stamp text is read with Tesseract, including a 180-degree pass for stamps
that were pressed upside down. Handwriting is not read here.
"""

from __future__ import annotations

import re

import cv2
import numpy as np

_CODE_RE = re.compile(r"\d{6,8}")
_BRANCH_RE = re.compile(r"\b0*([0-9])\b")
_TEL_RE = re.compile(r"\b\d{8}\b")


def read_stamps(card_bgr: np.ndarray) -> dict:
    mask = _blue_mask(card_bgr)
    height = card_bgr.shape[0]
    # The brand header is also blue on SF SONIC cards. Ignore the top band.
    mask[: int(height * 0.16), :] = 0
    blue_pixels = int(cv2.countNonZero(mask))
    rect = _largest_rect(mask, card_bgr.shape)
    dealer = _dealer_circle(card_bgr, mask, rect)

    if rect is None:
        condition = "faint" if blue_pixels > 80 else "missing"
        return {
            "stamp_code": None,
            "stamp_branch": None,
            "stamp_tel": None,
            "stamp_condition": condition,
            "dealer_stamp": dealer,
        }

    crop = _slice(card_bgr, rect)
    upright_text = _ocr(crop)
    flipped_text = _ocr(cv2.rotate(crop, cv2.ROTATE_180))
    upright = _parse_stamp(upright_text)
    flipped = _parse_stamp(flipped_text)

    chosen = upright
    condition = "ok"
    if flipped.get("stamp_code") and not upright.get("stamp_code"):
        chosen = flipped
        condition = "upside_down"
    elif flipped.get("stamp_code") and upright.get("stamp_code"):
        # Prefer whichever pass actually resolved a code. If only the flip is cleaner
        # (more digits grouped), keep the upright reading unless it failed.
        condition = "ok"

    area = (rect[2] - rect[0]) * (rect[3] - rect[1])
    if not chosen.get("stamp_code"):
        condition = "faint" if area < 4000 or blue_pixels < 400 else "unreadable"
    return {
        "stamp_code": chosen.get("stamp_code"),
        "stamp_branch": chosen.get("stamp_branch"),
        "stamp_tel": chosen.get("stamp_tel"),
        "stamp_condition": condition,
        "dealer_stamp": dealer,
    }


def _parse_stamp(text: str) -> dict:
    if not text:
        return {}
    code_match = _CODE_RE.search(text)
    tel_match = _TEL_RE.search(text)
    branch = None
    # The branch number sits in a small circle, printed as 01 or 02.
    for token in re.findall(r"\d{1,2}", text):
        if code_match and token in code_match.group(0):
            continue
        if tel_match and token in tel_match.group(0):
            continue
        if len(token) <= 2:
            branch = int(token)
            break
    if branch is None:
        loose = _BRANCH_RE.search(text)
        if loose:
            branch = int(loose.group(1))
    return {
        "stamp_code": code_match.group(0) if code_match else None,
        "stamp_branch": branch,
        "stamp_tel": tel_match.group(0) if tel_match else None,
    }


def _blue_mask(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (95, 50, 40), (140, 255, 255))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)


def _largest_rect(mask: np.ndarray, shape) -> tuple[int, int, int, int] | None:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = shape[:2]
    page_area = height * width
    best = None
    best_area = 0
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area < page_area * 0.004 or area > page_area * 0.35:
            continue
        ratio = max(w, h) / max(1, min(w, h))
        if ratio > 4.5:
            continue
        if area > best_area:
            best_area = area
            best = (x, y, x + w, y + h)
    return best


def _dealer_circle(image: np.ndarray, mask: np.ndarray, rect) -> str | None:
    blurred = cv2.GaussianBlur(mask, (9, 9), 0)
    circles = cv2.HoughCircles(
        blurred,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=40,
        param1=80,
        param2=22,
        minRadius=18,
        maxRadius=120,
    )
    if circles is None:
        return None
    height, width = image.shape[:2]
    for circle in np.round(circles[0]).astype(int):
        cx, cy, radius = int(circle[0]), int(circle[1]), int(circle[2])
        if rect and rect[0] <= cx <= rect[2] and rect[1] <= cy <= rect[3] and radius < 40:
            # Small circle inside the warranty stamp is the branch number, not the dealer.
            continue
        x0, y0 = max(0, cx - radius), max(0, cy - radius)
        x1, y1 = min(width, cx + radius), min(height, cy + radius)
        text = _ocr(image[y0:y1, x0:x1])
        letters = re.sub(r"[^A-Za-z ]", "", text).strip()
        if len(letters) >= 4:
            return " ".join(letters.split())
    return None


def _slice(image: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = rect
    return image[y0:y1, x0:x1]


def _ocr(image: np.ndarray) -> str:
    try:
        import pytesseract
    except Exception:
        return ""
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    try:
        return pytesseract.image_to_string(gray, lang="eng", config="--psm 6") or ""
    except Exception:
        return ""
