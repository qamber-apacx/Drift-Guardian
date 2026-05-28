from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum


class Verdict(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"


class DriftType(str, Enum):
    THRESHOLD_DRIFT = "threshold_drift"
    ROLE_DRIFT = "role_drift"
    TIME_WINDOW_DRIFT = "time_window_drift"
    STEP_OMISSION = "step_omission"
    NO_DRIFT = "no_drift"


class OKRField(BaseModel):
    """
    Structured control field extracted from a policy or SOP document.

    Both human-readable strings (e.g. 'risk_score >= 80', 'within 24 hours')
    AND model-normalised numeric forms are stored. The numeric forms are what
    the conformance checker actually compares — the strings are kept for
    audit/evidence display.
    """
    control_id: str
    trigger: Optional[str] = None

    # human-readable form (for display/audit)
    threshold: Optional[str] = None
    time_window: Optional[str] = None

    # model-normalised numeric form (for comparison)
    threshold_value: Optional[float] = None
    threshold_operator: Optional[str] = None  # ">=", "<=", "==", ">", "<"
    threshold_unit: Optional[str] = None      # "risk_score", "usd", "count", ...
    time_window_hours: Optional[float] = None

    required_actor: Optional[str] = None
    required_action: Optional[str] = None
    evidence_span: Optional[str] = None


class DriftFinding(BaseModel):
    control_id: str
    drift_type: DriftType
    expected: str
    observed: str
    evidence_span_policy: str
    evidence_span_sop: str
    severity: Verdict
    confidence: float
    remediation: str


class ValidationRequest(BaseModel):
    """
    Validation can be driven three ways:
    1) by filename (legacy: refer to a file in data/sop_drafts/)
    2) by raw text (SOP pasted in)
    3) after a prior /upload-sop call (sop_doc_id returned by upload)
    """
    sop_filename: Optional[str] = None
    sop_text: Optional[str] = None
    sop_doc_id: Optional[str] = None

    # optional: override the policy source the same three ways
    policy_filename: Optional[str] = None
    policy_text: Optional[str] = None
    policy_doc_id: Optional[str] = None

    # optional: override document (same three input modes).
    # Exceptions are applied based solely on the control_ids present here.
    override_filename: Optional[str] = None
    override_text: Optional[str] = None
    override_doc_id: Optional[str] = None


class ValidationResult(BaseModel):
    verdict: Verdict
    sop_filename: Optional[str] = None
    findings: List[DriftFinding]
    summary: str
    jira_payload: Optional[dict] = None
    confluence_payload: Optional[dict] = None


class UploadResponse(BaseModel):
    """Returned by /upload-policy and /upload-sop."""
    doc_id: str
    filename: str
    content_type: str
    chars: int
    preview: str = Field(..., description="First 300 chars of extracted text")