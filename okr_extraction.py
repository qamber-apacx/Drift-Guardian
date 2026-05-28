"""
OKR field extraction via an OpenAI-compatible LLM endpoint.

Defaults target OPEA's LLM textgen microservice (OpenAI-compatible API).
Override with env vars LLM_ENDPOINT / LLM_MODEL / LLM_API_KEY to point at
Ollama, vLLM, TGI, or any other OpenAI-compatible backend.

Key improvements over the original:
  - Model returns NORMALISED numeric fields (threshold_value, time_window_hours)
    so the conformance checker no longer has to regex free text.
  - Uses response_format={"type":"json_object"} for reliable JSON output.
  - Real exception logging instead of swallowed errors.
"""
import json
import logging
import os
import re
from typing import List

import httpx

from schemas import OKRField

logger = logging.getLogger(__name__)

# OpenAI-compatible endpoint.
# OPEA LLM textgen microservice:  http://llm-textgen:9000/v1/chat/completions
# Ollama (with openai compat):     http://localhost:11434/v1/chat/completions
# Ollama default — override with env vars for other backends.
# Ollama OpenAI-compat: http://localhost:11434/v1/chat/completions
# OPEA textgen:         http://llm-textgen:9000/v1/chat/completions
# vLLM:                 http://localhost:8000/v1/chat/completions
LLM_ENDPOINT = os.environ.get(
    "LLM_ENDPOINT",
    "http://localhost:11434/v1/chat/completions",
)
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5:7b-instruct")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "ollama")
# 300s default — Qwen 2.5 7B on CPU can take 3-5 min for a long document.
# Reduce to 120 if running on GPU.
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "300"))

# Backends that support response_format=json_object (set to "0" to disable).
# Ollama supports it for instruct models; some older OPEA builds do not.
LLM_JSON_MODE = os.environ.get("LLM_JSON_MODE", "1") == "1"

SYSTEM_PROMPT = (
    "You are a compliance document analyst. "
    "You extract structured control fields from policy and SOP documents. "
    "You MUST respond with ONLY a valid JSON object. "
    "Do NOT include any prose, explanation, preamble, or markdown code fences. "
    "Do NOT wrap the JSON in ```json or ``` blocks. "
    "Your entire response must start with { and end with }."
)

USER_PROMPT_TEMPLATE = """Extract every compliance control from the document below.

For each control, return an object with these fields:

  control_id          short stable identifier, e.g. "KYC_HIGH_RISK_REVIEW"
  trigger             the event that triggers this control
  threshold           the threshold expression as written, e.g. "risk_score >= 80"
  threshold_value     the NUMBER from the threshold as a float, e.g. 80.0.
                      If the threshold is written in words ("eighty"), convert it.
                      If there is no numeric threshold, use null.
  threshold_operator  one of ">=", "<=", "==", ">", "<", or null
  threshold_unit      short unit token, e.g. "risk_score", "usd", "count". null if unclear.
  required_actor      role/title responsible, e.g. "L2 Compliance Analyst"
  required_action     what action must be taken
  time_window         the time limit as written, e.g. "within 24 hours" or "two business days"
  time_window_hours   the time window NORMALISED TO HOURS as a float.
                      1 day = 24, 1 business day = 24, 1 week = 168.
                      If there is no time window, use null.
  evidence_span       the EXACT sentence(s) from the document supporting this control.
                      This must be a verbatim quote — do not paraphrase.

Return a JSON object with one key, "controls", whose value is an array of
these objects. Example:

{{"controls": [{{"control_id": "...", "threshold_value": 80, ...}}]}}

If the document contains no controls, return {{"controls": []}}.

DOCUMENT:
\"\"\"
{text}
\"\"\"
"""


def _strip_markdown_fences(raw: str) -> str:
    """LLMs sometimes wrap JSON in ```json ... ``` despite being told not to."""
    raw = raw.strip()
    if raw.startswith("```"):
        # remove the opening fence (possibly ```json)
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        # remove the closing fence
        raw = re.sub(r"\n?```\s*$", "", raw)
    return raw.strip()


def _normalise_for_evidence_match(s: str) -> str:
    """
    Normalise a string for evidence matching: unify quotes, collapse whitespace,
    lowercase. Does NOT remove spaces — that is done separately in _despaced().
    """
    if not s:
        return ""
    replacements = {
        "\u2018": "'", "\u2019": "'", "\u201A": "'", "\u201B": "'",
        "\u201C": '"', "\u201D": '"', "\u201E": '"', "\u201F": '"',
        "\u2013": "-", "\u2014": "-", "\u2212": "-",
        "\xa0": " ",
    }
    out = s
    for k, v in replacements.items():
        out = out.replace(k, v)
    out = " ".join(out.split())
    return out.lower()


def _despaced(s: str) -> str:
    """Remove all spaces for broken-PDF token matching."""
    return s.replace(" ", "")


def verify_evidence_span(evidence: str, source_text: str) -> bool:
    """
    Return True if evidence appears (loosely) in source_text.

    Two strategies:
    1. Normalised substring match — handles quote/whitespace variants.
    2. De-spaced match — handles pypdf mid-word space artefacts where
       words like "employment" are extracted as "em ployment". Removing
       all spaces from both strings makes them match again.
       Requires at least 20 chars after de-spacing to avoid false positives.
    """
    if not evidence or not source_text:
        return False

    e_norm = _normalise_for_evidence_match(evidence)
    s_norm = _normalise_for_evidence_match(source_text)

    # Strategy 1: standard normalised match
    if len(e_norm) >= 12 and e_norm in s_norm:
        return True

    # Strategy 2: de-spaced match for broken PDF artefacts
    e_ds = _despaced(e_norm)
    s_ds = _despaced(s_norm)
    if len(e_ds) >= 20 and e_ds in s_ds:
        return True

    return False


def _parse_response(raw: str) -> list[dict]:
    """Parse the LLM response into a list of control dicts. Resilient to shape drift."""
    raw = _strip_markdown_fences(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        # try to recover by grabbing the outermost {...} or [...]
        match = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
        if not match:
            logger.error("LLM returned non-JSON: %s", raw[:500])
            raise ValueError("LLM did not return valid JSON") from e
        data = json.loads(match.group(1))

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("controls", "data", "results", "items"):
            if key in data and isinstance(data[key], list):
                return data[key]
    logger.error("LLM JSON had unexpected shape: %s", type(data))
    return []


async def extract_okr_fields(text: str, source: str = "") -> List[OKRField]:
    """
    Extract structured OKR control fields from a policy or SOP document
    using the configured LLM endpoint. Returns an empty list on any failure
    (caller must handle).
    """
    if not text or not text.strip():
        logger.warning("extract_okr_fields called with empty text (source=%s)", source)
        return []

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(text=text)},
        ],
        "temperature": 0.1,
        "stream": False,
        # Request a larger context window explicitly.
        # Ollama defaults to 4096 (derived from VRAM) which is too small for
        # a full policy document + extraction prompt (~5000-8000 tokens total).
        # Ollama honours this via the "options" key; OpenAI-compat backends
        # ignore unknown keys so this is safe to always include.
        "options": {
            "num_ctx": int(os.environ.get("LLM_NUM_CTX", "8192")),
        },
    }
    # response_format=json_object is supported by Ollama (instruct models),
    # OpenAI, and recent vLLM builds. Some OPEA/older backends return 400 if
    # this field is present — set LLM_JSON_MODE=0 to disable it.
    if LLM_JSON_MODE:
        payload["response_format"] = {"type": "json_object"}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LLM_API_KEY}",
    }

    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT_S) as client:
            resp = await client.post(LLM_ENDPOINT, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPError as e:
        logger.exception("LLM HTTP error (source=%s, endpoint=%s): %s",
                         source, LLM_ENDPOINT, e)
        return []

    # OpenAI-compatible response shape
    try:
        raw_content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        # Fallback for non-OpenAI-shaped responses (e.g. Ollama /api/generate)
        raw_content = body.get("response") or body.get("content") or ""
        if not raw_content:
            logger.error("Could not find generated text in LLM response: %s",
                         json.dumps(body)[:500])
            return []

    try:
        items = _parse_response(raw_content)
    except ValueError:
        return []

    fields: List[OKRField] = []
    allowed = set(OKRField.model_fields.keys())
    dropped_hallucinated = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if not item.get("control_id"):
            continue
        evidence = item.get("evidence_span")
        if not evidence:
            # Hard requirement: no claim without evidence.
            logger.debug("Dropping control with no evidence_span: %s",
                         item.get("control_id"))
            continue
        # Anti-hallucination: the LLM was instructed to return a verbatim
        # quote. If that "quote" doesn't actually appear in the source
        # document, the rest of the extracted fields are suspect too.
        if not verify_evidence_span(evidence, text):
            dropped_hallucinated += 1
            logger.warning(
                "Dropping control %s — evidence_span not found in source (%s): %r",
                item.get("control_id"), source or "<unnamed>", evidence[:120],
            )
            continue
        clean = {k: v for k, v in item.items() if k in allowed}
        try:
            fields.append(OKRField(**clean))
        except Exception as e:  # noqa: BLE001
            logger.warning("Pydantic validation failed for control %s: %s",
                           item.get("control_id"), e)
            continue

    if dropped_hallucinated:
        logger.info(
            "Dropped %d control(s) from %s due to unverifiable evidence spans.",
            dropped_hallucinated, source or "<unnamed>",
        )
    logger.info("Extracted %d controls from %s", len(fields), source or "<unnamed>")
    return fields