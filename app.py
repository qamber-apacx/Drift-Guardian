"""
DriftGuardian — Streamlit UI (simple).

A clean demo front-end for the DriftGuardian governance API.
Calls /upload-policy, /upload-sop, /upload-override, and /validate.

Run with:
    streamlit run app.py

Configure the backend URL via DRIFTGUARDIAN_API (default: http://localhost:8000).
"""
from __future__ import annotations

import json
import os
from typing import Optional

import httpx
import streamlit as st

API_URL = os.environ.get("DRIFTGUARDIAN_API", "http://localhost:8000").rstrip("/")
REQUEST_TIMEOUT_S = 180.0


# ============================================================== #
# Page config
# ============================================================== #
st.set_page_config(
    page_title="DriftGuardian",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Minimal CSS — just a few small touches.
st.markdown(
    """
    <style>
      .doc-id {
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 0.8rem;
        color: #888;
      }
      /* Tighten the upload columns a bit */
      [data-testid="stFileUploader"] section { padding: 0.5rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================== #
# API helpers
# ============================================================== #
def check_health() -> bool:
    try:
        r = httpx.get(f"{API_URL}/health", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


def upload_file(endpoint: str, uploaded_file) -> Optional[dict]:
    if not uploaded_file:
        st.warning("Please choose a file first.")
        return None
    try:
        files = {
            "file": (
                uploaded_file.name,
                uploaded_file.getvalue(),
                uploaded_file.type or "application/octet-stream",
            )
        }
        r = httpx.post(
            f"{API_URL}/{endpoint}", files=files, timeout=REQUEST_TIMEOUT_S
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Upload failed: {e}")
        return None


def doc_status_line(doc_key: str, name_key: str) -> None:
    """Show a small confirmation that a document is loaded."""
    if doc_key in st.session_state:
        st.caption(
            f"✓ **{st.session_state[name_key]}** "
            f"<span class='doc-id'>(id: {st.session_state[doc_key]})</span>",
            unsafe_allow_html=True,
        )
    else:
        st.caption("_Not uploaded_")


# ============================================================== #
# Sidebar
# ============================================================== #
def get_config() -> dict | None:
    try:
        r = httpx.get(f"{API_URL}/debug/config", timeout=3.0)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


with st.sidebar:
    st.subheader("System")
    if check_health():
        st.success("Backend online")
        cfg = get_config()
        if cfg:
            # Show reachability with a clear icon
            if cfg.get("llm_reachable"):
                st.success("LLM reachable")
            else:
                st.error("LLM unreachable")
                st.caption(f"Error: `{cfg.get('llm_error', 'unknown')}`")

            st.divider()
            st.caption("**LLM config (live)**")
            endpoint = cfg.get("llm_endpoint", "?")
            model    = cfg.get("llm_model", "?")
            timeout  = cfg.get("llm_timeout_s", "?")
            env_set  = cfg.get("env_LLM_ENDPOINT", "")

            # Warn if env var is not set (backend using hardcoded default)
            if "(not set" in str(env_set):
                st.warning("⚠ LLM_ENDPOINT env var not set — backend is using its built-in default, NOT your docker-compose values.")

            st.caption(f"Endpoint: `{endpoint}`")
            st.caption(f"Model: `{model}`")
            st.caption(f"Timeout: `{timeout}s`")
    else:
        st.error("Backend offline")
        st.caption(f"Cannot reach `{API_URL}`")

    st.divider()

    st.subheader("Session")
    loaded = []
    if "policy_doc_id" in st.session_state:
        loaded.append(f"Policy: `{st.session_state['policy_filename']}`")
    if "sop_doc_id" in st.session_state:
        loaded.append(f"SOP: `{st.session_state['sop_filename']}`")
    if "override_doc_id" in st.session_state:
        loaded.append(f"Override: `{st.session_state['override_filename']}`")

    if loaded:
        for item in loaded:
            st.markdown(f"- {item}")
    else:
        st.caption("No documents uploaded yet.")

    st.divider()
    if st.button("Reset session", use_container_width=True):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()


# ============================================================== #
# Header
# ============================================================== #
st.title("◆ DriftGuardian")
st.write(
    "Automated compliance gate. Upload a baseline policy and an AI-drafted SOP "
    "to check the SOP against the policy."
)

st.divider()

tab1, tab2, tab3, tab4 = st.tabs(["1 · Upload", "2 · Validate", "3 · Payloads", "4 · Debug"])


# -------------------------------------------------------------- #
# Tab 1: Upload — all three inputs in a single row
# -------------------------------------------------------------- #
with tab1:
    st.subheader("Upload documents")
    st.caption(
        "Supported formats: Markdown, PDF, DOCX, TXT. "
        "Policy and SOP are required; override is optional."
    )

    col_policy, col_sop, col_override = st.columns(3)

    # ---- Baseline policy (required) ----
    with col_policy:
        st.markdown("**Baseline policy** _(required)_")
        policy_file = st.file_uploader(
            "Policy",
            key="policy_file",
            label_visibility="collapsed",
        )
        if st.button("Upload policy", key="btn_policy", use_container_width=True):
            res = upload_file("upload-policy", policy_file)
            if res:
                st.session_state["policy_doc_id"] = res["doc_id"]
                st.session_state["policy_filename"] = res["filename"]
                st.success(f"Policy uploaded: {res['filename']}")
        doc_status_line("policy_doc_id", "policy_filename")

    # ---- SOP (required) ----
    with col_sop:
        st.markdown("**SOP draft** _(required)_")
        sop_file = st.file_uploader(
            "SOP",
            key="sop_file",
            label_visibility="collapsed",
        )
        if st.button("Upload SOP", key="btn_sop", use_container_width=True):
            res = upload_file("upload-sop", sop_file)
            if res:
                st.session_state["sop_doc_id"] = res["doc_id"]
                st.session_state["sop_filename"] = res["filename"]
                st.success(f"SOP uploaded: {res['filename']}")
        doc_status_line("sop_doc_id", "sop_filename")

    # ---- Override (optional) ----
    with col_override:
        st.markdown("**Override exceptions** _(optional)_")
        override_file = st.file_uploader(
            "Override",
            key="override_file",
            label_visibility="collapsed",
        )
        if st.button("Upload override", key="btn_override", use_container_width=True):
            res = upload_file("upload-override", override_file)
            if res:
                st.session_state["override_doc_id"] = res["doc_id"]
                st.session_state["override_filename"] = res["filename"]
                st.success(f"Override uploaded: {res['filename']}")
        doc_status_line("override_doc_id", "override_filename")

    st.divider()

    # Readiness banner
    have_policy = "policy_doc_id" in st.session_state
    have_sop = "sop_doc_id" in st.session_state
    if have_policy and have_sop:
        st.success("Both required documents uploaded — head to the **Validate** tab.")
    else:
        missing = []
        if not have_policy:
            missing.append("baseline policy")
        if not have_sop:
            missing.append("SOP")
        st.info(f"Still required: {', '.join(missing)}.")


# -------------------------------------------------------------- #
# Tab 2: Validation
# -------------------------------------------------------------- #
with tab2:
    st.subheader("Run conformance check")

    have_policy = "policy_doc_id" in st.session_state
    have_sop = "sop_doc_id" in st.session_state

    if not (have_policy and have_sop):
        st.info(
            "Upload both a **baseline policy** and an **SOP** in the Upload tab "
            "to enable validation."
        )
    else:
        st.caption(
            f"Validating **{st.session_state['sop_filename']}** against "
            f"**{st.session_state['policy_filename']}**"
            + (
                f" with override **{st.session_state['override_filename']}**."
                if "override_doc_id" in st.session_state
                else "."
            )
        )

        if st.button("Run analysis", type="primary"):
            with st.spinner("Extracting controls and evaluating drift…"):
                payload = {
                    "sop_doc_id": st.session_state.get("sop_doc_id"),
                    "policy_doc_id": st.session_state.get("policy_doc_id"),
                    "override_doc_id": st.session_state.get("override_doc_id"),
                }
                try:
                    r = httpx.post(
                        f"{API_URL}/validate",
                        json=payload,
                        timeout=REQUEST_TIMEOUT_S,
                    )
                    if not r.is_success:
                        # Extract the actual error detail from the JSON body
                        # so the user sees a meaningful message, not just "422"
                        try:
                            err_body = r.json()
                            detail = err_body.get("detail", r.text)
                        except Exception:
                            detail = r.text or f"HTTP {r.status_code}"
                        st.error(f"**Validation error ({r.status_code}):** {detail}")
                        st.caption(
                            "**Common fixes:** "
                            "(1) Check your LLM endpoint is running — "
                            f"backend expects it at `{API_URL.replace(':8000', ':9000')}/v1/chat/completions`; "
                            "(2) Try the **Debug** tab to test extraction directly; "
                            "(3) Check backend logs for more detail."
                        )
                    else:
                        st.session_state["validation_result"] = r.json()
                except httpx.TimeoutException:
                    st.error(
                        "**Request timed out.** The LLM is taking too long to respond. "
                        "Try a smaller document or increase LLM_TIMEOUT_S."
                    )
                except Exception as e:
                    st.error(f"**Validation failed:** {e}")

        result = st.session_state.get("validation_result")
        if result:
            verdict = result["verdict"]
            findings = result["findings"]

            # Verdict banner
            if verdict == "PASS":
                st.success(f"**PASS** — {result['summary']}")
            elif verdict == "WARN":
                st.warning(f"**WARN** — {result['summary']}")
            else:
                st.error(f"**BLOCK** — {result['summary']}")

            # Quick metrics
            col1, col2, col3 = st.columns(3)
            col1.metric("Verdict", verdict)
            col2.metric("Findings", len(findings))
            col3.metric(
                "Blocking",
                sum(1 for f in findings if f["severity"] == "BLOCK"),
            )

            # Findings list
            if findings:
                st.divider()
                st.subheader(f"Findings ({len(findings)})")

                for i, f in enumerate(findings, start=1):
                    sev = f["severity"]
                    icon = "🔴" if sev == "BLOCK" else "🟡"
                    drift_label = f["drift_type"].replace("_", " ").title()
                    title = f"{icon} {sev} · {f['control_id']} — {drift_label}"

                    with st.expander(title, expanded=(i == 1)):
                        c1, c2 = st.columns(2)
                        with c1:
                            st.markdown("**Expected (policy)**")
                            st.code(f["expected"], language="text")
                        with c2:
                            st.markdown("**Observed (SOP)**")
                            st.code(f["observed"], language="text")

                        st.markdown("**Policy evidence**")
                        st.caption(f'"{f["evidence_span_policy"]}"')

                        st.markdown("**SOP evidence**")
                        st.caption(f'"{f["evidence_span_sop"]}"')

                        st.markdown("**Remediation**")
                        st.info(f["remediation"])

                        st.caption(f"Confidence: {f['confidence']:.0%}")
            else:
                st.success("No drift findings — SOP conforms to baseline.")


# -------------------------------------------------------------- #
# Tab 3: Payloads
# -------------------------------------------------------------- #
with tab3:
    st.subheader("Enterprise payloads")

    result = st.session_state.get("validation_result")
    if not result:
        st.info("Run validation in the **Validate** tab to generate payloads.")
    else:
        jira = result.get("jira_payload")
        confluence = result.get("confluence_payload")

        if not jira and not confluence:
            st.caption(
                "No payloads generated — nothing to report when the SOP passes cleanly."
            )

        if jira:
            st.markdown("#### Jira issue")
            jira_json = json.dumps(jira, indent=2)
            with st.expander("Preview JSON", expanded=False):
                st.code(jira_json, language="json")
            st.download_button(
                "Download jira_payload.json",
                data=jira_json,
                file_name="jira_payload.json",
                mime="application/json",
            )
            st.divider()

        if confluence:
            st.markdown("#### Confluence audit page")
            conf_json = json.dumps(confluence, indent=2)
            with st.expander("Preview JSON", expanded=False):
                st.code(conf_json, language="json")
            st.download_button(
                "Download confluence_payload.json",
                data=conf_json,
                file_name="confluence_payload.json",
                mime="application/json",
            )


# -------------------------------------------------------------- #
# Tab 4: Debug — raw extraction output
# -------------------------------------------------------------- #
with tab4:
    st.subheader("Debug: raw LLM extraction")
    st.caption(
        "Upload any document to see exactly what controls the LLM extracts "
        "before any conformance checking. Use this to diagnose extraction failures."
    )

    debug_file = st.file_uploader(
        "Upload document to test extraction",
        key="debug_file",
    )
    debug_source = st.selectbox(
        "Document type (for logging only)",
        ["policy", "sop", "override", "debug"],
        index=3,
    )

    if st.button("Run extraction", key="btn_debug"):
        if not debug_file:
            st.warning("Please choose a file first.")
        else:
            with st.spinner("Calling LLM extractor…"):
                try:
                    files = {
                        "file": (
                            debug_file.name,
                            debug_file.getvalue(),
                            debug_file.type or "application/octet-stream",
                        )
                    }
                    r = httpx.post(
                        f"{API_URL}/debug/extract",
                        files=files,
                        params={"source": debug_source},
                        timeout=REQUEST_TIMEOUT_S,
                    )
                    if not r.is_success:
                        try:
                            detail = r.json().get("detail", r.text)
                        except Exception:
                            detail = r.text
                        st.error(f"Extraction error ({r.status_code}): {detail}")
                    else:
                        result = r.json()
                        st.session_state["debug_result"] = result
                except Exception as e:
                    st.error(f"Request failed: {e}")

    debug = st.session_state.get("debug_result")
    if debug:
        col1, col2, col3 = st.columns(3)
        col1.metric("Controls extracted", debug["controls_extracted"])
        col2.metric("Document chars", debug["doc_chars"])
        col3.metric("LLM model", debug["llm_model"].split("/")[-1])

        st.caption(f"Endpoint: `{debug['llm_endpoint']}`")

        st.markdown("**Document preview (first 300 chars)**")
        st.caption(debug["doc_preview"])

        if debug["controls_extracted"] == 0:
            st.error(
                "No controls extracted. Possible reasons: "
                "(1) LLM endpoint is not reachable or returned an error; "
                "(2) All controls were dropped — their evidence_span was not "
                "found verbatim in the document (check backend logs for "
                "'Dropping control … evidence_span not found'); "
                "(3) The document contains no enforceable controls."
            )
        else:
            st.divider()
            st.subheader(f"Extracted controls ({debug['controls_extracted']})")
            for i, ctrl in enumerate(debug["controls"], start=1):
                label = f"{i}. {ctrl['control_id']}"
                with st.expander(label, expanded=(i == 1)):
                    # Show only non-null fields to keep it readable
                    display = {k: v for k, v in ctrl.items() if v is not None}
                    for k, v in display.items():
                        st.markdown(f"**{k}:** {v}")


# ============================================================== #
# Footer
# ============================================================== #
st.divider()
st.caption("DriftGuardian · OPEA-aligned governance gate · FastAPI + Streamlit")