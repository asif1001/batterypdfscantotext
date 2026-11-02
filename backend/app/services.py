"""Shared card rules: flags, duplicates, audit, part numbers, JSON shapes."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import AuditLog, Batch, Card, PartNumber, PartNumberHistory, User
from app.rules import BATCH_RE, SERIAL_RE, full_serial, is_missing, normalize_type, split_full_serial, validate_card


def audit(db: Session, user: User | None, action: str, entity_type: str, entity_id: str | None, detail: dict | None = None) -> None:
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail or {},
        )
    )


def refresh_card_flags(card: Card, *, uploaded_on: date, max_age_months: int, stamp_expected: str | None) -> None:
    issues = validate_card(
        serial=card.serial,
        batch_code=card.batch_code,
        battery_type=card.battery_type,
        brand=card.brand,
        tel=card.tel,
        date_of_sale=card.date_of_sale,
        date_raw=card.date_raw,
        uploaded_on=uploaded_on,
        max_age_months=max_age_months,
        stamp_code=card.stamp_code,
        stamp_code_expected=stamp_expected,
        part_no=card.part_no,
        dealer_text=card.dealer_text,
        make_model=card.make_model,
        vehicle_no=card.vehicle_no,
    )
    flags = [issue.code if issue.field is None else f"{issue.code}:{issue.field}" for issue in issues]
    if card.duplicate_kind == "claim":
        flags.append("claim")
    elif card.duplicate_kind:
        flags.append("duplicate")
    card.flags = flags

    field_flags = dict(card.field_flags or {})
    # Keep source marks (qr, disagree, manual) and rebuild check marks.
    preserved = {}
    for key, marks in field_flags.items():
        keep = [mark for mark in marks if mark in {"qr", "header", "disagree", "manual", "unclear"}]
        if keep:
            preserved[key] = keep
    for issue in issues:
        if not issue.field:
            continue
        marks = list(preserved.get(issue.field) or [])
        token = "missing" if issue.code == "missing" else issue.code
        if token not in marks:
            marks.append(token)
        preserved[issue.field] = marks
    if card.duplicate_kind:
        marks = list(preserved.get("serial") or [])
        token = "claim" if card.duplicate_kind == "claim" else "duplicate"
        if token not in marks:
            marks.append(token)
        preserved["serial"] = marks
    card.field_flags = preserved


def link_duplicates(db: Session, batch: Batch) -> None:
    """Flag repeats inside this PDF and against every earlier card that was kept."""
    cards = (
        db.query(Card)
        .filter(Card.batch_id == batch.id, Card.excluded.is_(False))
        .order_by(Card.page, Card.position)
        .all()
    )
    first_in_batch: dict[str, Card] = {}
    for card in cards:
        card.duplicate_of_card_id = None
        card.duplicate_kind = None
        if not card.serial:
            continue
        earlier = first_in_batch.get(card.serial)
        if earlier is not None:
            card.duplicate_of_card_id = earlier.id
            card.duplicate_kind = "within_pdf"
            if earlier.duplicate_kind is None:
                earlier.duplicate_kind = "within_pdf"
                earlier.duplicate_of_card_id = card.id
            continue
        first_in_batch[card.serial] = card
        prior = (
            db.query(Card)
            .join(Batch, Card.batch_id == Batch.id)
            .filter(
                Card.serial == card.serial,
                Card.id != card.id,
                Card.excluded.is_(False),
                Batch.id != batch.id,
                Batch.uploaded_at <= batch.uploaded_at,
            )
            .order_by(Batch.uploaded_at.asc(), Card.page.asc(), Card.position.asc())
            .first()
        )
        if prior is None:
            continue
        card.duplicate_of_card_id = prior.id
        card.duplicate_kind = "claim" if _conflicts(prior, card) else "prior_batch"


def _conflicts(prior: Card, card: Card) -> bool:
    phone_differs = not is_missing(prior.tel) and not is_missing(card.tel) and _norm(prior.tel) != _norm(card.tel)
    vehicle_differs = (
        not is_missing(prior.vehicle_no)
        and not is_missing(card.vehicle_no)
        and _norm(prior.vehicle_no) != _norm(card.vehicle_no)
    )
    return phone_differs or vehicle_differs


def _norm(value: str | None) -> str:
    return "".join((value or "").split()).upper()


def part_and_serial_ok(card: Card) -> bool:
    """A card needs no review when both the part number and the full serial (serial + batch) are present and valid."""
    if card.excluded or is_missing(card.part_no):
        return False
    if not card.serial or not SERIAL_RE.fullmatch(card.serial):
        return False
    if not card.batch_code or not BATCH_RE.fullmatch(card.batch_code):
        return False
    return True


def needs_attention(card: Card) -> bool:
    if card.excluded or card.status == "approved":
        return False
    return not part_and_serial_ok(card)


def card_json(card: Card) -> dict:
    sources: dict[str, list] = {}
    for field in sorted(card.fields, key=lambda item: (item.field, item.created_at.isoformat() if item.created_at else "")):
        sources.setdefault(field.field, []).append(
            {
                "value": field.value,
                "source": field.source,
                "confidence": field.confidence,
                "flags": field.flags or [],
            }
        )
    return {
        "id": card.id,
        "batch_id": card.batch_id,
        "page": card.page,
        "position": card.position,
        "brand": card.brand,
        "battery_type": card.battery_type,
        "serial": card.serial,
        "batch_code": card.batch_code,
        "serial_full": full_serial(card.serial, card.batch_code),
        "review_status": "pending" if needs_attention(card) else ("approved" if card.status == "approved" else "ok"),
        "part_no": card.part_no,
        "date_of_sale": card.date_of_sale.isoformat() if card.date_of_sale else None,
        "date_raw": card.date_raw,
        "vehicle_no": card.vehicle_no,
        "make_model": card.make_model,
        "tel": card.tel,
        "bill_no": card.bill_no,
        "dealer_text": card.dealer_text,
        "customer_name": card.customer_name,
        "signature_present": card.signature_present,
        "stamp_code": card.stamp_code,
        "stamp_branch": card.stamp_branch,
        "stamp_tel": card.stamp_tel,
        "stamp_condition": card.stamp_condition,
        "dealer_stamp": card.dealer_stamp,
        "qr_verified": card.qr_verified,
        "status": card.status,
        "excluded": card.excluded,
        "duplicate_of_card_id": card.duplicate_of_card_id,
        "duplicate_kind": card.duplicate_kind,
        "remarks": card.remarks,
        "flags": card.flags or [],
        "field_flags": card.field_flags or {},
        "needs_attention": needs_attention(card),
        "sources": sources,
        "ai_skipped": card.ai_skipped,
    }


def batch_json(batch: Batch) -> dict:
    return {
        "id": batch.id,
        "file_name": batch.file_name,
        "stamp_code_from_name": batch.stamp_code_from_name,
        "uploaded_by": batch.uploaded_by,
        "uploaded_at": batch.uploaded_at.isoformat() if batch.uploaded_at else None,
        "status": batch.status,
        "page_count": batch.page_count,
        "card_count": batch.card_count,
        "ai_cost": batch.ai_cost,
        "progress": batch.progress or {},
        "error": batch.error,
    }


def user_json(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role, "active": user.active}


def apply_part_to_open_cards(db: Session, battery_type: str, part_no: str) -> int:
    norm = normalize_type(battery_type)
    updated = 0
    cards = (
        db.query(Card)
        .join(Batch, Card.batch_id == Batch.id)
        .filter(Batch.status.notin_(["approved", "exported"]), Card.status != "approved")
        .all()
    )
    for card in cards:
        if normalize_type(card.battery_type) != norm:
            continue
        card.part_no = part_no
        uploaded = card.batch.uploaded_at.date() if card.batch and card.batch.uploaded_at else date.today()
        refresh_card_flags(
            card,
            uploaded_on=uploaded,
            max_age_months=12,
            stamp_expected=card.batch.stamp_code_from_name if card.batch else None,
        )
        updated += 1
    return updated


def record_part_history(db: Session, user: User | None, row: PartNumber | None, battery_type: str, old: str | None, new: str | None, action: str) -> None:
    db.add(
        PartNumberHistory(
            part_number_id=row.id if row else None,
            battery_type=battery_type,
            old_part_no=old,
            new_part_no=new,
            action=action,
            user_id=user.id if user else None,
        )
    )
