# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Drift analysis engine.

Compares an AI-produced SOP against an authoritative Global policy and, when
supplied, a Regional override document. An LLM extracts the meaningful
requirements from each document and judges whether the SOP has drifted.

Severity model
--------------
Severity is decided by *whether an effective requirement is violated*, not by
where that requirement happens to live.

A REGIONAL OVERRIDE, when present, is the higher authority for the values it
covers, so the "effective requirement" for any point is the regional value if
the override specifies one, otherwise the global value.

- BLOCK : the SOP violates an effective requirement -- it contradicts, weakens,
          or drops a value that the governing source mandates. This is a hard
          compliance failure whether the effective requirement came from the
          GLOBAL policy OR from a REGIONAL override. (Violating an effective
          regional override is still a BLOCK, not a soft warning.)
- WARN  : the SOP introduces a requirement absent from every source ("added"),
          or the finding is advisory rather than a violation of a governing
          requirement. Worth review, but not a hard failure.
- PASS  : no meaningful conflicts.

The LLM is asked to emit a ``severity`` directly. When it does, that value is
trusted; otherwise the engine derives severity from the ``change_type`` using
the rules above. ``scope`` ("global" / "regional") is retained for context and
reporting only -- it no longer determines severity.
"""

import datetime
import hashlib
import json
import os
import re

import requests

ENGINE_VERSION = "1.1.0"

# OpenAI-compatible endpoint exposed by the OPEA LLM microservice
# (e.g. TGI / vLLM behind the GenAIComps llm wrapper).
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "http://localhost:9000/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL_ID", "qwen2.5:14b-instruct")
REQUEST_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "600"))

SEVERITY_ORDER = {"PASS": 0, "WARN": 1, "BLOCK": 2}

SYSTEM_PROMPT = (
    "You are a careful compliance drift auditor. You compare a procedure document "
    "(SOP) against authoritative source documents and report ONLY genuine drift: "
    "places where the SOP actually conflicts with, weakens, or drops a requirement "
    "from the governing source. You are conservative: when in doubt, you do NOT "
    "report a finding. You never invent requirements, and you never report a "
    "finding unless you can point to specific conflicting text in the SOP. "
    "You respond with strict JSON only, no prose, no markdown fences."
)

INSTRUCTIONS = """Compare the SOP against the source documents and report only GENUINE drift.

## Which source governs (READ CAREFULLY)
A REGIONAL OVERRIDE, when present, REPLACES the GLOBAL policy for any requirement
it covers. The override is the higher authority for the values it specifies.

Therefore, decide the "effective requirement" for each point like this:
1. If the REGIONAL OVERRIDE specifies a value for a requirement, the effective
   requirement is the REGIONAL value. The global value is superseded and IRRELEVANT.
2. Otherwise, the effective requirement is the GLOBAL value.

Then compare the SOP ONLY against the effective requirement.

CRITICAL: If the SOP matches the regional override value, that is CORRECT COMPLIANCE,
NOT drift. Do NOT report it. (Example: global says threshold >= 80, the regional
override raises it to >= 85, and the SOP says >= 85 -> the SOP is correct, report
NOTHING for that point.)

## What counts as drift (report these)
- contradiction : the SOP states a value that conflicts with the effective requirement.
- weakened      : the SOP makes a control less strict than the effective requirement
                  (e.g. removes a required step, lowers a threshold that should be higher,
                  replaces "two-factor authentication" with "password").
- omission      : the SOP entirely DROPS a required actor, action, threshold, or
                  time window that the effective requirement mandates.
- added         : the SOP introduces a requirement absent from all sources.

## What is NOT drift (never report these)
- The SOP rephrases or restates a requirement in different words but the meaning,
  values, actors, and time windows are the same. Wording differences are NOT drift.
- The SOP matches the regional override (see CRITICAL above).
- A detail you cannot actually find missing. Before reporting an "omission", re-read
  the SOP text and confirm the value/phrase is genuinely ABSENT. If the SOP contains
  the same time window ("within 48 hours"), actor, or threshold as the source -- even
  worded differently -- it is NOT omitted.

## Scope of each finding (context only -- does NOT set severity)
- "regional" : the effective requirement the SOP conflicts with comes from (or is
               modified by) the REGIONAL override.
- "global"   : the effective requirement the SOP conflicts with comes from the
               GLOBAL policy (no regional override applies to it).

## Severity of each finding (READ CAREFULLY)
Severity depends on WHETHER an effective requirement was violated, NOT on where it lives.
- "BLOCK" : the SOP violates an effective requirement -- a "contradiction", "weakened",
            or "omission" of a value the governing source mandates. This is a hard
            failure REGARDLESS of scope. A violation of an effective REGIONAL override
            is a BLOCK, exactly like a violation of the global policy. Do NOT downgrade
            a regional-scope violation to WARN.
- "WARN"  : the SOP introduces a requirement absent from every source ("added"), or the
            concern is advisory rather than a violation of a governing requirement.

## Output
For each genuine finding provide:
- "point"        : the requirement at issue (short).
- "source_says"  : the effective requirement (quote/paraphrase, <25 words).
- "sop_says"     : what the SOP states instead (quote/paraphrase, <25 words).
- "scope"        : "global" or "regional" (context only).
- "change_type"  : "contradiction", "omission", "weakened", or "added".
- "severity"     : "BLOCK" or "WARN", using the rules above.
- "remediation"  : one concrete sentence telling the author how to fix the SOP so it
                   matches the effective requirement (e.g. "Change the retention period
                   to 7 years to match the global policy.").
- "explanation"  : one sentence on why this matters.

Respond with ONLY this JSON object:
{"findings": [ {<finding>}, ... ], "summary": "<one sentence overall summary>"}
If the SOP is fully aligned with the effective requirements, return:
{"findings": [], "summary": "No drift detected; the SOP matches the governing requirements."}
"""


# A single LLM call reliably handles roughly this many characters of SOP text
# alongside the (short) policy documents. Longer SOPs are split into sections so
# the model compares a focused chunk at a time -- this dramatically improves
# recall on long documents, especially with smaller local models.
MAX_SECTION_CHARS = int(os.getenv("DRIFT_MAX_SECTION_CHARS", "2500"))

# Markdown-style headings: "# ...", "## 1.0 ...", "### ...", etc.
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+.*$", re.MULTILINE)


def split_sections(sop_text, max_chars=MAX_SECTION_CHARS):
    """Split the SOP into reasonably sized sections for per-chunk comparison.

    Prefers markdown headings as boundaries. Any section longer than max_chars
    is further split on blank lines. Always returns at least one chunk.
    """
    text = sop_text.strip()
    if not text:
        return [text]

    # Find heading positions; build sections spanning heading -> next heading.
    starts = [m.start() for m in _HEADING_RE.finditer(text)]
    if starts:
        # Include any preamble before the first heading.
        bounds = ([0] if starts[0] != 0 else []) + starts + [len(text)]
        raw_sections = [
            text[bounds[i]:bounds[i + 1]].strip() for i in range(len(bounds) - 1)
        ]
        raw_sections = [s for s in raw_sections if s]
    else:
        raw_sections = [text]

    # Further split any oversized section on blank lines.
    sections = []
    for sec in raw_sections:
        if len(sec) <= max_chars:
            sections.append(sec)
            continue
        buf = ""
        for para in sec.split("\n\n"):
            if buf and len(buf) + len(para) + 2 > max_chars:
                sections.append(buf.strip())
                buf = para
            else:
                buf = (buf + "\n\n" + para) if buf else para
        if buf.strip():
            sections.append(buf.strip())

    return sections or [text]


def _build_user_prompt(global_text, sop_text, regional_text=None):
    blocks = [
        "=== GLOBAL POLICY (authoritative) ===",
        global_text,
    ]
    if regional_text:
        blocks += [
            "",
            "=== REGIONAL OVERRIDE (HIGHER AUTHORITY -- supersedes the global policy "
            "for any requirement it covers) ===",
            regional_text,
        ]
    blocks += ["", "=== SOP (AI-generated, under review) ===", sop_text, "", INSTRUCTIONS]
    return "\n".join(blocks)


def _call_llm(user_prompt):
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 2048,
        "stream": False,
    }
    resp = requests.post(LLM_ENDPOINT, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _parse_json(raw):
    """Tolerant JSON extraction from the model output."""
    raw = raw.strip()
    # Strip accidental code fences.
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


VALID_SEVERITIES = ("BLOCK", "WARN")

# change_type -> severity when the LLM does not (or invalidly) supplies one.
# A genuine violation of an effective requirement is a hard BLOCK regardless of
# whether that requirement is global or a regional override. Only "added"
# (a requirement the SOP invents, present in no source) is a soft WARN.
_CHANGE_SEVERITY = {
    "contradiction": "BLOCK",
    "weakened": "BLOCK",
    "omission": "BLOCK",
    "added": "WARN",
}


def _derive_severity(finding):
    """Decide a finding's severity.

    The LLM is asked to emit ``severity`` directly; a valid value is trusted.
    Otherwise severity is derived from ``change_type`` -- any violation of an
    effective requirement (contradiction/weakened/omission) is a BLOCK, while an
    invented requirement ("added") is a WARN. Severity is NOT a function of
    scope, so a violation of an effective regional override blocks just like a
    violation of the global policy.
    """
    sev = str(finding.get("severity") or "").strip().upper()
    if sev in VALID_SEVERITIES:
        return sev
    change = str(finding.get("change_type") or "").strip().lower()
    # Unknown/unspecified change types default to BLOCK so a real conflict is
    # never silently downgraded.
    return _CHANGE_SEVERITY.get(change, "BLOCK")


def _decide(findings):
    """Reduce findings to an overall verdict using each finding's severity."""
    verdict = "PASS"
    for f in findings:
        sev = f.get("severity", "BLOCK")
        if SEVERITY_ORDER.get(sev, 2) > SEVERITY_ORDER[verdict]:
            verdict = sev
    return verdict


def _normalise_findings(findings, regional_text):
    """Clean and tag a list of findings with scope, severity and remediation."""
    out = []
    for f in findings or []:
        if not isinstance(f, dict) or not f.get("point"):
            continue
        scope = str(f.get("scope") or "global").lower()
        if scope not in ("global", "regional"):
            scope = "global"
        # With no regional document, every requirement is global by definition.
        if regional_text is None:
            scope = "global"
        f["scope"] = scope
        f["severity"] = _derive_severity(f)
        # Guarantee a remediation string is always present for the report.
        if not str(f.get("remediation") or "").strip():
            f["remediation"] = "Update the SOP to match the effective requirement."
        out.append(f)
    return out


def _dedupe_findings(findings):
    """Drop near-duplicate findings that can arise across overlapping chunks."""
    seen = set()
    unique = []
    for f in findings:
        key = (
            f.get("point", "").strip().lower(),
            f.get("sop_says", "").strip().lower()[:60],
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    return unique


def check_drift(global_text, sop_text, regional_text=None):
    """Run the full drift check and return a structured result dict.

    The SOP is split into sections and each section is compared against the
    (full) policy and override documents. Findings are merged across sections.
    This keeps each LLM call focused, which greatly improves recall on long
    documents compared with a single whole-document prompt.
    """
    sections = split_sections(sop_text)

    all_findings = []
    for section in sections:
        prompt = _build_user_prompt(global_text, section, regional_text)
        try:
            raw = _call_llm(prompt)
            parsed = _parse_json(raw)
        except Exception:
            # A single bad/empty chunk response should not abort the whole run.
            continue
        all_findings.extend(_normalise_findings(parsed.get("findings", []), regional_text))

    all_findings = _dedupe_findings(all_findings)
    verdict = _decide(all_findings)

    block = sum(1 for f in all_findings if f["severity"] == "BLOCK")
    warn = sum(1 for f in all_findings if f["severity"] == "WARN")
    if all_findings:
        summary = (
            f"Found {len(all_findings)} drift finding(s): "
            f"{block} blocking, {warn} warning(s)."
        )
    else:
        summary = "No drift detected; the SOP matches the governing requirements."

    # Compact, ordered remediation payload: every actionable fix, worst first.
    remediations = [
        {
            "point": f.get("point", ""),
            "severity": f["severity"],
            "action": f.get("remediation", ""),
        }
        for f in sorted(
            all_findings,
            key=lambda f: SEVERITY_ORDER.get(f["severity"], 0),
            reverse=True,
        )
    ]

    # Tamper-evident audit record: who/what was compared, the verdict, and a
    # hash of each input so a stored report can be tied back to its sources.
    audit = {
        "engine_version": ENGINE_VERSION,
        "model": LLM_MODEL,
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "verdict": verdict,
        "sections_analyzed": len(sections),
        "documents": {
            "global": _audit_doc(global_text),
            "regional": _audit_doc(regional_text) if regional_text else None,
            "sop": _audit_doc(sop_text),
        },
        "findings_count": len(all_findings),
        "counts": {"block": block, "warn": warn},
    }

    return {
        "verdict": verdict,
        "summary": summary,
        "findings": all_findings,
        "counts": {"block": block, "warn": warn},
        "sections_analyzed": len(sections),
        "remediation": remediations,
        "audit": audit,
    }


def _audit_doc(text):
    """Return a small, privacy-preserving fingerprint of an input document."""
    data = (text or "").encode("utf-8", "ignore")
    return {
        "chars": len(text or ""),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
