# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""DriftGuardian evaluation harness.

Runs a set of ground-truth cases through the drift engine and reports three
metrics:

1. Verdict accuracy   -- did the overall PASS/WARN/BLOCK verdict match the label?
2. Latency            -- wall-clock seconds per case (mean / min / max).
3. Unsupported-claim rate -- the fraction of findings whose claim about the SOP
   is NOT grounded in the SOP text. This is a heuristic proxy for hallucinated
   findings: for an "omission" we flag it when the supposedly-missing requirement
   actually appears in the SOP; for other change types we flag it when the text
   the finding attributes to the SOP ("sop_says") is largely absent from it.
   It is a lexical approximation, not a perfect judge -- treat it as a smoke
   detector for false findings, not ground truth.

Requires a reachable LLM (set LLM_ENDPOINT / LLM_MODEL_ID as for the backend).

Usage:
    python eval/run_eval.py
    python eval/run_eval.py --cases data/demo/eval_cases.json --json report.json
"""

import argparse
import json
import os
import re
import sys
import time

# Make the repo importable no matter where the script is invoked from.
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from drift.engine import check_drift  # noqa: E402
from drift.loader import extract_text  # noqa: E402

GROUNDING_THRESHOLD = 0.5   # fraction of content tokens that must be present
MIN_TOKENS_TO_JUDGE = 2     # skip grounding check below this many content tokens

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "at", "by",
    "is", "are", "be", "as", "that", "this", "with", "must", "shall", "should",
    "will", "any", "all", "each", "within", "than", "from", "into", "it", "its",
    "their", "they", "we", "our", "not", "no", "every", "least", "more", "less",
    "sop", "policy", "requirement", "requirements", "states", "state", "value",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _content_tokens(text):
    """Lowercased word/number tokens with stopwords removed."""
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS]


def _present_fraction(claim_text, doc_tokens):
    """Fraction of the claim's content tokens that appear in the document."""
    claim = _content_tokens(claim_text)
    if len(claim) < MIN_TOKENS_TO_JUDGE:
        return None  # too little signal to judge
    present = sum(1 for t in set(claim) if t in doc_tokens)
    return present / len(set(claim))


def finding_is_unsupported(finding, sop_tokens):
    """Heuristic: is this finding's claim about the SOP ungrounded?"""
    change = str(finding.get("change_type") or "").lower()
    if change == "omission":
        # Claim = "the SOP is missing X". Unsupported if X is actually present.
        claim = f"{finding.get('point', '')} {finding.get('source_says', '')}"
        frac = _present_fraction(claim, sop_tokens)
        return frac is not None and frac >= GROUNDING_THRESHOLD
    # Claim = "the SOP says sop_says". Unsupported if that text isn't in the SOP.
    frac = _present_fraction(finding.get("sop_says", ""), sop_tokens)
    return frac is not None and frac < GROUNDING_THRESHOLD


def run_case(case, base_dir):
    """Execute one case and return a result record."""
    g = extract_text(os.path.join(base_dir, case["global_doc"]))
    s = extract_text(os.path.join(base_dir, case["sop_doc"]))
    r = (
        extract_text(os.path.join(base_dir, case["regional_doc"]))
        if case.get("regional_doc")
        else None
    )

    start = time.perf_counter()
    result = check_drift(g, s, r)
    latency = time.perf_counter() - start

    sop_tokens = set(_content_tokens(s))
    findings = result.get("findings", [])
    unsupported = [f for f in findings if finding_is_unsupported(f, sop_tokens)]

    expected = case["expected_verdict"].upper()
    actual = result.get("verdict", "?")
    return {
        "name": case["name"],
        "expected": expected,
        "actual": actual,
        "correct": expected == actual,
        "latency_s": latency,
        "findings": len(findings),
        "unsupported": len(unsupported),
        "unsupported_points": [f.get("point", "") for f in unsupported],
    }


def evaluate(cases_path):
    """Run all cases, print a report, and return a summary dict."""
    base_dir = REPO_ROOT
    with open(cases_path) as fh:
        manifest = json.load(fh)
    cases = manifest.get("cases", [])

    rows, errors = [], []
    for case in cases:
        try:
            rows.append(run_case(case, base_dir))
        except Exception as exc:  # LLM down, missing file, etc.
            errors.append({"name": case.get("name", "?"), "error": str(exc)})

    # ---- Report ----
    print(f"\nDriftGuardian evaluation — {len(rows)} case(s) run, "
          f"{len(errors)} error(s)\n")
    print(f"{'case':<28}{'expected':<10}{'actual':<10}{'ok':<5}"
          f"{'sec':<8}{'find':<6}{'unsup':<6}")
    print("-" * 73)
    for r in rows:
        print(f"{r['name']:<28}{r['expected']:<10}{r['actual']:<10}"
              f"{'✓' if r['correct'] else '✗':<5}{r['latency_s']:<8.2f}"
              f"{r['findings']:<6}{r['unsupported']:<6}")
    for e in errors:
        print(f"{e['name']:<28}ERROR: {e['error']}")

    summary = {}
    if rows:
        correct = sum(1 for r in rows if r["correct"])
        total_findings = sum(r["findings"] for r in rows)
        total_unsupported = sum(r["unsupported"] for r in rows)
        latencies = [r["latency_s"] for r in rows]
        summary = {
            "cases_run": len(rows),
            "errors": len(errors),
            "verdict_accuracy": correct / len(rows),
            "latency_mean_s": sum(latencies) / len(latencies),
            "latency_min_s": min(latencies),
            "latency_max_s": max(latencies),
            "total_findings": total_findings,
            "unsupported_claim_rate": (
                total_unsupported / total_findings if total_findings else 0.0
            ),
        }
        print("\nSummary")
        print(f"  Verdict accuracy        {summary['verdict_accuracy']*100:5.1f}% "
              f"({correct}/{len(rows)})")
        print(f"  Latency (mean/min/max)  {summary['latency_mean_s']:.2f} / "
              f"{summary['latency_min_s']:.2f} / {summary['latency_max_s']:.2f} s")
        print(f"  Unsupported-claim rate  {summary['unsupported_claim_rate']*100:5.1f}% "
              f"({total_unsupported}/{total_findings} findings)")

        # Surface the suspicious findings so they can be inspected.
        flagged = [(r["name"], p) for r in rows for p in r["unsupported_points"]]
        if flagged:
            print("\n  Findings flagged as possibly unsupported:")
            for name, point in flagged:
                print(f"    - [{name}] {point}")
    print()

    summary["per_case"] = rows
    summary["case_errors"] = errors
    return summary


def main():
    parser = argparse.ArgumentParser(description="Evaluate DriftGuardian on labelled cases.")
    parser.add_argument("--cases", default=os.path.join(REPO_ROOT, "data/demo/eval_cases.json"),
                        help="Path to the cases manifest JSON.")
    parser.add_argument("--json", default=None,
                        help="Optional path to write the full report as JSON.")
    parser.add_argument("--min-accuracy", type=float, default=None,
                        help="Exit non-zero if verdict accuracy falls below this (0-1). For CI.")
    args = parser.parse_args()

    summary = evaluate(args.cases)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(summary, fh, indent=2)
        print(f"Wrote full report to {args.json}")

    if args.min_accuracy is not None:
        acc = summary.get("verdict_accuracy", 0.0)
        if acc < args.min_accuracy:
            print(f"FAIL: accuracy {acc:.2f} < required {args.min_accuracy:.2f}")
            sys.exit(1)


if __name__ == "__main__":
    main()
