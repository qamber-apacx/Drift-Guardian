# DriftGuardian

**Pre-Deployment Governance Gate for AI-Generated SOPs.**

DriftGuardian compares an
AI-produced Standard Operating Procedure against the authoritative policy it was
supposed to follow and reports exactly where it has *drifted* — weakened a
control, dropped a required step, contradicted a threshold, or invented a rule
that no source mandates. It returns a structured `PASS` / `WARN` / `BLOCK`
verdict with per-finding remediation and a tamper-evident audit record.

The reference domain is **KYC/AML governance**: as teams use LLMs to draft
onboarding and monitoring procedures, those drafts can silently relax binding
requirements (retention periods, reporting windows, due-diligence steps).
DriftGuardian is the gate that catches that before publication.

Built as an [OPEA](https://opea.dev) GenAIExamples-style application: a thin
FastAPI **megaservice gateway** orchestrates a document loader and an
OpenAI-compatible **LLM microservice**, with a Flask UI on top.

![Architecture](assets/img/architecture.svg)

## How it works

1. You upload up to three documents: an authoritative **Global policy**, the
   **SOP** under review, and optionally a **Regional override**.
2. The loader extracts plain text from PDF / DOCX / TXT / Markdown.
3. **Stage 1 — extract requirements.** The LLM reads the global policy and
   override and produces a checklist of *effective* requirements as typed,
   atomic obligations (each tagged `numeric`, `role`, `sequence`, `time_window`,
   `frequency`, `presence`, or `scope`), applying the rule that a regional
   override supersedes the global value where it specifies one.
4. **Stage 2 — verify against the checklist.** The LLM checks the whole SOP
   against the requirements. By default it runs **one focused call per
   requirement** (`DRIFT_REQ_CHECK=per_requirement`), which maximises recall on
   subtle drifts like a changed approver or a control moved from before to after
   onboarding. Set `DRIFT_REQ_CHECK=batched` to check the whole list in one call
   (faster, lower recall). Drift is reported only for violated requirements.
5. Findings are scored and reduced to a single verdict, with a remediation list,
   a **requirement-coverage** summary (what was checked vs. what drifted), an
   audit record, and Jira/Confluence payloads.

If requirement extraction yields nothing (e.g. a vague, aspirational policy), the
engine falls back to a single whole-document comparison so the SOP is still
analysed. Set `DRIFT_MODE=one_shot` to force that path. If the LLM is unreachable,
the engine fails loudly rather than returning a false `PASS`.

## Severity model

A **regional override is the higher authority** for the values it covers, so the
*effective requirement* for any point is the regional value when the override
specifies one, otherwise the global value. Severity is decided by **whether an
effective requirement is violated — not by where that requirement lives.**

| Verdict | Meaning |
| --- | --- |
| `BLOCK` | The SOP violates an effective requirement (contradiction / weakened / omission). A hard failure **whether the requirement came from the global policy or a regional override** — violating an effective regional override blocks just like violating the global policy. |
| `WARN` | The SOP introduces a requirement absent from every source (`added`), or the finding is advisory rather than a violation. Worth review, not a hard failure. |
| `PASS` | No meaningful conflicts. |

The LLM is asked to emit a `severity` directly; a valid value is trusted.
Otherwise the engine derives it from `change_type`. The `scope` field
(`global` / `regional`) is retained for reporting only and never softens a
violation.

## Requirements

**Software**
- Docker + Docker Compose (for the container path), or Python 3.11+ (for the native path)
- An OpenAI-compatible LLM server. The defaults target a local [Ollama](https://ollama.com); the production path uses Text Generation Inference (TGI).
- **Recommended model: `qwen2.5:14b`** (Qwen2.5-14B-Instruct). The larger model gives
  noticeably better recall on subtle drifts — changed approvers, moved controls — which
  is what the multi-drift demo relies on, so it is the model used for the documented
  results. The lighter **`qwen2.5:7b`** is the zero-config fallback for modest hardware.
  Select either with `LLM_MODEL_ID`.

**Hardware**
- `qwen2.5:14b` on Ollama: ~9 GB disk for the weights and ~16 GB RAM free is comfortable.
  A GPU is recommended; on CPU a check takes longer (tens of seconds), and per-requirement
  Stage 2 makes one model call per requirement, so latency scales with the number of
  requirements.
- `qwen2.5:7b`: lighter — ~5 GB disk, ~6–8 GB RAM, runs CPU-only at a few seconds per check.
- To trade recall for speed on either model, set `DRIFT_REQ_CHECK=batched` (one Stage 2
  call total instead of one per requirement).

## Quick start

You need a running LLM that exposes an OpenAI-compatible chat endpoint. The
defaults target a local [Ollama](https://ollama.com) server.

### Option A — one-click script

```bash
./deploy.sh            # native: venv + local Ollama, starts backend + UI
./deploy.sh --docker   # containers: docker compose up --build
```

Then open <http://localhost:5173>.

### Option B — Docker Compose

```bash
docker compose up -d --build
docker compose exec ollama ollama pull qwen2.5:14b   # first run only
# UI:  http://localhost:5173      API: http://localhost:8888
```

### Option C — Make targets (manual, two terminals)

```bash
make model      # pull the LLM into local Ollama (first run)
make backend    # terminal 1 — FastAPI gateway on :8888
make ui         # terminal 2 — Flask UI on :5173
make test       # run the unit suite
make help       # list every target
```

> **Changing the UI port.** The UI port lives in one place. Run any target with
> an override, e.g. `make ui UI_PORT=8501` or `UI_PORT=8501 ./deploy.sh`, and it
> propagates to the app, Compose, and the script.

## Try the bundled demo

**Headline case — APAC KYC ([`data/kyc_apac_demo/`](data/kyc_apac_demo/)).** One
AI-generated SOP with four combined critical drifts against an APAC regional
override: the EDD risk threshold is weakened (approved 85 → 90), the approval
authority is changed (MLRO → onboarding agent), pre-approval is downgraded to a
post-onboarding checkpoint, and the escalation review window is stretched (24h →
72h). **Expected verdict: `BLOCK`.**

```bash
curl -s -X POST http://localhost:8888/v1/drift_check \
  -F "global_doc=@data/kyc_apac_demo/global_baseline_kyc_policy.md" \
  -F "regional_doc=@data/kyc_apac_demo/apac_policy_bundle.md" \
  -F "sop_doc=@data/kyc_apac_demo/sop_drift_combo_critical.md" | python3 -m json.tool
```

**Verdict matrix — KYC set ([`data/demo/`](data/demo/)).** Four SOPs exercising
every outcome:

| SOP | Expected |
| --- | --- |
| `sop_pass.md` | `PASS` |
| `sop_block_global.md` | `BLOCK` (weakens global-only rules) |
| `sop_block_regional.md` | `BLOCK` (violates the regional override) |
| `sop_warn_added.md` | `WARN` (adds a rule absent from all sources) |

See [`data/demo/README.md`](data/demo/README.md) and
[`data/kyc_apac_demo/README.md`](data/kyc_apac_demo/README.md) for the
finding-by-finding breakdown.

### Expected output

A check returns JSON with the `verdict`, evidence-backed `findings` (each with
`source_says`, `sop_says`, `severity`, `remediation`, `explanation`), an ordered
`remediation` list, a `coverage` summary (which effective requirements were
checked and which drifted), a tamper-evident `audit` record, and ready-to-POST
`jira_payload` / `confluence_payload` objects for WARN/BLOCK results (`null` on
PASS). The full shape is shown under [API](#api). In the UI, the same result
renders as a verdict banner, per-finding cards, a coverage table, and an audit
footer.

## API

`GET /v1/health` → `{"status": "ok"}`

`POST /v1/drift_check` (multipart form)

| Field | Required | Description |
| --- | --- | --- |
| `global_doc` | yes | Authoritative global policy |
| `sop_doc` | yes | AI-generated SOP under review |
| `regional_doc` | no | Regional override (higher authority where it applies) |

Example response (a regional-override violation, abbreviated):

```json
{
  "verdict": "BLOCK",
  "summary": "Found 1 drift finding(s): 1 blocking, 0 warning(s).",
  "findings": [
    {
      "point": "SAR filing window",
      "source_says": "File SARs within 15 days (UK override).",
      "sop_says": "Reports suspicious activity within 30 days.",
      "scope": "regional",
      "change_type": "weakened",
      "severity": "BLOCK",
      "remediation": "Change the SAR filing window to within 15 days to match the UK override.",
      "explanation": "A slower window breaches the binding regional deadline."
    }
  ],
  "counts": { "block": 1, "warn": 0 },
  "sections_analyzed": 1,
  "mode": "two_stage",
  "coverage": {
    "requirements_extracted": 5,
    "requirements_drifted": 1,
    "requirements_satisfied": 4,
    "requirements": [
      { "id": "R3", "topic": "SAR filing window", "kind": "time_window", "source": "regional", "status": "drifted" }
    ]
  },
  "remediation": [
    { "point": "SAR filing window", "severity": "BLOCK", "action": "Change the SAR filing window to within 15 days to match the UK override." }
  ],
  "audit": {
    "engine_version": "1.2.0",
    "model": "qwen2.5:14b",
    "mode": "two_stage",
    "timestamp_utc": "2026-06-04T09:30:00+00:00",
    "verdict": "BLOCK",
    "sections_analyzed": 1,
    "requirements_checked": 5,
    "documents": {
      "global":   { "chars": 1111, "sha256": "..." },
      "regional": { "chars": 705,  "sha256": "..." },
      "sop":      { "chars": 1283, "sha256": "..." }
    },
    "findings_count": 1,
    "counts": { "block": 1, "warn": 0 }
  },
  "jira_payload": {
    "fields": {
      "project": { "key": "COMP" },
      "issuetype": { "name": "Bug" },
      "priority": { "name": "Highest" },
      "summary": "DriftGuardian BLOCK: 1 blocking / 0 warning finding(s)",
      "description": "DriftGuardian verdict: BLOCK ...",
      "labels": ["driftguardian", "compliance-drift", "block"]
    }
  },
  "confluence_payload": {
    "type": "page",
    "title": "Drift Report — BLOCK — 2026-06-04T09:30:00+00:00",
    "space": { "key": "COMP" },
    "body": { "storage": { "value": "<p>...</p>", "representation": "storage" } }
  }
}
```

The `remediation` array is the ordered, actionable fix list (worst first). The
`audit` block fingerprints every input with a SHA-256 so a stored report can be
tied back to the exact documents that produced it. The `jira_payload` /
`confluence_payload` are ready-to-POST shapes for enterprise workflows — illustrative,
not live integrations — and are `null` when the verdict is `PASS`.

## Configuration

| Variable | Default | Used by | Purpose |
| --- | --- | --- | --- |
| `MEGA_SERVICE_PORT` | `8888` | backend | Gateway listen port |
| `LLM_ENDPOINT` | `http://localhost:9000/v1/chat/completions` | engine | OpenAI-compatible chat endpoint |
| `LLM_MODEL_ID` | `qwen2.5:7b` | engine | Model name passed to the LLM server (set to `qwen2.5:14b` for best recall) |
| `DRIFT_MODE` | `two_stage` | engine | `two_stage` (extract-then-check) or `one_shot` |
| `DRIFT_REQ_CHECK` | `per_requirement` | engine | Stage 2 strategy: `per_requirement` (best recall) or `batched` (faster) |
| `LLM_TIMEOUT` | `600` | engine | Per-request timeout (seconds) |
| `DRIFT_MAX_SECTION_CHARS` | `2500` | engine | Max characters per SOP section (chunked mode) |
| `DRIFT_MAX_WHOLE_DOC_CHARS` | `12000` | engine | SOPs up to this size are analysed whole (one pass) |
| `UI_PORT` | `5173` | UI | Flask UI listen port |
| `BACKEND_URL` | `http://localhost:8888` | UI | Where the UI reaches the gateway |

The `make` / `deploy.sh` / Compose defaults point `LLM_ENDPOINT` at Ollama
(`http://localhost:11434/v1/chat/completions`).

## Project structure

```
.
├── policydriftcheck.py              # FastAPI megaservice gateway
├── drift/
│   ├── engine.py                    # Drift analysis, severity model, payloads
│   └── loader.py                    # PDF / DOCX / TXT / MD text extraction
├── ui/                              # Flask presentation layer
│   ├── app.py
│   ├── templates/  ·  static/
│   └── Dockerfile
├── data/
│   ├── demo/                        # KYC verdict-matrix demo + eval_cases.json
│   └── kyc_apac_demo/               # APAC headline BLOCK demo
├── evaluation.py                    # Eval harness (accuracy / latency / unsupported)
├── tests/                           # Unit tests (LLM stubbed) + e2e shell test
├── docker-compose.yml               # One-click local stack (Ollama + backend + UI)
├── deploy.sh                        # One-click deploy script
├── Makefile                         # backend / ui / test / eval / deploy / ...
├── Dockerfile                       # Backend image
├── docker_compose/intel/cpu/xeon/   # Production Xeon + TGI deployment
└── docker_image_build/build.yaml
```

## Evaluation

A small harness measures the engine against labelled cases so prompt or model
changes can be judged by numbers rather than guesswork:

```bash
make eval        # or: python evaluation.py
```

It runs every case in `data/demo/eval_cases.json` (add your own) and reports:

- **Verdict accuracy** — did the PASS/WARN/BLOCK verdict match the label?
- **Latency** — wall-clock seconds per case (mean / min / max).
- **Unsupported-claim rate** — a heuristic proxy for hallucinated findings: the
  fraction of findings whose claim about the SOP is not grounded in the SOP text
  (e.g. an "omission" of something that is actually present). It is a lexical
  smoke detector, not a perfect judge.

`--json report.json` writes the full report; `--min-accuracy 0.9` makes it exit
non-zero below a threshold, for CI. The harness needs a reachable LLM, the same
as the backend.

## Testing

```bash
make test                              # or: python -m pytest tests/test_drift_logic.py -v
```

The unit tests stub the LLM call, so they run with no model server. A full
end-to-end test that builds the images and submits a live request is in
[`tests/test_compose_on_xeon.sh`](tests/test_compose_on_xeon.sh).

## Production deployment

For a production-style deployment on Intel Xeon (CPU) serving the model with
Text Generation Inference (TGI), use the manifest under
[`docker_compose/intel/cpu/xeon/`](docker_compose/intel/cpu/xeon/) and its
[README](docker_compose/intel/cpu/xeon/README.md). The root `docker-compose.yml`
above is the simpler, token-free path intended for local evaluation.

## Limitations

- **Findings are LLM judgments.** Drift detection is performed by the model, so
  results are not perfectly deterministic and depend on the model used. Temperature
  is set to 0 for stability, but an occasional missed or over-flagged finding on a
  genuinely ambiguous point is possible. Treat output as decision support, not a
  certified legal compliance ruling.
- **Best on concrete, rule-based policies.** Documents with specific values
  (thresholds, time windows, named roles) are judged far more reliably than broad,
  aspirational corporate policies, where "what counts as a requirement" is fuzzier.
- **No live integrations.** The Jira/Confluence payloads are illustrative JSON; no
  data is sent anywhere. The audit record is returned in the response, not persisted
  to a store.
- **Single-node demo scope.** The defaults target one machine and a 7B model.
  Very large policy sets and high throughput would need the policy-side chunking and
  a larger served model.
- **Unsupported-claim rate is heuristic.** The evaluation metric is a lexical proxy
  for hallucinated findings, not a perfect judge.

## Third-party notes

- **Models:** `qwen2.5:14b` (recommended) and `qwen2.5:7b`, and the TGI
  `Qwen/Qwen2.5-14B-Instruct` / `Qwen/Qwen2.5-7B-Instruct`, are third-party models
  under their own licenses; review the Qwen license before production use.
- **Runtimes:** [Ollama](https://ollama.com) and
  [Text Generation Inference](https://github.com/huggingface/text-generation-inference)
  are used as the LLM servers under their respective licenses.
- **Python libraries:** FastAPI, Flask, requests, pypdf, and python-docx, each under
  its own OSI-approved license (see `requirements.txt` / `ui/requirements.txt`).
- This project follows the OPEA GenAIExamples structure; OPEA components are
  Apache-2.0.

## License

Apache-2.0. See [LICENSE](LICENSE). Third-party models, runtimes, and libraries
remain under their own licenses as noted above.