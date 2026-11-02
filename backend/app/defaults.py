"""Default configuration. Every value here is a chosen default and is listed in the README."""

from __future__ import annotations

# Relative boxes on an upright card: [x0, y0, x1, y1].
# CEIL, SF SONIC and DYNEX share this layout. Tune them in Settings → Calibration.
DEFAULT_ZONES: dict[str, list[float]] = {
    "header": [0.0, 0.0, 1.0, 0.085],
    "label": [0.05, 0.09, 0.95, 0.325],
    "qr": [0.06, 0.10, 0.40, 0.30],
    "barcode": [0.08, 0.22, 0.70, 0.325],
    "dealer_text": [0.40, 0.335, 0.96, 0.395],
    "bill_no": [0.48, 0.395, 0.96, 0.45],
    "date_of_sale": [0.40, 0.45, 0.96, 0.505],
    "vehicle_no": [0.48, 0.505, 0.96, 0.565],
    "make_model": [0.40, 0.565, 0.96, 0.63],
    "customer_name": [0.40, 0.78, 0.96, 0.84],
    "tel": [0.28, 0.84, 0.70, 0.91],
    "signature": [0.28, 0.90, 0.70, 0.98],
}

HANDWRITING_FIELDS = (
    "dealer_text",
    "bill_no",
    "date_of_sale",
    "vehicle_no",
    "make_model",
    "customer_name",
    "tel",
    "signature",
)

# Navigation columns are the assumption used when a template has no prototype row.
# Uploading the real workbook replaces this: any {TAB} or {DOWN} already in the file is kept.
DEFAULT_LOADER_COLUMNS: dict[str, dict] = {}


def _loader_columns() -> dict[str, dict]:
    columns: dict[str, dict] = {}
    field_at = {
        "B": "part_no",
        "D": "serial_batch",
        "F": "date_of_sale",
        "J": "branch",
        "T": "vehicle_no",
        "V": "make_model",
    }
    constant_at = {"H": 0, "Y": 0}
    for index in range(26):
        letter = chr(ord("A") + index)
        if letter in field_at:
            columns[letter] = {"kind": "field", "field": field_at[letter]}
        elif letter in constant_at:
            columns[letter] = {"kind": "constant", "value": constant_at[letter]}
        elif letter == "Z":
            columns[letter] = {"kind": "literal", "value": "{DOWN}"}
        else:
            columns[letter] = {"kind": "literal", "value": "{TAB}"}
    return columns


DEFAULT_LOADER_COLUMNS = _loader_columns()

LOADER_HEADERS = {
    "A": "",
    "B": "Part No",
    "C": "",
    "D": "Serial + Batch",
    "E": "",
    "F": "Date of Sale",
    "G": "",
    "H": "",
    "I": "",
    "J": "Branch No",
    "K": "",
    "L": "",
    "M": "",
    "N": "",
    "O": "",
    "P": "",
    "Q": "",
    "R": "",
    "S": "",
    "T": "Vehicle Reg. No.",
    "U": "",
    "V": "Make/Model",
    "W": "",
    "X": "",
    "Y": "",
    "Z": "",
}


def default_config() -> dict:
    return {
        "ai_enabled": True,
        "confidence_threshold": 0.8,
        "cards_per_request": 6,
        "max_batch_spend": 2.0,
        "max_month_spend": 50.0,
        "date_max_age_months": 12,
        "duplicate_policy": "warn",  # warn | block | supervisor
        "stage2_order": ["deepseek", "openai", "anthropic"],
        "stage3_order": ["anthropic", "openai", "deepseek"],
        "providers": {
            "deepseek": {
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-flash",
                "api_key_enc": "",
                "input_per_million": 0.14,
                "output_per_million": 0.28,
                "timeout": 90,
            },
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "api_key_enc": "",
                "input_per_million": 0.15,
                "output_per_million": 0.60,
                "timeout": 90,
            },
            "anthropic": {
                "base_url": "https://api.anthropic.com",
                "model": "claude-sonnet-4-5",
                "api_key_enc": "",
                "input_per_million": 3.0,
                "output_per_million": 15.0,
                "timeout": 90,
            },
        },
        "loader": {
            "sheet": "Data Loader",
            "columns": DEFAULT_LOADER_COLUMNS,
        },
        "prompt_version": "v1",
    }


def default_zones() -> dict:
    return {"zones": DEFAULT_ZONES}
