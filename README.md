# Warranty Card Scanner

Operators upload a PDF of scanned battery warranty cards (CEIL, SF SONIC, DYNEX). The app reads the printed label without calling an AI, sends only the handwriting that is actually on the card, and lets a person correct the result before it is written into the Data Loader workbook.

## Run it

Docker is the supported way to run the whole system:

```bash
docker compose up --build
```

Open http://localhost:8080. The API is also on http://localhost:8000.

Sign in with a demo account. Change these before real use (Settings are admin-only; users are under Users):

| Email | Password | Role |
|---|---|---|
| admin@ekk.local | Admin@12345 | Settings, users, API keys, everything else |
| supervisor@ekk.local | Super@12345 | Approve duplicates, edit the part list |
| operator@ekk.local | Oper@12345 | Upload, correct, approve when policy allows |

`APP_SECRET` in `docker-compose.yml` is a development default. Set a long random value in the environment before storing real API keys. Keys are encrypted with it.

### What comes up

| Service | Role |
|---|---|
| web | React app, nginx, port 8080 |
| api | FastAPI, port 8000 |
| worker | RQ worker. Scanning never runs inside the web request |
| db | PostgreSQL 16 |
| redis | Queue |

Uploads, card images and the Data Loader template live in the `storage` volume.

### Local API without Docker

Python 3.12 is what the image uses. 3.11 works for the tests on Windows. Tesseract and ZBar are optional on the host: QR falls back to OpenCV, and stamp OCR is skipped when Tesseract is missing. The Docker image installs Tesseract (eng + ara) and ZBar.

```bash
cd backend
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

On Windows the API uses an in-process worker (`WORKER_INLINE=1`) because RQ cannot fork there. Docker sets `WORKER_INLINE=0` and the `worker` service runs the queue.

```bash
cd frontend
npm install
npm run dev
```

The dev server proxies `/api` to port 8000.

### Tests

```bash
cd backend
pytest
```

The suite covers the validation rules, part-number normalisation, the Data Loader workbook, QR reading, six-card page detection, duplicate serials across batches, and upload → scan → approve → export.

## How a scan works

Stage 1 is free and runs on every card:

1. Render the PDF at 300 dpi.
2. Find card rectangles and rotate each one upright from the coloured header.
3. Brand from the header hue: green CEIL, blue SF SONIC, red DYNEX.
4. QR, then barcode, then Tesseract on the label only if both codes fail.
5. Measure ink in each handwriting zone. An empty zone is stored as 0 and is not sent to a model.
6. Blue warranty stamp (code, phone, branch) and a round dealer stamp.
7. Validation and duplicate checks.

Stage 2 sends one greyscale strip per card, only the zones that contain ink, up to 6 cards per request. Stage 3 sends only the fields that scored under 0.80 or failed the phone or date check. If the two models disagree the field is marked orange and a third model is not called.

Results are cached by the strip's SHA-256. Scanning the same image again records a cache hit and costs nothing.

If AI is off, or the batch or monthly budget is spent, those fields stay yellow for a person to type.

## AI providers

An administrator pastes keys in **Settings**. They are encrypted on the server and are never returned to the browser. Leave the key box blank to keep the saved key.

| Provider | Default base URL | Default model |
|---|---|---|
| DeepSeek (Stage 2) | `https://api.deepseek.com` | `deepseek-flash` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| Anthropic (Stage 3) | `https://api.anthropic.com` | `claude-sonnet-4-5` |

DeepSeek uses the OpenAI chat-completions shape. The image is a base64 `image_url`. Claude uses the Messages API with an `image` block. The prompt and the JSON schema are the same for all three. A reply that is not valid JSON is sent back once with a fix instruction. HTTP 429 and 5xx are retried with backoff, then the next provider in the stage list is used.

Prices per 1M tokens in Settings are estimates used for the dashboard. They are not a bill from the provider. Defaults: DeepSeek 0.14 / 0.28, OpenAI 0.15 / 0.60, Anthropic 3 / 15.

Stage 2 order starts with DeepSeek. Stage 3 order starts with Anthropic, then OpenAI. Both lists are editable.

## Calibrate card zones

CEIL, SF SONIC and DYNEX share one layout. **Settings → Card zones** stores each box as `x0, y0, x1, y1` fractions of the upright card. Save, then rescan a batch that is not approved yet.

The defaults put the handwriting to the right of the printed captions so the caption itself is not counted as ink. If a field is often empty when it was filled, widen that box. If empty fields are sent to the model, narrow it.

## Part numbers

**Part numbers** imports an Excel file with columns `Battery Type` and `BATTERY_ITEM_CODE`. Preview shows added, changed and removed rows before Merge or Replace. Search, add, edit and delete are in the same screen. History records who changed what.

`FSE0` and `FSEO` are the same type, and so are `FDM0` and `FDMO`. Spaces and case are ignored.

Types seen on cards that have no part number sit in the unmatched list. Assigning a part updates every card of that type in batches that are not approved or exported.

## Data Loader template

Export copies the workbook stored in Settings and fills it. It does not build a workbook from scratch. **Settings → Data Loader template** replaces that file. The sheet must be named `Data Loader`.

Until the official template is uploaded, the app ships a starter workbook:

- Row 1 is the header and is left as it is.
- Row 2 of the starter is a prototype. Any `{TAB}` or `{DOWN}` in the template is repeated on every card row.
- Data columns, which the instructions name explicitly: **B** Part No, **D** Serial + Batch, **F** Date of Sale (`dd-mmm-yyyy`), **J** Branch, **T** Vehicle, **V** Make/Model.
- **H** and **Y** are the number 0.
- Other columns are `{TAB}`. **Z** is `{DOWN}`.
- A missing value in B, D, F, J, T or V is the number 0 with fill `FFC7CE`, bold dark-red font and number format `0`.
- An unclear value keeps the text and gets fill `FFF2CC`.
- `Sheet1` is preserved. A Legend sheet explains the colours.

Upload the real `Copy of Battery-Card-3` workbook when you have it. If its row 2 already contains `{TAB}` and `{DOWN}`, those tokens are what the export repeats, including where they differ from the starter.

The detailed workbook is the optional second download. It has one row per card: stamp code, branch, stamp phone, condition, dealer stamp, QR verified, duplicate flag and remarks.

## Duplicates

Approved cards have a unique serial. During the scan the app flags:

- the same serial twice in one PDF (keep one of them from the Verify screen)
- a serial already stored from an earlier upload, shown in purple with the first file, date and phone
- the same serial with a different phone or vehicle, shown as a possible duplicate claim

Settings → Duplicate policy: warn only (default), block approval, or require a supervisor.

## Backup

Back up the Postgres volume and the storage volume together. The storage volume holds the original PDFs, card crops and the template. The database holds the approved rows, the part list, the audit log and the encrypted keys.

```bash
docker compose exec db pg_dump -U warranty warranty > warranty.sql
docker compose cp api:/data/storage ./storage-backup
```

Restoring onto a new `APP_SECRET` makes the saved API keys unreadable. Keep the secret with the backup.

## Defaults chosen because the reference files were not on this machine

These files were named in the brief and were not in the project folder or elsewhere on the computer:

- `Battery_Card_Data_Loader_Instructions.json`
- `Copy of Battery-Card-3 - Copy 2.xlsx`
- `Battery_Part_No.xlsx`
- `Battery_Card.pdf`, `1046339-1-11-2026.pdf`, `1046906-1-11-2026.pdf`
- `Warranty_Cards_*.xlsx`, `Data_Loader_*.xlsx`

The rules that the brief states exactly are implemented that way (serial and batch patterns, brand prefixes, Bahrain phone numbers, the 12-month date window, stamp code from the file name, the Data Loader column letters, the red and yellow fills). Everything else is a default:

- Navigation columns are `{TAB}`, and column Z is `{DOWN}`, until a real template row says otherwise.
- Serial and batch are joined with one space.
- A two-digit year is 2000 plus that year, so 26 is 2026.
- Dates written as numbers are day/month/year.
- Duplicate policy starts as warn only.
- Stage 2 batches 6 cards. Confidence below 0.80 goes to Stage 3.
- Budgets start at 2 per batch and 50 per month, in the currency of the prices you enter.
- Demo users are created when the database is empty (`SEED_DEMO_USERS=1`).

Drop the real PDFs and workbooks into `samples/` and upload the template and the part list in the app. Re-run `pytest` after pointing an accuracy check at those files; see `ACCURACY_REPORT.md`.
