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
LLM_ENDPOINT = os.environ.get(
    "LLM_ENDPOINT",
    "http://localhost:9000/v1/chat/completions",
)
LLM_MODEL = os.environ.get("LLM_MODEL", "Intel/neural-chat-7b-v3-3")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "EMPTY")  # OPEA / vLLM ignore this
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "120"))

SYSTEM_PROMPT = (
    "You are a compliance document analyst. You extract structured control "
    "fields from KYC/AML policy and SOP text. You return STRICT JSON only — "
    "no prose, no markdown fences."
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
  region              geographic region, e.g. "GLOBAL", "APAC", "EU". null if unclear.
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
        # Hint the server to enforce JSON. Ignored by backends that don't support it.
        "response_format": {"type": "json_object"},
        "stream": False,
    }
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
    for item in items:
        if not isinstance(item, dict):
            continue
        if not item.get("control_id"):
            continue
        if not item.get("evidence_span"):
            # Hard requirement: no claim without evidence.
            logger.debug("Dropping control with no evidence_span: %s",
                         item.get("control_id"))
            continue
        clean = {k: v for k, v in item.items() if k in allowed}
        try:
            fields.append(OKRField(**clean))
        except Exception as e:  # noqa: BLE001
            logger.warning("Pydantic validation failed for control %s: %s",
                           item.get("control_id"), e)
            continue

    logger.info("Extracted %d controls from %s", len(fields), source or "<unnamed>")
    return fields