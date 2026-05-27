"""
DriftGuardian — FastAPI backend.

This is the governance API the Streamlit UI (ui/streamlit_app.py) talks to.
It wires together the four pipeline stages:

    1. dataprep            — ingest/extract text from uploads or disk
    2. okr_extraction      — LLM extracts structured control fields
    3. conformance_checker — compare SOP controls vs the approved policy
                             hierarchy
    4. remediation         — build Jira + Confluence payloads for findings

Endpoints (matched exactly to what the Streamlit UI calls):
    GET  /health
    POST /upload-policy     (multipart file field "file")  -> UploadResponse
    POST /upload-sop        (multipart file field "file")  -> UploadResponse
    POST /upload-override   (multipart file field "file")  -> UploadResponse
    POST /validate          (ValidationRequest JSON)       -> ValidationResult

Run with:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from conformance_checker import compute_verdict, run_conformance_check
from dataprep import (
    POLICY_DIR,
    _DOC_STORE,
    get_uploaded_text,
    load_global_baseline,
    load_sop,
    load_text,
    store_upload,
)
from okr_extraction import extract_okr_fields
from remediation import generate_confluence_audit_log, generate_jira_ticket
from schemas import (
    OKRField,
    UploadResponse,
    ValidationRequest,
    ValidationResult,
    Verdict,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("driftguardian")

app = FastAPI(
    title="DriftGuardian",
    version="1.1.0",
    description="A governance gate for AI-drafted compliance SOPs.",
)

# The Streamlit UI runs on a different origin; allow it through.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================== #
# Health
# ============================================================== #
@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "driftguardian", "version": "1.1.0"}


# ============================================================== #
# Upload endpoints
# ============================================================== #
async def _handle_upload(file: UploadFile) -> UploadResponse:
    """Shared logic for the three /upload-* endpoints."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    try:
        info = await store_upload(
            filename=file.filename or "upload",
            content_type=file.content_type or "application/octet-stream",
            raw=raw,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return UploadResponse(**info)


@app.post("/upload-policy", response_model=UploadResponse)
async def upload_policy(file: UploadFile = File(...)) -> UploadResponse:
    return await _handle_upload(file)


@app.post("/upload-sop", response_model=UploadResponse)
async def upload_sop(file: UploadFile = File(...)) -> UploadResponse:
    return await _handle_upload(file)


@app.post("/upload-override", response_model=UploadResponse)
async def upload_override(file: UploadFile = File(...)) -> UploadResponse:
    return await _handle_upload(file)


# ============================================================== #
# Text resolution helpers
# ============================================================== #
def _load_policy_file(filename: str) -> str:
    """Load a named policy file from the policy hierarchy directory on disk."""
    path = Path(POLICY_DIR) / filename
    if not path.exists():
        raise FileNotFoundError(f"Policy file not found: {filename}")
    return load_text(path)


def _resolve_doc(
    doc_id: Optional[str],
    text: Optional[str],
    filename: Optional[str],
    disk_loader,
    label: str,
) -> Optional[str]:
    """
    Resolve a document's text from one of three input modes, in priority order:
    uploaded doc_id -> inline text -> on-disk filename.
    Returns None if no source was provided (caller decides if that's fatal).
    """
    if doc_id:
        resolved = get_uploaded_text(doc_id)
        if resolved is None:
            raise HTTPException(
                status_code=404,
                detail=f"{label} doc_id '{doc_id}' not found (did the upload expire?).",
            )
        return resolved

    if text and text.strip():
        return text

    if filename and disk_loader is not None:
        try:
            return disk_loader(filename)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    return None


def _uploaded_filename(doc_id: Optional[str]) -> Optional[str]:
    if not doc_id:
        return None
    entry = _DOC_STORE.get(doc_id)
    return entry["filename"] if entry else None


def _build_summary(verdict: Verdict, findings: list) -> str:
    if verdict == Verdict.PASS:
        return (
            "No unauthorized divergence detected. "
            "The SOP conforms to the approved policy baseline and is cleared "
            "for publication."
        )

    n = len(findings)
    block_n = sum(1 for f in findings if f.severity == Verdict.BLOCK)
    warn_n = sum(1 for f in findings if f.severity == Verdict.WARN)

    if verdict == Verdict.BLOCK:
        return (
            f"Publication blocked: {block_n} unauthorized policy divergence(s) "
            f"detected in this SOP"
            + (f" ({warn_n} additional warning(s))" if warn_n else "")
            + ". Each finding is anchored to verbatim evidence below."
        )

    return (
        f"Conditional pass: {n} deviation(s) detected, all matching "
        "approved overrides or representing stricter controls. "
        "Attach the override reference before publishing."
    )


# ============================================================== #
# Validate
# ============================================================== #
@app.post("/validate", response_model=ValidationResult)
async def validate(req: ValidationRequest) -> ValidationResult:

    # ---- 1. Resolve SOP text (required) ----
    sop_text = _resolve_doc(
        req.sop_doc_id, req.sop_text, req.sop_filename, load_sop, "SOP"
    )
    if not sop_text:
        raise HTTPException(
            status_code=400,
            detail="No SOP provided. Supply sop_doc_id, sop_text, or sop_filename.",
        )

    # ---- 2. Resolve policy text (falls back to disk baseline) ----
    policy_text = _resolve_doc(
        req.policy_doc_id,
        req.policy_text,
        req.policy_filename,
        _load_policy_file,
        "Policy",
    )
    if not policy_text:
        try:
            policy_text = load_global_baseline()
        except FileNotFoundError as e:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No policy provided and no on-disk baseline found. "
                    "Supply policy_doc_id, policy_text, or policy_filename."
                ),
            ) from e

    # ---- 3. Resolve override text (optional) ----
    # No longer falls back to regional disk based on a 'region' parameter.
    override_text = _resolve_doc(
        req.override_doc_id, req.override_text, req.override_filename, None, "Override"
    )

    # ---- 4. Extract structured control fields via the LLM ----
    try:
        policy_fields: List[OKRField] = await extract_okr_fields(
            policy_text, source="policy"
        )
        sop_fields: List[OKRField] = await extract_okr_fields(sop_text, source="sop")
        override_fields: List[OKRField] = (
            await extract_okr_fields(override_text, source="override")
            if override_text
            else []
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("LLM extraction failed")
        raise HTTPException(
            status_code=502,
            detail=f"Control extraction failed (LLM backend unreachable?): {e}",
        ) from e

    if not policy_fields:
        raise HTTPException(
            status_code=422,
            detail=(
                "No controls could be extracted from the policy document. "
                "Check the policy text and the LLM endpoint."
            ),
        )

    # ---- 5. Run the conformance check ----
    findings = run_conformance_check(policy_fields, sop_fields, override_fields)
    verdict = compute_verdict(findings)

    sop_filename = (
        req.sop_filename
        or _uploaded_filename(req.sop_doc_id)
        or "pasted_sop.md"
    )

    # ---- 6. Build enterprise payloads (only when there's something to report) ----
    jira_payload = None
    confluence_payload = None
    if findings:
        jira_payload = generate_jira_ticket(sop_filename, verdict, findings)
        confluence_payload = generate_confluence_audit_log(
            sop_filename, verdict, findings
        )

    summary = _build_summary(verdict, findings)

    return ValidationResult(
        verdict=verdict,
        sop_filename=sop_filename,
        findings=findings,
        summary=summary,
        jira_payload=jira_payload,
        confluence_payload=confluence_payload,
    )