"""Keep the warranty records in the Constaff Firebase Realtime Database.

SQLite stays the local query store so the existing screens keep working.
Every commit is written to Firebase, and a machine with an empty batch
list restores the records from Firebase.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import Date, DateTime, event, inspect
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal

log = logging.getLogger("warranty.firebase")

ROOT = "warrantyScanner"
TABLES = (
    "users",
    "batches",
    "cards",
    "card_fields",
    "card_images",
    "part_numbers",
    "part_number_history",
    "ai_usage",
    "ai_cache",
    "audit_log",
    "settings",
)
_lock = threading.Lock()
_ready = False
_app = None


def enabled() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if "test.db" in settings.database_url:
        return False
    return bool(settings.firebase_database_url and _credentials_path())


def _credentials_path() -> Path | None:
    path = Path(settings.firebase_credentials_file)
    if not path.is_file():
        path = Path(__file__).resolve().parents[1] / "firebase-service-account.json"
    return path if path.is_file() else None


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _row(obj) -> tuple[str, str, dict]:
    mapper = inspect(obj).mapper
    table = obj.__tablename__
    key = str(getattr(obj, mapper.primary_key[0].key))
    data = {}
    for column in mapper.columns:
        value = getattr(obj, column.key)
        if value is None:
            continue
        data[column.key] = _jsonable(value)
    return table, key, data


def _reference():
    from firebase_admin import db

    return db.reference(ROOT)


def connect() -> bool:
    global _ready, _app
    if _ready:
        return True
    if not enabled():
        log.info("Firebase database is not configured")
        return False
    import firebase_admin
    from firebase_admin import credentials

    path = _credentials_path()
    options = {"databaseURL": settings.firebase_database_url}
    if settings.firebase_project_id:
        options["projectId"] = settings.firebase_project_id
    _app = firebase_admin.initialize_app(credentials.Certificate(str(path)), options)
    _ready = True
    return True


def _apply(updates: dict[str, dict | None]) -> None:
    if not updates or not _ready:
        return
    ref = _reference()
    items = list(updates.items())
    with _lock:
        for start in range(0, len(items), 200):
            chunk = dict(items[start : start + 200])
            ref.update(chunk)


def push_all() -> int:
    from app import models

    classes = [cls for cls in models.Base.__subclasses__() if getattr(cls, "__tablename__", None) in TABLES]
    updates: dict[str, dict] = {}
    db = SessionLocal()
    try:
        for cls in classes:
            for obj in db.query(cls).all():
                table, key, data = _row(obj)
                updates[f"{table}/{key}"] = data
    finally:
        db.close()
    _apply(updates)
    return len(updates)


def restore_if_empty() -> bool:
    from app.models import Batch, User

    remote = _reference().child("batches").get() or {}
    if not remote:
        return False
    db = SessionLocal()
    try:
        if db.query(Batch).count():
            return False
        from app import models

        classes = {cls.__tablename__: cls for cls in models.Base.__subclasses__() if getattr(cls, "__tablename__", None) in TABLES}
        for table in TABLES:
            payload = _reference().child(table).get() or {}
            cls = classes.get(table)
            if cls is None:
                continue
            for key, data in payload.items():
                if not isinstance(data, dict):
                    continue
                mapper = inspect(cls)
                data.setdefault(mapper.primary_key[0].key, key)
                if table == "users" and db.query(User).filter(User.email == data.get("email")).first():
                    continue
                values = {}
                for column in mapper.columns:
                    if column.key not in data and column.key != mapper.primary_key[0].key:
                        continue
                    raw = data.get(column.key)
                    if isinstance(raw, str) and isinstance(column.type, DateTime):
                        raw = datetime.fromisoformat(raw)
                    elif isinstance(raw, str) and isinstance(column.type, Date):
                        raw = date.fromisoformat(raw[:10])
                    values[column.key] = raw
                db.merge(cls(**values))
        db.commit()
        return True
    finally:
        db.close()


def _collect(session: Session, flush_context, instances) -> None:
    if not _ready:
        return
    pending: dict[str, dict | None] = session.info.setdefault("firebase_pending", {})
    for obj in list(session.new) + list(session.dirty):
        table = getattr(obj, "__tablename__", None)
        if table not in TABLES:
            continue
        name, key, data = _row(obj)
        pending[f"{name}/{key}"] = data
    for obj in list(session.deleted):
        table = getattr(obj, "__tablename__", None)
        if table not in TABLES:
            continue
        name, key, _data = _row(obj)
        pending[f"{name}/{key}"] = None


def _publish(session: Session) -> None:
    pending = session.info.pop("firebase_pending", None)
    if pending and _ready:
        try:
            _apply(pending)
        except Exception:
            log.exception("Firebase update failed")


def _clear(session: Session) -> None:
    session.info.pop("firebase_pending", None)


def register() -> None:
    if getattr(register, "_done", False):
        return
    event.listen(Session, "before_flush", _collect)
    event.listen(Session, "after_commit", _publish)
    event.listen(Session, "after_rollback", _clear)
    register._done = True


def start_firebase() -> None:
    register()
    if not connect():
        return

    def run() -> None:
        try:
            if restore_if_empty():
                log.info("Restored warranty records from Firebase")
            count = push_all()
            log.info("Firebase database ready with %s records", count)
        except Exception:
            log.exception("Firebase startup sync failed")

    threading.Thread(target=run, name="firebase-sync", daemon=True).start()
