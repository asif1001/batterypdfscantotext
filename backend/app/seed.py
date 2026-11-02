"""Create the demo users, default settings and the starter Data Loader template."""

from __future__ import annotations

from app.config import settings
from app.db import SessionLocal
from app.defaults import default_config, default_zones
from app.excel.export import build_default_template
from app.models import Setting, User
from app.security import hash_password

DEMO_USERS = (
    ("admin@ekk.local", "Administrator", "Admin@12345", "admin"),
    ("supervisor@ekk.local", "Supervisor", "Super@12345", "supervisor"),
    ("operator@ekk.local", "Operator", "Oper@12345", "operator"),
)


def seed() -> None:
    db = SessionLocal()
    try:
        if settings.seed_demo_users and db.query(User).count() == 0:
            for email, name, password, role in DEMO_USERS:
                db.add(User(email=email, name=name, password_hash=hash_password(password), role=role, active=True))
        if db.get(Setting, "config") is None:
            db.add(Setting(key="config", value=default_config()))
        if db.get(Setting, "zones") is None:
            db.add(Setting(key="zones", value=default_zones()))
        db.commit()
        template = settings.storage_path / "data_loader_template.xlsx"
        if not template.exists():
            build_default_template(template)
    finally:
        db.close()
