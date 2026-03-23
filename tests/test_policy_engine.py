"""Unit tests for policy engine matching and decisions."""

from __future__ import annotations

from pathlib import Path

from deepcode.governance import AuditLogger, PolicyEngine, PolicyRule, PolicyStore


def test_policy_engine_defaults_to_allow_when_no_rules(tmp_path: Path):
    store = PolicyStore(file_path=str(tmp_path / "policies.json"))
    engine = PolicyEngine(
        policy_store=store,
        audit_logger=AuditLogger(file_path=str(tmp_path / "audit.log")),
    )

    result = engine.evaluate("file_manager", {"action": "read", "path": "README.md"})

    assert result.decision == "allow"
    assert result.allowed is True
    assert result.matched_rule is None


def test_policy_engine_prefers_operation_specific_rule(tmp_path: Path):
    store = PolicyStore(file_path=str(tmp_path / "policies.json"))
    store.upsert(
        PolicyRule(
            name="ask-shell",
            scope="global",
            target="tool:shell",
            decision="ask",
            enabled=True,
        )
    )
    store.upsert(
        PolicyRule(
            name="deny-rm",
            scope="global",
            target="tool:shell:rm",
            decision="deny",
            enabled=True,
        )
    )
    engine = PolicyEngine(
        policy_store=store,
        audit_logger=AuditLogger(file_path=str(tmp_path / "audit.log")),
    )

    result = engine.evaluate("shell", {"command": "rm -rf /tmp/demo"})

    assert result.decision == "deny"
    assert result.matched_rule is not None
    assert result.matched_rule.name == "deny-rm"


def test_policy_engine_matches_file_manager_action_target(tmp_path: Path):
    store = PolicyStore(file_path=str(tmp_path / "policies.json"))
    store.upsert(
        PolicyRule(
            name="deny-file-write",
            scope="project",
            target="tool:file_manager:write",
            decision="deny",
            enabled=True,
        )
    )
    engine = PolicyEngine(
        policy_store=store,
        audit_logger=AuditLogger(file_path=str(tmp_path / "audit.log")),
    )

    result = engine.evaluate("file_manager", {"action": "write", "path": "a.txt", "content": "x"})

    assert result.decision == "deny"
    assert result.matched_rule is not None
    assert result.matched_rule.target == "tool:file_manager:write"
