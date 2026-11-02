# Accuracy and cost report

Date: 1 Nov 2026.

## Sample files

The brief names three PDFs and their ground-truth workbooks:

| File | Cards expected |
|---|---|
| Battery_Card.pdf | 36 |
| 1046339-1-11-2026.pdf | 60 |
| 1046906-1-11-2026.pdf | 109 |

It also names 27 serials shared by `Battery_Card.pdf` and `1046906-1-11-2026.pdf`.

Those files were not in the project folder, the Desktop, Downloads, or Documents. There is nothing to score cell by cell against `Warranty_Cards_*.xlsx` or `Data_Loader_*.xlsx`. This report does not invent that score.

Put the PDFs and workbooks in `samples/` and the comparison can be run on the same pipeline the tests use (`app.jobs.scan_batch` and `app.excel.export`).

## What was measured

`pytest` in `backend`: **15 passed**.

Stage 1 on a synthetic page with the stated layout (4 cards rotated 90° on the left, 2 upright on the right):

| Check | Result |
|---|---|
| Cards found | 6 of 6 |
| Each card rotated upright, brand SF SONIC from the blue header | yes |
| Serial, batch and type from the QR | 6 of 6, source `qr` |
| Empty dealer, vehicle and phone zones | not marked as ink |
| Date zone, which has writing | marked as ink |
| Second upload of the same serial | flagged against the earlier batch |

The QR value is not sent to a model. Empty zones are not put on the handwriting strip.

Data Loader export from that batch, after a person fills the handwriting and approves:

- Sheet names `Data Loader`, `Sheet1`, `Legend`
- Header row kept
- `{TAB}` and `{DOWN}` on every data row
- No blank in B, D, F, J, T, V
- Date cells are real dates with format `dd-mmm-yyyy`
- H and Y are the number 0

Rules covered by unit tests, from the brief rather than from a guessed JSON file:

- Serial `A2H6D024885` matches batch `2H63` (characters 2–4). A different batch is a warning, not an error.
- `12 OCT 26` is 12 Oct 2026.
- `FSEO` matches `FSE0`, `FDMO` matches `FDM0`.
- Prefixes map to CEIL, SF SONIC and DYNEX.
- `1046906-1-11-2026.pdf` yields stamp code 1046906.
- A phone that is not 8 digits starting with 3, 6 or 1 is an error.
- A sale date after the upload date is an error.
- A car name in the dealer line is a warning.

## Cost

The synthetic scan had no API key, so Stage 2 did not run. Token count for that batch is 0. Empty fields and the QR fields were not included in a request.

When a key is set, Stage 2 sends at most one request per 6 cards (`cards_per_request`, inside the brief's 4–6 range). Stage 3 sends a second request only for fields under 0.80 confidence or a failed phone or date check, and only those crops. A repeat of the same strip is a cache hit with zero tokens. The dashboard sums tokens and estimated cost per provider per day. The prices in Settings are estimates, not the provider's invoice.

Per-field accuracy of Stage 1 alone, and of Stage 1+2+3, on the three real PDFs is not in this report. It needs those PDFs and the ground-truth workbooks.
