"""Scan job. A web request only stores the PDF and enqueues this function."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import cv2
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai.extract import run_handwriting
from app.config import settings
from app.db import SessionLocal
from app.defaults import HANDWRITING_FIELDS
from app.models import AiUsage, Batch, Card, CardField, CardImage
from app.pipeline.decode import read_label
from app.pipeline.detect import detect_cards
from app.pipeline.fields import build_field_strip, field_ink
from app.pipeline.render import render_pdf
from app.pipeline.stamp import read_stamps
from app.rules import is_missing, normalize_type, parse_sale_date, validate_card
from app.services import link_duplicates, refresh_card_flags
from app.settings_store import get_config, get_zones


def enqueue_scan(batch_id: str) -> None:
    if settings.worker_inline:
        threading.Thread(target=scan_batch, args=(batch_id,), daemon=True, name=f"scan-{batch_id[:8]}").start()
        return
    from redis import Redis
    from rq import Queue

    queue = Queue("scans", connection=Redis.from_url(settings.redis_url))
    queue.enqueue("app.jobs.scan_batch", batch_id, job_timeout=60 * 60)


def scan_batch(batch_id: str) -> None:
    db = SessionLocal()
    try:
        batch = db.get(Batch, batch_id)
        if batch is None:
            return
        batch.status = "scanning"
        batch.error = None
        batch.progress = {"stage": "render", "percent": 1, "message": "Rendering pages"}
        db.commit()
        zones = get_zones(db)
        pages = render_pdf(batch.storage_path, dpi=settings.render_dpi)
        batch.page_count = len(pages)
        for existing in list(batch.cards):
            db.delete(existing)
        db.commit()

        prepared: list[dict] = []
        card_images: dict[str, str] = {}
        seen = 0
        for page_number, page in enumerate(pages, start=1):
            _progress(db, batch, stage="detect", page=page_number, pages=len(pages), percent=5 + int(40 * page_number / max(1, len(pages))), message=f"Finding cards on page {page_number}")
            found = detect_cards(page)
            for position, detected in enumerate(found, start=1):
                seen += 1
                card, prep = _store_card(db, batch, page_number, position, detected.image, detected.brand, zones)
                if prep:
                    prepared.append(prep)
                    card_images[card.id] = prep["image_path"]
        batch.card_count = seen
        db.commit()
        _progress(db, batch, stage="ai", percent=60, message="Reading handwriting", cards=seen)

        def rebuild(card_id: str, fields: list[str]):
            path = card_images.get(card_id)
            if not path:
                return b"", ""
            image = cv2.imread(path)
            if image is None:
                return b"", ""
            ink = {name: name in fields for name in HANDWRITING_FIELDS}
            strip, _names = build_field_strip(image, zones, ink)
            if strip is None:
                return b"", ""
            ok, encoded = cv2.imencode(".jpg", strip, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if not ok:
                return b"", ""
            data = encoded.tobytes()
            return data, hashlib.sha256(data).hexdigest()

        def extra_low(card_id: str) -> list[str]:
            card = db.get(Card, card_id)
            if card is None:
                return []
            return _invalid_handwriting(card, batch)

        stats = run_handwriting(db, batch.id, prepared, rebuild=rebuild, extra_low=extra_low)
        _progress(db, batch, stage="duplicates", percent=90, message="Checking duplicates", ai=stats)
        link_duplicates(db, batch)
        config = get_config(db)
        for card in db.query(Card).filter(Card.batch_id == batch.id).all():
            refresh_card_flags(card, uploaded_on=batch.uploaded_at.date(), max_age_months=int(config.get("date_max_age_months") or 12), stamp_expected=batch.stamp_code_from_name)
        spent = db.query(func.coalesce(func.sum(AiUsage.cost), 0)).filter(AiUsage.batch_id == batch.id, AiUsage.cache_hit.is_(False)).scalar()
        batch.ai_cost = float(spent or 0)
        batch.status = "needs_review" if seen else "needs_review"
        batch.progress = {
            "stage": "done",
            "percent": 100,
            "message": f"Found {seen} cards" if seen else "No cards found",
            "cards": seen,
            "ai": stats,
        }
        if seen == 0:
            batch.error = "No cards were found. Check the scan and the zone calibration."
        db.commit()
    except Exception as exc:
        db.rollback()
        failed = db.get(Batch, batch_id)
        if failed is not None:
            failed.status = "failed"
            failed.error = str(exc)[:2000]
            failed.progress = {"stage": "failed", "percent": 100, "message": str(exc)[:300]}
            db.commit()
    finally:
        db.close()


def _store_card(db: Session, batch: Batch, page: int, position: int, image, brand: str | None, zones: dict):
    label = read_label(image, zones)
    ink = field_ink(image, zones)
    stamps = read_stamps(image)
    part_no = _lookup_part(db, label.get("battery_type"))
    folder = Path(batch.storage_path).parent / "cards"
    folder.mkdir(parents=True, exist_ok=True)
    full_path = folder / f"p{page}_{position}.jpg"
    cv2.imwrite(str(full_path), image, [int(cv2.IMWRITE_JPEG_QUALITY), 85])

    strip, ink_names = build_field_strip(image, zones, ink)
    strip_path = None
    digest = None
    strip_jpeg = None
    if strip is not None:
        ok, encoded = cv2.imencode(".jpg", strip, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if ok:
            strip_jpeg = encoded.tobytes()
            digest = hashlib.sha256(strip_jpeg).hexdigest()
            strip_path = folder / f"p{page}_{position}_strip.jpg"
            strip_path.write_bytes(strip_jpeg)

    card = Card(
        batch_id=batch.id,
        page=page,
        position=position,
        brand=brand,
        battery_type=label.get("battery_type"),
        serial=label.get("serial"),
        batch_code=label.get("batch_code"),
        part_no=part_no,
        qr_verified=bool(label.get("qr_verified")),
        stamp_code=stamps.get("stamp_code"),
        stamp_branch=stamps.get("stamp_branch"),
        stamp_tel=stamps.get("stamp_tel"),
        stamp_condition=stamps.get("stamp_condition"),
        dealer_stamp=stamps.get("dealer_stamp"),
        status="pending",
        field_flags={},
        flags=[],
    )
    for name in HANDWRITING_FIELDS:
        if name == "signature":
            if not ink.get(name):
                card.signature_present = False
            continue
        if not ink.get(name):
            setattr(card, name, "0")
            if name == "date_of_sale":
                card.date_raw = "0"
                card.date_of_sale = None
    db.add(card)
    db.flush()
    db.add(CardImage(card_id=card.id, full_crop_path=str(full_path), field_strip_path=str(strip_path) if strip_path else None, image_hash=digest))
    _add_exact_fields(db, card, label, stamps)
    for name in HANDWRITING_FIELDS:
        if ink.get(name):
            continue
        db.add(CardField(card_id=card.id, field=name, value="0", source="rule", confidence=1, flags=["missing"]))
    db.flush()
    prepared = None
    if strip_jpeg and ink_names:
        prepared = {
            "card_id": card.id,
            "strip_jpeg": strip_jpeg,
            "image_hash": digest,
            "ink_fields": ink_names,
            "image_path": str(full_path),
        }
    return card, prepared


def _add_exact_fields(db: Session, card: Card, label: dict, stamps: dict) -> None:
    mapping = {
        "serial": (label.get("serial"), label.get("serial_source") or "", 1.0 if label.get("qr_verified") else 0.8),
        "batch_code": (label.get("batch_code"), label.get("batch_source") or "", 1.0 if label.get("batch_source") == "qr" else 0.8),
        "battery_type": (label.get("battery_type"), label.get("type_source") or "", 1.0 if label.get("type_source") == "qr" else 0.8),
        "stamp_code": (stamps.get("stamp_code"), "ocr", 0.9 if stamps.get("stamp_code") else None),
        "stamp_branch": (None if stamps.get("stamp_branch") is None else str(stamps.get("stamp_branch")), "ocr", 0.9),
    }
    flags = dict(card.field_flags or {})
    for field, (value, source, confidence) in mapping.items():
        if not value or not source:
            continue
        mark = ["qr"] if source == "qr" else []
        flags[field] = mark
        db.add(CardField(card_id=card.id, field=field, value=str(value), source=source, confidence=confidence, flags=mark))
    if card.brand:
        flags["brand"] = ["header"]
        db.add(CardField(card_id=card.id, field="brand", value=card.brand, source="header", confidence=0.95, flags=["header"]))
    card.field_flags = flags


def _lookup_part(db: Session, battery_type: str | None) -> str | None:
    if not battery_type:
        return None
    from app.models import PartNumber

    row = (
        db.query(PartNumber)
        .filter(PartNumber.normalized_type == normalize_type(battery_type), PartNumber.active.is_(True))
        .one_or_none()
    )
    return row.part_no if row else None


def _invalid_handwriting(card: Card, batch: Batch) -> list[str]:
    issues = validate_card(
        serial=card.serial,
        batch_code=card.batch_code,
        battery_type=card.battery_type,
        brand=card.brand,
        tel=card.tel,
        date_of_sale=card.date_of_sale or parse_sale_date(card.date_raw),
        date_raw=card.date_raw,
        uploaded_on=batch.uploaded_at.date(),
        max_age_months=12,
        stamp_code=card.stamp_code,
        stamp_code_expected=batch.stamp_code_from_name,
        part_no=card.part_no or "skip",
        dealer_text=card.dealer_text,
        make_model=card.make_model,
        vehicle_no=card.vehicle_no,
    )
    bad = []
    for issue in issues:
        if issue.level == "error" and issue.code == "invalid" and issue.field in {"tel", "date_of_sale"}:
            if not is_missing(card.tel if issue.field == "tel" else card.date_raw):
                bad.append(issue.field)
    return bad


def _progress(db: Session, batch: Batch, **payload) -> None:
    current = dict(batch.progress or {})
    current.update(payload)
    batch.progress = current
    db.commit()
