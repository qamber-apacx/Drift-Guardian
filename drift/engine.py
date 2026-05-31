# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Drift analysis engine.

Compares an AI-produced SOP against an authoritative Global policy and, when
supplied, a Regional override document. An LLM extracts the meaningful
requirements from each document and judges whether the SOP has drifted.

Decision rules
--------------
- A change that conflicts with the GLOBAL policy  -> BLOCK
- A change that conflicts only with the REGIONAL override -> WARN
- No meaningful conflicts -> PASS
"""

import json
import os
import re

import requests

# OpenAI-compatible endpoint exposed by the OPEA LLM microservice
# (e.g. TGI / vLLM behind the GenAIComps llm wrapper).
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "http://localhost:9000/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL_ID", "qwen2.5:7b")
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

## Scope of each finding
- "regional" : the SOP conflicts with the effective requirement, and that requirement
               comes from (or is modified by) the REGIONAL override.
- "global"   : the SOP conflicts with the effective requirement, and that requirement
               comes from the GLOBAL policy (no regional override applies to it).

## Output
For each genuine finding provide:
- "point"        : the requirement at issue (short).
- "source_says"  : the effective requirement (quote/paraphrase, <25 words).
- "sop_says"     : what the SOP states instead (quote/paraphrase, <25 words).
- "scope"        : "global" or "regional".
- "change_type"  : "contradiction", "omission", "weakened", or "added".
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


def _decide(findings):
    """Reduce findings to an overall verdict."""
    verdict = "PASS"
    for f in findings:
        scope = (f.get("scope") or "").lower()
        sev = "BLOCK" if scope == "global" else "WARN"
        if SEVERITY_ORDER[sev] > SEVERITY_ORDER[verdict]:
            verdict = sev
    return verdict


def _normalise_findings(findings, regional_text):
    """Clean and tag a list of findings with scope + severity."""
    out = []
    for f in findings or []:
        if not isinstance(f, dict) or not f.get("point"):
            continue
        scope = (f.get("scope") or "global").lower()
        if scope not in ("global", "regional"):
            scope = "global"
        if regional_text is None:
            scope = "global"
        f["scope"] = scope
        f["severity"] = "BLOCK" if scope == "global" else "WARN"
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

    return {
        "verdict": verdict,
        "summary": summary,
        "findings": all_findings,
        "counts": {"block": block, "warn": warn},
        "sections_analyzed": len(sections),
    }
