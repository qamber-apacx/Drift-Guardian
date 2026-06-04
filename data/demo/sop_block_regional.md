# SOP: Customer Onboarding & Monitoring (BLOCK — regional-override drift example)

Use with: global_policy.md + regional_override.md.
Expected verdict: **BLOCK**.

This is the case the severity fix is about. The SOP falls back to the GLOBAL
values for requirements that the UK regional override has tightened. Because the
override is the higher authority, those global values are NO LONGER the effective
requirement — so the SOP is *violating the effective requirement*. That is a hard
**BLOCK**, not a soft warning. (The earlier scope-only logic wrongly treated
regional-scope findings as WARN.)

## Onboarding
- We confirm the customer's identity before activation.
- High-risk customers go through Enhanced Due Diligence, including a source-of-funds check.
- PEPs are onboarded after standard Enhanced Due Diligence.   <!-- override requires senior-management approval -->

## Monitoring & Reporting
- Suspicious activity is reported within **30 days** of detection.   <!-- effective requirement is 15 days -->
- Each customer's risk rating is reviewed at least once every **24 months**.   <!-- effective requirement is 12 months -->

## Records
- KYC records are kept for 7 years after an account closes.
- Pulling up a customer's KYC records requires two-factor authentication.
