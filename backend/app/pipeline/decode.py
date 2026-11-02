"""QR, barcode and printed-label reading. AI is never asked for these values."""

from __future__ import annotations

import re

import cv2
import numpy as np

from app.rules import BATCH_RE, SERIAL_RE, normalize_type

def _load_zbar():
    """pyzbar ships libzbar beside the package. Python 3.8+ will not search that folder."""
    import os
    import sys
    from pathlib import Path

    if sys.platform == "win32":
        try:
            import importlib.util

            spec = importlib.util.find_spec("pyzbar")
            if spec and spec.origin:
                os.add_dll_directory(str(Path(spec.origin).parent))
        except Exception:
            pass
    from pyzbar.pyzbar import decode as zbar_decode

    return zbar_decode


try:
    zbar_decode = _load_zbar()
except Exception:  # pragma: no cover - native library or the VC++ 2013 runtime may be absent
    zbar_decode = None

# Validation patterns are anchored. These are the same shapes used to find a value inside a longer string.
SERIAL_FIND = re.compile(r"A\d[A-Z]\d[A-Z]\d{6}")
BATCH_FIND = re.compile(r"\d[A-Z]\d{2}")
TYPE_RE = re.compile(r"F(?:CE7|SE0|DM0)[A-Z0-9()\-]*")


def read_label(card_bgr: np.ndarray, zones: dict[str, list[float]]) -> dict:
    label = crop_zone(card_bgr, zones.get("label", [0.05, 0.09, 0.95, 0.33]))
    qr_crop = crop_zone(card_bgr, zones.get("qr", [0.06, 0.10, 0.40, 0.30]))
    barcode_crop = crop_zone(card_bgr, zones.get("barcode", [0.08, 0.22, 0.70, 0.33]))

    qr_text = _decode_qr(qr_crop) or _decode_qr(label) or _decode_qr(card_bgr)
    barcode_text = _decode_barcode(barcode_crop) or _decode_barcode(label)
    parsed = parse_label_text(qr_text or "")
    if not parsed.get("serial") or not parsed.get("battery_type"):
        # The serial box is a ruled rectangle. Reading that box, the way a bordered
        # table crop is read, keeps the QR away from the stamp and the handwriting.
        box_symbols = _symbols_in_label_box(card_bgr)
        qr_text, barcode_text, parsed = _merge_symbols(qr_text, barcode_text, parsed, box_symbols)
    if not parsed.get("serial"):
        # Some scans need a larger view of the label before the code is readable.
        top = card_bgr[: max(1, int(card_bgr.shape[0] * 0.42)), :]
        larger = cv2.resize(top, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        extra = _symbol_texts(larger)
        qr_text, barcode_text, parsed = _merge_symbols(qr_text, barcode_text, parsed, extra)
    barcode_parsed = parse_label_text(barcode_text or "")

    source = "qr" if qr_text and parsed.get("serial") else None
    if not parsed.get("serial") and barcode_parsed.get("serial"):
        parsed["serial"] = barcode_parsed["serial"]
        source = "barcode"
    if not parsed.get("batch_code") and barcode_parsed.get("batch_code"):
        parsed["batch_code"] = barcode_parsed["batch_code"]
        parsed["batch_code_source"] = "barcode"
    if parsed.get("serial") and barcode_parsed.get("serial") and parsed["serial"] != barcode_parsed["serial"]:
        parsed["barcode_conflict"] = barcode_parsed["serial"]

    if not parsed.get("serial") or not parsed.get("battery_type"):
        ocr_text = _ocr(label)
        ocr_parsed = parse_label_text(ocr_text)
        for key in ("serial", "batch_code", "battery_type"):
            if not parsed.get(key) and ocr_parsed.get(key):
                parsed[key] = ocr_parsed[key]
                parsed[f"{key}_source"] = "ocr"
        if source is None and ocr_text:
            source = "ocr"

    serial = parsed.get("serial")
    batch_code = parsed.get("batch_code")
    battery_type = parsed.get("battery_type")
    qr_verified = bool(qr_text and serial and SERIAL_RE.fullmatch(serial))
    serial_source = "qr" if qr_verified else parsed.get("serial_source") or source or ""
    return {
        "serial": serial,
        "batch_code": batch_code,
        "battery_type": battery_type,
        "qr_text": qr_text or "",
        "barcode_text": barcode_text or "",
        "qr_verified": qr_verified,
        "serial_source": serial_source if serial else "",
        "batch_source": "qr" if qr_text and batch_code else parsed.get("batch_code_source") or (source or ""),
        "type_source": "qr" if qr_text and battery_type else parsed.get("battery_type_source") or (source or ""),
    }


def parse_label_text(text: str) -> dict:
    if not text:
        return {}
    compact = re.sub(r"\s+", "", text).upper()
    compact = compact.replace("FSEO", "FSE0").replace("FDMO", "FDM0")
    serial_match = SERIAL_FIND.search(compact)
    serial = serial_match.group(0) if serial_match else None
    rest = compact.replace(serial, "", 1) if serial else compact
    batch_match = BATCH_FIND.search(rest)
    batch_code = batch_match.group(0) if batch_match else None
    if batch_code:
        rest = rest.replace(batch_code, "", 1)
    type_match = TYPE_RE.search(rest)
    battery_type = type_match.group(0) if type_match else None
    if battery_type:
        embedded = SERIAL_FIND.search(battery_type)
        if embedded:
            battery_type = battery_type[: embedded.start()]
        # Some labels append a print date to the type with no separator.
        battery_type = re.sub(r"\d{8,}$", "", battery_type)
        battery_type = normalize_type(battery_type) or None
    return {"serial": serial, "batch_code": batch_code, "battery_type": battery_type}


def _label_box(image: np.ndarray):
    """The printed serial block is a ruled rectangle in the top half of the card."""
    height, width = image.shape[:2]
    if height < 40 or width < 40:
        return None
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    band = gray[: int(height * 0.48)]
    edges = cv2.Canny(cv2.GaussianBlur(band, (3, 3), 0), 40, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_area = 0
    minimum = width * height * 0.02
    limit = width * height * 0.28
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        area = bw * bh
        if area < minimum or area > limit:
            continue
        ratio = bw / max(1, bh)
        if ratio < 1.2 or ratio > 3.2:
            continue
        if area > best_area:
            best = (x, y, bw, bh)
            best_area = area
    return best


def _symbol_texts(image: np.ndarray) -> list[tuple[str, str]]:
    try:
        import zxingcpp

        symbols = zxingcpp.read_barcodes(image)
    except Exception:
        return []
    found = []
    for symbol in symbols:
        text = (getattr(symbol, "text", "") or "").strip()
        if not text:
            continue
        kind = "qr" if "QR" in str(getattr(symbol, "format", "")) else "barcode"
        found.append((kind, text))
    return found


def _symbols_in_label_box(card: np.ndarray) -> list[tuple[str, str]]:
    box = _label_box(card)
    if box is None:
        return []
    height, width = card.shape[:2]
    x, y, bw, bh = box
    crop = card[max(0, y - 4) : min(height, y + bh + 4), max(0, x - 4) : min(width, x + bw + 4)]
    if crop.size == 0:
        return []
    found: list[tuple[str, str]] = []
    for scale in (2, 3):
        view = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        found.extend(_symbol_texts(view))
        if any(kind == "qr" and parse_label_text(text).get("battery_type") for kind, text in found):
            break
    return found


def _merge_symbols(qr_text: str, barcode_text: str, parsed: dict, symbols: list[tuple[str, str]]):
    for kind, text in symbols:
        got = parse_label_text(text)
        if kind == "qr" and got.get("serial"):
            qr_text = qr_text or text
        elif kind != "qr":
            barcode_text = barcode_text or text
        for key in ("serial", "batch_code", "battery_type"):
            if got.get(key) and not parsed.get(key):
                parsed[key] = got[key]
    return qr_text, barcode_text, parsed


def crop_zone(image: np.ndarray, box: list[float]) -> np.ndarray:
    height, width = image.shape[:2]
    x0 = int(max(0, min(width - 1, box[0] * width)))
    y0 = int(max(0, min(height - 1, box[1] * height)))
    x1 = int(max(x0 + 1, min(width, box[2] * width)))
    y1 = int(max(y0 + 1, min(height, box[3] * height)))
    return image[y0:y1, x0:x1]


def _decode_qr(image: np.ndarray) -> str:
    if image is None or image.size == 0:
        return ""
    text = _try_zxing(image, qr_only=True) or _try_zbar(image, qr_only=True) or _try_opencv_qr(image)
    if text:
        return text
    # Scanned modules are often under 20 px. A 2x view is what makes them readable.
    upscaled = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    text = _try_zxing(upscaled, qr_only=True) or _try_opencv_qr(upscaled)
    if text:
        return text
    if max(image.shape[:2]) > 500:
        top = image[: max(1, int(image.shape[0] * 0.42)), :]
        larger = cv2.resize(top, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        text = _try_zxing(larger, qr_only=True)
        if text:
            return text
    for variant in _binaries(upscaled):
        text = _try_zbar(variant, qr_only=True) or _try_opencv_qr(variant)
        if text:
            return text
    return ""


def _decode_barcode(image: np.ndarray) -> str:
    if image is None or image.size == 0:
        return ""
    text = _try_zxing(image, qr_only=False) or _try_zbar(image, qr_only=False)
    if text:
        return text
    upscaled = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    text = _try_zxing(upscaled, qr_only=False)
    if text:
        return text
    for variant in _binaries(upscaled):
        text = _try_zbar(variant, qr_only=False)
        if text:
            return text
    return ""


def _try_zxing(image: np.ndarray, qr_only: bool) -> str:
    try:
        import zxingcpp
    except Exception:
        return ""
    try:
        symbols = zxingcpp.read_barcodes(image)
    except Exception:
        return ""
    texts = []
    for symbol in symbols:
        kind = str(getattr(symbol, "format", ""))
        is_qr = "QR" in kind
        if qr_only and not is_qr:
            continue
        if not qr_only and is_qr:
            continue
        data = (getattr(symbol, "text", "") or "").strip()
        if data:
            texts.append(data)
    return "\n".join(texts)


def _binaries(image: np.ndarray) -> list[np.ndarray]:
    if image.ndim == 2:
        channels = [image, image, image]
    else:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blue = image[:, :, 0]
        max_channel = np.max(image, axis=2)
        channels = [gray, blue, max_channel]
    out = []
    for channel in channels:
        _, binary = cv2.threshold(channel, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        out.append(binary)
    return out


def _try_zbar(image: np.ndarray, qr_only: bool) -> str:
    if zbar_decode is None:
        return ""
    try:
        symbols = zbar_decode(image)
    except Exception:
        return ""
    texts = []
    for symbol in symbols:
        kind = str(getattr(symbol, "type", ""))
        if qr_only and kind != "QRCODE":
            continue
        if not qr_only and kind == "QRCODE":
            continue
        data = symbol.data.decode("utf-8", errors="ignore").strip()
        if data:
            texts.append(data)
    return "\n".join(texts)


_QR_CLASSIC = None


def _try_opencv_qr(image: np.ndarray) -> str:
    """OpenCV QRCodeDetector. One detector is reused; building it per card is slow and does not read more codes."""
    global _QR_CLASSIC
    if _QR_CLASSIC is None:
        _QR_CLASSIC = cv2.QRCodeDetector()
    try:
        value, _, _ = _QR_CLASSIC.detectAndDecode(image)
    except Exception:
        return ""
    if value:
        return value.strip()
    try:
        ok, decoded, _, _ = _QR_CLASSIC.detectAndDecodeMulti(image)
    except Exception:
        return ""
    if not ok or decoded is None:
        return ""
    parts = [item.strip() for item in decoded if item and item.strip()]
    return "\n".join(parts)


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
