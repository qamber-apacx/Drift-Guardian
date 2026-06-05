# APAC KYC headline demo

A single, high-impact case showing DriftGuardian catching four combined critical
drifts in one AI-generated SOP.

- **Global baseline:** `global_baseline_kyc_policy.md` — EDD threshold 80, MLRO approval, pre-approval before onboarding, 24h escalation review.
- **Regional override:** `apac_policy_bundle.md` — the higher authority for APAC; sets the **approved EDD threshold to 85** and reaffirms the MLRO, pre-approval, and 24h controls.
- **SOP under review:** `sop_drift_combo_critical.md` — the AI-generated draft.

## The drift story → expected verdict: BLOCK
| # | Control | Effective requirement (APAC) | SOP says | Type |
| --- | --- | --- | --- | --- |
| 1 | EDD risk threshold | 85 | 90 (weaker — fewer customers flagged) | weakened |
| 2 | Approval authority | MLRO | onboarding agent | contradiction |
| 3 | Pre-approval control | pre-approval **before** onboarding | post-onboarding **checkpoint** | weakened |
| 4 | Escalation review time | within 24 hours | within 72 hours | weakened |

Each is a violation of an *effective* APAC requirement, so all four are hard
**BLOCK** findings. The result also carries the remediation list and the
Jira/Confluence payloads.

## Run it
```bash
curl -s -X POST http://localhost:8888/v1/drift_check \
  -F "global_doc=@data/kyc_apac_demo/global_baseline_kyc_policy.md" \
  -F "regional_doc=@data/kyc_apac_demo/apac_policy_bundle.md" \
  -F "sop_doc=@data/kyc_apac_demo/sop_drift_combo_critical.md" | python3 -m json.tool
```
Or upload the three files in the UI at `http://localhost:5173`.
