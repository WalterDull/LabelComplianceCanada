"""
Canada Food Label Compliance Checker — rules engine.

Encodes the CORE, DETERMINISTIC labelling requirements published by the
Canadian Food Inspection Agency (CFIA) Industry Labelling Tool
(https://inspection.canada.ca/en/food-labels/labelling/industry) and
Health Canada's Front-of-package nutrition symbol labelling guide.

IMPORTANT — READ BEFORE RELYING ON RESULTS
--------------------------------------------
This engine checks the things that are checkable from structured data:
presence of mandatory elements, bilingual pairing, list formatting,
Nutrition Facts table (NFt) core-nutrient presence and rounding
self-consistency, net quantity units, date marking wording, and the
front-of-package (FOP) nutrition symbol trigger math.

It does NOT and CANNOT reliably determine:
  - Whether a common name is an approved "standard of identity" name
    (hundreds of foods have their own prescribed compositional standard
    in the Canadian Standards of Identity / Food and Drug Regulations).
  - Whether a nutrient-content or health claim is substantiated.
  - Precise type-size / legibility measurements from a photo.
  - Provincial requirements layered on top of federal rules (e.g. Quebec's
    Charter of the French Language / Bill 96).
  - Whether ingredients are genuinely in descending order by weight
    (this requires actual formulation data, not just the printed list).

Any rule whose correct answer depends on that kind of judgment is marked
NEEDS_REVIEW rather than PASS/FAIL. Treat NEEDS_REVIEW items as "a human
familiar with CFIA's Industry Labelling Tool should look at this," not as
a soft pass.

This tool is a first-pass triage aid. It is not legal advice and does not
constitute a compliance certification. Consult the CFIA Industry Labelling
Tool (https://inspection.canada.ca/en/food-labels/labelling/industry) and,
for anything ambiguous, a regulatory affairs professional.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    WARNING = "WARNING"


@dataclass
class RuleResult:
    rule_id: str
    category: str
    status: Status
    message: str
    citation: str = ""

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "status": self.status.value,
            "message": self.message,
            "citation": self.citation,
        }


@dataclass
class Report:
    results: list[RuleResult] = field(default_factory=list)

    def add(self, rule_id: str, category: str, status: Status, message: str, citation: str = "") -> None:
        self.results.append(RuleResult(rule_id, category, status, message, citation))

    def summary(self) -> dict:
        counts = {s.value: 0 for s in Status}
        for r in self.results:
            counts[r.status.value] += 1
        return counts

    def to_dict(self) -> dict:
        return {
            "summary": self.summary(),
            "results": [r.to_dict() for r in self.results],
        }


CFIA_CHECKLIST = "CFIA Food labelling requirements checklist — https://inspection.canada.ca/en/food-labels/labelling/industry/requirements-checklist"

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

# Health Canada priority food allergens + gluten sources + sulphites.
# Source: CFIA "Allergen-free, gluten-free and cross-contamination statements"
# and "List of ingredients and allergens" pages.
PRIORITY_ALLERGENS = {
    "peanut", "peanuts",
    "tree nut", "tree nuts", "almond", "brazil nut", "cashew", "hazelnut",
    "macadamia", "pecan", "pine nut", "pistachio", "walnut",
    "sesame", "sesame seeds",
    "milk",
    "egg", "eggs",
    "fish",
    "crustacean", "crustaceans", "shrimp", "crab", "lobster",
    "mollusc", "molluscs", "mollusk", "mollusks", "clam", "mussel", "oyster", "scallop",
    "soy", "soybean", "soybeans",
    "wheat", "triticale",
    "mustard",
}
GLUTEN_SOURCES = {"wheat", "barley", "oats", "rye", "triticale"}
SULPHITE_TERMS = {
    "sulphites", "sulfites", "sulphur dioxide", "sulfur dioxide",
    "sodium sulphite", "sodium bisulphite", "sodium metabisulphite",
    "potassium bisulphite", "potassium metabisulphite", "calcium sulphite",
}

# NFt core-nutrient rounding rules.
# Source: CFIA "Information within the Nutrition Facts table" — Core nutrition
# information table (rounding column of table following B.01.401, FDR).
# Each entry: list of (upper_bound_exclusive_or_None, increment) tiers, in mg/g
# as applicable, applied to the *declared* value to check self-consistency
# (i.e. is the printed number a legal multiple for its own magnitude tier?).
NFT_ROUNDING = {
    "calories": [(5, 1), (50, 5), (None, 10)],          # <5:1, 5-50:5, >50:10 (approx; 0 handled separately)
    "fat_g": [(0.5, 0.1), (5, 0.5), (None, 1)],
    "saturated_fat_g": [(0.5, 0.1), (5, 0.5), (None, 1)],
    "trans_fat_g": [(0.5, 0.1), (5, 0.5), (None, 1)],
    "cholesterol_mg": [(None, 5)],                        # nearest 5 mg (0 handled separately)
    "sodium_mg": [(5, 1), (140, 5), (None, 10)],
    "carbohydrate_g": [(None, 1)],                        # nearest 1 g (0 handled separately, <0.5 -> 0)
    "fibre_g": [(None, 1)],
    "sugars_g": [(None, 1)],
    "protein_g": [(0.5, 0.1), (None, 1)],
    "potassium_mg": [(50, 10), (250, 25), (None, 50)],
    "calcium_mg": [(50, 10), (250, 25), (None, 50)],
    "iron_mg": [(0.5, 0.1), (2.5, 0.25), (None, 0.5)],
}

NFT_CITATION = (
    "CFIA — Information within the Nutrition Facts table, Core nutrition information table — "
    "https://inspection.canada.ca/en/food-labels/labelling/industry/nutrition-labelling/nutrition-facts-table"
)

CORE_NFT_NUTRIENTS = [
    "fat_g", "saturated_fat_g", "trans_fat_g", "cholesterol_mg", "sodium_mg",
    "carbohydrate_g", "fibre_g", "sugars_g", "protein_g", "potassium_mg",
    "calcium_mg", "iron_mg",
]

FOP_CITATION = (
    "Health Canada — Front-of-package nutrition symbol labelling guide for industry — "
    "https://www.canada.ca/en/health-canada/services/food-nutrition/legislation-guidelines/guidance-documents/"
    "front-package-nutrition-symbol-labelling-industry.html"
)


def _is_legal_rounding(value: float, tiers: list[tuple[Optional[float], float]]) -> bool:
    """Check whether `value` is a legal multiple of the increment for its tier."""
    if value == 0:
        return True
    lower = 0.0
    for upper, increment in tiers:
        if upper is None or value < upper:
            # value falls in [lower, upper) tier
            ratio = value / increment
            return math.isclose(ratio, round(ratio), abs_tol=1e-6)
        lower = upper
    return True  # pragma: no cover


# ---------------------------------------------------------------------------
# Rule: Common name
# ---------------------------------------------------------------------------

def check_common_name(data: dict, report: Report) -> None:
    cn = data.get("common_name", {}) or {}
    cat = "Common name"
    cite = "CFIA — Common name — https://inspection.canada.ca/en/food-labels/labelling/industry/common-name"

    if not cn.get("present"):
        if cn.get("exempt"):
            report.add("common_name.presence", cat, Status.NOT_APPLICABLE,
                       "Common name absent but marked exempt.", cite)
        else:
            report.add("common_name.presence", cat, Status.FAIL,
                       "No common name declared, and no exemption indicated.", cite)
        return
    report.add("common_name.presence", cat, Status.PASS, "Common name is present.", cite)

    if cn.get("on_pdp") is False:
        report.add("common_name.pdp", cat, Status.FAIL,
                   "Common name must appear on the principal display panel (PDP).", cite)
    elif cn.get("on_pdp") is None:
        report.add("common_name.pdp", cat, Status.NEEDS_REVIEW,
                   "PDP placement not specified — confirm the common name is on the principal display panel.", cite)
    else:
        report.add("common_name.pdp", cat, Status.PASS, "Common name is on the PDP.", cite)

    # Type size: 1.6 mm minimum, or 0.8 mm if PDS <= 10 cm^2.
    th = cn.get("type_height_mm")
    pds = cn.get("pds_area_cm2")
    if th is not None:
        min_required = 0.8 if (pds is not None and pds <= 10) else 1.6
        if th + 1e-9 < min_required:
            report.add("common_name.type_size", cat, Status.FAIL,
                       f"Common name type height {th} mm is below the required minimum of {min_required} mm.", cite)
        else:
            report.add("common_name.type_size", cat, Status.PASS,
                       f"Common name type height {th} mm meets the {min_required} mm minimum.", cite)
    else:
        report.add("common_name.type_size", cat, Status.NEEDS_REVIEW,
                   "Type height not provided — cannot verify the 1.6 mm (or 0.8 mm for small PDS) minimum.", cite)

    # Standard of identity — cannot be verified without the full CSI/CFCS reference data.
    report.add("common_name.standard_of_identity", cat, Status.NEEDS_REVIEW,
               "Whether this is an approved common/standard-of-identity name cannot be verified "
               "automatically. Check the name against the Canadian Standards of Identity / Canadian "
               "Food Compositional Standards documents incorporated by reference into the SFCR/FDR, "
               "or the CFIA Fish List if applicable.",
               "https://inspection.canada.ca/en/about-cfia/acts-and-regulations/list-acts-and-regulations/"
               "documents-incorporated-reference")


# ---------------------------------------------------------------------------
# Rule: Net quantity
# ---------------------------------------------------------------------------

def check_net_quantity(data: dict, report: Report) -> None:
    nq = data.get("net_quantity", {}) or {}
    cat = "Net quantity"
    cite = "CFIA — Net quantity — https://inspection.canada.ca/en/food-labels/labelling/industry/net-quantity"

    if not nq.get("present"):
        if nq.get("exempt"):
            report.add("net_quantity.presence", cat, Status.NOT_APPLICABLE,
                       "Net quantity absent but marked exempt.", cite)
        else:
            report.add("net_quantity.presence", cat, Status.FAIL,
                       "No net quantity declaration found, and no exemption indicated.", cite)
        return
    report.add("net_quantity.presence", cat, Status.PASS, "Net quantity is declared.", cite)

    if nq.get("on_pdp") is False:
        report.add("net_quantity.pdp", cat, Status.FAIL, "Net quantity must be on the PDP.", cite)

    if nq.get("metric") is False:
        report.add("net_quantity.metric", cat, Status.FAIL,
                   "Net quantity must be declared in metric units (Canadian units may be added "
                   "voluntarily, and are permitted alone only for consumer-prepackaged food "
                   "packaged from bulk at retail).", cite)
    elif nq.get("metric") is True:
        report.add("net_quantity.metric", cat, Status.PASS, "Metric units used.", cite)
    else:
        report.add("net_quantity.metric", cat, Status.NEEDS_REVIEW, "Units not specified.", cite)

    value = nq.get("value")
    if value is not None:
        sig_figs = len(str(value).replace(".", "").lstrip("0")) if value >= 100 else None
        if value >= 100 and sig_figs is not None and sig_figs > 3:
            report.add("net_quantity.rounding", cat, Status.NEEDS_REVIEW,
                       f"Value {value} may exceed the 3-significant-figure display rule for "
                       "quantities of 100 or more (values below 100 are exempt from this rule) — verify.", cite)
        else:
            report.add("net_quantity.rounding", cat, Status.PASS,
                       "Declared value is consistent with the 3-figure rounding rule.", cite)


# ---------------------------------------------------------------------------
# Rule: Ingredients & allergens
# ---------------------------------------------------------------------------

def _normalize(term: str) -> str:
    return term.strip().lower()


def check_ingredients_allergens(data: dict, report: Report) -> None:
    ing = data.get("ingredients", {}) or {}
    alg = data.get("allergens", {}) or {}
    cat = "Ingredients & allergens"
    cite = ("CFIA — List of ingredients and allergens — "
            "https://inspection.canada.ca/en/food-labels/labelling/industry/list-ingredients-and-allergens")

    if not ing.get("present"):
        if ing.get("exempt_or_single_ingredient"):
            report.add("ingredients.presence", cat, Status.NOT_APPLICABLE,
                       "Ingredient list absent — marked exempt or single-ingredient food.", cite)
        else:
            report.add("ingredients.presence", cat, Status.FAIL,
                       "No ingredient list found, and no exemption/single-ingredient flag set.", cite)
    else:
        report.add("ingredients.presence", cat, Status.PASS, "Ingredient list is present.", cite)
        report.add("ingredients.descending_order", cat, Status.NEEDS_REVIEW,
                   "Descending order by weight cannot be verified from the printed list alone "
                   "(requires formulation data) — confirm against your recipe/formulation records.", cite)

        list_en = ing.get("list_en") or []
        list_fr = ing.get("list_fr") or []
        if list_en and list_fr:
            if len(list_en) != len(list_fr):
                report.add("ingredients.bilingual_match", cat, Status.FAIL,
                           f"English list has {len(list_en)} entries, French list has {len(list_fr)} — "
                           "the English and French ingredient lists must match in content.", cite)
            else:
                report.add("ingredients.bilingual_match", cat, Status.PASS,
                           "English and French ingredient lists have matching entry counts.", cite)
        elif list_en and not list_fr:
            report.add("ingredients.bilingual_match", cat, Status.NEEDS_REVIEW,
                       "Only an English ingredient list was supplied — confirm a French list is present "
                       "unless a bilingual exemption applies.", cite)

    # Allergen cross-check: does the declared "contains" list match what's actually
    # in the ingredient text (best-effort keyword match)?
    declared = {_normalize(a) for a in (alg.get("declared_allergens") or [])}
    actual = {_normalize(a) for a in (alg.get("actual_allergens_present") or [])}
    ingredient_text = " ".join(list_en if ing.get("present") else []).lower() if ing.get("present") else ""

    if actual:
        missing = actual - declared
        if missing:
            report.add("allergens.declaration_completeness", cat, Status.FAIL,
                       f"Allergen(s) present in the product but not declared in the ingredient list or "
                       f"'Contains' statement: {', '.join(sorted(missing))}.",
                       "CFIA — Allergen-free, gluten-free and cross-contamination statements — "
                       "https://inspection.canada.ca/en/food-labels/labelling/industry/allergens-and-gluten")
        else:
            report.add("allergens.declaration_completeness", cat, Status.PASS,
                       "All specified priority allergens present in the product are declared.", cite)
    else:
        report.add("allergens.declaration_completeness", cat, Status.NEEDS_REVIEW,
                   "No 'actual_allergens_present' data supplied — cannot cross-check completeness. "
                   "Provide the true allergen content of the formulation to enable this check.", cite)

    unknown_declared = declared - PRIORITY_ALLERGENS
    if unknown_declared:
        report.add("allergens.unrecognized_terms", cat, Status.NEEDS_REVIEW,
                   f"Declared allergen term(s) not matched to Health Canada's priority allergen list: "
                   f"{', '.join(sorted(unknown_declared))}. Confirm the prescribed source name is used.",
                   cite)

    # Statement ordering: phenylalanine -> Contains -> May contain
    if alg.get("aspartame_present") and not alg.get("phenylalanine_statement_present"):
        report.add("allergens.phenylalanine_statement", cat, Status.FAIL,
                   "Product contains aspartame but no phenylalanine statement was recorded. A "
                   "phenylalanine statement is mandatory and must appear at the end of the ingredient "
                   "list, before any 'Contains' or 'May contain' statement.", cite)
    elif alg.get("aspartame_present"):
        report.add("allergens.phenylalanine_statement", cat, Status.PASS,
                   "Phenylalanine statement present for aspartame-containing product.", cite)

    if alg.get("cross_contamination_statement") and declared & {
        _normalize(x) for x in (alg.get("cross_contamination_statement") or "").split(",")
    }:
        report.add("allergens.free_claim_conflict", cat, Status.NEEDS_REVIEW,
                   "A 'may contain' / cross-contamination statement and an allergen-free claim for the "
                   "same allergen cannot both appear — verify no conflicting claim exists.", cite)


# ---------------------------------------------------------------------------
# Rule: Name and principal place of business
# ---------------------------------------------------------------------------

def check_name_and_place_of_business(data: dict, report: Report) -> None:
    nb = data.get("name_and_place_of_business", {}) or {}
    cat = "Name and place of business"
    cite = ("CFIA — Name and principal place of business — "
            "https://inspection.canada.ca/en/food-labels/labelling/industry/name-and-principal-place-business")

    if not nb.get("present"):
        if nb.get("exempt"):
            report.add("dealer.presence", cat, Status.NOT_APPLICABLE,
                       "Name/place of business absent but marked exempt.", cite)
        else:
            report.add("dealer.presence", cat, Status.FAIL,
                       "No name and principal place of business found, and no exemption indicated.", cite)
        return
    report.add("dealer.presence", cat, Status.PASS, "Name and principal place of business present.", cite)

    th = nb.get("type_height_mm")
    if th is not None and th + 1e-9 < 1.6:
        report.add("dealer.type_size", cat, Status.FAIL,
                   f"Type height {th} mm is below the 1.6 mm minimum "
                   "(0.8 mm permitted only if PDS <= 10 cm2).", cite)
    elif th is None:
        report.add("dealer.type_size", cat, Status.NEEDS_REVIEW, "Type height not provided.", cite)
    else:
        report.add("dealer.type_size", cat, Status.PASS, "Type height meets minimum requirement.", cite)


# ---------------------------------------------------------------------------
# Rule: Date marking
# ---------------------------------------------------------------------------

def check_date_marking(data: dict, report: Report) -> None:
    dm = data.get("date_marking", {}) or {}
    cat = "Date marking"
    cite = ("CFIA — Date markings and storage instructions — "
            "https://inspection.canada.ca/en/food-labels/labelling/industry/date-markings-and-storage-instructions")

    durable_life = dm.get("durable_life_days")
    if durable_life is not None:
        requires_bb = durable_life <= 90
        if requires_bb and not dm.get("best_before_exempt"):
            if dm.get("best_before_present"):
                report.add("date.best_before_presence", cat, Status.PASS,
                           "'Best before' date present, consistent with durable life <= 90 days.", cite)
                if dm.get("best_before_text_en") and "best before" not in dm["best_before_text_en"].lower():
                    report.add("date.best_before_wording_en", cat, Status.FAIL,
                               "English 'best before' wording does not match the prescribed phrase.", cite)
                if dm.get("best_before_text_fr") and "meilleur avant" not in dm["best_before_text_fr"].lower():
                    report.add("date.best_before_wording_fr", cat, Status.FAIL,
                               "French 'meilleur avant' wording does not match the prescribed phrase.", cite)
            else:
                report.add("date.best_before_presence", cat, Status.FAIL,
                           "Durable life is 90 days or less, so a 'best before' date is required but "
                           "was not found (unless the food qualifies for a specific exemption, e.g. "
                           "sold only at retail where packaged).", cite)
        elif requires_bb:
            report.add("date.best_before_presence", cat, Status.NOT_APPLICABLE,
                       "'Best before' date not required — exemption flagged.", cite)
        else:
            report.add("date.best_before_presence", cat, Status.NOT_APPLICABLE,
                       "Durable life exceeds 90 days — no 'best before' date required.", cite)
    else:
        report.add("date.best_before_presence", cat, Status.NEEDS_REVIEW,
                   "Durable life not supplied — cannot determine whether a 'best before' date is required.", cite)

    if dm.get("storage_instructions_required") and not dm.get("storage_instructions_present"):
        report.add("date.storage_instructions", cat, Status.FAIL,
                   "Storage conditions differ from normal room temperature but no storage "
                   "instructions were found.", cite)
    elif dm.get("storage_instructions_required"):
        report.add("date.storage_instructions", cat, Status.PASS, "Storage instructions present.", cite)


# ---------------------------------------------------------------------------
# Rule: Bilingual labelling
# ---------------------------------------------------------------------------

def check_bilingual(data: dict, report: Report) -> None:
    cat = "Bilingual labelling"
    cite = ("CFIA — Bilingual food labelling — "
            "https://inspection.canada.ca/en/food-labels/labelling/industry/bilingual-food-labelling")
    be = data.get("bilingual_exemption", {}) or {}

    if be.get("applies"):
        report.add("bilingual.exemption", cat, Status.NOT_APPLICABLE,
                   f"Bilingual exemption claimed: {be.get('reason', 'no reason given')}. "
                   "Confirm this matches one of the recognized exemption categories (local food "
                   "below the 10% mother-tongue threshold, test market food, specialty imports, "
                   "or foods requiring language knowledge for use).", cite)
        return

    missing_fr = []
    cn = data.get("common_name", {}) or {}
    if cn.get("present") and cn.get("text_en") and not cn.get("text_fr"):
        missing_fr.append("common name")
    nb = data.get("name_and_place_of_business", {}) or {}
    # name/place of business is exempt from bilingual requirement by design (may be EN or FR only)
    ing = data.get("ingredients", {}) or {}
    if ing.get("present") and ing.get("list_en") and not ing.get("list_fr"):
        missing_fr.append("ingredient list")
    dm = data.get("date_marking", {}) or {}
    if dm.get("best_before_present") and dm.get("best_before_text_en") and not dm.get("best_before_text_fr"):
        missing_fr.append("'best before' wording")

    if missing_fr:
        report.add("bilingual.completeness", cat, Status.FAIL,
                   f"French version missing for: {', '.join(missing_fr)}. All mandatory information "
                   "must appear in both English and French (the name/address of the responsible "
                   "person is the one exception — it may be in either language alone), unless a "
                   "bilingual exemption applies.", cite)
    else:
        report.add("bilingual.completeness", cat, Status.PASS,
                   "No missing French text detected among the fields supplied.", cite)


# ---------------------------------------------------------------------------
# Rule: Nutrition Facts table
# ---------------------------------------------------------------------------

def check_nutrition_facts_table(data: dict, report: Report) -> None:
    nft = data.get("nutrition_facts_table", {}) or {}
    cat = "Nutrition Facts table"

    if nft.get("exempt"):
        report.add("nft.presence", cat, Status.NOT_APPLICABLE,
                   f"NFt exempt or prohibited: {nft.get('exemption_reason', 'reason not given')}.",
                   NFT_CITATION)
        return

    if not nft.get("present"):
        report.add("nft.presence", cat, Status.FAIL,
                   "No Nutrition Facts table found and no exemption/prohibition indicated.", NFT_CITATION)
        return
    report.add("nft.presence", cat, Status.PASS, "Nutrition Facts table is present.", NFT_CITATION)

    fmt = nft.get("format", "standard")
    if fmt not in ("standard", "simplified", "simplified_single_serving", "dual", "aggregate", "infant"):
        report.add("nft.format", cat, Status.NEEDS_REVIEW,
                   f"Unrecognized format '{fmt}' — confirm the correct NFt format family/version was chosen.",
                   "CFIA — Nutrition Facts table formats — "
                   "https://inspection.canada.ca/en/food-labels/labelling/industry/nutrition-labelling/"
                   "nutrition-facts-table-formats")

    serving_hm = nft.get("serving_size_household")
    serving_mm = nft.get("serving_size_metric")
    if not (serving_hm and serving_mm):
        report.add("nft.serving_size", cat, Status.FAIL,
                   "Serving size must show the household measure followed by the metric measure "
                   "in parentheses, e.g. '1 cup (250 mL)'.", NFT_CITATION)
    else:
        report.add("nft.serving_size", cat, Status.PASS,
                   f"Serving size declared as '{serving_hm} ({serving_mm})'.", NFT_CITATION)

    nutrients = nft.get("nutrients", {}) or {}

    if fmt == "standard":
        missing = [n for n in CORE_NFT_NUTRIENTS if nutrients.get(n) is None]
        if missing:
            report.add("nft.core_nutrients", cat, Status.FAIL,
                       f"Standard-format NFt is missing declared value(s) for: {', '.join(missing)}. "
                       "The 12 core nutrients (fat, saturated fat, trans fat, cholesterol, sodium, "
                       "carbohydrate, fibre, sugars, protein, potassium, calcium, iron) plus calories "
                       "must all be declared unless a simplified/single-serving format applies.", NFT_CITATION)
        else:
            report.add("nft.core_nutrients", cat, Status.PASS,
                       "All 12 core nutrients plus calories are present.", NFT_CITATION)
    else:
        report.add("nft.core_nutrients", cat, Status.NEEDS_REVIEW,
                   f"Format '{fmt}' has its own reduced nutrient declaration list — verify against the "
                   "specific requirements for this format rather than the standard 12-nutrient list.", NFT_CITATION)

    # Rounding self-consistency
    calories = nutrients.get("calories")
    if calories is not None:
        if calories == 0 or _is_legal_rounding(calories, NFT_ROUNDING["calories"]):
            report.add("nft.rounding.calories", cat, Status.PASS, "Calories value matches a legal rounding increment.", NFT_CITATION)
        else:
            report.add("nft.rounding.calories", cat, Status.FAIL,
                       f"Calories value {calories} does not match the required rounding increment for its "
                       "range (nearest 1 if <5, nearest 5 if 5-50, nearest 10 if >50).", NFT_CITATION)

    for key in CORE_NFT_NUTRIENTS:
        val = nutrients.get(key)
        if val is None:
            continue
        tiers = NFT_ROUNDING.get(key)
        if tiers is None:
            continue
        if _is_legal_rounding(val, tiers):
            report.add(f"nft.rounding.{key}", cat, Status.PASS,
                       f"{key} value {val} matches a legal rounding increment.", NFT_CITATION)
        else:
            report.add(f"nft.rounding.{key}", cat, Status.FAIL,
                       f"{key} value {val} does not match the required rounding increment for its range — "
                       "double-check against the CFIA rounding table.", NFT_CITATION)

    if fmt == "standard" and nft.get("percent_dv_shown") is False:
        report.add("nft.percent_dv", cat, Status.FAIL,
                   "% Daily Value column appears to be missing for nutrients that require it "
                   "(fat, saturated+trans, cholesterol [optional], sodium, fibre, sugars, potassium, "
                   "calcium, iron).", NFT_CITATION)


# ---------------------------------------------------------------------------
# Rule: Front-of-package (FOP) nutrition symbol
# ---------------------------------------------------------------------------

# Daily Values used for the FOP symbol calculation (Health Canada Table of
# Daily Values, adult/general population figures).
FOP_DV = {
    "saturated_fat_g": 20.0,
    "sugars_g": 100.0,
    "sodium_mg": 2300.0,
}


def check_fop_symbol(data: dict, report: Report) -> None:
    fop = data.get("fop_symbol", {}) or {}
    nft = data.get("nutrition_facts_table", {}) or {}
    product = data.get("product", {}) or {}
    cat = "Front-of-package nutrition symbol"

    if fop.get("product_prohibited_or_exempt"):
        report.add("fop.applicability", cat, Status.NOT_APPLICABLE,
                   "Product flagged as prohibited or exempt from FOP symbol requirements "
                   "(e.g. raw single-ingredient meat/poultry/fish that doesn't otherwise require an "
                   "NFt, foods for further processing, etc.).", FOP_CITATION)
        return

    if nft.get("exempt"):
        report.add("fop.applicability", cat, Status.NOT_APPLICABLE,
                   "Product is exempt from carrying an NFt, so it is also exempt from the FOP "
                   "nutrition symbol (unless the NFt exemption has been lost).", FOP_CITATION)
        return

    nutrients = (nft.get("nutrients") or {})
    ref_amount_g = product.get("reference_amount_g")
    category = product.get("reference_amount_category", "standard")

    if ref_amount_g is None or not nutrients:
        report.add("fop.threshold_calc", cat, Status.NEEDS_REVIEW,
                   "Insufficient data (reference amount and/or nutrient values) to calculate whether "
                   "the FOP symbol threshold is triggered. Supply saturated fat, sugars, and sodium "
                   "amounts per reference amount, plus the reference amount category.", FOP_CITATION)
        return

    threshold_pct = {"standard": 15, "small_package_le_30g": 10, "main_dish": 30}.get(category, 15)

    triggered = {}
    for nutrient_key, dv in FOP_DV.items():
        amount = nutrients.get(nutrient_key)
        if amount is None:
            continue
        pct_dv = (amount / dv) * 100
        if pct_dv >= threshold_pct:
            triggered[nutrient_key] = round(pct_dv, 1)

    symbol_present = fop.get("present")
    if triggered:
        nutrient_list = ", ".join(f"{k} ({v}% DV)" for k, v in triggered.items())
        if symbol_present:
            report.add("fop.threshold_calc", cat, Status.PASS,
                       f"Threshold triggered by: {nutrient_list} (>= {threshold_pct}% DV for this "
                       "category) and a symbol is declared as present.", FOP_CITATION)
        else:
            report.add("fop.threshold_calc", cat, Status.FAIL,
                       f"Based on supplied nutrient values, the FOP symbol should be required — "
                       f"threshold triggered by: {nutrient_list} (>= {threshold_pct}% DV for this "
                       "category) — but no symbol is declared as present.", FOP_CITATION)
    else:
        if symbol_present:
            report.add("fop.threshold_calc", cat, Status.WARNING,
                       "A FOP symbol is declared present, but supplied nutrient values do not appear "
                       f"to cross the {threshold_pct}% DV threshold for this category — confirm the "
                       "symbol is actually required (or double check the input nutrient values).", FOP_CITATION)
        else:
            report.add("fop.threshold_calc", cat, Status.PASS,
                       f"Supplied nutrient values are below the {threshold_pct}% DV threshold for this "
                       "category — no FOP symbol required.", FOP_CITATION)

    report.add("fop.dv_note", cat, Status.NEEDS_REVIEW,
               "This calculation uses general/adult Daily Values (saturated fat 20 g, sugars 100 g, "
               "sodium 2300 mg). Products intended solely for children 1-4 years of age use different "
               "reference amounts/thresholds and category-specific Table of Reference Amounts entries "
               "not modeled here — verify manually for that population.", FOP_CITATION)


# ---------------------------------------------------------------------------
# Rule: Nutrient content claims (curated subset — NOT exhaustive)
# ---------------------------------------------------------------------------

# Curated subset of Health Canada's Table of Permitted Nutrient Content
# Statements and Claims. This is NOT the full table (which covers dozens of
# claims with per-food-group variations) — only common, unambiguous claims
# are checked here. Everything else is flagged NEEDS_REVIEW.
CLAIMS_TABLE_CITATION = (
    "Health Canada — Table of Permitted Nutrient Content Statements and Claims — "
    "https://www.canada.ca/en/health-canada/services/technical-documents-labelling-requirements/"
    "table-permitted-nutrient-content-statements-claims/table-document.html"
)

KNOWN_CLAIM_CHECKS = {
    # claim keyword -> (nutrient_key, comparison, threshold, unit)
    "low fat": ("fat_g", "<=", 3, "g"),
    "fat free": ("fat_g", "<", 0.5, "g"),
    "sodium free": ("sodium_mg", "<", 5, "mg"),
    "salt free": ("sodium_mg", "<", 5, "mg"),
    "low sodium": ("sodium_mg", "<=", 140, "mg"),
    "low salt": ("sodium_mg", "<=", 140, "mg"),
    "cholesterol free": ("cholesterol_mg", "<", 2, "mg"),
    "sugar free": ("sugars_g", "<", 0.5, "g"),
    "source of fibre": ("fibre_g", ">=", 2, "g"),
    "high source of fibre": ("fibre_g", ">=", 4, "g"),
    "very high source of fibre": ("fibre_g", ">=", 6, "g"),
}


def _compare(value: float, op: str, threshold: float) -> bool:
    return {"<=": value <= threshold, "<": value < threshold, ">=": value >= threshold, ">": value > threshold}[op]


def check_nutrient_claims(data: dict, report: Report) -> None:
    claims = data.get("claims", []) or []
    nutrients = ((data.get("nutrition_facts_table") or {}).get("nutrients") or {})
    cat = "Nutrient content claims"

    if not claims:
        return

    for claim in claims:
        text = (claim.get("claim_text") or "").strip().lower()
        matched_key = None
        for phrase in KNOWN_CLAIM_CHECKS:
            if phrase in text:
                matched_key = phrase
                break

        if matched_key is None:
            report.add(f"claims.{text[:30]}", cat, Status.NEEDS_REVIEW,
                       f"Claim '{claim.get('claim_text')}' is not in the curated rule set covered by this "
                       "tool. Verify it against Health Canada's full Table of Permitted Nutrient Content "
                       "Statements and Claims (dozens of claims with per-food-category conditions apply).",
                       CLAIMS_TABLE_CITATION)
            continue

        nutrient_key, op, threshold, unit = KNOWN_CLAIM_CHECKS[matched_key]
        amount = nutrients.get(nutrient_key)
        if amount is None:
            report.add(f"claims.{matched_key}", cat, Status.NEEDS_REVIEW,
                       f"Claim '{claim.get('claim_text')}' requires {nutrient_key} to evaluate, but no "
                       "value was supplied in the Nutrition Facts data.", CLAIMS_TABLE_CITATION)
            continue

        if _compare(amount, op, threshold):
            report.add(f"claims.{matched_key}", cat, Status.PASS,
                       f"'{claim.get('claim_text')}' is consistent with {nutrient_key}={amount}{unit} "
                       f"(requires {op} {threshold}{unit} per reference amount and per serving — "
                       "confirm both apply, not just the declared serving).", CLAIMS_TABLE_CITATION)
        else:
            report.add(f"claims.{matched_key}", cat, Status.FAIL,
                       f"'{claim.get('claim_text')}' is NOT consistent with {nutrient_key}={amount}{unit} "
                       f"(requires {op} {threshold}{unit}).", CLAIMS_TABLE_CITATION)


# ---------------------------------------------------------------------------
# Rule: Country of origin (for the categories where it's mandatory)
# ---------------------------------------------------------------------------

ORIGIN_MANDATORY_CATEGORIES = {
    "wine", "brandy", "dairy", "honey", "fish", "fresh_produce",
    "shell_egg", "processed_egg", "meat_poultry", "maple", "processed_fruit_vegetable",
}


def check_country_of_origin(data: dict, report: Report) -> None:
    product = data.get("product", {}) or {}
    origin = data.get("country_of_origin", {}) or {}
    cat = "Country of origin"
    cite = "CFIA — Country of origin — https://inspection.canada.ca/en/food-labels/labelling/industry/country-origin"

    food_category = product.get("food_category")
    is_imported = product.get("is_imported")

    if food_category in ORIGIN_MANDATORY_CATEGORIES and is_imported:
        if origin.get("declared"):
            report.add("origin.declared", cat, Status.PASS,
                       f"Country of origin declared: '{origin['declared']}'.", cite)
        else:
            report.add("origin.declared", cat, Status.FAIL,
                       f"Category '{food_category}' is imported and requires a country-of-origin "
                       "declaration (e.g. 'Product of [country]'), but none was found.", cite)
    elif food_category in ORIGIN_MANDATORY_CATEGORIES:
        report.add("origin.declared", cat, Status.NEEDS_REVIEW,
                   f"Category '{food_category}' has commodity-specific origin rules — confirm whether "
                   "origin declaration is required given the product's import/trade status.", cite)


# ---------------------------------------------------------------------------
# Rule: Meat, poultry & fish specific
# ---------------------------------------------------------------------------

def check_meat_poultry_fish(data: dict, report: Report) -> None:
    product = data.get("product", {}) or {}
    cat = "Meat, poultry & fish specific"
    food_category = product.get("food_category")

    if food_category not in ("meat_poultry", "fish"):
        return

    mp = data.get("meat_poultry_specific", {}) or {}
    fs = data.get("fish_specific", {}) or {}

    if food_category == "meat_poultry":
        cite = ("CFIA — Meat and poultry products — "
                "https://inspection.canada.ca/en/food-labels/labelling/industry/meat-and-poultry-products")
        report.add("meat.standard_of_identity", cat, Status.NEEDS_REVIEW,
                   "Meat/poultry common names (e.g. 'ground beef' vs 'hamburger', sausage "
                   "composition rules) are governed by detailed standards of identity and "
                   "compositional standards this tool does not encode — verify the common name "
                   "against the applicable standard.", cite)
        if mp.get("grade_name_present") is False and mp.get("grade_required"):
            report.add("meat.grade_name", cat, Status.FAIL,
                       "Grade name required for this product but not found.", cite)
        report.add("meat.sfcr_licence", cat, Status.NEEDS_REVIEW,
                   "Confirm SFCR licence-holder status and any related labelling obligations "
                   "(e.g. legend use) are met for meat/poultry products crossing provincial or "
                   "international borders.", cite)

    if food_category == "fish":
        cite = ("CFIA — Fish and fish products — "
                "https://inspection.canada.ca/en/food-labels/labelling/industry/fish")
        if fs.get("was_previously_frozen") and not fs.get("previously_frozen_declared"):
            report.add("fish.previously_frozen", cat, Status.FAIL,
                       "Product was previously frozen and thawed but the required 'Previously "
                       "frozen' declaration was not found on the principal display panel.", cite)
        elif fs.get("was_previously_frozen"):
            report.add("fish.previously_frozen", cat, Status.PASS,
                       "'Previously frozen' declaration present as required.", cite)

        if fs.get("grade_required") and not fs.get("grade_name_present"):
            report.add("fish.grade_name", cat, Status.FAIL,
                       "Grade name required for this fish product but not found (see Canadian Grade "
                       "Compendium Volume 8/9 for which fish products require grading).", cite)

        report.add("fish.common_name", cat, Status.NEEDS_REVIEW,
                   "Confirm the species-specific common name is on the CFIA Fish List (generic names "
                   "like 'fish fillets' are not permitted for single-species products) — "
                   "https://inspection.canada.ca/en/food-labels/labelling/industry/fish/list", cite)


# ---------------------------------------------------------------------------
# Rule: Irradiation
# ---------------------------------------------------------------------------

def check_irradiation(data: dict, report: Report) -> None:
    irr = data.get("irradiation", {}) or {}
    cat = "Irradiation"
    cite = "CFIA — Irradiated foods — https://inspection.canada.ca/en/food-labels/labelling/industry/irradiated-foods"

    if not irr.get("irradiated"):
        return
    if not irr.get("statement_present") or not irr.get("symbol_present"):
        report.add("irradiation.declaration", cat, Status.FAIL,
                   "Product is irradiated but is missing the required written statement and/or the "
                   "international radura symbol on the principal display panel.", cite)
    else:
        report.add("irradiation.declaration", cat, Status.PASS,
                   "Irradiation statement and symbol both present.", cite)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

CHECKS = [
    check_common_name,
    check_net_quantity,
    check_ingredients_allergens,
    check_name_and_place_of_business,
    check_date_marking,
    check_bilingual,
    check_nutrition_facts_table,
    check_fop_symbol,
    check_nutrient_claims,
    check_country_of_origin,
    check_meat_poultry_fish,
    check_irradiation,
]


def run_all_checks(data: dict[str, Any]) -> Report:
    report = Report()
    for check_fn in CHECKS:
        check_fn(data, report)
    return report
