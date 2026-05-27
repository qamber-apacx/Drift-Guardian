"""
Generate Jira + Confluence payloads for findings.

Improvements:
  - Use datetime.now(timezone.utc) (datetime.utcnow is deprecated in 3.12+).
  - HTML-escape every value inserted into the Confluence storage XHTML so
    a policy that contains "<" or "&" doesn't break the page or open an
    injection hole.
"""
from datetime import datetime, timezone
from html import escape
from typing import List

from schemas import DriftFinding, Verdict


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def generate_jira_ticket(
    sop_filename: str,
    verdict: Verdict,
    findings: List[DriftFinding],
) -> dict:
    """Realistic Jira REST API payload for the COMP project."""
    if not findings:
        return {}

    description_lines = [
        f"DriftGuardian detected unauthorized policy divergence in: *{sop_filename}*",
        "",
        "*Detected Issues:*",
    ]
    for f in findings:
        description_lines.append(
            f"- [{f.severity.value}] "
            f"{f.drift_type.value.replace('_', ' ').title()} "
            f"in {f.control_id}\n"
            f"  Expected: {f.expected}\n"
            f"  Observed: {f.observed}\n"
            f"  Remediation: {f.remediation}"
        )

    return {
        "fields": {
            "project": {"key": "COMP"},
            "summary": (
                f"[DriftGuardian] {verdict.value}: "
                f"Unauthorized policy divergence in {sop_filename}"
            ),
            "description": "\n".join(description_lines),
            "issuetype": {"name": "Compliance Task"},
            "priority": {
                "name": "High" if verdict == Verdict.BLOCK else "Medium"
            },
            "labels": ["driftguardian", "kyc-governance", verdict.value.lower()],
            "customfield_drift_types": [f.drift_type.value for f in findings],
            "customfield_sop_file": sop_filename,
            "customfield_detected_at": _now_iso(),
        }
    }


def generate_confluence_audit_log(
    sop_filename: str,
    verdict: Verdict,
    findings: List[DriftFinding],
) -> dict:
    """Confluence REST API page payload as audit log. All values HTML-escaped."""
    timestamp = _now_iso()

    if findings:
        rows = "".join(
            (
                f"<tr>"
                f"<td>{escape(f.control_id)}</td>"
                f"<td>{escape(f.drift_type.value.replace('_', ' ').title())}</td>"
                f"<td>{escape(f.expected)}</td>"
                f"<td>{escape(f.observed)}</td>"
                f"<td><strong>{escape(f.severity.value)}</strong></td>"
                f"<td>{escape(f.remediation)}</td>"
                f"</tr>"
            )
            for f in findings
        )
        table = (
            "<table>"
            "<thead><tr>"
            "<th>Control ID</th><th>Drift Type</th><th>Expected</th>"
            "<th>Observed</th><th>Severity</th><th>Remediation</th>"
            "</tr></thead>"
            f"<tbody>{rows}</tbody>"
            "</table>"
        )
    else:
        table = "<p>No drift findings detected.</p>"

    body = (
        "<h2>DriftGuardian Audit Log</h2>"
        f"<p><strong>SOP File:</strong> {escape(sop_filename)}</p>"
        f"<p><strong>Validated At:</strong> {escape(timestamp)}</p>"
        f"<p><strong>Overall Verdict:</strong> {escape(verdict.value)}</p>"
        "<h3>Findings</h3>"
        f"{table}"
    )

    return {
        "type": "page",
        "title": (
            f"[DriftGuardian Audit] {sop_filename} — {timestamp[:10]}"
        ),
        "space": {"key": "COMP"},
        "body": {
            "storage": {
                "value": body,
                "representation": "storage",
            }
        },
        "metadata": {
            "labels": [
                {"name": "driftguardian"},
                {"name": "kyc-audit"},
                {"name": verdict.value.lower()},
            ]
        },
    }