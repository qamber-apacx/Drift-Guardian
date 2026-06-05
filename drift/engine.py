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

ENGINE_VERSION = "1.2.0"

# Analysis strategy. "two_stage" first extracts the effective requirements as a
# typed checklist, then verifies the SOP against each one (better recall on
# documents with several independent drifts). "one_shot" does a single
# compare-everything call. Two-stage falls back to one-shot if extraction yields
# nothing, so a vague policy still gets analysed.
DRIFT_MODE = os.getenv("DRIFT_MODE", "two_stage").strip().lower()

# Atomic obligation kinds the extractor classifies requirements into. Used to
# steer the per-requirement check toward the dimension that matters.
VALID_REQ_KINDS = (
    "numeric", "role", "sequence", "time_window", "frequency", "presence",
    "scope", "other",
)

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

## Be exhaustive (READ CAREFULLY)
Do NOT stop after the first problem. Work through EVERY requirement in the source
documents one by one and report ALL drifts you find. A single SOP often contains
several independent drifts at once. For each requirement, check each of these
dimensions separately:
- **Numeric values**: thresholds, limits, scores, amounts, percentages.
- **Roles / actors**: who must perform or approve an action (e.g. MLRO vs an
  onboarding agent). A changed approver is drift even if the step still exists.
- **Sequencing / timing of control**: when a control happens (e.g. pre-approval
  BEFORE onboarding vs a checkpoint AFTER onboarding). A control moved from
  before to after is drift.
- **Time windows / deadlines**: hours, days (e.g. within 24h vs within 72h).
- **Frequency / cadence**: how often something must happen.
- **Scope / applicability**: who or what a rule applies to.
Report a separate finding for EACH dimension that has drifted, even within the
same topic.

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
- omission      : the SOP addresses a topic/process but DROPS a specific mandatory
                  element of it -- a required actor, threshold, time window, or step.
                  Report an omission ONLY when the SOP clearly covers the SAME topic
                  and leaves out that specific. You are given the COMPLETE SOP below;
                  confirm the specific appears NOWHERE in it (in any section, however
                  worded) before reporting. If the SOP simply does not cover a policy
                  topic at all, that is OUT OF SCOPE -- do NOT report it.
- added         : the SOP introduces a requirement absent from all sources.

## What is NOT drift (never report these)
- A policy theme, principle, governance framework, or aspirational commitment that
  the SOP simply does not cover (e.g. continuous-improvement models, supplier
  obligations, sustainability goals, diversity commitments, stakeholder dialogue).
  An operational SOP is not expected to restate an entire corporate policy. The
  ABSENCE of a whole topic is OUT OF SCOPE, not drift. Only a specific mandatory
  element that is WEAKENED, CONTRADICTED, or DROPPED within a topic the SOP DOES
  address counts.
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

DO NOT output an entry for a point where the SOP is compliant. Every object in
"findings" MUST be real drift with "change_type" set to exactly one of:
"contradiction", "weakened", "omission", "added". Never use "none", "match", or
any other value, and never include a finding whose remediation is "no change
needed".
"""


# --- Two-stage prompts --------------------------------------------------------

EXTRACTION_INSTRUCTIONS = """Extract the EFFECTIVE requirements from the policy documents below as a checklist.

A REGIONAL OVERRIDE, when present, REPLACES the GLOBAL policy for any requirement
it covers. So for each point, the effective requirement is the REGIONAL value if
the override specifies one, otherwise the GLOBAL value.

Be EXHAUSTIVE and ATOMIC: list every distinct, checkable obligation as its own
item. Split a topic into multiple items when it mandates several things (e.g. a
threshold value, an approver, an approval timing, and a deadline are FOUR
separate requirements, not one).

For each requirement provide:
- "id"        : a short unique id like "R1", "R2", ...
- "topic"     : a few words naming the control.
- "kind"      : one of "numeric", "role", "sequence", "time_window", "frequency",
                "presence", "scope", "other".
- "expected"  : the effective requirement stated concisely (<25 words), including
                the exact value/role/timing.
- "source"    : "regional" if the value comes from the override, else "global".

Respond with ONLY this JSON object:
{"requirements": [ {<requirement>}, ... ]}
"""


CHECK_INSTRUCTIONS = """You are given a CHECKLIST of effective requirements and the COMPLETE SOP.

Check the SOP against EVERY requirement on the checklist, one by one. For each
requirement decide whether the SOP satisfies it or has drifted from it. Consider
the requirement's "kind" -- for "role" check the actor, for "sequence" check
whether a control happens before vs after, for "time_window" check the deadline,
for "numeric" check the value, etc.

Report a finding ONLY for requirements the SOP VIOLATES. Do not report satisfied
requirements. For each violated requirement provide:
- "id"           : the requirement id it refers to.
- "point"        : the control name.
- "source_says"  : the effective requirement (<25 words).
- "sop_says"     : what the SOP states instead (<25 words).
- "change_type"  : "contradiction", "weakened", or "omission".
- "severity"     : "BLOCK" (a violation of an effective requirement) or "WARN".
- "remediation"  : one concrete sentence on how to fix the SOP.
- "explanation"  : one sentence on why it matters.

A requirement is satisfied (report NOTHING) if the SOP states the same value,
role, timing, or deadline -- even worded differently. Respond with ONLY:
{"findings": [ {<finding>}, ... ]}
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

# The only change types that represent real drift. Anything else ("none",
# "match", "", ...) is a compliance confirmation the model should not have
# emitted, and is discarded.
VALID_CHANGE_TYPES = ("contradiction", "weakened", "omission", "added")

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


def _normalise_findings(findings, regional_text, allow_omissions=True):
    """Clean and tag findings; drop anything that isn't a real drift finding.

    ``allow_omissions`` is False when the SOP was analysed in chunks: a chunk
    cannot see the whole document, so an "omission" judged against a single
    chunk is unreliable and is dropped.
    """
    out = []
    for f in findings or []:
        if not isinstance(f, dict) or not f.get("point"):
            continue
        change = str(f.get("change_type") or "").strip().lower()
        # Discard non-drift entries (e.g. change_type "none"/"match"): the model
        # sometimes lists compliant points, which must never become findings.
        if change not in VALID_CHANGE_TYPES:
            continue
        if change == "omission" and not allow_omissions:
            continue
        scope = str(f.get("scope") or "global").lower()
        if scope not in ("global", "regional"):
            scope = "global"
        # With no regional document, every requirement is global by definition.
        if regional_text is None:
            scope = "global"
        f["change_type"] = change
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


# An SOP up to this many characters is analysed in a SINGLE pass so the model
# always sees the WHOLE document. This is essential for correct omission
# detection: a requirement is only "omitted" if it is absent from the entire
# SOP, which cannot be judged from an isolated section. Only genuinely large
# SOPs above this budget fall back to chunked analysis.
MAX_WHOLE_DOC_CHARS = int(os.getenv("DRIFT_MAX_WHOLE_DOC_CHARS", "12000"))


def _build_extraction_prompt(global_text, regional_text):
    blocks = ["=== GLOBAL POLICY ===", global_text or ""]
    if regional_text:
        blocks += ["", "=== REGIONAL OVERRIDE (higher authority) ===", regional_text]
    blocks += ["", EXTRACTION_INSTRUCTIONS]
    return "\n".join(blocks)


def _build_check_prompt(sop_text, requirements):
    checklist = json.dumps({"requirements": requirements}, ensure_ascii=False, indent=2)
    blocks = [
        "=== EFFECTIVE REQUIREMENTS CHECKLIST ===",
        checklist,
        "",
        "=== SOP (AI-generated, under review) ===",
        sop_text or "",
        "",
        CHECK_INSTRUCTIONS,
    ]
    return "\n".join(blocks)


def _extract_requirements(global_text, regional_text):
    """Stage 1: extract the effective requirements as a typed checklist.

    Returns a cleaned list of requirement dicts, or [] on any failure so the
    caller can fall back to one-shot analysis.
    """
    try:
        parsed = _parse_json(_call_llm(_build_extraction_prompt(global_text, regional_text)))
    except Exception:
        return []
    out = []
    for i, r in enumerate(parsed.get("requirements", []) or [], start=1):
        if not isinstance(r, dict) or not str(r.get("expected") or "").strip():
            continue
        kind = str(r.get("kind") or "other").strip().lower()
        if kind not in VALID_REQ_KINDS:
            kind = "other"
        source = str(r.get("source") or "global").strip().lower()
        if source not in ("global", "regional") or regional_text is None:
            source = source if source == "regional" and regional_text else "global"
        out.append({
            "id": str(r.get("id") or f"R{i}"),
            "topic": str(r.get("topic") or "").strip() or f"Requirement {i}",
            "kind": kind,
            "expected": str(r.get("expected")).strip(),
            "source": source,
        })
    return out


def _evaluate_requirements(sop_text, requirements, regional_text):
    """Stage 2: check the SOP against the checklist; return drift findings."""
    parsed = _parse_json(_call_llm(_build_check_prompt(sop_text, requirements)))
    by_id = {r["id"]: r for r in requirements}
    findings = parsed.get("findings", []) or []
    # Tag each finding's scope from its requirement's source when available.
    for f in findings:
        if isinstance(f, dict):
            req = by_id.get(str(f.get("id")))
            if req and not f.get("scope"):
                f["scope"] = req["source"]
    return _normalise_findings(findings, regional_text, allow_omissions=True)


def _oneshot_findings(global_text, sop_text, regional_text):
    """One-shot fallback: compare the whole SOP in a single call (chunked if huge).

    Returns (findings, sections_count, successful_calls, last_error).
    """
    whole_doc = len(sop_text) <= MAX_WHOLE_DOC_CHARS
    sections = [sop_text.strip()] if whole_doc else split_sections(sop_text)
    findings, successful_calls, last_error = [], 0, None
    for section in sections:
        try:
            parsed = _parse_json(_call_llm(_build_user_prompt(global_text, section, regional_text)))
        except Exception as exc:
            last_error = exc
            continue
        successful_calls += 1
        findings.extend(
            _normalise_findings(parsed.get("findings", []), regional_text, allow_omissions=whole_doc)
        )
    return findings, len(sections), successful_calls, last_error


def check_drift(global_text, sop_text, regional_text=None, mode=None):
    """Run the full drift check and return a structured result dict.

    mode="two_stage" (default) extracts the effective requirements as a typed
    checklist, then verifies the SOP against each one -- better recall when a
    single SOP contains several independent drifts. It falls back to "one_shot"
    if extraction yields no requirements. mode="one_shot" forces the single-call
    path. The default comes from the DRIFT_MODE environment variable.
    """
    sop_text = sop_text or ""
    mode = (mode or DRIFT_MODE).lower()

    requirements = []
    used_mode = "one_shot"
    sections_count = 1
    last_error = None

    if mode == "two_stage":
        requirements = _extract_requirements(global_text, regional_text)

    if requirements:
        used_mode = "two_stage"
        try:
            all_findings = _evaluate_requirements(sop_text, requirements, regional_text)
        except Exception as exc:
            # Extraction worked but the check call failed -- fall back rather than
            # silently passing.
            last_error = exc
            all_findings, sections_count, successful_calls, fb_error = _oneshot_findings(
                global_text, sop_text, regional_text
            )
            used_mode = "one_shot"
            last_error = fb_error or last_error
            if successful_calls == 0:
                raise RuntimeError(
                    f"Drift analysis failed: no successful LLM responses from "
                    f"{LLM_ENDPOINT} (model '{LLM_MODEL}'). Last error: {last_error}"
                )
    else:
        all_findings, sections_count, successful_calls, last_error = _oneshot_findings(
            global_text, sop_text, regional_text
        )
        # If NOT ONE call succeeded, the model was unreachable or always errored.
        # Returning "PASS" here would mean a compliance gate silently passes every
        # document whenever the LLM is down -- the most dangerous failure mode.
        if successful_calls == 0:
            raise RuntimeError(
                f"Drift analysis failed: no successful LLM responses from "
                f"{LLM_ENDPOINT} (model '{LLM_MODEL}'). Last error: {last_error}"
            )

    all_findings = _dedupe_findings(all_findings)
    verdict = _decide(all_findings)

    # Requirement coverage (two-stage only): which obligations were checked and
    # which drifted. This makes the extraction-coverage explicit for reviewers.
    coverage = _build_coverage(requirements, all_findings) if used_mode == "two_stage" else None

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
        "mode": used_mode,
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "verdict": verdict,
        "sections_analyzed": sections_count,
        "requirements_checked": len(requirements) if used_mode == "two_stage" else None,
        "documents": {
            "global": _audit_doc(global_text),
            "regional": _audit_doc(regional_text) if regional_text else None,
            "sop": _audit_doc(sop_text),
        },
        "findings_count": len(all_findings),
        "counts": {"block": block, "warn": warn},
    }

    integrations = _build_integration_payloads(
        verdict, summary, all_findings, remediations, audit
    )

    return {
        "verdict": verdict,
        "summary": summary,
        "findings": all_findings,
        "counts": {"block": block, "warn": warn},
        "sections_analyzed": sections_count,
        "mode": used_mode,
        "coverage": coverage,
        "remediation": remediations,
        "audit": audit,
        "jira_payload": integrations["jira_payload"],
        "confluence_payload": integrations["confluence_payload"],
    }


def _build_coverage(requirements, findings):
    """Summarise which extracted requirements were checked and which drifted."""
    drifted_ids = {str(f.get("id")) for f in findings if f.get("id")}
    items = []
    for r in requirements:
        items.append({
            "id": r["id"],
            "topic": r["topic"],
            "kind": r["kind"],
            "source": r["source"],
            "status": "drifted" if r["id"] in drifted_ids else "satisfied",
        })
    drifted = sum(1 for i in items if i["status"] == "drifted")
    return {
        "requirements_extracted": len(requirements),
        "requirements_drifted": drifted,
        "requirements_satisfied": len(requirements) - drifted,
        "requirements": items,
    }


def _build_integration_payloads(verdict, summary, findings, remediations, audit):
    """Build illustrative Jira / Confluence payloads for enterprise workflows.

    These are ready-to-POST JSON shapes, NOT live integrations -- a downstream
    automation can forward them to the Jira/Confluence REST APIs as-is. They are
    only meaningful for WARN/BLOCK; a PASS produces empty payloads.
    """
    if verdict == "PASS":
        return {"jira_payload": None, "confluence_payload": None}

    priority = "Highest" if verdict == "BLOCK" else "Medium"
    block = audit["counts"]["block"]
    warn = audit["counts"]["warn"]

    # Human-readable body shared by both payloads.
    lines = [f"DriftGuardian verdict: {verdict}", "", summary, "", "Findings:"]
    for f in findings:
        lines.append(
            f"- [{f['severity']}] {f.get('point', '')}: "
            f"source requires \"{f.get('source_says', '')}\"; "
            f"SOP says \"{f.get('sop_says', '')}\". "
            f"Fix: {f.get('remediation', '')}"
        )
    body = "\n".join(lines)

    jira_payload = {
        "fields": {
            "project": {"key": "COMP"},
            "issuetype": {"name": "Bug" if verdict == "BLOCK" else "Task"},
            "priority": {"name": priority},
            "summary": f"DriftGuardian {verdict}: {block} blocking / {warn} warning finding(s)",
            "description": body,
            "labels": ["driftguardian", "compliance-drift", verdict.lower()],
        }
    }

    confluence_payload = {
        "type": "page",
        "title": f"Drift Report — {verdict} — {audit['timestamp_utc']}",
        "space": {"key": "COMP"},
        "body": {
            "storage": {
                "value": "<p>" + body.replace("\n", "<br/>") + "</p>",
                "representation": "storage",
            }
        },
    }

    return {"jira_payload": jira_payload, "confluence_payload": confluence_payload}


def _audit_doc(text):
    """Return a small, privacy-preserving fingerprint of an input document."""
    data = (text or "").encode("utf-8", "ignore")
    return {
        "chars": len(text or ""),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
