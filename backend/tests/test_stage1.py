from app.defaults import DEFAULT_ZONES
from app.pipeline.decode import parse_label_text, read_label
from app.pipeline.detect import detect_cards
from app.pipeline.fields import field_ink
from app.pipeline.synthetic import make_card, make_page


def test_parse_label_cuts_a_serial_glued_to_the_battery_type():
    parsed = parse_label_text("FDM0-DIN74LEFB-RISA2C6C0343612C64")
    assert parsed["serial"] == "A2C6C034361"
    assert parsed["batch_code"] == "2C64"
    assert parsed["battery_type"] == "FDM0-DIN74LEFB-RIS"


def test_parse_label_does_not_take_batch_from_inside_the_serial():
    parsed = parse_label_text("FSE0-Q65R(EFB)-BH-IS\nA4C6E031610\n4C63")
    assert parsed["serial"] == "A4C6E031610"
    assert parsed["batch_code"] == "4C63"
    assert parsed["battery_type"].startswith("FSE0")


def test_stage1_reads_qr_and_skips_empty_fields():
    card = make_card()
    label = read_label(card, DEFAULT_ZONES)
    assert label["qr_verified"] is True
    assert label["serial"] == "A4C6E031610"
    assert label["batch_code"] == "4C63"
    assert label["serial_source"] == "qr"
    ink = field_ink(card, DEFAULT_ZONES)
    assert ink["date_of_sale"] is True
    assert ink["dealer_text"] is False
    assert ink["tel"] is False
    assert ink["vehicle_no"] is False


def test_page_finds_six_cards_and_turns_them_upright():
    page = make_page()
    found = detect_cards(page)
    assert len(found) == 6, f"found {len(found)} cards"
    serials = []
    for card in found:
        label = read_label(card.image, DEFAULT_ZONES)
        serials.append(label["serial"])
        assert card.brand == "SF SONIC"
        assert label["qr_verified"] is True
    assert len(set(serials)) == 6
