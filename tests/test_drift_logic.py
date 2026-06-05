# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the drift engine logic and document loader.

These tests stub the LLM call so they run without any model server.
Run with:  python -m pytest tests/test_drift_logic.py -v
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drift import engine  # noqa: E402
from drift.loader import extract_text  # noqa: E402


def _write(suffix, content):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.write(fd, content.encode())
    os.close(fd)
    return path


def test_loader_txt_and_md():
    p = _write(".txt", "Retain data 7 years.")
    assert "7 years" in extract_text(p)
    os.remove(p)
    p = _write(".md", "# Policy\n- Retain 7 years")
    assert "Retain" in extract_text(p)
    os.remove(p)


def test_loader_rejects_unknown():
    p = _write(".xyz", "data")
    try:
        extract_text(p)
        assert False, "should have raised"
    except ValueError:
        pass
    finally:
        os.remove(p)


def test_global_conflict_blocks(monkeypatch):
    monkeypatch.setattr(
        engine, "_call_llm",
        lambda prompt: '{"findings":[{"point":"retention","source_says":"7y",'
                       '"sop_says":"3y","scope":"global","change_type":"weakened",'
                       '"explanation":"too short"}],"summary":"drift"}',
    )
    r = engine.check_drift("g", "s", "reg")
    assert r["verdict"] == "BLOCK"
    assert r["counts"]["block"] == 1


def test_regional_override_violation_blocks(monkeypatch):
    # A violation of an effective REGIONAL override is a hard failure, not a
    # soft warning. Severity is decided by the violation, not by the scope.
    monkeypatch.setattr(
        engine, "_call_llm",
        lambda prompt: '{"findings":[{"point":"language","source_says":"local",'
                       '"sop_says":"english","scope":"regional","change_type":"contradiction",'
                       '"explanation":"regional rule"}],"summary":"drift"}',
    )
    r = engine.check_drift("g", "s", "reg")
    assert r["verdict"] == "BLOCK"
    assert r["counts"]["block"] == 1
    assert r["findings"][0]["scope"] == "regional"   # scope is still reported
    assert r["findings"][0]["severity"] == "BLOCK"   # but it does not soften severity


def test_added_requirement_warns(monkeypatch):
    # An "added" requirement (present in no source) is advisory -> WARN.
    monkeypatch.setattr(
        engine, "_call_llm",
        lambda prompt: '{"findings":[{"point":"extra step","source_says":"-",'
                       '"sop_says":"requires a notarised copy","scope":"global",'
                       '"change_type":"added","explanation":"not in any source"}],'
                       '"summary":"drift"}',
    )
    r = engine.check_drift("g", "s", None)
    assert r["verdict"] == "WARN"
    assert r["counts"]["warn"] == 1


def test_llm_supplied_severity_is_respected(monkeypatch):
    # When the model emits an explicit severity, the engine trusts it even if
    # the change_type default would differ ("added" would otherwise be WARN).
    monkeypatch.setattr(
        engine, "_call_llm",
        lambda prompt: '{"findings":[{"point":"x","source_says":"a","sop_says":"b",'
                       '"scope":"global","change_type":"added","severity":"BLOCK",'
                       '"explanation":"e"}],"summary":"s"}',
    )
    r = engine.check_drift("g", "s", None)
    assert r["verdict"] == "BLOCK"


def test_remediation_is_always_present(monkeypatch):
    # Every finding must carry a remediation, even if the model omits one.
    monkeypatch.setattr(
        engine, "_call_llm",
        lambda prompt: '{"findings":[{"point":"retention","source_says":"7y",'
                       '"sop_says":"3y","scope":"global","change_type":"weakened",'
                       '"explanation":"too short"}],"summary":"drift"}',
    )
    r = engine.check_drift("g", "s", None)
    assert r["findings"][0]["remediation"]            # non-empty fallback applied
    assert r["remediation"][0]["severity"] == "BLOCK"  # remediation payload built
    assert r["remediation"][0]["action"]


def test_audit_block_is_populated(monkeypatch):
    monkeypatch.setattr(
        engine, "_call_llm", lambda prompt: '{"findings":[],"summary":"aligned"}'
    )
    r = engine.check_drift("global policy text", "sop text", "regional text")
    audit = r["audit"]
    assert audit["engine_version"] == engine.ENGINE_VERSION
    assert audit["verdict"] == "PASS"
    # Inputs are fingerprinted (length + sha256) for tamper-evidence.
    assert audit["documents"]["global"]["chars"] == len("global policy text")
    assert len(audit["documents"]["sop"]["sha256"]) == 64
    assert audit["documents"]["regional"] is not None


def test_no_findings_passes(monkeypatch):
    monkeypatch.setattr(
        engine, "_call_llm", lambda prompt: '{"findings":[],"summary":"aligned"}'
    )
    r = engine.check_drift("g", "s", None)
    assert r["verdict"] == "PASS"


def test_regional_finding_without_regional_doc_becomes_block(monkeypatch):
    # If no regional doc is provided, a regional-scoped finding is treated vs global.
    monkeypatch.setattr(
        engine, "_call_llm",
        lambda prompt: '{"findings":[{"point":"x","source_says":"a","sop_says":"b",'
                       '"scope":"regional","change_type":"weakened","explanation":"e"}],'
                       '"summary":"s"}',
    )
    r = engine.check_drift("g", "s", None)
    assert r["verdict"] == "BLOCK"


def test_json_with_code_fences(monkeypatch):
    monkeypatch.setattr(
        engine, "_call_llm",
        lambda prompt: '```json\n{"findings":[],"summary":"ok"}\n```',
    )
    r = engine.check_drift("g", "s", None)
    assert r["verdict"] == "PASS"


def test_small_sop_is_analysed_in_one_pass(monkeypatch):
    # A normal-sized SOP must be sent to the model in a SINGLE call so it sees
    # the whole document -- this is what prevents false "omission" findings for
    # requirements that simply live in another section.
    sop = "\n\n".join(
        f"## {i}.0 SECTION {i}\nRule text for section {i}." for i in range(1, 13)
    )
    calls = {"n": 0}

    def fake(prompt):
        calls["n"] += 1
        return '{"findings":[],"summary":"none"}'

    monkeypatch.setattr(engine, "_call_llm", fake)
    r = engine.check_drift("global text", sop, None, mode="one_shot")
    assert calls["n"] == 1                 # exactly one whole-document pass
    assert r["sections_analyzed"] == 1
    assert r["verdict"] == "PASS"


def test_oversized_sop_is_chunked_and_omissions_suppressed(monkeypatch):
    # A very large SOP falls back to chunked analysis. Because no single chunk
    # can prove a requirement is absent from the whole document, per-chunk
    # "omission" findings are dropped while real conflicts are kept.
    big_para = "Filler sentence. " * 400                      # ~6.8k chars/section
    sop = "\n\n".join(f"## {i}.0 SECTION {i}\n{big_para}" for i in range(1, 4))
    assert len(sop) > engine.MAX_WHOLE_DOC_CHARS

    def fake(prompt):
        # Every chunk claims an omission (the classic false positive) plus one
        # real contradiction.
        return ('{"findings":['
                '{"point":"missing thing","source_says":"x","sop_says":"absent",'
                '"scope":"global","change_type":"omission","explanation":"e"},'
                '{"point":"bad value","source_says":"8h","sop_says":"6h",'
                '"scope":"global","change_type":"contradiction","explanation":"e"}'
                '],"summary":"x"}')

    monkeypatch.setattr(engine, "_call_llm", fake)
    r = engine.check_drift("global text", sop, None, mode="one_shot")
    assert r["sections_analyzed"] > 1
    # Omissions from chunks are suppressed; the contradiction survives.
    assert all(f["change_type"] != "omission" for f in r["findings"])
    assert any(f["change_type"] == "contradiction" for f in r["findings"])


def test_compliant_points_are_not_reported(monkeypatch):
    # The model sometimes lists points where the SOP is COMPLIANT (change_type
    # "none" / a "no change needed" note). These must never become findings.
    monkeypatch.setattr(
        engine, "_call_llm",
        lambda prompt: '{"findings":['
                       '{"point":"aligned point","source_says":"within 24h",'
                       '"sop_says":"within 24h","scope":"regional","change_type":"none",'
                       '"severity":"WARN","remediation":"No change needed.",'
                       '"explanation":"matches"}],"summary":"aligned"}',
    )
    r = engine.check_drift("g", "s", "reg")
    assert r["verdict"] == "PASS"
    assert r["findings"] == []


def test_duplicate_findings_are_merged(monkeypatch):
    sop = "\n\n".join(f"## {i}.0 S{i}\ntext {i}." for i in range(1, 6))
    monkeypatch.setattr(
        engine, "_call_llm",
        lambda prompt: '{"findings":[{"point":"dup","source_says":"a","sop_says":"b",'
                       '"scope":"global","change_type":"weakened","explanation":"e"}],'
                       '"summary":"s"}',
    )
    r = engine.check_drift("g", sop, None)
    assert len(r["findings"]) == 1        # same finding across chunks collapsed to one


def test_total_llm_failure_raises_not_false_pass(monkeypatch):
    # If the model is unreachable, the engine must FAIL LOUDLY rather than
    # return a false PASS (a compliance gate silently passing is dangerous).
    def boom(prompt):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(engine, "_call_llm", boom)
    raised = False
    try:
        engine.check_drift("global text", "sop text", None)
    except RuntimeError:
        raised = True
    assert raised, "check_drift should raise when no LLM call succeeds"


def test_block_result_includes_integration_payloads(monkeypatch):
    monkeypatch.setattr(
        engine, "_call_llm",
        lambda prompt: '{"findings":[{"point":"threshold","source_says":"85",'
                       '"sop_says":"90","scope":"regional","change_type":"weakened",'
                       '"severity":"BLOCK","explanation":"weaker"}],"summary":"drift"}',
    )
    r = engine.check_drift("g", "s", "reg")
    assert r["jira_payload"]["fields"]["priority"]["name"] == "Highest"
    assert "block" in r["jira_payload"]["fields"]["labels"]
    assert r["confluence_payload"]["type"] == "page"
    assert "BLOCK" in r["confluence_payload"]["title"]


def test_pass_result_has_null_payloads(monkeypatch):
    monkeypatch.setattr(
        engine, "_call_llm", lambda prompt: '{"findings":[],"summary":"aligned"}'
    )
    r = engine.check_drift("g", "s", None)
    assert r["jira_payload"] is None
    assert r["confluence_payload"] is None


def test_two_stage_extracts_then_checks_and_reports_coverage(monkeypatch):
    # Two-stage: extract a typed checklist, then check the SOP against each item.
    reqs = [
        {"id": "R1", "topic": "EDD threshold", "kind": "numeric",
         "expected": "risk score >= 85 triggers EDD", "source": "regional"},
        {"id": "R2", "topic": "Approval authority", "kind": "role",
         "expected": "MLRO approves", "source": "regional"},
    ]
    monkeypatch.setattr(engine, "_extract_requirements", lambda g, r: reqs)
    monkeypatch.setattr(
        engine, "_evaluate_requirements",
        lambda sop, requirements, regional: engine._normalise_findings(
            [{"id": "R2", "point": "Approval authority", "source_says": "MLRO approves",
              "sop_says": "onboarding agent approves", "scope": "regional",
              "change_type": "contradiction", "severity": "BLOCK", "explanation": "e"}],
            regional, allow_omissions=True,
        ),
    )
    r = engine.check_drift("g", "s", "reg")
    assert r["mode"] == "two_stage"
    assert r["verdict"] == "BLOCK"
    cov = r["coverage"]
    assert cov["requirements_extracted"] == 2
    assert cov["requirements_drifted"] == 1
    assert cov["requirements_satisfied"] == 1
    statuses = {i["id"]: i["status"] for i in cov["requirements"]}
    assert statuses == {"R1": "satisfied", "R2": "drifted"}
    assert r["audit"]["mode"] == "two_stage"
    assert r["audit"]["requirements_checked"] == 2


def test_two_stage_falls_back_to_one_shot_when_no_requirements(monkeypatch):
    # If extraction yields nothing (e.g. a vague policy), degrade to one-shot
    # rather than reporting nothing.
    monkeypatch.setattr(engine, "_extract_requirements", lambda g, r: [])
    monkeypatch.setattr(
        engine, "_call_llm",
        lambda prompt: '{"findings":[{"point":"x","source_says":"a","sop_says":"b",'
                       '"scope":"global","change_type":"weakened","explanation":"e"}],'
                       '"summary":"s"}',
    )
    r = engine.check_drift("g", "s", None)
    assert r["mode"] == "one_shot"
    assert r["coverage"] is None
    assert r["verdict"] == "BLOCK"


def test_two_stage_check_failure_falls_back(monkeypatch):
    # Extraction succeeds but the check call fails -> fall back to one-shot,
    # never silently pass.
    monkeypatch.setattr(
        engine, "_extract_requirements",
        lambda g, r: [{"id": "R1", "topic": "t", "kind": "numeric",
                       "expected": "x", "source": "global"}],
    )
    def boom(sop, requirements, regional):
        raise RuntimeError("check call failed")
    monkeypatch.setattr(engine, "_evaluate_requirements", boom)
    monkeypatch.setattr(
        engine, "_call_llm", lambda prompt: '{"findings":[],"summary":"aligned"}'
    )
    r = engine.check_drift("g", "s", None)
    assert r["mode"] == "one_shot"
    assert r["verdict"] == "PASS"
