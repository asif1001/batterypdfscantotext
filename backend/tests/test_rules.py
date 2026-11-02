from datetime import date

from app.rules import (
    batch_mismatches_serial,
    brand_for_type,
    normalize_type,
    parse_sale_date,
    stamp_code_from_filename,
    validate_card,
)


def test_serial_batch_example_matches():
    assert batch_mismatches_serial("A2H6D024885", "2H63") is False
    assert batch_mismatches_serial("A2H6D024885", "9Z11") is True


def test_two_digit_year_26_is_2026():
    assert parse_sale_date("12 OCT 26") == date(2026, 10, 12)
    assert parse_sale_date("12/10/2026") == date(2026, 10, 12)
    assert parse_sale_date("illegible") is None


def test_prefix_normalisation_and_brand():
    assert normalize_type("fseo-q65 r") == normalize_type("FSE0-Q65R")
    assert normalize_type("FDMO-N55") == normalize_type("FDM0-N55")
    assert brand_for_type("FCE7-DIN55") == "CEIL"
    assert brand_for_type("FSEO-Q65") == "SF SONIC"
    assert brand_for_type("FDM0-N55") == "DYNEX"


def test_stamp_code_from_filename():
    assert stamp_code_from_filename("1046906-1-11-2026.pdf") == "1046906"
    assert stamp_code_from_filename("Battery_Card.pdf") is None


def test_phone_and_future_date_are_errors_batch_mismatch_is_warning():
    issues = validate_card(
        serial="A2H6D024885",
        batch_code="9Z11",
        battery_type="FSE0-Q65",
        brand="SF SONIC",
        tel="123",
        date_of_sale=date(2027, 1, 1),
        date_raw="01 JAN 2027",
        uploaded_on=date(2026, 11, 1),
        max_age_months=12,
        stamp_code="1046906",
        stamp_code_expected="1046906",
        part_no="ABC123",
        dealer_text="TOYOTA",
        make_model="COROLLA",
        vehicle_no="332666",
    )
    codes = {(issue.level, issue.code, issue.field) for issue in issues}
    assert ("warning", "batch_serial_mismatch", "batch_code") in codes
    assert ("error", "invalid", "tel") in codes
    assert ("error", "invalid", "date_of_sale") in codes
    assert ("warning", "car_in_dealer", "dealer_text") in codes
