"""Find warranty cards on a scanned page and turn each one upright.

The usual page has four cards rotated 90 degrees in the left column and two upright
cards in the right column. Detection does not assume that count: the last page of a
PDF is often short. A coloured header bar (green, blue or red) fixes the rotation.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# OpenCV hue is 0–180.
_GREEN = ((32, 28, 40), (95, 255, 255))
_BLUE = ((90, 30, 40), (140, 255, 255))
_RED_LO = ((0, 40, 40), (12, 255, 255))
_RED_HI = ((168, 40, 40), (180, 255, 255))


@dataclass
class DetectedCard:
    image: np.ndarray
    center_x: float
    center_y: float
    brand: str | None


def detect_cards(page_bgr: np.ndarray) -> list[DetectedCard]:
    if page_bgr is None or page_bgr.size == 0:
        return []
    height, width = page_bgr.shape[:2]
    if _looks_like_single_card(page_bgr):
        upright, brand = orient_upright(page_bgr)
        return [DetectedCard(upright, width / 2, height / 2, brand)]

    by_header = _from_headers(page_bgr)
    # Coloured header bars are the reliable anchor on a real scan. Contours lock onto
    # the blue warranty stamp when the card border is faint.
    if by_header:
        found = by_header
    else:
        found = _from_contours(page_bgr)
    found = _suppress_overlaps(found)
    return _order_cards(found, width)


def orient_upright(image: np.ndarray) -> tuple[np.ndarray, str | None]:
    best = image
    best_score = -1
    best_brand: str | None = None
    for turn in range(4):
        rotated = np.ascontiguousarray(np.rot90(image, turn))
        score, brand = _header_score(rotated)
        if score > best_score:
            best = rotated
            best_score = score
            best_brand = brand
    return best, best_brand


def brand_from_header(image: np.ndarray) -> str | None:
    _, brand = _header_score(image)
    return brand


def _looks_like_single_card(image: np.ndarray) -> bool:
    height, width = image.shape[:2]
    ratio = max(width, height) / max(1, min(width, height))
    if ratio < 1.15 or ratio > 2.4:
        return False
    # A full scan page is much larger than one card and has a pale margin.
    # A single photographed card fills the frame.
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    border = np.concatenate(
        [gray[:8, :].ravel(), gray[-8:, :].ravel(), gray[:, :8].ravel(), gray[:, -8:].ravel()]
    )
    if border.size == 0:
        return False
    return float(np.mean(border)) < 180


def _header_mask(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, _GREEN[0], _GREEN[1])
    mask = cv2.bitwise_or(mask, cv2.inRange(hsv, _BLUE[0], _BLUE[1]))
    mask = cv2.bitwise_or(mask, cv2.inRange(hsv, _RED_LO[0], _RED_LO[1]))
    mask = cv2.bitwise_or(mask, cv2.inRange(hsv, _RED_HI[0], _RED_HI[1]))
    return mask


def _header_score(image: np.ndarray) -> tuple[int, str | None]:
    height = image.shape[0]
    strip = image[: max(1, int(height * 0.12))]
    hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, _GREEN[0], _GREEN[1])
    blue = cv2.inRange(hsv, _BLUE[0], _BLUE[1])
    red = cv2.bitwise_or(
        cv2.inRange(hsv, _RED_LO[0], _RED_LO[1]),
        cv2.inRange(hsv, _RED_HI[0], _RED_HI[1]),
    )
    scores = {
        "CEIL": int(cv2.countNonZero(green)),
        "SF SONIC": int(cv2.countNonZero(blue)),
        "DYNEX": int(cv2.countNonZero(red)),
    }
    brand = max(scores, key=scores.get)
    score = scores[brand]
    if score < strip.shape[0] * strip.shape[1] * 0.035:
        return score, None
    return score, brand


# Upright warranty card: height / width. Measured on the printed form and the synthetic page.
_CARD_ASPECT = 1.60


class _Bar:
    def __init__(self, x: int, y: int, w: int, h: int, brand: str | None):
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.brand = brand


def _from_headers(page: np.ndarray) -> list[DetectedCard]:
    """Build one upright card from each solid coloured header bar.

    On a scan the bar is often only the left part of the header, and the card border is
    too faint to trace. Bars in the same column share a pitch, and that pitch is the
    card's step down the page. A vertical bar is a card rotated with its header on the
    left; the body extends to the right.
    """
    height, width = page.shape[:2]
    bars = _header_bars(page)
    if not bars:
        return []
    cards: list[DetectedCard] = []
    vertical = [bar for bar in bars if bar.h > bar.w]
    horizontal = [bar for bar in bars if bar.h <= bar.w]
    for group in _cluster_bars(vertical, "x", width * 0.08):
        cards.extend(_cards_from_bars(page, group, vertical=True))
    for group in _cluster_bars(horizontal, "x", width * 0.10):
        cards.extend(_cards_from_bars(page, group, vertical=False))
    return cards


def _header_bars(page: np.ndarray) -> list[_Bar]:
    """Contour bars keep the full header. Projection adds bars the stamp bled into."""
    outlined = _contour_bars(page)
    extra: list[_Bar] = []
    for bar in _projection_bars(page):
        if any(_bar_iou(bar, other) > 0.25 or _same_header(bar, other) for other in outlined):
            continue
        extra.append(bar)
    return _dedupe_bars(outlined + extra)


def _same_header(a: _Bar, b: _Bar) -> bool:
    acx, acy = a.x + a.w / 2, a.y + a.h / 2
    return (b.x - 20) <= acx <= (b.x + b.w + 20) and (b.y - 20) <= acy <= (b.y + b.h + 20)


def _contour_bars(page: np.ndarray) -> list[_Bar]:
    mask = cv2.morphologyEx(_header_mask(page), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = page.shape[:2]
    minimum = max(width, height) * 0.08
    min_thick = max(18, int(min(width, height) * 0.025))
    bars: list[_Bar] = []
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        long_side, short_side = max(bw, bh), max(1, min(bw, bh))
        if long_side < minimum or short_side < min_thick:
            continue
        if long_side / short_side < 3.0:
            continue
        if cv2.contourArea(contour) / float(bw * bh) < 0.45:
            continue
        roi = page[y : y + bh, x : x + bw]
        bars.append(_Bar(x, y, bw, bh, _brand_of(roi)))
    return bars


def _projection_bars(page: np.ndarray) -> list[_Bar]:
    """Solid header bars, measured along the bar so a blue stamp beside it is ignored."""
    mask = _header_mask(page) > 0
    height, width = mask.shape
    min_long = int(max(height, width) * 0.12)
    min_thick = max(14, int(min(height, width) * 0.018))
    bars: list[_Bar] = []
    for vertical in (True, False):
        for x, y, bw, bh in _projection_rects(mask, min_long, min_thick, vertical):
            roi = page[y : y + bh, x : x + bw]
            bars.append(_Bar(int(x), int(y), int(bw), int(bh), _brand_of(roi)))
    return _dedupe_bars(bars)


def _projection_rects(mask: np.ndarray, min_long: int, min_thick: int, vertical: bool) -> list[tuple[int, int, int, int]]:
    view = mask if vertical else mask.T
    height, width = view.shape
    padded = np.pad(view, ((1, 1), (0, 0)))
    diff = np.diff(padded.astype(np.int8), axis=0)
    sy, sx = np.where(diff == 1)
    ey, ex = np.where(diff == -1)
    starts: dict[int, list[int]] = {}
    ends: dict[int, list[int]] = {}
    for y, x in zip(sy.tolist(), sx.tolist()):
        starts.setdefault(int(x), []).append(int(y))
    for y, x in zip(ey.tolist(), ex.tolist()):
        ends.setdefault(int(x), []).append(int(y))
    y0_list: list[int] = []
    y1_list: list[int] = []
    x_list: list[int] = []
    for column, tops in starts.items():
        bottoms = ends.get(column, [])
        for top, bottom in zip(sorted(tops), sorted(bottoms)):
            if bottom - top >= min_long:
                y0_list.append(top)
                y1_list.append(bottom)
                x_list.append(column)
    if not x_list:
        return []
    y0s = np.array(y0_list)
    y1s = np.array(y1_list)
    xs = np.array(x_list)
    order = np.lexsort((y0s, xs))
    y0s, y1s, xs = y0s[order], y1s[order], xs[order]
    by_x: dict[int, list[int]] = {}
    for index, column in enumerate(xs.tolist()):
        by_x.setdefault(int(column), []).append(index)
    used = np.zeros(len(xs), dtype=bool)
    rects: list[tuple[int, int, int, int]] = []
    for index in range(len(xs)):
        if used[index]:
            continue
        used[index] = True
        column = int(xs[index])
        top, bottom = int(y0s[index]), int(y1s[index])
        columns = [column]
        cursor = column
        while cursor + 1 < width:
            nxt = cursor + 1
            if nxt not in by_x and nxt + 1 in by_x:
                nxt = nxt + 1
            hit = None
            for candidate in by_x.get(nxt, []):
                if used[candidate]:
                    continue
                overlap = min(bottom, int(y1s[candidate])) - max(top, int(y0s[candidate]))
                if overlap >= min_long * 0.7:
                    hit = candidate
                    break
            if hit is None:
                break
            used[hit] = True
            columns.append(nxt)
            top = min(top, int(y0s[hit]))
            bottom = max(bottom, int(y1s[hit]))
            cursor = nxt
        thick = columns[-1] - columns[0] + 1
        if thick < min_thick:
            continue
        if vertical:
            rects.append((columns[0], top, thick, bottom - top))
        else:
            rects.append((top, columns[0], bottom - top, thick))
    return rects


def _brand_of(roi: np.ndarray) -> str | None:
    if roi.size == 0:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    red = cv2.bitwise_or(cv2.inRange(hsv, _RED_LO[0], _RED_LO[1]), cv2.inRange(hsv, _RED_HI[0], _RED_HI[1]))
    scores = {
        "CEIL": int(cv2.countNonZero(cv2.inRange(hsv, _GREEN[0], _GREEN[1]))),
        "SF SONIC": int(cv2.countNonZero(cv2.inRange(hsv, _BLUE[0], _BLUE[1]))),
        "DYNEX": int(cv2.countNonZero(red)),
    }
    brand = max(scores, key=scores.get)
    if scores[brand] < 30:
        return None
    return brand


def _dedupe_bars(bars: list[_Bar]) -> list[_Bar]:
    kept: list[_Bar] = []
    for bar in sorted(bars, key=lambda item: max(item.w, item.h), reverse=True):
        if any(_bar_iou(bar, other) > 0.5 for other in kept):
            continue
        kept.append(bar)
    return kept


def _bar_iou(a: _Bar, b: _Bar) -> float:
    ix0, iy0 = max(a.x, b.x), max(a.y, b.y)
    ix1, iy1 = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    union = a.w * a.h + b.w * b.h - inter
    if union <= 0:
        return 0.0
    return inter / union


def _cluster_bars(bars: list[_Bar], key: str, tolerance: float) -> list[list[_Bar]]:
    groups: list[list[_Bar]] = []
    for bar in sorted(bars, key=lambda item: getattr(item, key)):
        anchor = getattr(bar, key)
        if groups and abs(anchor - getattr(groups[-1][0], key)) <= tolerance:
            groups[-1].append(bar)
        else:
            groups.append([bar])
    return groups


def _cards_from_bars(page: np.ndarray, group: list[_Bar], vertical: bool) -> list[DetectedCard]:
    height, width = page.shape[:2]
    ordered = sorted(group, key=lambda bar: bar.y)
    pitches = [ordered[i + 1].y - ordered[i].y for i in range(len(ordered) - 1)]
    pitch = int(np.median(pitches)) if pitches else 0
    cards: list[DetectedCard] = []
    for bar in ordered:
        if vertical:
            card_h = _card_span(page, bar, pitch, vertical=True)
            card_w = min(width - bar.x, max(20, int(card_h * _CARD_ASPECT)))
            crop = page[bar.y : bar.y + card_h, bar.x : bar.x + card_w]
            if crop.size == 0:
                continue
            upright = np.ascontiguousarray(np.rot90(crop, 3))
        else:
            if pitch:
                card_h = min(height - bar.y, max(20, int(pitch * 0.96)))
                card_w = min(width - bar.x, max(20, int(card_h / _CARD_ASPECT)))
            else:
                card_w = _card_span(page, bar, 0, vertical=False)
                card_h = min(height - bar.y, max(20, int(card_w * _CARD_ASPECT)))
            upright = page[bar.y : bar.y + card_h, bar.x : bar.x + card_w]
        if upright.size == 0:
            continue
        # The bar already fixes the rotation: vertical bars are turned so the header
        # is on top, horizontal bars were already upright. Re-scoring all four turns
        # can lock onto the blue stamp instead of the header.
        _, brand = _header_score(upright)
        cards.append(
            DetectedCard(
                upright,
                float(bar.x + card_w / 2),
                float(bar.y + card_h / 2),
                brand or bar.brand,
            )
        )
    return cards


def _card_span(page: np.ndarray, bar: _Bar, pitch: int, vertical: bool) -> int:
    height, width = page.shape[:2]
    if pitch:
        return max(20, min(height - bar.y, int(pitch * 0.96)))
    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    if vertical:
        start = min(height - 1, bar.y + bar.h)
        limit = min(height, bar.y + int(max(bar.h, 40) * 2.4))
        x1 = min(width, bar.x + max(bar.w * 4, int(bar.h * 1.3)))
        end = _white_gap(gray, start, limit, bar.x, x1, along_rows=True)
        if end - bar.y > int(bar.h * 1.15):
            return max(20, min(height - bar.y, end - bar.y))
        return max(20, min(height - bar.y, bar.h))
    start = min(width - 1, bar.x + bar.w)
    limit = min(width, bar.x + int(max(bar.w, 40) * 2.2))
    y1 = min(height, bar.y + max(bar.h * 6, int(bar.w)))
    end = _white_gap(gray, start, limit, bar.y, y1, along_rows=False)
    if end - bar.x > int(bar.w * 1.15):
        return max(20, min(width - bar.x, end - bar.x))
    return max(20, min(width - bar.x, bar.w))


def _white_gap(gray: np.ndarray, start: int, limit: int, cross0: int, cross1: int, along_rows: bool) -> int:
    """Index where a run of blank lines begins. `limit` when the content runs off the page."""
    run = 0
    for pos in range(start, max(start, limit)):
        line = gray[pos, cross0:cross1] if along_rows else gray[cross0:cross1, pos]
        pale = line.size == 0 or float(np.mean(line > 210)) > 0.92
        if pale:
            run += 1
            if run >= 12:
                return pos - 11
        else:
            run = 0
    return limit


def _from_contours(page: np.ndarray) -> list[DetectedCard]:
    height, width = page.shape[:2]
    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 30, 100)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    page_area = float(height * width)
    cards: list[DetectedCard] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < page_area * 0.02 or area > page_area * 0.92:
            continue
        rect = cv2.minAreaRect(contour)
        (_, _), (side_a, side_b), _ = rect
        long_side, short_side = max(side_a, side_b), min(side_a, side_b)
        if short_side < 40:
            continue
        ratio = long_side / max(1.0, short_side)
        if ratio < 1.2 or ratio > 2.6:
            continue
        corners = cv2.boxPoints(rect)
        warped = _warp(page, corners)
        upright, brand = orient_upright(warped)
        center = np.mean(corners, axis=0)
        cards.append(DetectedCard(upright, float(center[0]), float(center[1]), brand))
    return cards


def _long_edge(box: np.ndarray) -> np.ndarray:
    lengths = []
    for index in range(4):
        start = box[index]
        end = box[(index + 1) % 4]
        lengths.append((np.linalg.norm(end - start), end - start))
    lengths.sort(key=lambda item: item[0], reverse=True)
    return lengths[0][1]


def _oriented_box(origin: np.ndarray, width_vec: np.ndarray, height_dir: np.ndarray, height: float) -> np.ndarray:
    half_w = width_vec / 2.0
    full_h = height_dir / np.linalg.norm(height_dir) * height
    # origin is the header centre. The body extends along height_dir.
    top_left = origin - half_w
    top_right = origin + half_w
    bottom_right = top_right + full_h
    bottom_left = top_left + full_h
    return np.array([top_left, top_right, bottom_right, bottom_left], dtype=np.float32)


def _inside(corners: np.ndarray, width: int, height: int, margin: int) -> bool:
    xs = corners[:, 0]
    ys = corners[:, 1]
    return bool(xs.min() >= margin and ys.min() >= margin and xs.max() <= width - margin and ys.max() <= height - margin)


def _warp(image: np.ndarray, corners: np.ndarray) -> np.ndarray:
    ordered = _order_corners(corners)
    width = int(max(np.linalg.norm(ordered[1] - ordered[0]), np.linalg.norm(ordered[2] - ordered[3])))
    height = int(max(np.linalg.norm(ordered[3] - ordered[0]), np.linalg.norm(ordered[2] - ordered[1])))
    width = max(width, 20)
    height = max(height, 20)
    destination = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(ordered, destination)
    return cv2.warpPerspective(image, matrix, (width, height), flags=cv2.INTER_NEAREST)


def _order_corners(corners: np.ndarray) -> np.ndarray:
    pts = np.array(corners, dtype=np.float32)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).ravel()
    ordered = np.zeros((4, 2), dtype=np.float32)
    ordered[0] = pts[np.argmin(s)]
    ordered[2] = pts[np.argmax(s)]
    ordered[1] = pts[np.argmin(diff)]
    ordered[3] = pts[np.argmax(diff)]
    return ordered


def _merge(primary: list[DetectedCard], extra: list[DetectedCard]) -> list[DetectedCard]:
    combined = list(primary)
    for card in extra:
        if all(_iou_centers(card, other) < 0.45 for other in combined):
            combined.append(card)
    return combined


def _suppress_overlaps(cards: list[DetectedCard]) -> list[DetectedCard]:
    ranked = sorted(cards, key=lambda card: card.image.shape[0] * card.image.shape[1], reverse=True)
    kept: list[DetectedCard] = []
    for card in ranked:
        if all(_iou_centers(card, other) < 0.45 for other in kept):
            kept.append(card)
    return kept


def _iou_centers(a: DetectedCard, b: DetectedCard) -> float:
    """Approximate overlap from card size and centre distance. Good enough to drop double hits."""
    aw, ah = a.image.shape[1], a.image.shape[0]
    bw, bh = b.image.shape[1], b.image.shape[0]
    ax0, ay0 = a.center_x - aw / 2, a.center_y - ah / 2
    ax1, ay1 = ax0 + aw, ay0 + ah
    bx0, by0 = b.center_x - bw / 2, b.center_y - bh / 2
    bx1, by1 = bx0 + bw, by0 + bh
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / union


def _order_cards(cards: list[DetectedCard], page_width: int) -> list[DetectedCard]:
    columns: list[list[DetectedCard]] = []
    for card in sorted(cards, key=lambda item: item.center_x):
        placed = False
        for column in columns:
            if abs(card.center_x - column[0].center_x) < max(80, page_width * 0.12):
                column.append(card)
                placed = True
                break
        if not placed:
            columns.append([card])
    columns.sort(key=lambda column: float(np.mean([item.center_x for item in column])))
    ordered: list[DetectedCard] = []
    for column in columns:
        column.sort(key=lambda item: item.center_y)
        ordered.extend(column)
    return ordered
