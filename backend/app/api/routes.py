"""HTTP API."""

from __future__ import annotations

import shutil
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from openpyxl import Workbook, load_workbook
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import get_db
from app.deps import current_user, require
from app.excel.export import build_default_template, card_export_view, export_data_loader, export_detailed
from app.jobs import enqueue_scan
from app.models import AiUsage, AuditLog, Batch, Card, CardField, PartNumber, PartNumberHistory, User
from app.rules import is_missing, normalize_type, parse_sale_date, split_full_serial, stamp_code_from_filename
from app.security import create_token, hash_password, verify_password
from app.services import (
    apply_part_to_open_cards,
    audit,
    batch_json,
    card_json,
    link_duplicates,
    record_part_history,
    refresh_card_flags,
    user_json,
)
from app.settings_store import get_config, get_zones, public_config, save_config, save_zones

router = APIRouter(prefix="/api")

OPERATORS = ("operator", "supervisor", "admin")
SUPERVISORS = ("supervisor", "admin")


class LoginIn(BaseModel):
    email: str
    password: str


class PasswordIn(BaseModel):
    old_password: str
    new_password: str


class UserIn(BaseModel):
    email: str
    name: str
    password: str
    role: str = "operator"


class UserPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    active: bool | None = None
    password: str | None = None


class CardPatch(BaseModel):
    brand: str | None = None
    battery_type: str | None = None
    serial: str | None = None
    batch_code: str | None = None
    part_no: str | None = None
    date_raw: str | None = None
    vehicle_no: str | None = None
    make_model: str | None = None
    tel: str | None = None
    bill_no: str | None = None
    dealer_text: str | None = None
    customer_name: str | None = None
    signature_present: bool | None = None
    stamp_code: str | None = None
    stamp_branch: int | None = None
    stamp_tel: str | None = None
    stamp_condition: str | None = None
    dealer_stamp: str | None = None
    remarks: str | None = None


class PartIn(BaseModel):
    battery_type: str
    part_no: str


@router.get("/health")
def health():
    return {"ok": True, "app": settings.app_name}


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.strip().lower()).one_or_none()
    if user is None or not user.active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is wrong")
    audit(db, user, "login", "user", user.id, {})
    db.commit()
    return {"access_token": create_token(user.id, user.role), "user": user_json(user)}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return user_json(user)


@router.post("/auth/password")
def change_password(body: PasswordIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is wrong")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Use at least 8 characters")
    user.password_hash = hash_password(body.new_password)
    audit(db, user, "password_change", "user", user.id, {})
    db.commit()
    return {"ok": True}


@router.get("/users")
def list_users(user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    return [user_json(row) for row in db.query(User).order_by(User.email).all()]


@router.post("/users")
def create_user(body: UserIn, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    if body.role not in {"operator", "supervisor", "admin"}:
        raise HTTPException(status_code=400, detail="Unknown role")
    email = body.email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="That email is already in use")
    row = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password), role=body.role)
    db.add(row)
    db.flush()
    audit(db, user, "user_create", "user", row.id, {"email": email, "role": body.role})
    db.commit()
    return user_json(row)


@router.patch("/users/{user_id}")
def patch_user(user_id: str, body: UserPatch, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    row = db.get(User, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    before = user_json(row)
    if body.name is not None:
        row.name = body.name
    if body.role is not None:
        if body.role not in {"operator", "supervisor", "admin"}:
            raise HTTPException(status_code=400, detail="Unknown role")
        row.role = body.role
    if body.active is not None:
        row.active = body.active
    if body.password:
        row.password_hash = hash_password(body.password)
    audit(db, user, "user_update", "user", row.id, {"before": before, "after": user_json(row)})
    db.commit()
    return user_json(row)


@router.get("/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_cost = (
        db.query(func.coalesce(func.sum(AiUsage.cost), 0))
        .filter(AiUsage.created_at >= start, AiUsage.cache_hit.is_(False))
        .scalar()
    )
    usage_rows = (
        db.query(
            func.date(AiUsage.created_at),
            AiUsage.provider,
            func.sum(AiUsage.input_tokens),
            func.sum(AiUsage.output_tokens),
            func.sum(AiUsage.cost),
        )
        .filter(AiUsage.cache_hit.is_(False))
        .group_by(func.date(AiUsage.created_at), AiUsage.provider)
        .all()
    )
    recent = db.query(Batch).order_by(Batch.uploaded_at.desc()).limit(8).all()
    return {
        "batches": db.query(func.count(Batch.id)).scalar() or 0,
        "cards": db.query(func.count(Card.id)).filter(Card.excluded.is_(False)).scalar() or 0,
        "pending_review": db.query(func.count(Batch.id)).filter(Batch.status == "needs_review").scalar() or 0,
        "duplicates": db.query(func.count(Card.id)).filter(Card.duplicate_kind.isnot(None), Card.excluded.is_(False)).scalar() or 0,
        "ai_cost_month": float(month_cost or 0),
        "usage": [
            {
                "day": str(row[0]),
                "provider": row[1],
                "input_tokens": int(row[2] or 0),
                "output_tokens": int(row[3] or 0),
                "cost": float(row[4] or 0),
            }
            for row in usage_rows
        ],
        "recent_batches": [batch_json(batch) for batch in recent],
    }


@router.post("/batches/upload")
async def upload_batches(
    files: list[UploadFile] = File(...),
    user: User = Depends(require(*OPERATORS)),
    db: Session = Depends(get_db),
):
    if not files:
        raise HTTPException(status_code=400, detail="Choose a PDF")
    created = []
    for upload in files:
        name = upload.filename or "cards.pdf"
        if not name.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"{name} is not a PDF")
        batch = Batch(
            file_name=Path(name).name,
            stamp_code_from_name=stamp_code_from_filename(name),
            uploaded_by=user.id,
            status="uploaded",
            progress={"stage": "uploaded", "percent": 0, "message": "Queued"},
        )
        db.add(batch)
        db.flush()
        folder = settings.storage_path / "batches" / batch.id
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "source.pdf"
        with target.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle)
        if target.stat().st_size > 80 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="PDF is larger than 80 MB")
        batch.storage_path = str(target)
        audit(db, user, "upload", "batch", batch.id, {"file_name": batch.file_name})
        created.append(batch)
    db.commit()
    if settings.auto_scan:
        for batch in created:
            enqueue_scan(batch.id)
    return {"batches": [batch_json(batch) for batch in created]}


@router.get("/batches")
def list_batches(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Batch).order_by(Batch.uploaded_at.desc()).limit(200).all()
    return [batch_json(row) for row in rows]


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch_json(batch)


@router.post("/batches/{batch_id}/rescan")
def rescan(batch_id: str, user: User = Depends(require(*OPERATORS)), db: Session = Depends(get_db)):
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch.status in {"approved", "exported"}:
        raise HTTPException(status_code=409, detail="Approved batches are not rescanned")
    batch.status = "uploaded"
    batch.progress = {"stage": "uploaded", "percent": 0, "message": "Queued"}
    db.commit()
    if settings.auto_scan:
        enqueue_scan(batch.id)
    return batch_json(batch)


@router.get("/batches/{batch_id}/cards")
def batch_cards(batch_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(Card)
        .options(selectinload(Card.fields))
        .filter(Card.batch_id == batch_id)
        .order_by(Card.page, Card.position)
        .all()
    )
    return [_with_duplicate(db, row) for row in rows]


@router.get("/cards/{card_id}")
def get_card(card_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    card = db.query(Card).options(selectinload(Card.fields)).filter(Card.id == card_id).one_or_none()
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return _with_duplicate(db, card)


@router.get("/cards/{card_id}/image")
def card_image(card_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    card = db.get(Card, card_id)
    if card is None or card.image is None or not card.image.full_crop_path:
        raise HTTPException(status_code=404, detail="Image not found")
    path = Path(card.image.full_crop_path).resolve()
    root = settings.storage_path.resolve()
    if root not in path.parents and path != root:
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path, media_type="image/jpeg")


@router.patch("/cards/{card_id}")
def patch_card(card_id: str, body: CardPatch, user: User = Depends(require(*OPERATORS)), db: Session = Depends(get_db)):
    card = db.query(Card).options(selectinload(Card.fields)).filter(Card.id == card_id).one_or_none()
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    changes = body.model_dump(exclude_unset=True)
    before = {key: getattr(card, key) if key != "date_raw" else card.date_raw for key in changes}
    for key, value in changes.items():
        if key == "date_raw":
            card.date_raw = value
            card.date_of_sale = parse_sale_date(value)
        elif key == "stamp_branch":
            card.stamp_branch = value
        elif key == "signature_present":
            card.signature_present = value
        elif key == "serial" and isinstance(value, str):
            base, batch = split_full_serial(value)
            card.serial = base or None
            if batch:
                card.batch_code = batch
        else:
            setattr(card, key, value.strip() if isinstance(value, str) else value)
        if key not in {"remarks"}:
            db.add(
                CardField(
                    card_id=card.id,
                    field=key,
                    value="" if value is None else str(value),
                    source="user",
                    confidence=1,
                    flags=["user"],
                )
            )
    if is_missing(card.part_no):
        looked_up = _part_for(db, card.battery_type)
        if looked_up:
            card.part_no = looked_up
    _refresh(db, card)
    if card.batch:
        link_duplicates(db, card.batch)
        _refresh(db, card)
    audit(db, user, "card_edit", "card", card.id, {"before": _plain(before), "after": _plain(changes)})
    db.commit()
    db.refresh(card)
    return _with_duplicate(db, card)


@router.post("/cards/{card_id}/approve")
def approve_card(card_id: str, user: User = Depends(require(*OPERATORS)), db: Session = Depends(get_db)):
    card = db.get(Card, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    _guard_duplicate(user, card, get_config(db))
    if card.excluded:
        raise HTTPException(status_code=400, detail="This card was excluded")
    card.status = "approved"
    audit(db, user, "approve_card", "card", card.id, {})
    _refresh_batch_status(card.batch)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail="This serial is already approved on another card")
    return card_json(card)


@router.post("/cards/{card_id}/keep")
def keep_card(card_id: str, user: User = Depends(require(*OPERATORS)), db: Session = Depends(get_db)):
    card = db.get(Card, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    siblings = (
        db.query(Card)
        .filter(Card.batch_id == card.batch_id, Card.serial == card.serial, Card.id != card.id, Card.excluded.is_(False))
        .all()
    )
    for sibling in siblings:
        if sibling.duplicate_kind == "within_pdf" or card.duplicate_kind == "within_pdf":
            sibling.excluded = True
            sibling.status = "excluded"
    link_duplicates(db, card.batch)
    for row in db.query(Card).filter(Card.batch_id == card.batch_id).all():
        _refresh(db, row)
    audit(db, user, "keep_card", "card", card.id, {"excluded": [row.id for row in siblings]})
    db.commit()
    return _with_duplicate(db, card)


@router.post("/batches/{batch_id}/approve")
def approve_batch(batch_id: str, user: User = Depends(require(*OPERATORS)), db: Session = Depends(get_db)):
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    config = get_config(db)
    blocked = []
    for card in batch.cards:
        if card.excluded or card.status == "approved":
            continue
        try:
            _guard_duplicate(user, card, config)
        except HTTPException as exc:
            blocked.append({"id": card.id, "serial": card.serial, "detail": exc.detail})
    if blocked:
        raise HTTPException(status_code=409, detail={"message": "Some cards need a duplicate decision", "cards": blocked})
    for card in batch.cards:
        if not card.excluded:
            card.status = "approved"
    batch.status = "approved"
    audit(db, user, "approve_batch", "batch", batch.id, {})
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail="A serial in this batch is already approved elsewhere")
    return batch_json(batch)


@router.get("/batches/{batch_id}/export/data-loader")
def export_loader(batch_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    batch = _approved_batch(db, batch_id)
    config = get_config(db)
    template = settings.storage_path / "data_loader_template.xlsx"
    if not template.exists():
        build_default_template(template)
    target = settings.storage_path / "exports" / f"Data_Loader_{batch.file_name}.xlsx"
    cards = [_view(card) for card in _export_cards(batch)]
    export_data_loader(cards, template, target, (config.get("loader") or {}).get("columns"))
    if batch.status != "exported":
        batch.status = "exported"
        audit(db, user, "export_loader", "batch", batch.id, {})
        db.commit()
    return FileResponse(target, filename=target.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/batches/{batch_id}/export/detailed")
def export_detail(batch_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    batch = _approved_batch(db, batch_id)
    target = settings.storage_path / "exports" / f"Warranty_Cards_{batch.file_name}.xlsx"
    export_detailed([_view(card) for card in _export_cards(batch)], target)
    audit(db, user, "export_detailed", "batch", batch.id, {})
    db.commit()
    return FileResponse(target, filename=target.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/search")
def search(
    q: str = "",
    brand: str = "",
    stamp_code: str = "",
    branch: int | None = None,
    batch_id: str = "",
    date_from: str = "",
    date_to: str = "",
    duplicates_only: bool = False,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = _search_query(db, q, brand, stamp_code, branch, batch_id, date_from, date_to, duplicates_only)
    total = rows.count()
    page = rows.order_by(Card.page, Card.position).offset(offset).limit(limit).all()
    return {"total": total, "cards": [_with_duplicate(db, card) for card in page]}


@router.get("/search/export")
def search_export(
    q: str = "",
    brand: str = "",
    stamp_code: str = "",
    branch: int | None = None,
    batch_id: str = "",
    date_from: str = "",
    date_to: str = "",
    duplicates_only: bool = False,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = _search_query(db, q, brand, stamp_code, branch, batch_id, date_from, date_to, duplicates_only).limit(5000).all()
    target = settings.storage_path / "exports" / "search.xlsx"
    export_detailed([_view(card) for card in rows], target)
    return FileResponse(target, filename="search.xlsx", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/duplicates")
def duplicates(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(Card)
        .options(selectinload(Card.fields), selectinload(Card.batch))
        .filter(Card.duplicate_kind.isnot(None), Card.excluded.is_(False))
        .order_by(Card.serial)
        .limit(500)
        .all()
    )
    return [_with_duplicate(db, row) for row in rows]


@router.get("/duplicates/export")
def duplicates_export(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Card).filter(Card.duplicate_kind.isnot(None), Card.excluded.is_(False)).all()
    target = settings.storage_path / "exports" / "duplicates.xlsx"
    export_detailed([_view(card) for card in rows], target)
    return FileResponse(target, filename="duplicates.xlsx", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/part-numbers")
def list_parts(q: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = db.query(PartNumber).filter(PartNumber.active.is_(True))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(PartNumber.battery_type.ilike(like), PartNumber.part_no.ilike(like)))
    rows = query.order_by(PartNumber.battery_type).limit(500).all()
    return [_part_json(row) for row in rows]


@router.post("/part-numbers")
def create_part(body: PartIn, user: User = Depends(require(*OPERATORS)), db: Session = Depends(get_db)):
    row = _upsert_part(db, user, body.battery_type, body.part_no, action="create")
    apply_part_to_open_cards(db, row.battery_type, row.part_no)
    db.commit()
    return _part_json(row)


@router.put("/part-numbers/{part_id}")
def update_part(part_id: str, body: PartIn, user: User = Depends(require(*SUPERVISORS)), db: Session = Depends(get_db)):
    row = db.get(PartNumber, part_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Part not found")
    old = row.part_no
    row.battery_type = body.battery_type.strip()
    row.normalized_type = normalize_type(body.battery_type)
    row.part_no = body.part_no.strip().upper()
    record_part_history(db, user, row, row.battery_type, old, row.part_no, "update")
    apply_part_to_open_cards(db, row.battery_type, row.part_no)
    audit(db, user, "part_update", "part_number", row.id, {"old": old, "new": row.part_no})
    db.commit()
    return _part_json(row)


@router.delete("/part-numbers/{part_id}")
def delete_part(part_id: str, user: User = Depends(require(*SUPERVISORS)), db: Session = Depends(get_db)):
    row = db.get(PartNumber, part_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Part not found")
    record_part_history(db, user, row, row.battery_type, row.part_no, None, "delete")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/part-numbers/import")
async def import_parts(
    file: UploadFile = File(...),
    mode: str = Query("preview"),
    user: User = Depends(require(*SUPERVISORS)),
    db: Session = Depends(get_db),
):
    content = await file.read()
    incoming = _read_part_workbook(content)
    existing = {row.normalized_type: row for row in db.query(PartNumber).all()}
    diff = {"added": [], "changed": [], "unchanged": [], "removed": []}
    seen = set()
    for battery_type, part_no in incoming:
        norm = normalize_type(battery_type)
        seen.add(norm)
        current = existing.get(norm)
        item = {"battery_type": battery_type, "part_no": part_no, "old_part_no": current.part_no if current else None}
        if current is None:
            diff["added"].append(item)
        elif current.part_no != part_no or not current.active:
            diff["changed"].append(item)
        else:
            diff["unchanged"].append(item)
    if mode == "replace":
        for norm, row in existing.items():
            if norm not in seen:
                diff["removed"].append({"battery_type": row.battery_type, "part_no": row.part_no})
    if mode == "preview":
        return diff
    if mode not in {"merge", "replace"}:
        raise HTTPException(status_code=400, detail="mode must be preview, merge or replace")
    for battery_type, part_no in incoming:
        _upsert_part(db, user, battery_type, part_no, action="import")
        apply_part_to_open_cards(db, battery_type, part_no)
    if mode == "replace":
        for norm, row in existing.items():
            if norm not in seen:
                record_part_history(db, user, row, row.battery_type, row.part_no, None, "delete")
                db.delete(row)
    audit(db, user, "part_import", "part_number", None, {"mode": mode, "count": len(incoming)})
    db.commit()
    return diff


@router.get("/part-numbers/export")
def export_parts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Parts"
    sheet.append(["Battery Type", "BATTERY_ITEM_CODE"])
    for row in db.query(PartNumber).filter(PartNumber.active.is_(True)).order_by(PartNumber.battery_type).all():
        sheet.append([row.battery_type, row.part_no])
    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=Battery_Part_No.xlsx"},
    )


@router.get("/part-numbers/history")
def part_history(user: User = Depends(require(*SUPERVISORS)), db: Session = Depends(get_db)):
    rows = db.query(PartNumberHistory).order_by(PartNumberHistory.created_at.desc()).limit(200).all()
    return [
        {
            "id": row.id,
            "battery_type": row.battery_type,
            "old_part_no": row.old_part_no,
            "new_part_no": row.new_part_no,
            "action": row.action,
            "user_id": row.user_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@router.get("/part-numbers/unmatched")
def unmatched(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(Card.battery_type, func.count(Card.id))
        .join(Batch, Card.batch_id == Batch.id)
        .filter(Batch.status.notin_(["approved", "exported"]), or_(Card.part_no.is_(None), Card.part_no == ""))
        .group_by(Card.battery_type)
        .all()
    )
    return [{"battery_type": row[0] or "", "count": row[1]} for row in rows if row[0]]


@router.post("/part-numbers/assign")
def assign_part(body: PartIn, user: User = Depends(require(*OPERATORS)), db: Session = Depends(get_db)):
    row = _upsert_part(db, user, body.battery_type, body.part_no, action="assign")
    updated = apply_part_to_open_cards(db, row.battery_type, row.part_no)
    db.commit()
    return {"part": _part_json(row), "cards_updated": updated}


@router.get("/settings")
def read_settings(user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    return {"config": public_config(get_config(db)), "zones": get_zones(db)}


@router.put("/settings")
def write_settings(body: dict, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    saved = save_config(db, body)
    audit(db, user, "settings", "settings", "config", {})
    db.commit()
    return public_config(saved)


@router.put("/settings/zones")
def write_zones(body: dict, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    zones = save_zones(db, body.get("zones") or body)
    audit(db, user, "zones", "settings", "zones", {})
    db.commit()
    return zones


@router.post("/settings/template")
async def upload_template(file: UploadFile = File(...), user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    content = await file.read()
    workbook = load_workbook(BytesIO(content))
    if "Data Loader" not in workbook.sheetnames:
        raise HTTPException(status_code=400, detail='The workbook needs a sheet named "Data Loader"')
    target = settings.storage_path / "data_loader_template.xlsx"
    target.write_bytes(content)
    audit(db, user, "template", "settings", "template", {"file_name": file.filename})
    db.commit()
    return {"ok": True, "sheets": workbook.sheetnames}


@router.get("/audit")
def audit_log(user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    rows = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(300).all()
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "action": row.action,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "detail": row.detail,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@router.post("/tools/inspect")
async def inspect_card(file: UploadFile = File(...), user: User = Depends(require(*OPERATORS)), db: Session = Depends(get_db)):
    """Run Stage 1 on one upright card image. Nothing is stored."""
    import cv2
    import numpy as np

    from app.pipeline.decode import read_label
    from app.pipeline.fields import field_ink
    from app.pipeline.stamp import read_stamps

    raw = await file.read()
    array = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Could not read that image")
    zones = get_zones(db)
    return {"label": read_label(image, zones), "ink": field_ink(image, zones), "stamp": read_stamps(image)}


def _with_duplicate(db: Session, card: Card) -> dict:
    payload = card_json(card)
    if card.duplicate_of_card_id:
        other = db.get(Card, card.duplicate_of_card_id)
        if other is not None:
            payload["duplicate_of"] = {
                "id": other.id,
                "batch_id": other.batch_id,
                "file_name": other.batch.file_name if other.batch else "",
                "uploaded_at": other.batch.uploaded_at.isoformat() if other.batch and other.batch.uploaded_at else None,
                "tel": other.tel,
                "vehicle_no": other.vehicle_no,
                "serial": other.serial,
                "page": other.page,
                "position": other.position,
            }
    return payload


def _refresh(db: Session, card: Card) -> None:
    config = get_config(db)
    uploaded = card.batch.uploaded_at.date() if card.batch and card.batch.uploaded_at else date.today()
    refresh_card_flags(
        card,
        uploaded_on=uploaded,
        max_age_months=int(config.get("date_max_age_months") or 12),
        stamp_expected=card.batch.stamp_code_from_name if card.batch else None,
    )


def _guard_duplicate(user: User, card: Card, config: dict) -> None:
    if not card.duplicate_kind or card.excluded:
        return
    policy = config.get("duplicate_policy") or "warn"
    if policy == "warn":
        return
    if policy == "block":
        raise HTTPException(status_code=409, detail="Duplicate policy is set to block. Keep one card and exclude the other.")
    if policy == "supervisor" and user.role == "operator":
        raise HTTPException(status_code=403, detail="A supervisor has to approve this duplicate.")


def _refresh_batch_status(batch: Batch | None) -> None:
    if batch is None or batch.status in {"uploaded", "scanning", "exported"}:
        return
    active = [card for card in batch.cards if not card.excluded]
    if active and all(card.status == "approved" for card in active):
        batch.status = "approved"


def _approved_batch(db: Session, batch_id: str) -> Batch:
    batch = db.query(Batch).options(selectinload(Batch.cards)).filter(Batch.id == batch_id).one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch.status not in {"approved", "exported"}:
        raise HTTPException(status_code=409, detail="Approve the batch before exporting")
    return batch


def _export_cards(batch: Batch) -> list[Card]:
    return sorted([card for card in batch.cards if not card.excluded], key=lambda card: (card.page, card.position))


def _view(card: Card) -> dict:
    if card.batch is None:
        card.batch = Batch(file_name="")
    return card_export_view(card)


def _search_query(db, q, brand, stamp_code, branch, batch_id, date_from, date_to, duplicates_only):
    query = db.query(Card).options(selectinload(Card.fields), selectinload(Card.batch)).filter(Card.excluded.is_(False))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Card.serial.ilike(like),
                (Card.serial + Card.batch_code).ilike(like),
                Card.tel.ilike(like),
                Card.part_no.ilike(like),
                Card.vehicle_no.ilike(like),
                Card.bill_no.ilike(like),
                Card.customer_name.ilike(like),
                Card.battery_type.ilike(like),
                Card.stamp_code.ilike(like),
            )
        )
    if brand:
        query = query.filter(Card.brand == brand)
    if stamp_code:
        query = query.filter(Card.stamp_code == stamp_code)
    if branch is not None:
        query = query.filter(Card.stamp_branch == branch)
    if batch_id:
        query = query.filter(Card.batch_id == batch_id)
    if date_from:
        query = query.filter(Card.date_of_sale >= date.fromisoformat(date_from))
    if date_to:
        query = query.filter(Card.date_of_sale <= date.fromisoformat(date_to))
    if duplicates_only:
        query = query.filter(Card.duplicate_kind.isnot(None))
    return query


def _part_for(db: Session, battery_type: str | None) -> str | None:
    if not battery_type:
        return None
    row = db.query(PartNumber).filter(PartNumber.normalized_type == normalize_type(battery_type), PartNumber.active.is_(True)).one_or_none()
    return row.part_no if row else None


def _upsert_part(db: Session, user: User, battery_type: str, part_no: str, action: str) -> PartNumber:
    battery_type = battery_type.strip()
    part_no = part_no.strip().upper()
    if not battery_type or not part_no:
        raise HTTPException(status_code=400, detail="Battery type and part number are required")
    norm = normalize_type(battery_type)
    row = db.query(PartNumber).filter(PartNumber.normalized_type == norm).one_or_none()
    if row is None:
        row = PartNumber(battery_type=battery_type, normalized_type=norm, part_no=part_no, active=True)
        db.add(row)
        db.flush()
        record_part_history(db, user, row, battery_type, None, part_no, action)
    else:
        old = row.part_no
        row.battery_type = battery_type
        row.part_no = part_no
        row.active = True
        if old != part_no:
            record_part_history(db, user, row, battery_type, old, part_no, action)
    return row


def _read_part_workbook(content: bytes) -> list[tuple[str, str]]:
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        raise HTTPException(status_code=400, detail="The workbook is empty")
    header = [str(cell or "").strip().lower() for cell in rows[0]]
    try:
        type_at = next(index for index, name in enumerate(header) if name in {"battery type", "battery_type"})
        code_at = next(index for index, name in enumerate(header) if "battery_item_code" in name or name in {"part no", "part_no"})
    except StopIteration:
        raise HTTPException(status_code=400, detail="Need columns Battery Type and BATTERY_ITEM_CODE")
    parsed = []
    for row in rows[1:]:
        if not row or row[type_at] in (None, ""):
            continue
        parsed.append((str(row[type_at]).strip(), str(row[code_at] or "").strip().upper()))
    return parsed


def _part_json(row: PartNumber) -> dict:
    return {"id": row.id, "battery_type": row.battery_type, "normalized_type": row.normalized_type, "part_no": row.part_no, "active": row.active}


def _plain(data: dict) -> dict:
    out = {}
    for key, value in data.items():
        if isinstance(value, (date, datetime)):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out
