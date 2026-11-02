from datetime import date
from io import BytesIO

from openpyxl import Workbook, load_workbook

from app.jobs import scan_batch
from app.pipeline.synthetic import make_page, write_pdf


def test_upload_scan_approve_export(client, admin_headers, tmp_path):
    pdf = tmp_path / "1046906-1-11-2026.pdf"
    write_pdf(pdf, [make_page()])
    with pdf.open("rb") as handle:
        uploaded = client.post(
            "/api/batches/upload",
            files={"files": ("1046906-1-11-2026.pdf", handle, "application/pdf")},
            headers=admin_headers,
        )
    assert uploaded.status_code == 200, uploaded.text
    batch_id = uploaded.json()["batches"][0]["id"]
    assert uploaded.json()["batches"][0]["stamp_code_from_name"] == "1046906"

    scan_batch(batch_id)
    cards = client.get(f"/api/batches/{batch_id}/cards", headers=admin_headers)
    assert cards.status_code == 200
    rows = cards.json()
    assert len(rows) == 6
    assert all(row["qr_verified"] for row in rows)
    assert all(row["serial_source"] if False else row["sources"]["serial"][0]["source"] == "qr" for row in rows)
    assert {row["serial"] for row in rows}  # unique inside this synthetic page

    # Empty handwriting stays off the AI strip: no provider is configured, so ink fields are manual.
    for row in rows:
        assert row["date_raw"] in (None, "", "0") or row["field_flags"].get("date_of_sale")

    part = client.post(
        "/api/part-numbers",
        json={"battery_type": "FSEO-Q65R(EFB)-BH-IS", "part_no": "sfs-100"},
        headers=admin_headers,
    )
    assert part.status_code == 200, part.text
    assert part.json()["part_no"] == "SFS-100"
    again = client.get(f"/api/batches/{batch_id}/cards", headers=admin_headers).json()
    assert all(row["part_no"] == "SFS-100" for row in again)

    # Fill the mandatory handwriting so approval and export are meaningful.
    for row in again:
        patched = client.patch(
            f"/api/cards/{row['id']}",
            json={
                "date_raw": "12 OCT 2026",
                "vehicle_no": "332666",
                "make_model": "TOYOTA",
                "tel": "39444939",
                "dealer_text": "Kanoo",
                "bill_no": "100",
                "customer_name": "Ali",
            },
            headers=admin_headers,
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["date_of_sale"] == "2026-10-12"
        assert any(item["source"] == "user" for item in patched.json()["sources"]["tel"])

    approved = client.post(f"/api/batches/{batch_id}/approve", headers=admin_headers)
    assert approved.status_code == 200, approved.text
    exported = client.get(f"/api/batches/{batch_id}/export/data-loader", headers=admin_headers)
    assert exported.status_code == 200
    workbook = load_workbook(BytesIO(exported.content))
    sheet = workbook["Data Loader"]
    assert sheet.max_row >= 7
    for excel_row in range(2, 8):
        for letter in ("B", "D", "F", "J", "T", "V"):
            assert sheet[f"{letter}{excel_row}"].value not in (None, "")
        assert sheet[f"A{excel_row}"].value == "{TAB}"
        assert sheet[f"Z{excel_row}"].value == "{DOWN}"
        assert sheet[f"H{excel_row}"].value == 0
        assert isinstance(sheet[f"F{excel_row}"].value, date)

    detail = client.get(f"/api/batches/{batch_id}/export/detailed", headers=admin_headers)
    assert detail.status_code == 200
    detailed = load_workbook(BytesIO(detail.content))
    assert detailed.active["A1"].value == "Batch file"
    assert detailed.active["V1"].value == "QR Verified"


def test_duplicate_serial_across_batches(client, admin_headers, tmp_path):
    def one_card_pdf(path, serial_index):
        from app.pipeline.synthetic import make_card
        import numpy as np

        card = make_card(serial="A9Z1B100001", batch_code="9Z10")
        page = np.full((card.shape[0] + 80, card.shape[1] + 80, 3), 232, np.uint8)
        page[40 : 40 + card.shape[0], 40 : 40 + card.shape[1]] = card
        write_pdf(path, [page])

    first = tmp_path / "a.pdf"
    second = tmp_path / "1046339-1-11-2026.pdf"
    one_card_pdf(first, 0)
    one_card_pdf(second, 0)
    ids = []
    for path in (first, second):
        with path.open("rb") as handle:
            response = client.post(
                "/api/batches/upload",
                files={"files": (path.name, handle, "application/pdf")},
                headers=admin_headers,
            )
        assert response.status_code == 200, response.text
        batch_id = response.json()["batches"][0]["id"]
        scan_batch(batch_id)
        ids.append(batch_id)
    second_cards = client.get(f"/api/batches/{ids[1]}/cards", headers=admin_headers).json()
    assert second_cards[0]["duplicate_kind"] in {"prior_batch", "claim"}
    assert second_cards[0]["duplicate_of"]["batch_id"] == ids[0]


def test_operator_cannot_change_settings(client, operator_headers):
    response = client.put("/api/settings", json={"ai_enabled": False}, headers=operator_headers)
    assert response.status_code == 403


def test_part_import_preview_shows_diff(client, admin_headers, tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Battery Type", "BATTERY_ITEM_CODE"])
    sheet.append(["FDM0-N55", "dyn-1"])
    sheet.append(["FCE7-DIN66", "ceil-9"])
    path = tmp_path / "parts.xlsx"
    workbook.save(path)
    with path.open("rb") as handle:
        preview = client.post(
            "/api/part-numbers/import?mode=preview",
            files={"file": ("parts.xlsx", handle, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers=admin_headers,
        )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert len(body["added"]) == 2
