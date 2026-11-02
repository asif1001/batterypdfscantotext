"""Shared vision prompt. The same text and JSON schema go to every provider."""

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You read handwritten fields on authorised-distributor battery warranty cards (CEIL, SF SONIC, DYNEX).

You receive one greyscale strip per card. Each strip is a vertical stack of labelled crops. Only fields that contain ink are shown. Do not guess fields that are not pictured.

Rules:
- Copy the writing exactly as written. Mark a doubtful character with a question mark immediately after it, for example 8?.
- A two-digit year 26 means 2026. Return the date as written.
- A number written in Make/Model is probably the vehicle number. Put it in vehicle_no and leave make_model empty when there is no car name.
- A car name written in the Dealer field is not a dealer. Leave dealer_text empty and, if make_model is empty, put the car name there.
- A value that has been scribbled over is "illegible".
- Never invent values. If you cannot read a field, return an empty string and confidence 0.
- signature_present is "yes" or "no".
- confidence is a number from 0 to 1.

Return strict JSON only, with no markdown:
{"cards":[{"card_id":"...","fields":{"field_name":{"value":"...","confidence":0.0}}}]}
Include every card_id you were given and every field label shown on that strip.
"""

FIX_PROMPT = "Your previous reply was not valid JSON matching the schema. Return only the JSON object, with no markdown."
