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


def test_long_sop_is_split_and_each_section_checked(monkeypatch):
    # A multi-heading SOP should be split into sections and the LLM called per
    # section, so a finding in any one section is still caught.
    sop = "\n\n".join(
        f"## {i}.0 SECTION {i}\nRule text for section {i}." for i in range(1, 13)
    )
    calls = {"n": 0}

    def fake(prompt):
        calls["n"] += 1
        if "7.0 SECTION 7" in prompt:
            return ('{"findings":[{"point":"p","source_says":"8h","sop_says":"6h",'
                    '"scope":"global","change_type":"weakened","explanation":"e"}],'
                    '"summary":"x"}')
        return '{"findings":[],"summary":"none"}'

    monkeypatch.setattr(engine, "_call_llm", fake)
    r = engine.check_drift("global text", sop, None)
    assert calls["n"] > 1                 # multiple sections were analysed
    assert r["sections_analyzed"] > 1
    assert r["verdict"] == "BLOCK"        # the planted finding was caught
    assert len(r["findings"]) == 1


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
