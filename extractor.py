"""
Label photo/PDF -> structured JSON extraction, via the Anthropic API.

This is the "less reliable, needs review" half of the two-step workflow
described in README.md: turning a label image/PDF into the structured
label_data.json that label_rules.py can deterministically check. Extraction
uses a vision-capable Claude model and is inherently best-effort — small
print, glare, and cropped edges can all cause misreads. Always review the
extracted JSON against the source document before trusting a compliance
report built from it.

Requires the ANTHROPIC_API_KEY environment variable to be set. On Render:
Dashboard -> your service -> Environment -> Add Environment Variable.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).parent
SCHEMA_TEMPLATE = (BASE_DIR / "schema_template.json").read_text()

MODEL_NAME = os.environ.get("MODEL_NAME", "claude-sonnet-5")

ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

EXTRACTION_PROMPT = f"""You are extracting structured data from a photo or PDF of a Canadian \
prepackaged food product label, for use by an automated CFIA/Health Canada label-compliance \
checking tool. Read all visible text carefully, including small print, both English and French \
text if the label is bilingual, and every value in the Nutrition Facts table.

Output ONLY a single JSON object — no markdown code fences, no commentary before or after it — \
that follows this exact structure (this is the schema, not literal values to copy):

{SCHEMA_TEMPLATE}

Rules:
- For any field you cannot determine from the document with reasonable confidence, OMIT it \
entirely. Do not guess, invent, or default a value you cannot actually see. Do not assume a \
boolean is false unless you can positively confirm that condition (e.g. only set \
"irradiated": true if you can see the radura symbol or an irradiation statement — otherwise \
omit the field rather than writing false).
- Preserve both English and French text separately where the label is bilingual.
- Extract Nutrition Facts numbers exactly as printed, including units.
- Do not include the "_comment" field from the schema in your output.
- Do not editorialize or add fields not present in the schema.
"""


def strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


def extract_label_json(file_bytes: bytes, media_type: str) -> str:
    """Call the Anthropic API with the uploaded file and return raw JSON text.

    Raises EnvironmentError if ANTHROPIC_API_KEY is not configured, or
    RuntimeError if the API call itself fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set on the server. Add it under your Render service's "
            "Environment tab (Dashboard -> service -> Environment -> Add Environment Variable) "
            "to enable PDF/photo extraction."
        )

    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("The 'anthropic' package is not installed — check requirements.txt.") from e

    client = anthropic.Anthropic(api_key=api_key)
    b64_data = base64.standard_b64encode(file_bytes).decode("utf-8")

    if media_type == "application/pdf":
        file_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": media_type, "data": b64_data},
        }
    else:
        file_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64_data},
        }

    try:
        response = client.messages.create(
            model=MODEL_NAME,
            max_tokens=4096,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": [
                        file_block,
                        {"type": "text", "text": EXTRACTION_PROMPT},
                    ],
                }
            ],
        )
    except Exception as e:  # noqa: BLE001 — surface API errors to the caller
        raise RuntimeError(f"Anthropic API call failed: {e}") from e

    raw_text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    return strip_json_fences(raw_text)


def validate_and_prettify(raw_json_text: str) -> str:
    """Parse raw_json_text and return it pretty-printed, or raise json.JSONDecodeError."""
    parsed = json.loads(raw_json_text)
    return json.dumps(parsed, indent=2, ensure_ascii=False)
