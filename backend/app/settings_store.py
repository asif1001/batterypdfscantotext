"""Read and update the single settings documents."""

from __future__ import annotations

from copy import deepcopy

from sqlalchemy.orm import Session

from app.defaults import default_config, default_zones
from app.models import Setting
from app.security import decrypt_secret, encrypt_secret


def get_config(db: Session) -> dict:
    row = db.get(Setting, "config")
    if row is None:
        row = Setting(key="config", value=default_config())
        db.add(row)
        db.commit()
    merged = default_config()
    _deep_update(merged, row.value or {})
    return merged


def get_zones(db: Session) -> dict[str, list[float]]:
    row = db.get(Setting, "zones")
    if row is None:
        row = Setting(key="zones", value=default_zones())
        db.add(row)
        db.commit()
    stored = (row.value or {}).get("zones") or {}
    zones = deepcopy(default_zones()["zones"])
    zones.update(stored)
    return zones


def save_config(db: Session, incoming: dict) -> dict:
    current = get_config(db)
    providers_in = incoming.get("providers") or {}
    incoming = dict(incoming)
    incoming.pop("providers", None)
    _deep_update(current, incoming)
    for name, patch in providers_in.items():
        if name not in current["providers"]:
            continue
        patch = dict(patch)
        api_key = patch.pop("api_key", None)
        patch.pop("api_key_set", None)
        patch.pop("api_key_enc", None)
        _deep_update(current["providers"][name], patch)
        if api_key:
            current["providers"][name]["api_key_enc"] = encrypt_secret(api_key)
    row = db.get(Setting, "config")
    row.value = current
    db.commit()
    return current


def save_zones(db: Session, zones: dict[str, list[float]]) -> dict[str, list[float]]:
    current = get_zones(db)
    for key, box in zones.items():
        if key in current and isinstance(box, list) and len(box) == 4:
            current[key] = [float(v) for v in box]
    row = db.get(Setting, "zones")
    row.value = {"zones": current}
    db.commit()
    return current


def public_config(config: dict) -> dict:
    """Strip secrets before a response leaves the server."""
    data = deepcopy(config)
    for provider in data.get("providers", {}).values():
        enc = provider.pop("api_key_enc", "")
        provider["api_key_set"] = bool(enc)
    return data


def provider_api_key(config: dict, name: str) -> str:
    enc = (config.get("providers") or {}).get(name, {}).get("api_key_enc") or ""
    if not enc:
        return ""
    try:
        return decrypt_secret(enc)
    except Exception:
        return ""


def _deep_update(base: dict, patch: dict) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
