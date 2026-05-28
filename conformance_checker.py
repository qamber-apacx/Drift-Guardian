"""
Conformance checking: compare SOP control fields against policy control fields.

Improvements over the original:
  - Threshold and time-window comparisons use the model-normalised numeric
    fields (threshold_value, time_window_hours) instead of regex on free text.
  - Universal, region-agnostic checks that rely solely on explicit overrides.
  - Fuzzy control_id matching uses rapidfuzz token_set_ratio with a threshold
    instead of loose substring matching, and only the best match wins.
  - Fixes the "0 is falsy" bug in apply_override.
  - Role comparison normalises whitespace/case and ignores common decorations.
  - Detects DROPPED policy controls: any policy control with no matching SOP
    control becomes a BLOCK-severity STEP_OMISSION finding. Previously these
    silent removals were invisible — the most dangerous drift type.
"""
import logging
from typing import List, Optional, Tuple

from schemas import DriftFinding, DriftType, OKRField, Verdict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------- #
def _norm(text: Optional[str]) -> str:
    """Lowercase, strip, collapse whitespace."""
    if not text:
        return ""
    return " ".join(text.lower().split())


def _norm_role(role: Optional[str]) -> str:
    """Normalise a role string for loose comparison."""
    if not role:
        return ""
    cleaned = role.lower().strip()
    # Drop common trailing punctuation / decorations.
    cleaned = cleaned.replace(".", "").replace(",", "")
    return " ".join(cleaned.split())


# ---------------------------------------------------------------- #
# Individual checks
# ---------------------------------------------------------------- #
def check_threshold(
    policy_field: OKRField, sop_field: OKRField
) -> Optional[DriftFinding]:
    """Detect unauthorized threshold changes using normalised numeric values."""
    p_val = policy_field.threshold_value
    s_val = sop_field.threshold_value

    if p_val is None or s_val is None:
        return None
    if p_val == s_val:
        return None

    expected = policy_field.threshold or f"{p_val}"
    observed = sop_field.threshold or f"{s_val}"

    return DriftFinding(
        control_id=policy_field.control_id,
        drift_type=DriftType.THRESHOLD_DRIFT,
        expected=expected,
        observed=observed,
        evidence_span_policy=policy_field.evidence_span or expected,
        evidence_span_sop=sop_field.evidence_span or observed,
        severity=Verdict.BLOCK,
        confidence=0.95,
        remediation=(
            f"Restore threshold to '{expected}' or attach an approved "
            f"policy override."
        ),
    )


def check_role(
    policy_field: OKRField, sop_field: OKRField
) -> Optional[DriftFinding]:
    """Detect unauthorized actor/role changes."""
    p_actor = _norm_role(policy_field.required_actor)
    s_actor = _norm_role(sop_field.required_actor)

    if not p_actor or not s_actor:
        return None
    if p_actor == s_actor:
        return None

    # Try a fuzzy match — small wording differences shouldn't BLOCK.
    try:
        from rapidfuzz import fuzz
        score = fuzz.token_set_ratio(p_actor, s_actor)
        if score >= 90:
            return None  # essentially the same role written differently
    except ImportError:
        pass

    return DriftFinding(
        control_id=policy_field.control_id,
        drift_type=DriftType.ROLE_DRIFT,
        expected=policy_field.required_actor or "",
        observed=sop_field.required_actor or "",
        evidence_span_policy=policy_field.evidence_span or (policy_field.required_actor or ""),
        evidence_span_sop=sop_field.evidence_span or (sop_field.required_actor or ""),
        severity=Verdict.BLOCK,
        confidence=0.90,
        remediation=f"Restore responsible actor to '{policy_field.required_actor}'.",
    )


def check_time_window(
    policy_field: OKRField, sop_field: OKRField
) -> Optional[DriftFinding]:
    """Detect time-window drift using normalised hours."""
    p_hours = policy_field.time_window_hours
    s_hours = sop_field.time_window_hours

    if p_hours is None or s_hours is None:
        return None
    if p_hours == s_hours:
        return None

    # Looser window = weaker control = BLOCK.
    # Stricter window = tighter than policy = WARN (deviation, but safer).
    severity = Verdict.BLOCK if s_hours > p_hours else Verdict.WARN

    expected = policy_field.time_window or f"{p_hours} hours"
    observed = sop_field.time_window or f"{s_hours} hours"

    return DriftFinding(
        control_id=policy_field.control_id,
        drift_type=DriftType.TIME_WINDOW_DRIFT,
        expected=expected,
        observed=observed,
        evidence_span_policy=policy_field.evidence_span or expected,
        evidence_span_sop=sop_field.evidence_span or observed,
        severity=severity,
        confidence=0.90,
        remediation=f"Restore time window to '{expected}'.",
    )


def check_step_omission(
    policy_field: OKRField, sop_field: OKRField
) -> Optional[DriftFinding]:
    """
    Detect omission/weakening of a mandatory control step.
    """
    p_action = _norm(policy_field.required_action)
    s_action = _norm(sop_field.required_action)

    if not p_action or not s_action:
        return None
    if p_action == s_action:
        return None

    p_before = "before" in p_action
    s_after = "after" in s_action or "post" in s_action

    if not (p_before and s_after):
        return None

    return DriftFinding(
        control_id=policy_field.control_id,
        drift_type=DriftType.STEP_OMISSION,
        expected=policy_field.required_action or "",
        observed=sop_field.required_action or "",
        evidence_span_policy=policy_field.evidence_span or (policy_field.required_action or ""),
        evidence_span_sop=sop_field.evidence_span or (sop_field.required_action or ""),
        severity=Verdict.BLOCK,
        confidence=0.88,
        remediation=(
            "Restore the mandatory pre-approval review gate: review must occur "
            "BEFORE onboarding approval, not after."
        ),
    )


# ---------------------------------------------------------------- #
# Matching
# ---------------------------------------------------------------- #
def _best_fuzzy_match(
    sop_field: OKRField,
    policy_fields: List[OKRField],
    min_score: int = 85,
) -> Optional[OKRField]:
    """Find the single best fuzzy match for a SOP control among policy controls."""
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return None

    sop_key = _norm(sop_field.control_id)

    best: Optional[OKRField] = None
    best_score = -1
    for pf in policy_fields:
        score = fuzz.token_set_ratio(sop_key, _norm(pf.control_id))
        
        if score > best_score:
            best_score = score
            best = pf

    if best_score >= min_score:
        return best
    return None


def match_sop_to_policy(
    policy_fields: List[OKRField],
    sop_fields: List[OKRField],
) -> Tuple[List[Tuple[OKRField, OKRField]], List[OKRField]]:
    """
    Match SOP controls to policy controls.
    Order of preference:
      1) exact normalised control_id
      2) best fuzzy match above threshold

    Returns:
      (matches, unmatched_policy_controls)
      where unmatched_policy_controls are policy controls that NO SOP
      control mapped to — i.e. controls the SOP appears to have dropped.
    """
    matches: List[Tuple[OKRField, OKRField]] = []
    matched_policy_ids: set[int] = set()  # track by python id(), policies aren't hashable

    # group policies by normalised id for O(1) lookup
    by_id: dict[str, list[OKRField]] = {}
    for pf in policy_fields:
        by_id.setdefault(_norm(pf.control_id), []).append(pf)

    for sop_f in sop_fields:
        sop_key = _norm(sop_f.control_id)
        candidates = by_id.get(sop_key, [])

        chosen: Optional[OKRField] = None
        if candidates:
            # Take the first exact match by ID
            chosen = candidates[0]

        if chosen is None:
            chosen = _best_fuzzy_match(sop_f, policy_fields)
            if chosen is not None:
                logger.info(
                    "Fuzzy-matched SOP control %s -> policy %s",
                    sop_f.control_id, chosen.control_id,
                )

        if chosen is None:
            logger.warning(
                "SOP control %s has no matching policy control",
                sop_f.control_id,
            )
            continue

        matched_policy_ids.add(id(chosen))
        matches.append((chosen, sop_f))

    unmatched_policies = [
        pf for pf in policy_fields if id(pf) not in matched_policy_ids
    ]

    return matches, unmatched_policies


# ---------------------------------------------------------------- #
# Override
# ---------------------------------------------------------------- #
def apply_override(
    finding: DriftFinding,
    sop_field: OKRField,
    override_fields: List[OKRField],
) -> bool:
    """
    Return True if the finding's observed value matches an approved
    override for the same control. 
    """
    for ov in override_fields:
        if _norm(ov.control_id) != _norm(finding.control_id):
            continue

        if finding.drift_type == DriftType.THRESHOLD_DRIFT:
            if (
                ov.threshold_value is not None
                and sop_field.threshold_value is not None
                and ov.threshold_value == sop_field.threshold_value
            ):
                return True

        if finding.drift_type == DriftType.TIME_WINDOW_DRIFT:
            if (
                ov.time_window_hours is not None
                and sop_field.time_window_hours is not None
                and ov.time_window_hours == sop_field.time_window_hours
            ):
                return True

        if finding.drift_type == DriftType.ROLE_DRIFT:
            ov_role = _norm_role(ov.required_actor)
            sop_role = _norm_role(sop_field.required_actor)
            if ov_role and sop_role and ov_role == sop_role:
                return True

    return False


# ---------------------------------------------------------------- #
# Top-level
# ---------------------------------------------------------------- #
def _missing_control_finding(policy_f: OKRField) -> DriftFinding:
    """Build a finding for a policy control that the SOP has dropped entirely."""
    label = policy_f.required_action or policy_f.trigger or policy_f.control_id
    evidence = policy_f.evidence_span or label
    return DriftFinding(
        control_id=policy_f.control_id,
        drift_type=DriftType.STEP_OMISSION,
        expected=label,
        observed="(control not present in SOP)",
        evidence_span_policy=evidence,
        # We have no SOP-side evidence — there's no span to point at because
        # the control is absent. Mirror the policy span so audit consumers
        # can still link back to the source.
        evidence_span_sop="(no matching control found in SOP)",
        severity=Verdict.BLOCK,
        confidence=0.85,
        remediation=(
            f"The SOP appears to omit policy control '{policy_f.control_id}'. "
            "Either add a corresponding step to the SOP, or attach an approved "
            "override authorising its removal."
        ),
    )


def run_conformance_check(
    policy_fields: List[OKRField],
    sop_fields: List[OKRField],
    override_fields: Optional[List[OKRField]] = None,
) -> List[DriftFinding]:
    """
    Compare SOP fields against policy fields.
    Apply overrides where applicable (downgrading BLOCK -> WARN).
    Returns only findings with evidence spans and confidence >= 0.4.
    """
    override_fields = override_fields or []
    findings: List[DriftFinding] = []

    matches, unmatched_policies = match_sop_to_policy(policy_fields, sop_fields)

    for policy_f, sop_f in matches:
        checks = [
            check_threshold(policy_f, sop_f),
            check_role(policy_f, sop_f),
            check_time_window(policy_f, sop_f),
            check_step_omission(policy_f, sop_f),
        ]
        for finding in checks:
            if finding is None:
                continue
            if not finding.evidence_span_policy or not finding.evidence_span_sop:
                continue
            if finding.confidence < 0.4:
                continue

            # Now simply calls apply_override
            if apply_override(finding, sop_f, override_fields):
                finding.severity = Verdict.WARN
                finding.remediation = (
                    "Change matches an approved override. "
                    "Attach the override reference before publishing."
                )

            findings.append(finding)

    # --- Dropped controls: policy controls with no SOP equivalent --- #
    # These are the most dangerous drift type and were previously invisible.
    for policy_f in unmatched_policies:
        missing = _missing_control_finding(policy_f)
        # Allow overrides to suppress a missing-control finding too —
        # a compliance team can explicitly authorise removing a control.
        # We treat presence of an override with the same control_id as
        # the signal, since there's no SOP-side value to compare against.
        override_match = any(
            _norm(ov.control_id) == _norm(policy_f.control_id)
            for ov in override_fields
        )
        if override_match:
            missing.severity = Verdict.WARN
            missing.remediation = (
                f"Control '{policy_f.control_id}' is absent from the SOP, "
                "but an override with this control_id is attached. Verify "
                "the override authorises full removal before publishing."
            )
        findings.append(missing)

    return findings


def compute_verdict(findings: List[DriftFinding]) -> Verdict:
    """Derive overall verdict from list of findings."""
    if not findings:
        return Verdict.PASS
    severities = {f.severity for f in findings}
    if Verdict.BLOCK in severities:
        return Verdict.BLOCK
    if Verdict.WARN in severities:
        return Verdict.WARN
    return Verdict.PASS