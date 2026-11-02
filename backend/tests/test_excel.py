from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from app.excel.export import build_default_template, export_data_loader


def test_data_loader_has_no_blanks_and_colours(tmp_path: Path):
    template = build_default_template(tmp_path / "template.xlsx")
    cards = [
        {
            "part_no": "DINN55",
            "serial": "A2H6D024885",
            "batch_code": "2H63",
            "date_of_sale": date(2026, 10, 12),
            "stamp_branch": 1,
            "vehicle_no": "332666",
            "make_model": "TOYOTA COSTOR",
            "field_flags": {},
            "flags": [],
        },
        {
            "part_no": "",
            "serial": "",
            "batch_code": "",
            "date_of_sale": None,
            "stamp_branch": None,
            "vehicle_no": "",
            "make_model": "unclear text",
            "field_flags": {"make_model": ["unclear"]},
            "flags": [],
        },
    ]
    output = export_data_loader(cards, template, tmp_path / "out.xlsx")
    workbook = load_workbook(output)
    assert "Data Loader" in workbook.sheetnames
    assert "Sheet1" in workbook.sheetnames
    assert "Legend" in workbook.sheetnames
    sheet = workbook["Data Loader"]
    assert sheet["B1"].value == "Part No"
    assert sheet["B2"].value == "DINN55"
    assert sheet["D2"].value == "A2H6D0248852H63"
    assert sheet["F2"].value.date() == date(2026, 10, 12) if hasattr(sheet["F2"].value, "date") else sheet["F2"].value == date(2026, 10, 12)
    assert sheet["F2"].number_format == "dd-mmm-yyyy"
    assert sheet["H2"].value == 0
    assert sheet["J2"].value == 1
    assert sheet["T2"].value == "332666"
    assert sheet["V2"].value == "TOYOTA COSTOR"
    assert sheet["Y2"].value == 0
    assert sheet["A2"].value == "{TAB}"
    assert sheet["Z2"].value == "{DOWN}"
    assert sheet["C2"].value == "{TAB}"

    for letter in ("B", "D", "F", "J", "T"):
        assert sheet[f"{letter}3"].value == 0
        assert sheet[f"{letter}3"].fill.fgColor.rgb.endswith("FFC7CE")
        assert sheet[f"{letter}3"].font.color.rgb.endswith("9C0006")
        assert sheet[f"{letter}3"].font.bold is True
        assert sheet[f"{letter}3"].number_format == "0"
    assert sheet["V3"].value == "unclear text"
    assert sheet["V3"].fill.fgColor.rgb.endswith("FFF2CC")
    assert sheet["H3"].value == 0
    assert sheet["Y3"].value == 0
    assert sheet["Z3"].value == "{DOWN}"
