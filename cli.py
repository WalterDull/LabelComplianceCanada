#!/usr/bin/env python3
"""
Command-line entry point for the Canada Food Label Compliance Checker.

Usage:
    python cli.py --input label_data.json [--output report.json] [--format text|json]

The input JSON must follow the structure documented in schema_template.json
and README.md. This tool does not read images directly — see README.md for
the recommended workflow for extracting structured data from a label photo
before running this checker.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from label_rules import run_all_checks, Status


STATUS_ORDER = [Status.FAIL, Status.NEEDS_REVIEW, Status.WARNING, Status.PASS, Status.NOT_APPLICABLE]
STATUS_SYMBOL = {
    Status.PASS: "[PASS]",
    Status.FAIL: "[FAIL]",
    Status.NEEDS_REVIEW: "[REVIEW]",
    Status.NOT_APPLICABLE: "[N/A]",
    Status.WARNING: "[WARN]",
}


def format_text(report_dict: dict) -> str:
    lines = []
    summary = report_dict["summary"]
    lines.append("=" * 72)
    lines.append("CANADA FOOD LABEL COMPLIANCE CHECK — SUMMARY")
    lines.append("=" * 72)
    lines.append(
        f"FAIL: {summary['FAIL']}   NEEDS_REVIEW: {summary['NEEDS_REVIEW']}   "
        f"WARNING: {summary['WARNING']}   PASS: {summary['PASS']}   N/A: {summary['NOT_APPLICABLE']}"
    )
    lines.append("")
    lines.append(
        "NOTE: This is an automated first-pass check of core, rules-based CFIA/Health Canada"
        " requirements. It is not a legal compliance certification. NEEDS_REVIEW items require"
        " human judgment and are not a soft pass. See README.md for scope and limitations."
    )
    lines.append("")

    by_status: dict[Status, list[dict]] = {s: [] for s in STATUS_ORDER}
    for r in report_dict["results"]:
        by_status[Status(r["status"])].append(r)

    for status in STATUS_ORDER:
        items = by_status[status]
        if not items:
            continue
        lines.append("-" * 72)
        lines.append(f"{status.value} ({len(items)})")
        lines.append("-" * 72)
        for r in items:
            lines.append(f"{STATUS_SYMBOL[status]} [{r['category']}] {r['rule_id']}")
            lines.append(f"    {r['message']}")
            if r["citation"]:
                lines.append(f"    Source: {r['citation']}")
            lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Canada food label compliance first-pass checker.")
    parser.add_argument("--input", "-i", required=True, help="Path to label data JSON file.")
    parser.add_argument("--output", "-o", help="Path to write the report (defaults to stdout).")
    parser.add_argument("--format", "-f", choices=["text", "json"], default="text",
                        help="Output format (default: text).")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 1

    try:
        data = json.loads(input_path.read_text())
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON in {input_path}: {e}", file=sys.stderr)
        return 1

    report = run_all_checks(data)
    report_dict = report.to_dict()

    if args.format == "json":
        output = json.dumps(report_dict, indent=2)
    else:
        output = format_text(report_dict)

    if args.output:
        Path(args.output).write_text(output)
        print(f"Report written to {args.output}")
    else:
        print(output)

    # Exit non-zero if there are any FAILs, useful for CI-style usage.
    return 1 if report_dict["summary"]["FAIL"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
