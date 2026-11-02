"""Data Loader and detailed Excel exports.

The Data Loader file is a copy of the stored template. Data is written into that copy.
A missing value in B, D, F, J, T or V is the number 0 with a light red fill. An unclear
value keeps the text and gets a light yellow fill. H and Y are always 0.
"""

from __future__ import annotations

import shutil
from copy import copy
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.defaults import DEFAULT_LOADER_COLUMNS, LOADER_HEADERS
from app.rules import is_missing

RED_FILL = PatternFill("solid", fgColor="FFC7CE")
YELLOW_FILL = PatternFill("solid", fgColor="FFF2CC")
RED_FONT = Font(name="Calibri", bold=True, color="9C0006", size=11)
NORMAL_FONT = Font(name="Calibri", size=11)
HEADER_FONT = Font(name="Calibri", bold=True, size=11)
HEADER_FILL = PatternFill("solid", fgColor="D9E2F3")

MANDATORY = {"part_no", "serial_batch", "date_of_sale", "branch", "vehicle_no", "make_model"}
DATE_FORMAT = "dd-mmm-yyyy"


def build_default_template(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    loader = workbook.active
    loader.title = "Data Loader"
    for index in range(1, 27):
        letter = get_column_letter(index)
        cell = loader.cell(1, index, LOADER_HEADERS.get(letter, ""))
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        loader.column_dimensions[letter].width = _width(letter)
    # Row 2 is a prototype of the navigation keys. Export overwrites it with the first card.
    for index in range(1, 27):
        letter = get_column_letter(index)
        spec = DEFAULT_LOADER_COLUMNS[letter]
        cell = loader.cell(2, index, spec["value"] if spec["kind"] == "literal" else None)
        cell.font = NORMAL_FONT
        if spec["kind"] == "literal":
            cell.number_format = "@"
            cell.alignment = Alignment(horizontal="center")
    sheet1 = workbook.create_sheet("Sheet1")
    sheet1.sheet_properties.tabColor = "BFBFBF"
    legend = workbook.create_sheet("Legend")
    _write_legend(legend)
    workbook.save(path)
    return path


def export_data_loader(cards: list[dict], template_path: str | Path, output_path: str | Path, columns: dict | None = None) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(template_path, output_path)
    workbook = load_workbook(output_path)
    if "Data Loader" not in workbook.sheetnames:
        raise ValueError('Template is missing the "Data Loader" sheet.')
    sheet = workbook["Data Loader"]
    columns = columns or DEFAULT_LOADER_COLUMNS
    prototype = _capture_prototype(sheet)
    _clear_data_rows(sheet)
    for offset, card in enumerate(cards):
        row = 2 + offset
        _write_card_row(sheet, row, card, columns, prototype)
    if "Legend" not in workbook.sheetnames:
        _write_legend(workbook.create_sheet("Legend"))
    else:
        _write_legend(workbook["Legend"])
    if "Sheet1" not in workbook.sheetnames:
        workbook.create_sheet("Sheet1", 1)
    workbook.save(output_path)
    return output_path


def export_detailed(cards: list[dict], output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Warranty Cards"
    headers = [
        "Batch file",
        "Page",
        "Position",
        "Brand",
        "Battery Type",
        "Serial",
        "Batch",
        "Part No",
        "Date of Sale",
        "Vehicle No",
        "Make/Model",
        "Tel",
        "Bill No",
        "Dealer",
        "Customer",
        "Signature",
        "Stamp Code",
        "Stamp Branch",
        "Stamp Tel",
        "Stamp Condition",
        "Dealer Stamp",
        "QR Verified",
        "Duplicate",
        "Duplicate Of",
        "Status",
        "Remarks",
        "Flags",
    ]
    for index, header in enumerate(headers, start=1):
        cell = sheet.cell(1, index, header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
    for row_index, card in enumerate(cards, start=2):
        values = [
            card.get("file_name"),
            card.get("page"),
            card.get("position"),
            card.get("brand"),
            card.get("battery_type"),
            card.get("serial"),
            card.get("batch_code"),
            card.get("part_no"),
            card.get("date_of_sale"),
            card.get("vehicle_no"),
            card.get("make_model"),
            card.get("tel"),
            card.get("bill_no"),
            card.get("dealer_text"),
            card.get("customer_name"),
            "yes" if card.get("signature_present") else "no",
            card.get("stamp_code"),
            card.get("stamp_branch"),
            card.get("stamp_tel"),
            card.get("stamp_condition"),
            card.get("dealer_stamp"),
            "yes" if card.get("qr_verified") else "no",
            card.get("duplicate_kind") or "",
            card.get("duplicate_of_card_id") or "",
            card.get("status"),
            card.get("remarks") or "",
            ", ".join(card.get("flags") or []),
        ]
        for index, value in enumerate(values, start=1):
            cell = sheet.cell(row_index, index, value)
            cell.font = NORMAL_FONT
            if index == 9 and isinstance(value, (date, datetime)):
                cell.number_format = DATE_FORMAT
    sheet.auto_filter.ref = f"A1:AA{max(1, len(cards) + 1)}"
    sheet.freeze_panes = "A2"
    for column in sheet.columns:
        letter = column[0].column_letter
        sheet.column_dimensions[letter].width = 18
    workbook.save(output_path)
    return output_path


def card_export_view(card) -> dict:
    """Shape a Card ORM object the way the exporters expect."""
    return {
        "file_name": card.batch.file_name if card.batch else "",
        "page": card.page,
        "position": card.position,
        "brand": card.brand,
        "battery_type": card.battery_type,
        "serial": card.serial,
        "batch_code": card.batch_code,
        "part_no": card.part_no,
        "date_of_sale": card.date_of_sale,
        "date_raw": card.date_raw,
        "vehicle_no": card.vehicle_no,
        "make_model": card.make_model,
        "tel": card.tel,
        "bill_no": card.bill_no,
        "dealer_text": card.dealer_text,
        "customer_name": card.customer_name,
        "signature_present": card.signature_present,
        "stamp_code": card.stamp_code,
        "stamp_branch": card.stamp_branch,
        "stamp_tel": card.stamp_tel,
        "stamp_condition": card.stamp_condition,
        "dealer_stamp": card.dealer_stamp,
        "qr_verified": card.qr_verified,
        "duplicate_kind": card.duplicate_kind,
        "duplicate_of_card_id": card.duplicate_of_card_id,
        "status": card.status,
        "remarks": card.remarks,
        "flags": card.flags or [],
        "field_flags": card.field_flags or {},
        "excluded": card.excluded,
    }


def _write_card_row(sheet: Worksheet, row: int, card: dict, columns: dict, prototype: dict | None) -> None:
    values = _data_values(card)
    unclear = set()
    for flag in card.get("field_flags") or {}:
        marks = (card.get("field_flags") or {}).get(flag) or []
        if any(mark in {"unclear", "invalid", "disagree"} for mark in marks):
            unclear.add(flag)
    if "batch_serial_mismatch" in (card.get("flags") or []):
        unclear.add("serial_batch")
    for index in range(1, 27):
        letter = get_column_letter(index)
        spec = columns.get(letter) or {"kind": "literal", "value": "{TAB}"}
        cell = sheet.cell(row, index)
        cell.font = copy(NORMAL_FONT)
        cell.fill = PatternFill()
        cell.alignment = Alignment(vertical="center")
        literal = _literal_from_prototype(prototype, index)
        if spec["kind"] == "field":
            field = spec["field"]
            raw = values.get(field)
            missing = _field_missing(field, raw)
            if missing:
                cell.value = 0
                cell.number_format = "0"
                if field in unclear or _value_unclear(field, card):
                    cell.fill = YELLOW_FILL
                    cell.font = copy(NORMAL_FONT)
                else:
                    cell.fill = RED_FILL
                    cell.font = copy(RED_FONT)
            else:
                cell.value = raw
                cell.font = copy(NORMAL_FONT)
                if field == "date_of_sale":
                    cell.number_format = DATE_FORMAT
                elif field == "branch":
                    cell.number_format = "0"
                else:
                    cell.number_format = "@"
                if field in unclear or _value_unclear(field, card):
                    cell.fill = YELLOW_FILL
        elif spec["kind"] == "constant":
            cell.value = 0 if spec.get("value") in (0, "0") else spec.get("value")
            cell.font = copy(NORMAL_FONT)
            cell.number_format = "0"
        else:
            token = literal if literal in ("{TAB}", "{DOWN}") else spec.get("value")
            cell.value = token
            cell.number_format = "@"
            cell.font = copy(NORMAL_FONT)


def _data_values(card: dict) -> dict:
    serial = (card.get("serial") or "").strip()
    batch = (card.get("batch_code") or "").strip()
    if serial and batch and batch != "0":
        serial_batch = f"{serial}{batch}"
    elif serial:
        serial_batch = serial
    else:
        serial_batch = ""
    sale = card.get("date_of_sale")
    if isinstance(sale, str):
        sale = None
    branch = card.get("stamp_branch")
    return {
        "part_no": (card.get("part_no") or "").strip(),
        "serial_batch": serial_batch,
        "date_of_sale": sale,
        "branch": branch if branch not in (None, "") else "",
        "vehicle_no": (card.get("vehicle_no") or "").strip(),
        "make_model": (card.get("make_model") or "").strip(),
    }


def _field_missing(field: str, value) -> bool:
    if field == "date_of_sale":
        return not isinstance(value, (date, datetime))
    if field == "branch":
        return value in (None, "", "0")
    return is_missing(str(value) if value is not None else None)


def _value_unclear(field: str, card: dict) -> bool:
    flags = card.get("field_flags") or {}
    marks = flags.get(field) or []
    if field == "serial_batch":
        marks = list(marks) + list(flags.get("serial") or []) + list(flags.get("batch_code") or [])
    return any(mark in {"unclear", "invalid", "disagree"} for mark in marks)


def _capture_prototype(sheet: Worksheet) -> dict | None:
    last = min(sheet.max_row or 2, 15)
    for row in range(2, last + 1):
        values = {}
        found = False
        for col in range(1, 27):
            value = sheet.cell(row, col).value
            values[col] = value
            if isinstance(value, str) and value.strip() in ("{TAB}", "{DOWN}"):
                found = True
        if found:
            return values
    return None


def _literal_from_prototype(prototype: dict | None, col: int):
    if not prototype:
        return None
    value = prototype.get(col)
    if isinstance(value, str):
        return value.strip()
    return None


def _clear_data_rows(sheet: Worksheet) -> None:
    # Styles on row 2 are captured by the caller before this runs only if we read values first.
    # Values are cleared so a short export does not leave a stale prototype under the data.
    if sheet.max_row < 2:
        return
    for row in range(2, sheet.max_row + 1):
        for col in range(1, 27):
            sheet.cell(row, col).value = None


def _copy_style(src, dst) -> None:
    if src.has_style:
        dst.font = copy(src.font)
        dst.border = copy(src.border)
        dst.fill = copy(src.fill)
        dst.number_format = src.number_format
        dst.alignment = copy(src.alignment)


def _write_legend(sheet: Worksheet) -> None:
    sheet["A1"] = "Legend"
    sheet["A1"].font = HEADER_FONT
    sheet["A3"] = 0
    sheet["A3"].fill = RED_FILL
    sheet["A3"].font = copy(RED_FONT)
    sheet["A3"].number_format = "0"
    sheet["B3"] = "Missing. The cell is the number 0. Confirm it on the card before import."
    sheet["A4"] = "unclear"
    sheet["A4"].fill = YELLOW_FILL
    sheet["B4"] = "Unclear, failed a check, or the two models disagreed. The text is what was read."
    sheet["A5"] = "{TAB}"
    sheet["B5"] = "Navigation key. Do not edit."
    sheet["A6"] = "{DOWN}"
    sheet["B6"] = "Move to the next record. Do not edit."
    sheet["A8"] = "Columns H and Y are always 0."
    sheet.column_dimensions["A"].width = 14
    sheet.column_dimensions["B"].width = 88


def _width(letter: str) -> float:
    if letter in {"B", "D", "V"}:
        return 24
    if letter in {"F", "T"}:
        return 18
    if letter == "J":
        return 12
    return 8
