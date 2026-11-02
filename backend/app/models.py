"""Database tables. UUIDs are stored as strings so SQLite tests and PostgreSQL share one model."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="operator")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    file_name: Mapped[str] = mapped_column(String(512))
    stamp_code_from_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    status: Mapped[str] = mapped_column(String(32), default="uploaded", index=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    card_count: Mapped[int] = mapped_column(Integer, default=0)
    ai_cost: Mapped[float] = mapped_column(Float, default=0)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    storage_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    cards: Mapped[list["Card"]] = relationship(back_populates="batch", cascade="all, delete-orphan")
    uploader: Mapped[User] = relationship()


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (
        Index("ix_cards_serial", "serial"),
        Index("ix_cards_tel", "tel"),
        Index("ix_cards_part_no", "part_no"),
        Index(
            "uq_cards_serial_approved",
            "serial",
            unique=True,
            sqlite_where=text("status = 'approved' AND excluded = 0 AND serial IS NOT NULL AND serial != ''"),
            postgresql_where=text(
                "status = 'approved' AND excluded = false AND serial IS NOT NULL AND serial <> ''"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), index=True)
    page: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)
    brand: Mapped[str | None] = mapped_column(String(32), nullable=True)
    battery_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    serial: Mapped[str | None] = mapped_column(String(32), nullable=True)
    batch_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    part_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    date_of_sale: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vehicle_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    make_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bill_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dealer_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signature_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    stamp_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stamp_branch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stamp_tel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stamp_condition: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dealer_stamp: Mapped[str | None] = mapped_column(String(128), nullable=True)
    qr_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_of_card_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    duplicate_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    field_flags: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_skipped: Mapped[bool] = mapped_column(Boolean, default=False)

    batch: Mapped[Batch] = relationship(back_populates="cards")
    fields: Mapped[list["CardField"]] = relationship(back_populates="card", cascade="all, delete-orphan")
    image: Mapped["CardImage | None"] = relationship(back_populates="card", cascade="all, delete-orphan", uselist=False)


class CardField(Base):
    __tablename__ = "card_fields"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    card_id: Mapped[str] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(64))
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    card: Mapped[Card] = relationship(back_populates="fields")


class CardImage(Base):
    __tablename__ = "card_images"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    card_id: Mapped[str] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), unique=True)
    full_crop_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    field_strip_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    image_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    card: Mapped[Card] = relationship(back_populates="image")


class PartNumber(Base):
    __tablename__ = "part_numbers"
    __table_args__ = (UniqueConstraint("normalized_type", name="uq_part_normalized"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    battery_type: Mapped[str] = mapped_column(String(128))
    normalized_type: Mapped[str] = mapped_column(String(128), index=True)
    part_no: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PartNumberHistory(Base):
    __tablename__ = "part_number_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    part_number_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    battery_type: Mapped[str] = mapped_column(String(128))
    old_part_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_part_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(32))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AiUsage(Base):
    __tablename__ = "ai_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("batches.id"), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    stage: Mapped[int] = mapped_column(Integer, default=2)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class AiCache(Base):
    __tablename__ = "ai_cache"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    image_hash: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(32))
    response_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("image_hash", "provider", "model", "prompt_version", name="uq_ai_cache"),)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
