"""
DriftGuardian — Streamlit UI.

A demo front-end for the DriftGuardian governance API. Calls the FastAPI
backend's /upload-policy, /upload-sop, and /validate endpoints.

Run with:
    streamlit run ui/streamlit_app.py

Configure the backend URL via the DRIFTGUARDIAN_API env var, otherwise
defaults to http://localhost:8000.
"""
from __future__ import annotations

import os
import json
from typing import Optional

import httpx
import streamlit as st

API_URL = os.environ.get("DRIFTGUARDIAN_API", "http://localhost:8000").rstrip("/")
REQUEST_TIMEOUT_S = 180.0


# ============================================================== #
# Page config + global styling
# ============================================================== #
st.set_page_config(
    page_title="DriftGuardian",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS — dark editorial aesthetic, monospace-forward.
st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,800&family=Inter:wght@400;500;600&display=swap');

      :root {
        --bg: #0c0d10;
        --bg-panel: #14161b;
        --bg-panel-alt: #1a1d24;
        --border: #2a2e38;
        --border-strong: #3a3f4d;
        --ink: #e8e6e1;
        --ink-dim: #9aa0ad;
        --ink-mute: #6b7280;
        --accent: #f5d061;
        --pass: #5db075;
        --warn: #e8a838;
        --block: #d94c5e;
      }

      .stApp {
        background: var(--bg);
        color: var(--ink);
      }

      /* Main container width */
      .block-container {
        padding-top: 2rem;
        max-width: 1200px;
      }

      /* Display font for titles */
      h1, h2, h3 {
        font-family: 'Fraunces', Georgia, serif !important;
        font-weight: 600 !important;
        letter-spacing: -0.02em;
        color: var(--ink) !important;
      }
      h1 { font-weight: 800 !important; }

      /* Body / mono */
      .stMarkdown, .stMarkdown p, label, .stTextInput, .stTextArea {
        font-family: 'Inter', -apple-system, sans-serif;
      }
      code, pre, .stCode, .stJson {
        font-family: 'JetBrains Mono', monospace !important;
      }

      /* Sidebar */
      [data-testid="stSidebar"] {
        background: var(--bg-panel);
        border-right: 1px solid var(--border);
      }
      [data-testid="stSidebar"] * {
        color: var(--ink-dim);
      }
      [data-testid="stSidebar"] h2,
      [data-testid="stSidebar"] h3 {
        color: var(--ink) !important;
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.7rem !important;
        text-transform: uppercase;
        letter-spacing: 0.15em;
        font-weight: 700 !important;
      }

      /* Hero header */
      .hero {
        padding: 1.5rem 0 2rem 0;
        border-bottom: 1px solid var(--border);
        margin-bottom: 2rem;
      }
      .hero-mark {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.7rem;
        letter-spacing: 0.25em;
        color: var(--accent);
        text-transform: uppercase;
        margin-bottom: 0.5rem;
      }
      .hero-title {
        font-family: 'Fraunces', serif;
        font-size: 3.2rem;
        font-weight: 800;
        line-height: 1;
        margin: 0;
        letter-spacing: -0.04em;
      }
      .hero-subtitle {
        font-family: 'Inter', sans-serif;
        color: var(--ink-dim);
        margin-top: 0.75rem;
        font-size: 1rem;
        max-width: 560px;
      }

      /* Tabs */
      .stTabs [data-baseweb="tab-list"] {
        gap: 0;
        border-bottom: 1px solid var(--border);
        background: transparent;
      }
      .stTabs [data-baseweb="tab"] {
        background: transparent !important;
        border: none !important;
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.75rem !important;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        color: var(--ink-mute) !important;
        padding: 1rem 1.5rem !important;
      }
      .stTabs [aria-selected="true"] {
        color: var(--ink) !important;
        border-bottom: 2px solid var(--accent) !important;
      }

      /* Buttons */
      .stButton > button, .stDownloadButton > button {
        background: var(--ink) !important;
        color: var(--bg) !important;
        border: none !important;
        border-radius: 2px !important;
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.75rem !important;
        font-weight: 700 !important;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        padding: 0.65rem 1.25rem !important;
        transition: all 0.15s ease;
      }
      .stButton > button:hover {
        background: var(--accent) !important;
        color: var(--bg) !important;
      }
      .stButton > button:disabled {
        background: var(--border) !important;
        color: var(--ink-mute) !important;
      }

      /* File uploaders */
      [data-testid="stFileUploader"] {
        background: var(--bg-panel);
        border: 1px dashed var(--border-strong);
        border-radius: 2px;
        padding: 1rem;
      }
      [data-testid="stFileUploader"] section {
        background: transparent;
      }

      /* Text inputs / textareas */
      .stTextInput input, .stTextArea textarea, .stSelectbox > div > div {
        background: var(--bg-panel) !important;
        border: 1px solid var(--border) !important;
        border-radius: 2px !important;
        color: var(--ink) !important;
        font-family: 'JetBrains Mono', monospace !important;
      }

      /* Verdict badge — used in results panel */
      .verdict-badge {
        display: inline-block;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 0.2em;
        text-transform: uppercase;
        padding: 0.4rem 0.9rem;
        border-radius: 2px;
        border: 1px solid currentColor;
      }
      .verdict-pass  { color: var(--pass);  background: rgba(93,176,117,0.08); }
      .verdict-warn  { color: var(--warn);  background: rgba(232,168,56,0.08); }
      .verdict-block { color: var(--block); background: rgba(217,76,94,0.10); }

      .verdict-hero {
        font-family: 'Fraunces', serif;
        font-size: 4rem;
        font-weight: 800;
        line-height: 1;
        letter-spacing: -0.04em;
      }
      .verdict-hero-pass  { color: var(--pass); }
      .verdict-hero-warn  { color: var(--warn); }
      .verdict-hero-block { color: var(--block); }

      /* Status dot */
      .dot {
        display: inline-block;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        margin-right: 0.5rem;
        vertical-align: middle;
      }
      .dot-on  { background: var(--pass); box-shadow: 0 0 8px var(--pass); }
      .dot-off { background: var(--block); }

      /* Finding card */
      .finding-card {
        background: var(--bg-panel);
        border-left: 3px solid var(--border-strong);
        padding: 1.5rem;
        margin-bottom: 1rem;
      }
      .finding-card.block { border-left-color: var(--block); }
      .finding-card.warn  { border-left-color: var(--warn); }

      .finding-header {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        margin-bottom: 1rem;
      }
      .finding-control {
        font-family: 'JetBrains Mono', monospace;
        font-size: 1rem;
        font-weight: 700;
        color: var(--ink);
        letter-spacing: -0.01em;
      }
      .finding-type {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.7rem;
        color: var(--ink-mute);
        text-transform: uppercase;
        letter-spacing: 0.15em;
      }

      .compare {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 1rem;
        margin: 1rem 0;
      }
      .compare-cell {
        background: var(--bg);
        padding: 0.9rem 1rem;
        border-top: 1px solid var(--border);
      }
      .compare-label {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.65rem;
        color: var(--ink-mute);
        text-transform: uppercase;
        letter-spacing: 0.18em;
        margin-bottom: 0.4rem;
      }
      .compare-value {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.9rem;
        color: var(--ink);
      }
      .compare-value.observed {
        color: var(--block);
      }
      .compare-value.observed.warn {
        color: var(--warn);
      }

      .evidence {
        background: var(--bg);
        border-left: 2px solid var(--ink-mute);
        padding: 0.75rem 1rem;
        margin: 0.5rem 0;
        font-family: 'Fraunces', serif;
        font-style: italic;
        font-size: 0.95rem;
        color: var(--ink-dim);
        line-height: 1.5;
      }
      .evidence-label {
        font-family: 'JetBrains Mono', monospace;
        font-style: normal;
        font-size: 0.6rem;
        color: var(--ink-mute);
        text-transform: uppercase;
        letter-spacing: 0.2em;
        margin-bottom: 0.4rem;
        display: block;
      }
      .remediation {
        font-family: 'Inter', sans-serif;
        font-size: 0.9rem;
        color: var(--ink);
        background: var(--bg-panel-alt);
        padding: 0.75rem 1rem;
        margin-top: 1rem;
        border-top: 1px solid var(--border);
      }
      .remediation-label {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.6rem;
        color: var(--accent);
        text-transform: uppercase;
        letter-spacing: 0.2em;
        margin-bottom: 0.4rem;
      }

      /* Metric strip */
      .metric-strip {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 1px;
        background: var(--border);
        border: 1px solid var(--border);
        margin: 1.5rem 0;
      }
      .metric {
        background: var(--bg-panel);
        padding: 1.25rem;
      }
      .metric-label {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.6rem;
        color: var(--ink-mute);
        text-transform: uppercase;
        letter-spacing: 0.2em;
        margin-bottom: 0.5rem;
      }
      .metric-value {
        font-family: 'Fraunces', serif;
        font-size: 1.8rem;
        font-weight: 600;
        color: var(--ink);
        line-height: 1;
      }
      .metric-value.accent { color: var(--accent); }

      /* Doc card */
      .doc-card {
        background: var(--bg-panel);
        border: 1px solid var(--border);
        padding: 1rem 1.25rem;
        margin-top: 1rem;
      }
      .doc-card-id {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.7rem;
        color: var(--accent);
        letter-spacing: 0.1em;
      }
      .doc-card-name {
        font-family: 'Inter', sans-serif;
        font-weight: 600;
        font-size: 1rem;
        color: var(--ink);
        margin-top: 0.3rem;
      }
      .doc-card-meta {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.7rem;
        color: var(--ink-mute);
        margin-top: 0.5rem;
      }
      .doc-card-preview {
        font-family: 'Fraunces', serif;
        font-style: italic;
        color: var(--ink-dim);
        margin-top: 0.75rem;
        padding-top: 0.75rem;
        border-top: 1px solid var(--border);
        font-size: 0.85rem;
        line-height: 1.5;
      }

      /* Hide Streamlit chrome */
      #MainMenu, footer { visibility: hidden; }
      header[data-testid="stHeader"] { background: transparent; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================== #
# Session state init
# ============================================================== #
for k, default in {
    "policy_doc": None,       # dict from /upload-policy
    "sop_doc": None,          # dict from /upload-sop
    "policy_text_inline": "",
    "sop_text_inline": "",
    "result": None,           # dict from /validate
    "last_error": None,
}.items():
    if k not in st.session_state:
        st.session_state[k] = default


# ============================================================== #
# Backend client
# ============================================================== #
def check_health() -> tuple[bool, str]:
    try:
        r = httpx.get(f"{API_URL}/health", timeout=3.0)
        if r.status_code == 200:
            return True, "online"
        return False, f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, str(e).splitlines()[0][:80]


def upload_file(endpoint: str, file) -> dict:
    """POST a file to /upload-policy or /upload-sop."""
    files = {"file": (file.name, file.getvalue(), file.type or "application/octet-stream")}
    r = httpx.post(f"{API_URL}/{endpoint}", files=files, timeout=REQUEST_TIMEOUT_S)
    r.raise_for_status()
    return r.json()


def validate(body: dict) -> dict:
    """POST /validate."""
    r = httpx.post(f"{API_URL}/validate", json=body, timeout=REQUEST_TIMEOUT_S)
    if r.status_code >= 400:
        # Surface the backend's detail message rather than a generic httpx error
        detail = ""
        try:
            detail = r.json().get("detail", "")
        except Exception:  # noqa: BLE001
            detail = r.text[:300]
        raise RuntimeError(f"HTTP {r.status_code}: {detail}")
    return r.json()


# ============================================================== #
# Sidebar
# ============================================================== #
with st.sidebar:
    st.markdown("### System")
    online, status_msg = check_health()
    dot_class = "dot-on" if online else "dot-off"
    state_label = "ONLINE" if online else "OFFLINE"
    st.markdown(
        f'<div style="font-family:JetBrains Mono,monospace; font-size:0.8rem;">'
        f'<span class="dot {dot_class}"></span>{state_label}'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.caption(API_URL)
    if not online:
        st.caption(f"⚠ {status_msg}")

    st.markdown("---")
    st.markdown("### Region")
    region = st.selectbox(
        "Region",
        ["APAC", "EU", "GLOBAL"],
        index=0,
        label_visibility="collapsed",
    )

    st.markdown("---")
    st.markdown("### State")
    if st.session_state.policy_doc:
        st.caption(f"policy → {st.session_state.policy_doc['doc_id'][:8]}…")
    else:
        st.caption("policy → —")
    if st.session_state.sop_doc:
        st.caption(f"sop    → {st.session_state.sop_doc['doc_id'][:8]}…")
    else:
        st.caption("sop    → —")

    st.markdown("---")
    if st.button("Reset session", use_container_width=True):
        for k in ("policy_doc", "sop_doc", "policy_text_inline",
                  "sop_text_inline", "result", "last_error"):
            st.session_state[k] = None if "doc" in k or k in ("result", "last_error") else ""
        st.rerun()


# ============================================================== #
# Hero
# ============================================================== #
st.markdown(
    """
    <div class="hero">
      <div class="hero-mark">◆ Drift / Guardian — v1.1</div>
      <div class="hero-title">A governance gate<br/>for AI-drafted compliance.</div>
      <div class="hero-subtitle">
        Detects unauthorized divergence between AI-generated KYC SOPs and the
        approved policy hierarchy — threshold tampering, role swaps, time-window
        creep — before the document reaches production.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ============================================================== #
# Tabs
# ============================================================== #
tab_ingest, tab_validate, tab_findings, tab_payloads = st.tabs(
    ["01 ┄ Ingest", "02 ┄ Validate", "03 ┄ Findings", "04 ┄ Audit payloads"]
)


# -------------------------------------------------------------- #
# Tab 1: Ingest
# -------------------------------------------------------------- #
with tab_ingest:
    st.markdown("### Provide source documents")
    st.caption(
        "Upload a policy document and an AI-drafted SOP for comparison. "
        "Accepts PDF, DOCX, TXT, MD, HTML. Or paste raw text below."
    )

    col_pol, col_sop = st.columns(2, gap="large")

    with col_pol:
        st.markdown("#### Policy (source of truth)")
        pol_file = st.file_uploader(
            "Drop a policy document",
            type=["pdf", "docx", "txt", "md", "markdown", "html"],
            key="pol_uploader",
            label_visibility="collapsed",
        )
        if pol_file is not None:
            if st.button("Ingest policy", key="ingest_pol"):
                with st.spinner("Extracting text…"):
                    try:
                        st.session_state.policy_doc = upload_file("upload-policy", pol_file)
                        st.session_state.last_error = None
                    except Exception as e:  # noqa: BLE001
                        st.session_state.last_error = f"Policy upload failed: {e}"

        if st.session_state.policy_doc:
            d = st.session_state.policy_doc
            st.markdown(
                f"""
                <div class="doc-card">
                  <div class="doc-card-id">DOC_ID · {d['doc_id']}</div>
                  <div class="doc-card-name">{d['filename']}</div>
                  <div class="doc-card-meta">{d['chars']:,} chars · {d['content_type']}</div>
                  <div class="doc-card-preview">{d['preview'][:240]}…</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with st.expander("…or paste policy text inline"):
            st.session_state.policy_text_inline = st.text_area(
                "Policy text",
                value=st.session_state.policy_text_inline,
                height=160,
                label_visibility="collapsed",
                placeholder="Customers with risk_score >= 80 must be escalated to an L2 Compliance Analyst within 24 hours...",
            )

    with col_sop:
        st.markdown("#### SOP draft (under review)")
        sop_file = st.file_uploader(
            "Drop an SOP draft",
            type=["pdf", "docx", "txt", "md", "markdown", "html"],
            key="sop_uploader",
            label_visibility="collapsed",
        )
        if sop_file is not None:
            if st.button("Ingest SOP", key="ingest_sop"):
                with st.spinner("Extracting text…"):
                    try:
                        st.session_state.sop_doc = upload_file("upload-sop", sop_file)
                        st.session_state.last_error = None
                    except Exception as e:  # noqa: BLE001
                        st.session_state.last_error = f"SOP upload failed: {e}"

        if st.session_state.sop_doc:
            d = st.session_state.sop_doc
            st.markdown(
                f"""
                <div class="doc-card">
                  <div class="doc-card-id">DOC_ID · {d['doc_id']}</div>
                  <div class="doc-card-name">{d['filename']}</div>
                  <div class="doc-card-meta">{d['chars']:,} chars · {d['content_type']}</div>
                  <div class="doc-card-preview">{d['preview'][:240]}…</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with st.expander("…or paste SOP text inline"):
            st.session_state.sop_text_inline = st.text_area(
                "SOP text",
                value=st.session_state.sop_text_inline,
                height=160,
                label_visibility="collapsed",
                placeholder="High-risk customers with risk_score >= 90 must be escalated to an L2 Compliance Analyst within 24 hours.",
            )

    if st.session_state.last_error:
        st.error(st.session_state.last_error)


# -------------------------------------------------------------- #
# Tab 2: Validate
# -------------------------------------------------------------- #
with tab_validate:
    st.markdown("### Run conformance check")
    st.caption(
        "Extracts structured controls from both documents via the LLM, "
        "compares against the policy hierarchy, and produces a verdict "
        "with evidence-anchored findings."
    )

    # Build the request body from whatever inputs are populated
    def _build_request() -> Optional[dict]:
        body: dict = {"region": region}
        # SOP
        if st.session_state.sop_doc:
            body["sop_doc_id"] = st.session_state.sop_doc["doc_id"]
        elif st.session_state.sop_text_inline.strip():
            body["sop_text"] = st.session_state.sop_text_inline
        else:
            return None
        # Policy (optional — backend falls back to baseline)
        if st.session_state.policy_doc:
            body["policy_doc_id"] = st.session_state.policy_doc["doc_id"]
        elif st.session_state.policy_text_inline.strip():
            body["policy_text"] = st.session_state.policy_text_inline
        return body

    request_body = _build_request()
    ready = request_body is not None

    col_btn, col_preview = st.columns([1, 3], gap="large")
    with col_btn:
        run_clicked = st.button(
            "▸ Run validation",
            disabled=not ready,
            use_container_width=True,
        )
        if not ready:
            st.caption("Provide an SOP first (upload or paste).")
    with col_preview:
        if ready:
            with st.expander("Request payload", expanded=False):
                st.code(json.dumps(request_body, indent=2), language="json")

    if run_clicked and request_body:
        with st.spinner("Calling LLM, extracting controls, computing drift…"):
            try:
                st.session_state.result = validate(request_body)
                st.session_state.last_error = None
            except Exception as e:  # noqa: BLE001
                st.session_state.last_error = str(e)
                st.session_state.result = None

    if st.session_state.last_error:
        st.error(st.session_state.last_error)

    # ---- Verdict display ----
    if st.session_state.result:
        r = st.session_state.result
        v = r["verdict"].lower()
        findings = r.get("findings", [])
        block_n = sum(1 for f in findings if f["severity"] == "BLOCK")
        warn_n = sum(1 for f in findings if f["severity"] == "WARN")

        st.markdown("---")
        c1, c2 = st.columns([1, 2], gap="large")
        with c1:
            st.markdown(
                f'<div class="verdict-hero verdict-hero-{v}">{r["verdict"]}</div>',
                unsafe_allow_html=True,
            )
            st.caption(r.get("sop_filename") or "—")
        with c2:
            st.markdown(
                f"""
                <div style="font-family: Fraunces, serif; font-size:1.2rem;
                            color: var(--ink); margin-top:1rem; line-height:1.5;">
                  {r['summary']}
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown(
            f"""
            <div class="metric-strip">
              <div class="metric">
                <div class="metric-label">Findings</div>
                <div class="metric-value">{len(findings)}</div>
              </div>
              <div class="metric">
                <div class="metric-label">Block</div>
                <div class="metric-value" style="color: var(--block)">{block_n}</div>
              </div>
              <div class="metric">
                <div class="metric-label">Warn</div>
                <div class="metric-value" style="color: var(--warn)">{warn_n}</div>
              </div>
              <div class="metric">
                <div class="metric-label">Region</div>
                <div class="metric-value accent">{r['region']}</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if findings:
            st.caption("→ See **Findings** tab for evidence-anchored detail.")
        else:
            st.success("No unauthorized divergence detected. SOP cleared for publication.")


# -------------------------------------------------------------- #
# Tab 3: Findings
# -------------------------------------------------------------- #
with tab_findings:
    st.markdown("### Evidence-anchored findings")

    if not st.session_state.result:
        st.caption("Run a validation in the Validate tab to see findings here.")
    else:
        findings = st.session_state.result.get("findings", [])
        if not findings:
            st.success("No drift detected.")
        else:
            st.caption(
                f"{len(findings)} finding(s) — each anchored to the verbatim "
                "evidence span from the source documents."
            )
            for f in findings:
                sev = f["severity"].lower()
                drift = f["drift_type"].replace("_", " ").upper()
                observed_class = "observed warn" if sev == "warn" else "observed"

                # Confidence as a 0–100 bar
                conf_pct = int(round(f.get("confidence", 0) * 100))

                st.markdown(
                    f"""
                    <div class="finding-card {sev}">
                      <div class="finding-header">
                        <div>
                          <div class="finding-control">{f['control_id']}</div>
                          <div class="finding-type">{drift}</div>
                        </div>
                        <div class="verdict-badge verdict-{sev}">{f['severity']}</div>
                      </div>

                      <div class="compare">
                        <div class="compare-cell">
                          <div class="compare-label">Expected (policy)</div>
                          <div class="compare-value">{f['expected']}</div>
                        </div>
                        <div class="compare-cell">
                          <div class="compare-label">Observed (sop)</div>
                          <div class="compare-value {observed_class}">{f['observed']}</div>
                        </div>
                      </div>

                      <span class="evidence-label">Policy evidence</span>
                      <div class="evidence">{f['evidence_span_policy']}</div>

                      <span class="evidence-label">SOP evidence</span>
                      <div class="evidence">{f['evidence_span_sop']}</div>

                      <div style="font-family: JetBrains Mono, monospace;
                                  font-size: 0.7rem; color: var(--ink-mute);
                                  margin-top: 0.75rem;">
                        confidence: {conf_pct}%
                      </div>

                      <div class="remediation">
                        <div class="remediation-label">Remediation</div>
                        {f['remediation']}
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


# -------------------------------------------------------------- #
# Tab 4: Audit payloads
# -------------------------------------------------------------- #
with tab_payloads:
    st.markdown("### Enterprise audit payloads")
    st.caption(
        "Auto-generated Jira ticket and Confluence audit log, ready to POST "
        "to your enterprise REST APIs."
    )

    if not st.session_state.result:
        st.caption("Run a validation first.")
    else:
        r = st.session_state.result
        jira = r.get("jira_payload")
        confluence = r.get("confluence_payload")

        if not jira and not confluence:
            st.info(
                "No payloads generated — verdict was PASS, no remediation needed."
            )
        else:
            cj, cc = st.columns(2, gap="large")
            with cj:
                st.markdown("#### Jira ticket")
                if jira:
                    st.code(json.dumps(jira, indent=2), language="json")
                    st.download_button(
                        "Download jira_payload.json",
                        data=json.dumps(jira, indent=2),
                        file_name="jira_payload.json",
                        mime="application/json",
                        use_container_width=True,
                    )
                else:
                    st.caption("—")
            with cc:
                st.markdown("#### Confluence audit page")
                if confluence:
                    st.code(json.dumps(confluence, indent=2), language="json")
                    st.download_button(
                        "Download confluence_payload.json",
                        data=json.dumps(confluence, indent=2),
                        file_name="confluence_payload.json",
                        mime="application/json",
                        use_container_width=True,
                    )
                else:
                    st.caption("—")


# ============================================================== #
# Footer
# ============================================================== #
st.markdown(
    """
    <div style="margin-top: 4rem; padding-top: 1.5rem; border-top: 1px solid var(--border);
                font-family: JetBrains Mono, monospace; font-size: 0.7rem;
                color: var(--ink-mute); letter-spacing: 0.1em;">
      DRIFTGUARDIAN · OPEA-ALIGNED GOVERNANCE GATE · BUILT WITH FASTAPI + STREAMLIT
    </div>
    """,
    unsafe_allow_html=True,
)