"""Policy evaluation engine for tool execution governance."""

from __future__ import annotations

from dataclasses import dataclass
import shlex
from typing import Any
import fnmatch

from deepcode.governance.audit import AuditLogger
from deepcode.governance.policy_store import PolicyRule, PolicyStore


@dataclass
class PolicyDecisionResult:
    """Result of policy evaluation for a pending tool action."""

    decision: str
    matched_rule: PolicyRule | None = None
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


class PolicyEngine:
    """Evaluate allow/ask/deny rules for tool invocations."""

    def __init__(
        self,
        policy_store: PolicyStore | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._store = policy_store or PolicyStore()
        self._audit = audit_logger or AuditLogger()

    def evaluate(self, tool_name: str, action_input: dict[str, Any] | None = None) -> PolicyDecisionResult:
        """Evaluate policies for a tool call."""
        payload = action_input or {}
        operation = self._infer_operation(tool_name, payload)
        candidates = self._build_candidates(tool_name, operation)
        matched = self._match_rule(candidates)

        if matched is None:
            result = PolicyDecisionResult(
                decision="allow",
                matched_rule=None,
                reason="No policy matched. Default allow.",
            )
        else:
            result = PolicyDecisionResult(
                decision=matched.decision,
                matched_rule=matched,
                reason=f"Matched policy rule '{matched.name}' ({matched.id})",
            )

        self._audit_policy_check(tool_name, operation, candidates, result)
        return result

    def _match_rule(self, candidates: list[str]) -> PolicyRule | None:
        rules = [rule for rule in self._store.list_all() if rule.enabled]
        if not rules:
            return None

        # Prefer more specific targets first.
        sorted_rules = sorted(rules, key=lambda item: len(item.target), reverse=True)
        for candidate in candidates:
            for rule in sorted_rules:
                target = (rule.target or "").strip()
                if not target:
                    continue
                if target == "*":
                    return rule
                if fnmatch.fnmatch(candidate, target):
                    return rule
        return None

    @staticmethod
    def _infer_operation(tool_name: str, action_input: dict[str, Any]) -> str:
        raw_action = action_input.get("action")
        if isinstance(raw_action, str) and raw_action.strip():
            return raw_action.strip().lower()

        if tool_name == "shell":
            raw_command = action_input.get("command")
            if isinstance(raw_command, str) and raw_command.strip():
                try:
                    tokens = shlex.split(raw_command)
                except ValueError:
                    return "shell"
                if tokens:
                    return str(tokens[0]).strip().lower()
            return "shell"

        return ""

    @staticmethod
    def _build_candidates(tool_name: str, operation: str) -> list[str]:
        base = tool_name.strip().lower()
        candidates: list[str] = []
        if operation:
            candidates.extend(
                [
                    f"tool:{base}:{operation}",
                    f"tool:{base}:action={operation}",
                    f"{base}:{operation}",
                    f"action:{operation}",
                ]
            )
        candidates.extend([f"tool:{base}", base, "*"])
        return candidates

    def _audit_policy_check(
        self,
        tool_name: str,
        operation: str,
        candidates: list[str],
        result: PolicyDecisionResult,
    ) -> None:
        metadata = {
            "tool_name": tool_name,
            "operation": operation,
            "candidates": candidates,
            "decision": result.decision,
            "reason": result.reason,
        }
        if result.matched_rule is not None:
            metadata["rule_id"] = result.matched_rule.id
            metadata["rule_name"] = result.matched_rule.name
            metadata["rule_target"] = result.matched_rule.target

        status = "ok" if result.decision == "allow" else "error"
        self._audit.write(
            event="policy.check",
            actor="agent",
            status=status,
            resource=tool_name,
            metadata=metadata,
        )
