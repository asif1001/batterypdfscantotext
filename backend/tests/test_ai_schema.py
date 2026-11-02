import json

from app.ai.extract import parse_model_json


def test_parser_accepts_fenced_json():
    raw = """```json
    {"cards":[{"card_id":"c1","fields":{"tel":{"value":"39444939","confidence":0.91}}}]}
    ```"""
    parsed = parse_model_json(raw)
    assert parsed.cards[0].fields["tel"].value == "39444939"
    assert parsed.cards[0].fields["tel"].confidence == 0.91


def test_parser_rejects_confidence_above_one():
    try:
        parse_model_json(json.dumps({"cards": [{"card_id": "c1", "fields": {"tel": {"value": "1", "confidence": 2}}}]}))
    except Exception:
        return
    raise AssertionError("confidence above 1 should fail validation")
