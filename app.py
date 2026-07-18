"""
Minimal Flask web front-end for the Canada Food Label Compliance Checker.

Lets you paste/upload a label_data.json (see schema_template.json) and view
the PASS/FAIL/NEEDS_REVIEW report in the browser. This is a thin UI layer —
all the actual rule logic lives in label_rules.py and is unchanged from the
CLI version.

Run locally:
    pip install -r requirements.txt
    python app.py
    # open http://localhost:5000

Deploy: see README.md for Render.com instructions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Flask, render_template_string, request

from label_rules import run_all_checks, Status
from extractor import extract_label_json, validate_and_prettify, ALLOWED_EXTENSIONS, MAX_FILE_SIZE

app = Flask(__name__)

BASE_DIR = Path(__file__).parent
SAMPLE_COMPLIANT = (BASE_DIR / "samples" / "sample_compliant.json").read_text()
SAMPLE_NONCOMPLIANT = (BASE_DIR / "samples" / "sample_noncompliant.json").read_text()
SCHEMA_TEMPLATE = (BASE_DIR / "schema_template.json").read_text()

STATUS_ORDER = [Status.FAIL, Status.NEEDS_REVIEW, Status.WARNING, Status.PASS, Status.NOT_APPLICABLE]
STATUS_CLASS = {
    Status.PASS: "pass",
    Status.FAIL: "fail",
    Status.NEEDS_REVIEW: "review",
    Status.NOT_APPLICABLE: "na",
    Status.WARNING: "warn",
}
STATUS_LABEL = {
    Status.PASS: "PASS",
    Status.FAIL: "FAIL",
    Status.NEEDS_REVIEW: "NEEDS REVIEW",
    Status.NOT_APPLICABLE: "N/A",
    Status.WARNING: "WARNING",
}

PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Canada Food Label Compliance Checker</title>
<style>
  :root {
    --fail: #c62828; --review: #b8860b; --warn: #e08a00; --pass: #2e7d32; --na: #757575;
  }
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 980px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; line-height: 1.5; }
  h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
  .subtitle { color: #555; margin-top: 0; margin-bottom: 1.5rem; }
  .disclaimer { background: #fff8e1; border: 1px solid #ffe082; border-radius: 6px; padding: 0.75rem 1rem; font-size: 0.9rem; margin-bottom: 1.5rem; }
  textarea { width: 100%; box-sizing: border-box; font-family: ui-monospace, Menlo, monospace; font-size: 0.85rem; height: 340px; padding: 0.75rem; border: 1px solid #ccc; border-radius: 6px; }
  .row { display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; margin: 0.75rem 0; }
  button, .btn { background: #1a1a1a; color: white; border: none; padding: 0.55rem 1.1rem; border-radius: 6px; cursor: pointer; font-size: 0.9rem; text-decoration: none; display: inline-block; }
  button.secondary, .btn.secondary { background: #eee; color: #1a1a1a; }
  .summary { display: flex; gap: 0.5rem; flex-wrap: wrap; margin: 1rem 0; }
  .chip { border-radius: 999px; padding: 0.3rem 0.8rem; font-size: 0.85rem; font-weight: 600; color: white; }
  .chip.fail { background: var(--fail); } .chip.review { background: var(--review); }
  .chip.warn { background: var(--warn); } .chip.pass { background: var(--pass); } .chip.na { background: var(--na); }
  .group { margin-top: 1.5rem; }
  .group h2 { font-size: 1.05rem; border-bottom: 2px solid #eee; padding-bottom: 0.3rem; }
  .result { border-left: 4px solid #ccc; padding: 0.6rem 0.9rem; margin-bottom: 0.6rem; background: #fafafa; border-radius: 0 4px 4px 0; }
  .result.fail { border-color: var(--fail); } .result.review { border-color: var(--review); }
  .result.warn { border-color: var(--warn); } .result.pass { border-color: var(--pass); } .result.na { border-color: var(--na); }
  .result .cat { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.03em; color: #666; }
  .result .msg { margin: 0.15rem 0; }
  .result .cite { font-size: 0.8rem; color: #666; }
  .result .cite a { color: #555; }
  .error { background: #ffebee; border: 1px solid #ef9a9a; padding: 0.75rem 1rem; border-radius: 6px; }
  .notice { background: #e3f2fd; border: 1px solid #90caf9; padding: 0.75rem 1rem; border-radius: 6px; font-size: 0.9rem; margin-bottom: 1rem; }
  .upload-box { border: 1px dashed #bbb; border-radius: 8px; padding: 1rem; margin-bottom: 1rem; background: #fbfbfb; }
  .upload-box .label { font-size: 0.85rem; color: #555; margin-bottom: 0.5rem; }
  input[type="file"] { font-size: 0.85rem; }
  footer { margin-top: 3rem; color: #888; font-size: 0.8rem; }
</style>
</head>
<body>
  <h1>Canada Food Label Compliance Checker</h1>
  <p class="subtitle">First-pass triage against CFIA / Health Canada label requirements.</p>
  <div class="disclaimer">
    This is an automated first-pass check, not a legal compliance certification.
    "Needs review" results require human judgment against the CFIA Industry
    Labelling Tool — they are not a soft pass. See the
    <a href="https://github.com/{{ repo_owner }}/{{ repo_name }}#scope-and-limitations" target="_blank">README</a>
    for full scope and limitations.
  </div>

  <div class="upload-box">
    <div class="label">Upload a label PDF or photo to auto-extract it into the box below (uses Claude — review the result before checking, it can misread small print).</div>
    <form method="post" action="/extract" enctype="multipart/form-data" class="row">
      <input type="file" name="label_file" accept=".pdf,.png,.jpg,.jpeg,.webp" required>
      <button type="submit" class="secondary">Extract from PDF/photo</button>
    </form>
  </div>

  {% if extracted %}
  <div class="notice">
    This JSON was extracted automatically from your uploaded file and has <strong>not</strong> been
    verified. Review it against the original label &mdash; especially Nutrition Facts numbers,
    allergens, and bilingual text &mdash; before trusting the compliance results below.
  </div>
  {% endif %}

  <form method="post" action="/check">
    <textarea name="label_json" placeholder="Paste label_data.json here, or extract one above...">{{ prefill }}</textarea>
    <div class="row">
      <button type="submit">Check label</button>
      <a class="btn secondary" href="/?sample=compliant">Load compliant sample</a>
      <a class="btn secondary" href="/?sample=noncompliant">Load non-compliant sample</a>
      <a class="btn secondary" href="/?sample=template">Load blank template</a>
    </div>
  </form>

  {% if error %}
  <div class="error"><strong>Error:</strong> {{ error }}</div>
  {% endif %}

  {% if report %}
  <div class="summary">
    <span class="chip fail">FAIL {{ report.summary.FAIL }}</span>
    <span class="chip review">NEEDS REVIEW {{ report.summary.NEEDS_REVIEW }}</span>
    <span class="chip warn">WARNING {{ report.summary.WARNING }}</span>
    <span class="chip pass">PASS {{ report.summary.PASS }}</span>
    <span class="chip na">N/A {{ report.summary.NOT_APPLICABLE }}</span>
  </div>

  {% for status_key, status_label, items in grouped %}
    {% if items %}
    <div class="group">
      <h2>{{ status_label }} ({{ items|length }})</h2>
      {% for r in items %}
      <div class="result {{ status_key }}">
        <div class="cat">{{ r.category }} &middot; {{ r.rule_id }}</div>
        <div class="msg">{{ r.message }}</div>
        {% if r.citation %}<div class="cite">Source: {{ r.citation }}</div>{% endif %}
      </div>
      {% endfor %}
    </div>
    {% endif %}
  {% endfor %}
  {% endif %}

  <footer>
    Canada Food Label Compliance Checker &mdash; rules-based triage tool, not legal advice.
  </footer>
</body>
</html>
"""


def _group_results(report_dict: dict):
    by_status = {s: [] for s in STATUS_ORDER}
    for r in report_dict["results"]:
        by_status[Status(r["status"])].append(r)
    return [(STATUS_CLASS[s], STATUS_LABEL[s], by_status[s]) for s in STATUS_ORDER]


def _render(prefill="", report=None, grouped=None, error=None, extracted=False):
    return render_template_string(
        PAGE, prefill=prefill, report=report, grouped=grouped or [], error=error, extracted=extracted,
        repo_owner=os.environ.get("REPO_OWNER", "WalterDull"),
        repo_name=os.environ.get("REPO_NAME", "LabelComplianceCanada"),
    )


@app.route("/", methods=["GET"])
def index():
    sample = request.args.get("sample")
    prefill = ""
    if sample == "compliant":
        prefill = SAMPLE_COMPLIANT
    elif sample == "noncompliant":
        prefill = SAMPLE_NONCOMPLIANT
    elif sample == "template":
        prefill = SCHEMA_TEMPLATE
    return _render(prefill=prefill)


@app.route("/check", methods=["POST"])
def check():
    raw = request.form.get("label_json", "")
    error = None
    report_dict = None
    grouped = []
    try:
        data = json.loads(raw)
        report = run_all_checks(data)
        report_dict = report.to_dict()
        grouped = _group_results(report_dict)
    except json.JSONDecodeError as e:
        error = f"Invalid JSON: {e}"
    except Exception as e:  # noqa: BLE001 — surface any rule-engine error to the UI
        error = f"Error running checks: {e}"

    return _render(prefill=raw, report=report_dict, grouped=grouped, error=error)


@app.route("/extract", methods=["POST"])
def extract():
    file = request.files.get("label_file")
    error = None
    prefill = ""
    extracted = False

    if not file or file.filename == "":
        error = "No file selected."
    else:
        ext = Path(file.filename).suffix.lower()
        media_type = ALLOWED_EXTENSIONS.get(ext)
        if not media_type:
            error = f"Unsupported file type '{ext}'. Upload a PDF, PNG, JPG, or WEBP."
        else:
            file_bytes = file.read()
            if len(file_bytes) > MAX_FILE_SIZE:
                error = f"File too large ({len(file_bytes) // (1024 * 1024)} MB) — max is {MAX_FILE_SIZE // (1024 * 1024)} MB."
            else:
                try:
                    raw_json = extract_label_json(file_bytes, media_type)
                    prefill = validate_and_prettify(raw_json)
                    extracted = True
                except EnvironmentError as e:
                    error = str(e)
                except json.JSONDecodeError:
                    error = ("The extraction model did not return valid JSON. Try again, or use a "
                              "clearer/higher-resolution photo or PDF.")
                except Exception as e:  # noqa: BLE001
                    error = f"Extraction failed: {e}"

    return _render(prefill=prefill, error=error, extracted=extracted)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
