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

      /* Verdict badge */
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

      /* Metric strip (3 columns instead of 4) */
      .metric-strip {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
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
        margin-bottom: 0.5rem;
      }
      .doc-card-preview {
        font-size: 0.85rem;
        color: var(--ink-mute);
        line-height: 1.5;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================== #
# API calls
# ============================================================== #
def check_health() -> bool:
    try:
        r = httpx.get(f"{API_URL}/health", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False

def upload_file(endpoint: str, uploaded_file) -> Optional[dict]:
    if not uploaded_file:
        return None
    try:
        files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
        r = httpx.post(f"{API_URL}/{endpoint}", files=files, timeout=REQUEST_TIMEOUT_S)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Upload failed: {e}")
        return None


# ============================================================== #
# Sidebar
# ============================================================== #
with st.sidebar:
    st.markdown("### System Status")
    if check_health():
        st.markdown("<span class='dot dot-on'></span> Backend online", unsafe_allow_html=True)
    else:
        st.markdown("<span class='dot dot-off'></span> Backend offline", unsafe_allow_html=True)
        st.caption(f"Cannot reach `{API_URL}`")

    st.markdown("---")
    if st.button("Reset Session"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()


# ============================================================== #
# Main UI
# ============================================================== #
st.markdown(
    """
    <div class="hero">
      <div class="hero-mark">Internal Tools</div>
      <h1 class="hero-title">DriftGuardian</h1>
      <p class="hero-subtitle">
        Automated compliance gate. Upload an AI-drafted SOP to evaluate it against the policy baseline.
      </p>
    </div>
    """,
    unsafe_allow_html=True,
)

tab1, tab2, tab3 = st.tabs(["1. Upload Documents", "2. Run Validation", "3. Enterprise Payloads"])

# -------------------------------------------------------------- #
# Tab 1: Upload
# -------------------------------------------------------------- #
with tab1:
    st.markdown("### Source Documents")
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**1. Target SOP (Required)**")
        sop_file = st.file_uploader("Upload SOP draft (MD, PDF, DOCX)", key="sop_file")
        if st.button("Upload SOP"):
            res = upload_file("upload-sop", sop_file)
            if res:
                st.session_state["sop_doc_id"] = res["doc_id"]
                st.session_state["sop_filename"] = res["filename"]
                st.success("SOP uploaded successfully.")

        if "sop_doc_id" in st.session_state:
            st.markdown(
                f'''
                <div class="doc-card">
                  <div class="doc-card-id">ID: {st.session_state["sop_doc_id"]}</div>
                  <div><strong>{st.session_state["sop_filename"]}</strong></div>
                </div>
                ''',
                unsafe_allow_html=True
            )

    with col2:
        st.markdown("**2. Override Exceptions (Optional)**")
        override_file = st.file_uploader("Upload authorized exceptions", key="override_file")
        if st.button("Upload Override"):
            res = upload_file("upload-override", override_file)
            if res:
                st.session_state["override_doc_id"] = res["doc_id"]
                st.session_state["override_filename"] = res["filename"]
                st.success("Override uploaded successfully.")
        
        if "override_doc_id" in st.session_state:
            st.markdown(
                f'''
                <div class="doc-card">
                  <div class="doc-card-id">ID: {st.session_state["override_doc_id"]}</div>
                  <div><strong>{st.session_state["override_filename"]}</strong></div>
                </div>
                ''',
                unsafe_allow_html=True
            )

    st.markdown("---")
    st.markdown("**3. Global Baseline Policy (Optional)**")
    st.caption("If omitted, the system defaults to the on-disk global baseline policy.")
    policy_file = st.file_uploader("Upload custom baseline policy", key="policy_file")
    if st.button("Upload Policy"):
        res = upload_file("upload-policy", policy_file)
        if res:
            st.session_state["policy_doc_id"] = res["doc_id"]
            st.session_state["policy_filename"] = res["filename"]
            st.success("Policy uploaded successfully.")

    if "policy_doc_id" in st.session_state:
        st.markdown(
            f'''
            <div class="doc-card">
              <div class="doc-card-id">ID: {st.session_state["policy_doc_id"]}</div>
              <div><strong>{st.session_state["policy_filename"]}</strong></div>
            </div>
            ''',
            unsafe_allow_html=True
        )


# -------------------------------------------------------------- #
# Tab 2: Validation
# -------------------------------------------------------------- #
with tab2:
    if "sop_doc_id" not in st.session_state and "sop_text" not in st.session_state:
        st.info("Upload an SOP in Tab 1 to run validation.")
    else:
        st.markdown("### Conformance Check")
        
        if st.button("Run Analysis", type="primary"):
            with st.spinner("Extracting controls and evaluating drift..."):
                payload = {
                    "sop_doc_id": st.session_state.get("sop_doc_id"),
                    "policy_doc_id": st.session_state.get("policy_doc_id"),
                    "override_doc_id": st.session_state.get("override_doc_id"),
                }
                
                try:
                    r = httpx.post(f"{API_URL}/validate", json=payload, timeout=REQUEST_TIMEOUT_S)
                    r.raise_for_status()
                    st.session_state["validation_result"] = r.json()
                except Exception as e:
                    st.error(f"Validation failed: {e}")

        result = st.session_state.get("validation_result")
        if result:
            v = result["verdict"]
            v_lower = v.lower()
            
            st.markdown(
                f'''
                <div style="margin: 2rem 0;">
                  <span class="verdict-badge verdict-{v_lower}">{v}</span>
                  <div class="verdict-hero verdict-hero-{v_lower}">{v}</div>
                  <p style="color: var(--ink-dim); max-width: 600px; margin-top: 1rem; line-height: 1.6;">
                    {result["summary"]}
                  </p>
                </div>
                ''',
                unsafe_allow_html=True
            )

            st.markdown(
                f'''
                <div class="metric-strip">
                  <div class="metric">
                    <div class="metric-label">Target Document</div>
                    <div class="metric-value" style="font-size: 1.1rem; padding-top: 0.5rem; font-family: 'JetBrains Mono', monospace;">
                      {result.get("sop_filename", "Unknown")}
                    </div>
                  </div>
                  <div class="metric">
                    <div class="metric-label">Deviations</div>
                    <div class="metric-value accent">{len(result["findings"])}</div>
                  </div>
                  <div class="metric">
                    <div class="metric-label">Status</div>
                    <div class="metric-value" style="font-size: 1.2rem; padding-top: 0.4rem;">
                      {"Blocked" if v == "BLOCK" else "Cleared" if v == "PASS" else "Conditional"}
                    </div>
                  </div>
                </div>
                ''',
                unsafe_allow_html=True
            )

            if result["findings"]:
                st.markdown("### Detailed Findings")
                for f in result["findings"]:
                    sev = f["severity"].lower()
                    
                    st.markdown(
                        f'''
                        <div class="finding-card {sev}">
                          <div class="finding-header">
                            <div class="finding-control">{f["control_id"]}</div>
                            <div class="finding-type">{f["drift_type"].replace('_', ' ')}</div>
                          </div>
                          
                          <div class="compare">
                            <div class="compare-cell">
                              <div class="compare-label">Baseline Policy</div>
                              <div class="compare-value">{f["expected"]}</div>
                            </div>
                            <div class="compare-cell">
                              <div class="compare-label">SOP Document</div>
                              <div class="compare-value observed {sev}">{f["observed"]}</div>
                            </div>
                          </div>

                          <details style="margin-top: 1rem; cursor: pointer;">
                            <summary style="font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; color: var(--ink-mute); text-transform: uppercase; letter-spacing: 0.1em;">
                              View Evidence & Remediation
                            </summary>
                            <div style="margin-top: 1rem; padding-top: 1rem; border-top: 1px dashed var(--border-strong);">
                                <div class="evidence">
                                  <span class="evidence-label">Policy Evidence</span>
                                  "{f["evidence_span_policy"]}"
                                </div>
                                <div class="evidence">
                                  <span class="evidence-label">SOP Evidence</span>
                                  "{f["evidence_span_sop"]}"
                                </div>
                                <div class="remediation">
                                  <div class="remediation-label">Required Action</div>
                                  {f["remediation"]}
                                </div>
                            </div>
                          </details>
                        </div>
                        ''',
                        unsafe_allow_html=True
                    )


# -------------------------------------------------------------- #
# Tab 3: Payloads
# -------------------------------------------------------------- #
with tab3:
    result = st.session_state.get("validation_result")
    if not result:
        st.info("Run validation in Tab 2 to generate enterprise payloads.")
    else:
        jira = result.get("jira_payload")
        confluence = result.get("confluence_payload")
        
        st.markdown("### Generated Integrations")
        st.caption("JSON payloads ready to be POSTed to enterprise APIs.")
        
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### Jira Issue")
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
        with c2:
            st.markdown("#### Confluence Audit Page")
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