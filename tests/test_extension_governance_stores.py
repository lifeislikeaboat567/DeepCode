"""Unit tests for hook rule and policy stores."""

from __future__ import annotations

from pathlib import Path

from deepcode.extensions import HookEvent, HookRule, HookRuleStore, SkillToggleStore
from deepcode.governance import ApprovalStore, PolicyRule, PolicyStore


def test_hook_rule_store_upsert_and_remove(tmp_path: Path):
    store = HookRuleStore(file_path=str(tmp_path / "hook_rules.json"))

    created = store.upsert(
        HookRule(
            name="log tool use",
            event=HookEvent.BEFORE_TOOL,
            handler_type="command",
            handler_value="echo before_tool",
            enabled=True,
        )
    )

    all_rules = store.list_all()
    assert len(all_rules) == 1
    assert all_rules[0].id == created.id

    created.enabled = False
    store.upsert(created)
    assert store.list_all()[0].enabled is False

    removed = store.remove(created.id)
    assert removed is True
    assert store.list_all() == []


def test_policy_store_upsert_and_remove(tmp_path: Path):
    store = PolicyStore(file_path=str(tmp_path / "policies.json"))

    created = store.upsert(
        PolicyRule(
            name="deny shell rm",
            scope="project",
            target="shell:rm",
            decision="deny",
            enabled=True,
        )
    )

    all_rules = store.list_all()
    assert len(all_rules) == 1
    assert all_rules[0].id == created.id

    created.decision = "ask"
    store.upsert(created)
    assert store.list_all()[0].decision == "ask"

    removed = store.remove(created.id)
    assert removed is True
    assert store.list_all() == []


def test_approval_store_create_list_and_decide(tmp_path: Path):
    store = ApprovalStore(file_path=str(tmp_path / "approvals.json"))

    created = store.create(
        tool_name="shell",
        action_input={"command": "rm -rf /tmp/demo"},
        reason="Matched ask rule",
        rule_id="rule-1",
    )

    pending = store.list_all(status="pending")
    assert len(pending) == 1
    assert pending[0].id == created.id

    approved = store.decide(created.id, "approved")
    assert approved is not None
    assert approved.status == "approved"
    fetched = store.get(created.id)
    assert fetched is not None
    assert fetched.status == "approved"

    approved_rows = store.list_all(status="approved")
    assert len(approved_rows) == 1
    assert approved_rows[0].id == created.id


def test_skill_toggle_store_set_and_load(tmp_path: Path):
    store = SkillToggleStore(file_path=str(tmp_path / "skill_toggles.json"))

    assert store.is_enabled("/tmp/skills/a.md") is True

    store.set_enabled("/tmp/skills/a.md", False)
    store.set_enabled("/tmp/skills/b.md", True)

    rows = store.load()
    assert rows["/tmp/skills/a.md"] is False
    assert rows["/tmp/skills/b.md"] is True
