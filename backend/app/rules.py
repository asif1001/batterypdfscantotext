"""Pure business rules: lookup normalisation, serial/batch checks, dates, phones, export blanks.

These functions do not touch the database or the network. The acceptance tests import them directly.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime

SERIAL_RE = re.compile(r"^A\d[A-Z]\d[A-Z]\d{6}$")
BATCH_RE = re.compile(r"^\d[A-Z]\d{2}$")
TEL_RE = re.compile(r"^[361]\d{7}$")
STAMP_FILE_RE = re.compile(r"(\d{5,8})")

BRAND_CEIL = "CEIL"
BRAND_SF = "SF SONIC"
BRAND_DYNEX = "DYNEX"

# A car name written in the dealer line is not a dealer. Used as a free warning.
CAR_NAMES = (
    "TOYOTA",
    "NISSAN",
    "HONDA",
    "HYUNDAI",
    "LEXUS",
    "MITSUBISHI",
    "SUZUKI",
    "MAZDA",
    "MERCEDES",
    "BMW",
    "KIA",
    "FORD",
    "CHEVROLET",
    "NISSAN",
    "ISUZU",
    "JEEP",
    "AUDI",
    "VOLKSWAGEN",
    "GMC",
    "DODGE",
    "INFINITI",
    "PEUGEOT",
    "RENAULT",
    "MG",
    "BYD",
    "HAVAL",
    "CHANGAN",
    "GEELY",
    "CHERRY",
    "CHERY",
)

MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


@dataclass
class Issue:
    level: str  # "error" or "warning"
    code: str
    field: str | None
    message: str


def normalize_type(value: str | None) -> str:
    """Ignore spaces and case. Treat FDM0/FDMO and FSE0/FSEO as the same prefix."""
    text = re.sub(r"\s+", "", value or "").upper()
    text = text.replace("FSEO", "FSE0").replace("FDMO", "FDM0")
    return text


def brand_for_type(battery_type: str | None) -> str | None:
    token = normalize_type(battery_type)
    if token.startswith("FCE7"):
        return BRAND_CEIL
    if token.startswith("FSE0"):
        return BRAND_SF
    if token.startswith("FDM0"):
        return BRAND_DYNEX
    return None


def stamp_code_from_filename(file_name: str | None) -> str | None:
    if not file_name:
        return None
    stem = file_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = stem.rsplit(".", 1)[0]
    match = STAMP_FILE_RE.search(stem)
    return match.group(1) if match else None


def shift_months(value: date, months: int) -> date:
    year = value.year
    month = value.month - months
    while month <= 0:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def expand_year(year: int) -> int:
    """A two-digit year is 2000+year. 26 means 2026."""
    if year < 100:
        return 2000 + year
    return year


def parse_sale_date(value: str | None) -> date | None:
    if value is None:
        return None
    raw = str(value).strip()
    if raw == "" or raw == "0" or raw.lower() == "illegible":
        return None
    named = re.search(r"(\d{1,2})[\s\-/.]+([A-Za-z]{3,9})[\s\-/.]+(\d{2,4})", raw)
    if named:
        day = int(named.group(1))
        month = MONTHS.get(named.group(2).lower())
        if month is None:
            month = MONTHS.get(named.group(2)[:3].lower())
        year = expand_year(int(named.group(3)))
        if month:
            return _safe_date(year, month, day)
    numeric = re.search(r"(\d{1,2})[\s\-/.]+(\d{1,2})[\s\-/.]+(\d{2,4})", raw)
    if numeric:
        day = int(numeric.group(1))
        month = int(numeric.group(2))
        year = expand_year(int(numeric.group(3)))
        return _safe_date(year, month, day)
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def phone_digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def is_missing(value: str | None) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return text == "" or text == "0"


def batch_mismatches_serial(serial: str | None, batch_code: str | None) -> bool:
    """True when both values are well-formed and batch[0:3] != serial[1:4].

    A mismatch is a warning. Rare genuine exceptions exist, so it is not an error.
    """
    if not serial or not batch_code:
        return False
    if not SERIAL_RE.fullmatch(serial) or not BATCH_RE.fullmatch(batch_code):
        return False
    return batch_code[:3] != serial[1:4]


FULL_SERIAL_RE = re.compile(rf"^({SERIAL_RE.pattern.strip('^$')})({BATCH_RE.pattern.strip('^$')})$")


def full_serial(serial: str | None, batch_code: str | None) -> str:
    """The serial the loader expects: serial then batch, with no space. A2H6A033704 + 2H64 = A2H6A0337042H64."""
    base = re.sub(r"\s+", "", serial or "").upper()
    batch = re.sub(r"\s+", "", batch_code or "").upper()
    if not base:
        return ""
    if not batch or batch == "0":
        return base
    return f"{base}{batch}"


def split_full_serial(value: str | None) -> tuple[str, str | None]:
    """Split a typed full serial back into the serial and the batch. A bare serial is left as-is."""
    compact = re.sub(r"\s+", "", value or "").upper()
    match = FULL_SERIAL_RE.fullmatch(compact)
    if match:
        return match.group(1), match.group(2)
    return compact, None


def looks_like_car_name(value: str | None) -> bool:
    text = (value or "").upper()
    return any(name in text for name in CAR_NAMES)


def looks_like_only_number(value: str | None) -> bool:
    text = re.sub(r"[\s\-]", "", value or "")
    return bool(text) and bool(re.fullmatch(r"\d{2,8}", text))


def validate_card(
    *,
    serial: str | None,
    batch_code: str | None,
    battery_type: str | None,
    brand: str | None,
    tel: str | None,
    date_of_sale: date | None,
    date_raw: str | None,
    uploaded_on: date,
    max_age_months: int,
    stamp_code: str | None,
    stamp_code_expected: str | None,
    part_no: str | None,
    dealer_text: str | None,
    make_model: str | None,
    vehicle_no: str | None,
) -> list[Issue]:
    issues: list[Issue] = []

    if is_missing(serial):
        issues.append(Issue("error", "missing", "serial", "Serial number is missing."))
    elif not SERIAL_RE.fullmatch(serial or ""):
        issues.append(Issue("error", "invalid", "serial", "Serial does not match A#L#L######."))
    if is_missing(batch_code):
        issues.append(Issue("error", "missing", "batch_code", "Batch code is missing."))
    elif not BATCH_RE.fullmatch(batch_code or ""):
        issues.append(Issue("error", "invalid", "batch_code", "Batch does not match #L##."))
    if batch_mismatches_serial(serial, batch_code):
        issues.append(
            Issue(
                "warning",
                "batch_serial_mismatch",
                "batch_code",
                f"Batch {batch_code[:3]} does not match serial characters 2–4 ({(serial or '')[1:4]}). Rare exceptions exist.",
            )
        )

    expected = brand_for_type(battery_type)
    if battery_type and expected and brand and expected != brand:
        issues.append(
            Issue(
                "error",
                "brand_mismatch",
                "brand",
                f"Type prefix expects {expected}, header colour reads {brand}.",
            )
        )
    if battery_type and expected is None:
        issues.append(
            Issue(
                "warning",
                "unknown_prefix",
                "battery_type",
                "Battery type prefix is not FCE7, FSE0/FSEO or FDM0/FDMO.",
            )
        )

    if is_missing(part_no):
        issues.append(Issue("error", "unmatched_part", "part_no", "Part No was not found in the lookup."))

    if is_missing(tel):
        issues.append(Issue("error", "missing", "tel", "Telephone is empty."))
    elif not TEL_RE.fullmatch(phone_digits(tel)):
        issues.append(
            Issue(
                "error",
                "invalid",
                "tel",
                "Telephone must be 8 digits starting with 3, 6 or 1.",
            )
        )

    if is_missing(date_raw) and date_of_sale is None:
        issues.append(Issue("error", "missing", "date_of_sale", "Date of sale is empty."))
    elif date_of_sale is None:
        issues.append(Issue("error", "invalid", "date_of_sale", "Date of sale is not a real date."))
    else:
        if date_of_sale > uploaded_on:
            issues.append(Issue("error", "invalid", "date_of_sale", "Date of sale is after the upload date."))
        elif date_of_sale < shift_months(uploaded_on, max_age_months):
            issues.append(
                Issue(
                    "error",
                    "invalid",
                    "date_of_sale",
                    f"Date of sale is more than {max_age_months} months before the upload date.",
                )
            )

    if stamp_code_expected and stamp_code and stamp_code != stamp_code_expected:
        issues.append(
            Issue(
                "error",
                "stamp_mismatch",
                "stamp_code",
                f"Stamp code {stamp_code} does not match {stamp_code_expected} from the file name.",
            )
        )
    if stamp_code_expected and is_missing(stamp_code):
        issues.append(Issue("error", "missing", "stamp_code", "Warranty stamp code was not read."))

    if looks_like_car_name(dealer_text):
        issues.append(
            Issue(
                "warning",
                "car_in_dealer",
                "dealer_text",
                "Dealer line looks like a car name, not a dealer.",
            )
        )
    if looks_like_only_number(make_model) and is_missing(vehicle_no):
        issues.append(
            Issue(
                "warning",
                "number_in_make",
                "make_model",
                "Make/Model is only a number. It is probably the vehicle number.",
            )
        )

    if is_missing(vehicle_no):
        issues.append(Issue("error", "missing", "vehicle_no", "Vehicle number is empty."))
    if is_missing(make_model):
        issues.append(Issue("error", "missing", "make_model", "Make/Model is empty."))

    return issues


def uploaded_date(value: datetime | date) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value
