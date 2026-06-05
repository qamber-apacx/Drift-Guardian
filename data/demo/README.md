# Demo data
> **Synthetic benchmark note:** These documents are synthetic demo files created for the DriftGuardian submission. They are not real company policies, regulatory guidance, or legal compliance advice. The thresholds, time limits, roles, and control requirements are intentionally designed benchmark examples for testing policy-drift detection and governance-gate behaviour.

Ready-to-run documents for exercising DriftGuardian end to end. These files are
intentionally committed to the repo (see the negation rules in `.gitignore`) so
a reviewer can run the app with **zero setup beyond a model server**.

## Source documents
| File | Role |
| --- | --- |
| `global_policy.md` | Authoritative global KYC/AML policy |
| `regional_override.md` | UK (FCA) override — higher authority where it applies |

The override tightens three values: SAR filing → 15 days, risk review → 12 months,
and PEP onboarding → senior-management approval. Everything else inherits global.

## SOPs under review (the test matrix)
| SOP file | Run with | Expected verdict | Demonstrates |
| --- | --- | --- | --- |
| `sop_pass.md` | global + regional | **PASS** | Honours every effective requirement |
| `sop_block_global.md` | global + regional | **BLOCK** | Weakens global-only rules (3y retention, password instead of 2FA) |
| `sop_block_regional.md` | global + regional | **BLOCK** | Violates the effective **regional override** — the case the severity fix corrects |
| `sop_warn_added.md` | global + regional | **WARN** | Adds an obligation found in no source (scope creep) |

`sop_block_regional.md` is the headline case: it falls back to the *global*
values for requirements the override has tightened. Because the override is the
higher authority, those global values are no longer the effective requirement, so
the SOP is violating the effective requirement → **BLOCK** (not WARN).

## Example payloads
| File | What it is |
| --- | --- |
| `payloads/remediation_payload.json` | The ordered, actionable fix list for `sop_block_regional.md` |
| `payloads/audit_payload.json` | The tamper-evident audit record (model, verdict, per-document SHA-256) |

## How to run a check
With the stack running (see the root `README.md`):

```bash
# Regional-override BLOCK case
curl -s -X POST http://localhost:8888/v1/drift_check \
  -F "global_doc=@data/demo/global_policy.md" \
  -F "regional_doc=@data/demo/regional_override.md" \
  -F "sop_doc=@data/demo/sop_block_regional.md" | python3 -m json.tool
```

Or open the UI at `http://localhost:5173`, upload the global policy, the regional
override, and one of the SOPs.
