"""Stage 2 and stage 3 handwriting calls.

Empty fields and anything Stage 1 already read (QR serial, type, batch, brand, stamp)
are never sent. Several card strips share one request. A second model sees only the
fields that were uncertain or failed a check.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai.prompt import FIX_PROMPT, PROMPT_VERSION, SYSTEM_PROMPT
from app.ai.providers import ProviderResponse, build_extractor
from app.models import AiCache, AiUsage, Card, CardField
from app.settings_store import get_config, provider_api_key


class FieldRead(BaseModel):
    value: str = ""
    confidence: float = Field(default=0, ge=0, le=1)


class CardRead(BaseModel):
    card_id: str
    fields: dict[str, FieldRead] = Field(default_factory=dict)


class BatchRead(BaseModel):
    cards: list[CardRead]


def run_handwriting(db: Session, batch_id: str, prepared: list[dict], rebuild=None, extra_low=None) -> dict:
    """prepared items: card_id, strip_jpeg, image_hash, ink_fields, stage1_values already stored."""
    config = get_config(db)
    stats = {"stage2_requests": 0, "stage3_requests": 0, "cache_hits": 0, "skipped_empty": 0, "manual": 0}
    pending = [item for item in prepared if item.get("strip_jpeg") and item.get("ink_fields")]
    stats["skipped_empty"] = len(prepared) - len(pending)
    if not pending:
        return stats
    if not config.get("ai_enabled"):
        _mark_manual(db, pending)
        stats["manual"] = len(pending)
        return stats

    size = max(1, int(config.get("cards_per_request") or 6))
    unresolved: list[dict] = []
    for chunk in _chunks(pending, size):
        if _over_budget(db, batch_id, config):
            _mark_manual(db, chunk)
            stats["manual"] += len(chunk)
            continue
        parsed, cached, used = _call_stage(db, batch_id, config, chunk, stage=2)
        stats["stage2_requests"] += 0 if cached else (1 if parsed is not None else 0)
        stats["cache_hits"] += 1 if cached else 0
        if parsed is None:
            _mark_manual(db, chunk)
            stats["manual"] += len(chunk)
            continue
        for item in chunk:
            low = _apply_stage_result(db, item, parsed, source=used or "ai", stage=2)
            if extra_low is not None:
                low = list(dict.fromkeys(low + list(extra_low(item["card_id"]) or [])))
            if low:
                retry = dict(item)
                retry["retry_fields"] = low
                retry["ink_fields"] = low
                if rebuild is not None:
                    jpeg, digest = rebuild(item["card_id"], low)
                    if jpeg:
                        retry["strip_jpeg"] = jpeg
                        retry["image_hash"] = digest
                unresolved.append(retry)

    if unresolved and config.get("ai_enabled"):
        for chunk in _chunks(unresolved, size):
            if _over_budget(db, batch_id, config):
                break
            parsed, cached, used = _call_stage(db, batch_id, config, chunk, stage=3)
            stats["stage3_requests"] += 0 if cached else (1 if parsed is not None else 0)
            stats["cache_hits"] += 1 if cached else 0
            if parsed is None:
                continue
            for item in chunk:
                _apply_stage_result(db, item, parsed, source=used or "ai", stage=3, compare=True)
    db.commit()
    return stats


def parse_model_json(text: str) -> BatchRead:
    payload = _extract_json(text)
    if isinstance(payload, list):
        payload = {"cards": payload}
    try:
        return BatchRead.model_validate(payload)
    except ValidationError:
        # Some models nest the array one level deeper or use "results".
        if isinstance(payload, dict):
            for key in ("results", "data", "extractions"):
                if key in payload:
                    return BatchRead.model_validate({"cards": payload[key]})
        raise


def _call_stage(db: Session, batch_id: str, config: dict, chunk: list[dict], stage: int):
    order_key = "stage2_order" if stage == 2 else "stage3_order"
    # Cache is per strip. A chunk is cached as a whole only when every strip hits.
    cached_cards: list[CardRead] = []
    missing: list[dict] = []
    provider_name = _first_configured(db, config, order_key)
    model_name = ""
    if provider_name:
        model_name = config["providers"][provider_name]["model"]
    for item in chunk:
        hit = _cache_get(db, item["image_hash"], provider_name or "", model_name, config.get("prompt_version") or PROMPT_VERSION)
        if hit is not None:
            cached_cards.append(hit)
            _log_usage(db, batch_id, provider_name or "cache", model_name, stage, 0, 0, 0, cache_hit=True)
        else:
            missing.append(item)
    if not missing and cached_cards:
        return BatchRead(cards=cached_cards), True, provider_name
    if not provider_name:
        return (BatchRead(cards=cached_cards) if cached_cards else None), False, ""

    user_text = _user_text(missing)
    images = [item["strip_jpeg"] for item in missing]
    parsed, used_name, response = _complete(db, config, order_key, user_text, images)
    if parsed is None or response is None:
        return (BatchRead(cards=cached_cards) if cached_cards else None), False, used_name or ""
    price = _price(config, used_name, response.input_tokens, response.output_tokens)
    _log_usage(
        db,
        batch_id,
        used_name,
        response.model,
        stage,
        response.input_tokens,
        response.output_tokens,
        price,
        cache_hit=False,
    )
    by_id = {card.card_id: card for card in parsed.cards}
    for item in missing:
        card_read = by_id.get(item["card_id"])
        if card_read is not None:
            _cache_put(
                db,
                item["image_hash"],
                used_name,
                response.model,
                config.get("prompt_version") or PROMPT_VERSION,
                card_read,
            )
    parsed.cards.extend(cached_cards)
    return parsed, False, used_name


def _complete(db: Session, config: dict, order_key: str, user_text: str, images: list[bytes]):
    errors = []
    for name in config.get(order_key) or []:
        provider_config = (config.get("providers") or {}).get(name) or {}
        api_key = provider_api_key(config, name)
        extractor = build_extractor(name, provider_config, api_key)
        if not extractor.configured:
            continue
        try:
            first = extractor.chat(SYSTEM_PROMPT, user_text, images)
            try:
                return parse_model_json(first.text), name, first
            except (ValidationError, ValueError, json.JSONDecodeError):
                second = extractor.chat(SYSTEM_PROMPT, user_text + "\n\n" + FIX_PROMPT, images)
                return parse_model_json(second.text), name, _add_usage(first, second)
        except Exception as exc:  # provider down, bad JSON twice, or HTTP error
            errors.append(f"{name}: {exc}")
            continue
    return None, None, None


def _apply_stage_result(db: Session, item: dict, parsed: BatchRead, source: str, stage: int, compare: bool = False) -> list[str]:
    card = db.get(Card, item["card_id"])
    if card is None:
        return []
    found = next((entry for entry in parsed.cards if entry.card_id == item["card_id"]), None)
    if found is None:
        return list(item.get("ink_fields") or [])
    threshold = float(get_config(db).get("confidence_threshold") or 0.8)
    low: list[str] = []
    flags = dict(card.field_flags or {})
    for field_name in item.get("ink_fields") or []:
        reading = found.fields.get(field_name)
        if reading is None:
            low.append(field_name)
            continue
        value = (reading.value or "").strip()
        if field_name == "signature":
            card.signature_present = value.lower() in {"yes", "y", "present", "true", "1"}
        previous = _current_value(card, field_name)
        disagree = compare and previous is not None and _norm(previous) != _norm(value) and previous not in {"", "0"}
        db.add(
            CardField(
                card_id=card.id,
                field=field_name,
                value=value,
                source=source or "ai",
                confidence=reading.confidence,
                flags=["disagree"] if disagree else [],
            )
        )
        marks = list(flags.get(field_name) or [])
        marks = [mark for mark in marks if mark not in {"manual", "unclear", "disagree"}]
        if disagree:
            marks.append("disagree")
        elif reading.confidence < threshold or value.lower() == "illegible":
            marks.append("unclear")
            if stage == 2:
                low.append(field_name)
        if value == "":
            marks.append("missing")
            _assign(card, field_name, "0" if field_name != "signature" else "")
        else:
            _assign(card, field_name, value)
        flags[field_name] = marks
    card.field_flags = flags
    return low


def _assign(card: Card, field_name: str, value: str) -> None:
    if field_name == "date_of_sale":
        card.date_raw = value
        from app.rules import parse_sale_date

        card.date_of_sale = parse_sale_date(value)
        return
    if field_name == "signature":
        return
    setattr(card, field_name, value)


def _current_value(card: Card, field_name: str) -> str | None:
    if field_name == "date_of_sale":
        return card.date_raw
    if field_name == "signature":
        if card.signature_present is None:
            return None
        return "yes" if card.signature_present else "no"
    return getattr(card, field_name, None)


def _mark_manual(db: Session, items: list[dict]) -> None:
    for item in items:
        card = db.get(Card, item["card_id"])
        if card is None:
            continue
        card.ai_skipped = True
        flags = dict(card.field_flags or {})
        for field_name in item.get("ink_fields") or []:
            marks = list(flags.get(field_name) or [])
            if "unclear" not in marks:
                marks.append("unclear")
            flags[field_name] = marks
            db.add(
                CardField(
                    card_id=card.id,
                    field=field_name,
                    value="",
                    source="manual",
                    confidence=None,
                    flags=["manual"],
                )
            )
        card.field_flags = flags


def _over_budget(db: Session, batch_id: str, config: dict) -> bool:
    batch_cap = float(config.get("max_batch_spend") or 0)
    month_cap = float(config.get("max_month_spend") or 0)
    batch_spent = db.query(func.coalesce(func.sum(AiUsage.cost), 0)).filter(AiUsage.batch_id == batch_id, AiUsage.cache_hit.is_(False)).scalar()
    start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_spent = db.query(func.coalesce(func.sum(AiUsage.cost), 0)).filter(AiUsage.created_at >= start, AiUsage.cache_hit.is_(False)).scalar()
    if batch_cap and float(batch_spent or 0) >= batch_cap:
        return True
    if month_cap and float(month_spent or 0) >= month_cap:
        return True
    return False


def _log_usage(db, batch_id, provider, model, stage, input_tokens, output_tokens, cost, cache_hit: bool) -> None:
    db.add(
        AiUsage(
            batch_id=batch_id,
            provider=provider,
            model=model or "",
            stage=stage,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            cache_hit=cache_hit,
        )
    )


def _price(config: dict, provider: str, input_tokens: int, output_tokens: int) -> float:
    info = (config.get("providers") or {}).get(provider) or {}
    return (input_tokens / 1_000_000) * float(info.get("input_per_million") or 0) + (
        output_tokens / 1_000_000
    ) * float(info.get("output_per_million") or 0)


def _cache_get(db: Session, image_hash: str, provider: str, model: str, version: str) -> CardRead | None:
    if not image_hash or not provider:
        return None
    row = (
        db.query(AiCache)
        .filter(
            AiCache.image_hash == image_hash,
            AiCache.provider == provider,
            AiCache.model == model,
            AiCache.prompt_version == version,
        )
        .one_or_none()
    )
    if row is None:
        return None
    return CardRead.model_validate(row.response_json)


def _cache_put(db: Session, image_hash: str, provider: str, model: str, version: str, card: CardRead) -> None:
    if not image_hash:
        return
    db.add(
        AiCache(
            image_hash=image_hash,
            provider=provider,
            model=model,
            prompt_version=version,
            response_json=card.model_dump(),
        )
    )


def _user_text(chunk: list[dict]) -> str:
    lines = ["Read the handwriting on these strips. Images are in the same order as this list."]
    for item in chunk:
        fields = ", ".join(item.get("ink_fields") or [])
        lines.append(f"card_id={item['card_id']} fields={fields}")
    return "\n".join(lines)


def _extract_json(text: str):
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def _chunks(items: list, size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _first_configured(db: Session, config: dict, order_key: str) -> str:
    for name in config.get(order_key) or []:
        if provider_api_key(config, name):
            return name
    return ""


def _provider_used(config: dict, order_key: str) -> str:
    for name in config.get(order_key) or []:
        enc = (config.get("providers") or {}).get(name, {}).get("api_key_enc")
        if enc:
            return name
    return "ai"


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value or "").upper()


def _add_usage(first: ProviderResponse, second: ProviderResponse) -> ProviderResponse:
    return ProviderResponse(
        text=second.text,
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        provider=second.provider,
        model=second.model,
    )
