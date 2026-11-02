"""Build a synthetic scanned page so the pipeline can be tested without the sample PDFs."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.defaults import DEFAULT_ZONES

try:
    import qrcode
except Exception:  # pragma: no cover
    qrcode = None


def make_card(
    serial: str = "A4C6E031610",
    batch_code: str = "4C63",
    battery_type: str = "FSE0-Q65R(EFB)-BH-IS",
    brand: str = "SF SONIC",
    stamp_code: str = "1046906",
    date_text: str = "12 OCT 2026",
    width: int = 620,
    height: int = 980,
) -> np.ndarray:
    card = np.full((height, width, 3), 255, np.uint8)
    cv2.rectangle(card, (2, 2), (width - 3, height - 3), (40, 40, 40), 3)
    colours = {
        "CEIL": (70, 160, 60),
        "SF SONIC": (180, 90, 30),
        "DYNEX": (40, 40, 180),
    }
    colour = colours.get(brand, colours["SF SONIC"])
    header = DEFAULT_ZONES["header"]
    y1 = int(header[3] * height)
    cv2.rectangle(card, (4, 4), (width - 5, y1), colour, -1)
    cv2.putText(card, "Authorised Distributor's Copy", (16, int(y1 * 0.65)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

    payload = f"{battery_type}\n{serial}\n{batch_code}"
    qr_box = DEFAULT_ZONES["qr"]
    qr_img = _qr(payload, int((qr_box[2] - qr_box[0]) * width) - 8)
    _paste(card, qr_img, int(qr_box[0] * width) + 4, int(qr_box[1] * height) + 4)

    cv2.putText(card, battery_type[:22], (int(width * 0.42), int(height * 0.16)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.putText(card, serial, (int(width * 0.42), int(height * 0.21)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.putText(card, batch_code, (int(width * 0.42), int(height * 0.26)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

    _handwriting(card, DEFAULT_ZONES["date_of_sale"], date_text)
    _stamp(card, stamp_code)
    return card


def make_page(cards: list[tuple[np.ndarray, int]] | None = None) -> np.ndarray:
    """Place 4 rotated cards on the left and 2 upright cards on the right."""
    if cards is None:
        built = []
        for index in range(6):
            serial = f"A4C6E03161{index}"
            # Keep the serial regex: A # L # L ######
            serial = f"A4C6E{310000 + index:06d}"
            built.append(make_card(serial=serial, batch_code="4C63", width=480, height=760))
        cards = [(built[i], 90 if i < 4 else 0) for i in range(6)]

    page_w, page_h = 1500, 2200
    page = np.full((page_h, page_w, 3), 232, np.uint8)
    left_x, top = 30, 24
    cursor_y = top
    right_x, right_y = 840, 30
    for image, rotation in cards:
        placed = image
        if rotation:
            placed = np.ascontiguousarray(np.rot90(image, rotation // 90))
        h, w = placed.shape[:2]
        if rotation:
            if cursor_y + h > page_h - 10:
                break
            page[cursor_y : cursor_y + h, left_x : left_x + w] = placed
            cursor_y += h + 24
        else:
            if right_y + h > page_h - 10:
                break
            page[right_y : right_y + h, right_x : right_x + w] = placed
            right_y += h + 30
    return page


def write_pdf(path: str | Path, pages: list[np.ndarray], dpi: int = 150) -> None:
    import fitz

    doc = fitz.open()
    for image in pages:
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise RuntimeError("Could not encode page")
        width_pt = image.shape[1] / dpi * 72
        height_pt = image.shape[0] / dpi * 72
        page = doc.new_page(width=width_pt, height=height_pt)
        page.insert_image(page.rect, stream=encoded.tobytes())
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


def _qr(payload: str, size: int) -> np.ndarray:
    if qrcode is None:
        tile = np.full((size, size, 3), 255, np.uint8)
        cv2.putText(tile, "QR", (8, size // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
        return tile
    image = qrcode.make(payload, border=1).convert("RGB")
    array = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    return cv2.resize(array, (max(40, size), max(40, size)), interpolation=cv2.INTER_NEAREST)


def _paste(card: np.ndarray, patch: np.ndarray, x: int, y: int) -> None:
    h, w = patch.shape[:2]
    y1, x1 = min(card.shape[0], y + h), min(card.shape[1], x + w)
    card[y:y1, x:x1] = patch[: y1 - y, : x1 - x]


def _handwriting(card: np.ndarray, box: list[float], text: str) -> None:
    h, w = card.shape[:2]
    x = int(box[0] * w) + 6
    y = int((box[1] + box[3]) / 2 * h)
    cv2.putText(card, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (140, 40, 20), 2, cv2.LINE_AA)


def _stamp(card: np.ndarray, code: str) -> None:
    h, w = card.shape[:2]
    x0, y0, x1, y1 = int(w * 0.55), int(h * 0.66), int(w * 0.94), int(h * 0.76)
    cv2.rectangle(card, (x0, y0), (x1, y1), (200, 40, 20), 2)
    cv2.putText(card, code, (x0 + 8, y0 + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 30, 20), 2, cv2.LINE_AA)
    cv2.circle(card, (x1 - 28, y0 + 24), 16, (180, 30, 20), 2)
    cv2.putText(card, "01", (x1 - 40, y0 + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 30, 20), 1, cv2.LINE_AA)
