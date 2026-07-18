# Canada Food Label Compliance Checker

A first-pass, rules-based triage tool for Canadian prepackaged food label
compliance. It checks the label requirements published by the Canadian
Food Inspection Agency's **Industry Labelling Tool**
(https://inspection.canada.ca/en/food-labels/labelling/industry) and
Health Canada's **Front-of-package nutrition symbol labelling guide**.

**This tool is not a legal compliance certification.** It flags likely
issues in the deterministic, rules-based parts of Canadian label law and
explicitly marks the judgment-heavy parts as `NEEDS_REVIEW` rather than
guessing. See [Scope and limitations](#what-it-deliberately-does-not-rule-on) below before
relying on it for anything you intend to ship.

## What it covers

| Area | What's checked |
|---|---|
| Common name | Presence, PDP placement, minimum type size (1.6 mm / 0.8 mm for small PDS) |
| Net quantity | Presence, PDP placement, metric units, 3-figure rounding |
| Ingredient list & allergens | Presence, English/French entry-count match, priority-allergen declaration completeness (peanuts, tree nuts, sesame, milk, eggs, fish/crustaceans/molluscs, soy, wheat/triticale, mustard, sulphites), phenylalanine statement for aspartame |
| Name & place of business | Presence, minimum type size |
| Date marking | "Best before" requirement trigger (durable life ≤ 90 days), prescribed wording, storage instructions |
| Bilingual labelling | English/French pairing across common name, ingredient list, and date wording |
| Nutrition Facts table (NFt) | Presence, format, serving-size format, the 12 core nutrients + calories, and **rounding self-consistency** against the CFIA's official rounding table (e.g. sodium: <5 mg→nearest 1, 5–140 mg→nearest 5, >140 mg→nearest 10) |
| Front-of-package (FOP) nutrition symbol | Calculates whether saturated fat, sugars, or sodium cross the 15% / 10% (small package) / 30% (main dish) Daily Value trigger, and checks whether a symbol is declared accordingly |
| Nutrient content claims | A **curated subset** of common claims (low fat, fat free, sodium free/low, cholesterol free, sugar free, source/high/very high source of fibre) checked against their numeric thresholds |
| Country of origin | Flags when a mandatory-origin category (meat, poultry, fish, dairy, honey, maple, wine, eggs, fresh/processed produce) is imported without a declaration |
| Meat, poultry & fish specifics | "Previously frozen" declaration, grade name presence, flags standard-of-identity and CFIA Fish List checks for manual review |
| Irradiation | Statement + international symbol presence |

## What it deliberately does NOT rule on

These require information or judgment this tool can't supply on its own.
They come back as `NEEDS_REVIEW`, not a pass:

- **Standards of identity** — whether a common name (e.g. "ground beef,"
  "chocolate," "maple syrup") matches its prescribed composition in the
  Canadian Standards of Identity / Canadian Food Compositional Standards.
  There are hundreds of these; encoding them all is out of scope.
- **True descending-order-by-weight verification** — the tool can compare
  English vs. French ingredient lists for consistency, but confirming the
  order matches actual formulation weights requires your recipe data.
- **The full Table of Permitted Nutrient Content Statements and Claims** —
  only ~10 common claims are checked. Anything else (protein claims, fat
  types, comparative claims, "light," "lean," vitamin/mineral claims,
  claims for children under 4) is flagged for manual lookup against
  [Health Canada's table](https://www.canada.ca/en/health-canada/services/technical-documents-labelling-requirements/table-permitted-nutrient-content-statements-claims/table-document.html).
- **Precise type-size/legibility/colour-contrast measurement** from a
  photo — the tool checks numbers you supply, it doesn't measure pixels.
- **Provincial requirements**, most notably Quebec's Charter of the French
  Language (Bill 96), which layers additional French-language obligations
  on top of the federal bilingual rules.
- **Health claims**, supplemented food rules, infant formula/food rules,
  and organic certification — out of scope for this version.

## Installation

Requires Python 3.9+. No third-party dependencies for the CLI (Flask/gunicorn only needed for the web UI).

```bash
python3 cli.py --input samples/sample_compliant.json
```

## Usage

```bash
python3 cli.py --input label_data.json                # text report to stdout
python3 cli.py --input label_data.json --format json  # machine-readable report
python3 cli.py --input label_data.json --output report.txt
```

Exit code is `1` if any `FAIL` results are present (useful for scripting),
`0` otherwise.

### Input format

The checker takes a **structured JSON file**, not an image directly — see
`schema_template.json` for the full field list and `samples/` for two
worked examples (one mostly compliant, one deliberately broken, so you can
see both PASS-heavy and FAIL-heavy output).

### Getting from a label photo to the JSON input

Since the tool itself is a deterministic rules engine, it can't read an
image on its own. The recommended workflow is a two-step one:

1. **Extraction (needs vision/judgment):** Open the label image in a
   Claude conversation (or any vision-capable tool) and ask it to
   transcribe the label into the `schema_template.json` structure —
   common name, ingredient list (both languages if present), NFt values,
   allergen statement, net quantity, dates, claims, etc. This step is
   inherently less reliable than the rules engine itself (OCR/vision
   errors, small print, glare), so treat the extracted JSON as a draft to
   proofread against the photo before running the checker.
2. **Checking (deterministic):** Run `cli.py` against the resulting JSON.
   This step is fully reproducible — same input always gives the same
   output — which is why extraction and checking are kept as separate
   steps rather than one opaque "upload a photo, get a verdict" black box.

Keeping these separate matters: it means every FAIL/PASS in the report
traces back to a specific number or string you can verify against the
photo yourself, rather than an unauditable end-to-end guess.

## Web UI

`app.py` is a small Flask front-end over the same rules engine — paste or
load a sample JSON, click "Check label," see the same PASS/FAIL/NEEDS_REVIEW
report as the CLI, in the browser.

Run locally:

```bash
pip install -r requirements.txt
python app.py
# open http://localhost:5000
```

### Deploying to Render.com

This repo includes a `render.yaml` blueprint, so deployment is mostly
point-and-click on Render's side:

1. Go to [dashboard.render.com](https://dashboard.render.com) and sign in
   (or create a free account) with GitHub.
2. Click **New +** → **Blueprint**.
3. Select this repository (`LabelComplianceCanada`). Render will detect
   `render.yaml` and pre-fill the service config (Python web service,
   `pip install -r requirements.txt`, `gunicorn app:app`).
4. Click **Apply** / **Create**. First deploy takes a couple of minutes.
5. Render gives you a public URL like `https://label-compliance-canada.onrender.com`.

Note: the free Render plan spins the service down after inactivity, so the
first request after a while will be slow to wake up — that's expected, not
a bug.

## Extending the rule set

Each rule lives in `label_rules.py` as a small function that takes the
parsed JSON and a `Report` object. To add a rule:

1. Write a `check_xxx(data, report)` function following the existing
   pattern — pull the relevant sub-dict, `report.add(rule_id, category,
   Status.PASS/FAIL/NEEDS_REVIEW, message, citation)`.
2. Add it to the `CHECKS` list at the bottom of the file.
3. Always cite the specific CFIA/Health Canada page or FDR/SFCR section
   the rule is based on — this is what makes a FAIL actionable rather than
   just a red flag.

The most valuable next additions, in rough priority order: the full
nutrient-content-claims table, a lookup against the CFIA Fish List for
common names, and Table of Reference Amounts data to auto-populate
`reference_amount_category` from a food category instead of requiring it
as manual input.

## Keeping this current

CFIA/Health Canada labelling rules change on a rolling basis — the
front-of-package nutrition symbol requirement referenced here only became
mandatory January 1, 2026. Before relying on this tool for a real product
launch, re-check the cited pages for updates, particularly:

- https://inspection.canada.ca/en/food-labels/labelling/industry/requirements-checklist
- https://inspection.canada.ca/en/food-labels/labelling/industry/updates (CFIA's own change log)

## Disclaimer

This is a first-pass triage aid, not a legal or regulatory compliance
certification. It does not replace review by someone qualified in
Canadian food labelling regulation, and adherence to its output does not
preclude CFIA enforcement action for non-compliance. When in doubt, consult
the [CFIA Industry Labelling Tool](https://inspection.canada.ca/en/food-labels/labelling/industry)
directly or a regulatory affairs professional.
