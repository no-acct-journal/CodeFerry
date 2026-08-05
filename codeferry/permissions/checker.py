from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from codeferry.permissions.dangerous import DangerousCommandDetector, is_safe_command
from codeferry.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from codeferry.permissions.rules import RuleEngine, extract_content
from codeferry.permissions.sandbox import PathSandbox
from codeferry.tools.base import Tool

_PLAN_MODE_ALLOWED_TOOLS = frozenset({"Agent", "ToolSearch", "AskUserQuestion", "ExitPlanMode"})


@dataclass
class Decision:
    effect: DecisionEffect
    reason: str


class PermissionChecker:


    def __init__(
        self,
        detector: DangerousCommandDetector,
        sandbox: PathSandbox,
        rule_engine: RuleEngine,
        mode: PermissionMode = PermissionMode.DEFAULT,
    ) -> None:
        self.detector = detector
        self.sandbox = sandbox
        self.rule_engine = rule_engine
        self.mode = mode
        self.plan_file_path: str = ""


    def check(self, tool: Tool, arguments: dict[str, Any]) -> Decision:
        content = extract_content(tool.name, arguments)

        # Layer 0: Plan mode exceptions.
        if self.mode == PermissionMode.PLAN:
            if tool.name in _PLAN_MODE_ALLOWED_TOOLS:
                return Decision(effect="allow", reason="Plan mode: allowed tool")
            if tool.name in ("WriteFile", "EditFile") and content:
                if self._is_plan_file(content):
                    return Decision(effect="allow", reason="Plan mode: plan file write")

        # Layer 1: Safe read-only commands are allowed automatically.
        if tool.category == "command" and is_safe_command(content or ""):
            return Decision(effect="allow", reason="Safe read-only command")

        # Layer 1b: Dangerous command blocklist for Bash only.
        if tool.category == "command":
            hit, reason = self.detector.detect(content)
            if hit:
                return Decision(effect="deny", reason=f"Dangerous command blocked: {reason}")

        # Layer 2: Path sandbox for file tools only.
        if tool.category in ("read", "write") and content:
            ok, reason = self.sandbox.check(content)
            if not ok:
                return Decision(effect="deny", reason=f"Path sandbox blocked request: {reason}")

        # Layer 3: Rule engine matching.
        rule_result = self.rule_engine.evaluate(tool.name, content)
        if rule_result == "allow":
            return Decision(effect="allow", reason="Permission rule allowed request")
        if rule_result == "deny":
            return Decision(effect="deny", reason="Permission rule denied request")

        # Layer 4: Permission mode fallback.
        effect = mode_decide(self.mode, tool.category)
        if effect == "allow":
            return Decision(effect="allow", reason=f"Permission mode {self.mode.value} allowed request")
        if effect == "deny":
            return Decision(effect="deny", reason=f"Permission mode {self.mode.value} denied request")

        # Layer 5: Trigger human confirmation (HITL).
        return Decision(effect="ask", reason="User confirmation required")


    def _is_plan_file(self, target_path: str) -> bool:
        if not self.plan_file_path or not target_path:
            return ".codeferry/plans/" in target_path
        try:
            abs_target = os.path.abspath(target_path)
            abs_plan = os.path.abspath(self.plan_file_path)
            if abs_target == abs_plan:
                return True
        except Exception:
            pass
        if os.path.basename(target_path) == os.path.basename(self.plan_file_path):
            return True
        return ".codeferry/plans/" in target_path
