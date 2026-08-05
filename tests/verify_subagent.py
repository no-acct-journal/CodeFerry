from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Ensure the project root is on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codeferry.agents.loader import AgentLoader
from codeferry.agents.tool_filter import (
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    resolve_agent_tools,
)
from codeferry.agents.fork import FORK_BOILERPLATE_TAG, ForkError, build_forked_messages
from codeferry.agents.trace import TraceManager
from codeferry.agents.task_manager import TaskManager
from codeferry.agents.notification import format_task_notification, inject_task_notifications
from codeferry.conversation import ConversationManager, ToolUseBlock
from codeferry.tools import ToolRegistry
from codeferry.tools.base import Tool, ToolResult
from codeferry.config import load_config

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
passed = 0
failed = 0

def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  {PASS} {name}")
    else:
        failed += 1
        msg = f"  {FAIL} {name}"
        if detail:
            msg += f"  — {detail}"
        print(msg)

# ---------------------------------------------------------------------------
# Dummy tools for tests.
# ---------------------------------------------------------------------------
class DummyTool(Tool):
    from pydantic import BaseModel as _BM

    class _Params(_BM):
        pass

    params_model = _Params

    def __init__(self, name: str):
        self.name = name
        self.description = f"Dummy {name}"
        self.category = "read"
        self.is_concurrency_safe = True
        self.is_system_tool = False

    def get_schema(self):
        return {"name": self.name, "description": self.description, "input_schema": {}}

    async def execute(self, params):
        return ToolResult(output=f"{self.name} ok")

def make_registry(*names: str) -> ToolRegistry:
    reg = ToolRegistry()
    for n in names:
        reg.register(DummyTool(n))
    return reg

# ---------------------------------------------------------------------------
# 1. Agent definition loading
# ---------------------------------------------------------------------------

def verify_loader():
    print("\n== 1. Agent Definition Loading ==")
    work_dir = str(Path(__file__).resolve().parent.parent)

    # Without Verification.
    loader = AgentLoader(work_dir, enable_verification=False)
    agents = loader.load_all()

    check("Built-in Explore loads", "Explore" in agents)
    check("Built-in Plan loads", "Plan" in agents)
    check("Built-in general-purpose loads", "general-purpose" in agents)
    check("Verification is not loaded by default", "Verification" not in agents)

    # With Verification.
    loader_v = AgentLoader(work_dir, enable_verification=True)
    agents_v = loader_v.load_all()
    check("Verification loads when enabled", "Verification" in agents_v)

    # Custom agents.
    check(
        "Custom security-reviewer loads",
        "security-reviewer" in agents,
        f"Actually loaded: {list(agents.keys())}",
    )
    check(
        "Custom code-summarizer loads",
        "code-summarizer" in agents,
        f"Actually loaded: {list(agents.keys())}",
    )

    # Custom agent source marker.
    if "security-reviewer" in agents:
        check(
            "Custom agent source=project",
            agents["security-reviewer"].source == "project",
        )

    # Attribute verification.
    explore = loader.get("Explore")
    check("Explore model=haiku", explore is not None and explore.model == "haiku")
    check("Explore maxTurns=30", explore is not None and explore.max_turns == 30)
    check(
        "Explore disallowedTools includes Agent",
        explore is not None and "Agent" in explore.disallowed_tools,
    )

    plan = loader.get("Plan")
    check("Plan maxTurns=15", plan is not None and plan.max_turns == 15)

    sr = loader.get("security-reviewer")
    check("security-reviewer model=haiku", sr is not None and sr.model == "haiku")
    check(
        "security-reviewer permissionMode=dontAsk",
        sr is not None and sr.permission_mode == "dontAsk",
    )

    check("get returns None for unknown type", loader.get("nonexistent") is None)

    # list_agents
    agent_list = loader.list_agents()
    names = [n for n, _ in agent_list]
    check("list_agents includes all loaded agents", len(names) >= 5)

    return loader

# ---------------------------------------------------------------------------
# 2. Tool filtering
# ---------------------------------------------------------------------------
def verify_tool_filter(loader: AgentLoader):
    print("\n== 2. Tool Filtering (Four Layers) ==")

    all_tools = [
        "ReadFile", "EditFile", "WriteFile", "Bash", "Grep", "Glob",
        "Agent", "AskUserQuestion", "TaskStop",
        "EnterPlanMode", "ExitPlanMode", "LoadSkill",
    ]
    reg = make_registry(*all_tools)

    # Built-in Explore.
    explore = loader.get("Explore")
    filtered = resolve_agent_tools(reg, explore, is_background=False)
    names = {t.name for t in filtered.list_tools()}

    check("L1: Agent is globally disallowed", "Agent" not in names)
    check("L1: AskUserQuestion is globally disallowed", "AskUserQuestion" not in names)
    check("L1: TaskStop is globally disallowed", "TaskStop" not in names)
    check(
        "L4: Explore disallowedTools applies (EditFile)",
        "EditFile" not in names,
    )
    check(
        "L4: Explore disallowedTools applies (WriteFile)",
        "WriteFile" not in names,
    )
    check("Explore keeps ReadFile", "ReadFile" in names)
    check("Explore keeps Grep", "Grep" in names)
    check("Explore keeps Bash", "Bash" in names)

    # Custom agent (source=project) should trigger L2.
    sr = loader.get("security-reviewer")
    filtered_sr = resolve_agent_tools(reg, sr, is_background=False)
    names_sr = {t.name for t in filtered_sr.list_tools()}
    check("L2: Custom agent additionally disallows EnterPlanMode", "EnterPlanMode" not in names_sr)

    # general-purpose does not disallow EnterPlanMode; verify built-ins are not restricted by L2.
    gp_fg = resolve_agent_tools(reg, loader.get("general-purpose"), is_background=False)
    names_gp = {t.name for t in gp_fg.list_tools()}
    check("L2: Built-in agent does not disallow EnterPlanMode", "EnterPlanMode" in names_gp)

    # Background allowlist.
    gp = loader.get("general-purpose")
    filtered_bg = resolve_agent_tools(reg, gp, is_background=True)
    names_bg = {t.name for t in filtered_bg.list_tools()}
    check("L3: Background agent excludes Agent tool", "Agent" not in names_bg)
    for n in names_bg:
        if n not in ASYNC_AGENT_ALLOWED_TOOLS:
            check(f"L3: Background tool {n} is not in the allowlist", False)
            break
    else:
        check("L3: All background tools are in the allowlist", True)

    # Allowlist plus blocklist combination.
    from codeferry.agents.parser import AgentDef
    combo = AgentDef(
        agent_type="combo",
        when_to_use="test",
        tools=["ReadFile", "EditFile", "Grep"],
        disallowed_tools=["EditFile"],
        source="builtin",
    )
    filtered_combo = resolve_agent_tools(reg, combo)
    names_combo = {t.name for t in filtered_combo.list_tools()}
    check("Allowlist plus blocklist leaves only ReadFile+Grep", names_combo == {"ReadFile", "Grep"})

# ---------------------------------------------------------------------------
# 3. Fork mode
# ---------------------------------------------------------------------------

def verify_fork():
    print("\n== 3. Fork Mode ==")

    conv = ConversationManager()
    conv.add_user_message("Hello")
    conv.add_assistant_message("Hello! How can I help?")
    conv.add_user_message("Please review config.py")
    conv.add_assistant_message("Sure, I will read this file.")

    forked = build_forked_messages(conv, "Also write a unit test")
    check("Fork preserves the original conversation", len(forked.history) == 5)  # 4 original messages + 1 fork message.
    check(
        "Fork injects boilerplate at the end",
        FORK_BOILERPLATE_TAG in forked.history[-1].content,
    )
    check("Fork includes the task at the end", "Also write a unit test" in forked.history[-1].content)

    # Deep-copy verification.
    forked.add_user_message("Extra message")
    check("Fork is a deep copy and does not affect the original conversation", len(conv.history) == 4)

    # Pending tool_use wrapping.
    conv2 = ConversationManager()
    conv2.add_user_message("test")
    conv2.add_assistant_message(
        "reading",
        [ToolUseBlock(tool_use_id="tu1", tool_name="ReadFile", arguments={})],
    )
    forked2 = build_forked_messages(conv2, "task")
    has_placeholder = any(
        msg.tool_results and msg.tool_results[0].content == "interrupted"
        for msg in forked2.history
    )
    check("Pending tool_use is wrapped as a placeholder", has_placeholder)

    # Prevent double forking.
    try:
        build_forked_messages(forked, "Fork again")
        check("Prevent double fork", False, "ForkError should be raised")
    except ForkError:
        check("Prevent double fork", True)

# ---------------------------------------------------------------------------
# 4. Trace lineage tracking
# ---------------------------------------------------------------------------
def verify_trace():
    print("\n== 4. Parent-Child Trace Tracking ==")
    tm = TraceManager()

    root = tm.create("main", trace_id="trace-001")
    child1 = tm.create("Explore", parent_id=root.agent_id, trace_id="trace-001")
    child2 = tm.create("Plan", parent_id=root.agent_id, trace_id="trace-001")
    other = tm.create("other", trace_id="trace-002")

    check("Node created successfully", tm.get(root.agent_id) is not None)
    check("parent_id is correct", child1.parent_id == root.agent_id)
    check("trace_id is inherited", child1.trace_id == "trace-001")

    tm.update(root.agent_id, input_tokens=1000, output_tokens=500)
    tm.update(child1.agent_id, input_tokens=200, output_tokens=100)
    tm.update(child2.agent_id, input_tokens=300, output_tokens=150)

    tree = tm.get_tree("trace-001")
    check("get_tree returns nodes from the same trace", len(tree) == 3)
    check("get_tree excludes other traces", other.agent_id not in {n.agent_id for n in tree})

    total_in, total_out = tm.get_total_tokens("trace-001")
    check("Aggregated input_tokens=1500", total_in == 1500)
    check("Aggregated output_tokens=750", total_out == 750)

    tm.complete(child1.agent_id, "completed")
    check("complete sets status", tm.get(child1.agent_id).status == "completed")
    check("complete sets end_time", tm.get(child1.agent_id).end_time is not None)

# ---------------------------------------------------------------------------
# 5. TaskManager background tasks
# ---------------------------------------------------------------------------
async def verify_task_manager():
    print("\n== 5. TaskManager Background Tasks ==")

    from unittest.mock import MagicMock, AsyncMock

    agent = MagicMock()
    agent.total_input_tokens = 200
    agent.total_output_tokens = 80
    agent.run_to_completion = AsyncMock(return_value="Search complete, found 15 .py files")

    tm = TaskManager()

    # launch
    task_id = tm.launch(agent, "Search project structure", name="explore-task")
    check("launch returns task_id", task_id is not None and len(task_id) > 0)
    check("Initial task status is running", tm.get(task_id).status == "running")

    await asyncio.sleep(0.2)

    bg = tm.get(task_id)
    check("Task status is completed after finishing", bg.status == "completed")
    check("Task result is correct", "15 .py files" in bg.result)
    check("Token statistics are updated", bg.progress.input_tokens == 200)

    # poll
    completed = tm.poll_completed()
    check("poll_completed returns completed tasks", len(completed) == 1)
    check("Second poll is empty", len(tm.poll_completed()) == 0)

    # list
    check("list_tasks includes task", len(tm.list_tasks()) == 1)

    # cancel
    slow_agent = MagicMock()
    slow_agent.total_input_tokens = 0
    slow_agent.total_output_tokens = 0

    async def slow_run(*a, **kw):
        await asyncio.sleep(10)
        return "done"

    slow_agent.run_to_completion = slow_run
    slow_id = tm.launch(slow_agent, "Slow task", name="slow")
    await asyncio.sleep(0.1)
    check("Cancel running task", tm.cancel(slow_id) is True)
    await asyncio.sleep(0.2)
    check("Status after cancel", tm.get(slow_id).status == "cancelled")

    # failed
    bad_agent = MagicMock()
    bad_agent.total_input_tokens = 0
    bad_agent.total_output_tokens = 0
    bad_agent.run_to_completion = AsyncMock(side_effect=RuntimeError("boom"))
    bad_id = tm.launch(bad_agent, "Task that will fail", name="bad")
    await asyncio.sleep(0.2)
    check("Failed task status is failed", tm.get(bad_id).status == "failed")
    check("Failed task includes error message", "boom" in tm.get(bad_id).result)

# ---------------------------------------------------------------------------
# 6. Notification
# ---------------------------------------------------------------------------
def verify_notification():
    print("\n== 6. task-notification Notification ==")
    from codeferry.agents.task_manager import BackgroundTask

    bg = BackgroundTask(
        id="abc123",
        name="security-reviewer",
        agent=None,
        task="Review config.py",
        status="completed",
        result="Found 1 Critical issue: hard-coded API key",
        start_time=100.0,
        end_time=145.0,
    )

    text = format_task_notification(bg)
    check("Notification contains <task-notification>", "<task-notification>" in text)
    check("Notification contains task ID", "abc123" in text)
    check("Notification contains agent name", "security-reviewer" in text)
    check("Notification contains status", "completed" in text)
    check("Notification contains result", "hard-coded API key" in text)
    check("Notification contains </task-notification>", "</task-notification>" in text)

    conv = ConversationManager()
    inject_task_notifications(conv, [bg])
    check("Injected message role is user", conv.history[0].role == "user")
    check("Injected content contains notification", "<task-notification>" in conv.history[0].content)

# ---------------------------------------------------------------------------
# 7. Config
# ---------------------------------------------------------------------------

def verify_config():
    print("\n== 7. Config Extensions ==")
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not config_path.exists():
        check("config.yaml exists", False, str(config_path))
        return

    config = load_config(config_path)
    check("enable_fork is read successfully", isinstance(config.enable_fork, bool))
    check("enable_verification_agent is read successfully", isinstance(config.enable_verification_agent, bool))
    check("enable_fork=True", config.enable_fork is True)
    check("enable_verification_agent=True", config.enable_verification_agent is True)

# ---------------------------------------------------------------------------
# 8. Permission mode
# ---------------------------------------------------------------------------
def verify_permission():
    print("\n== 8. DONT_ASK Permission Mode ==")
    from codeferry.permissions.modes import PermissionMode, mode_decide

    check("DONT_ASK enum value", PermissionMode.DONT_ASK.value == "dontAsk")
    check("DONT_ASK read=allow", mode_decide(PermissionMode.DONT_ASK, "read") == "allow")
    check("DONT_ASK write=allow", mode_decide(PermissionMode.DONT_ASK, "write") == "allow")
    check("DONT_ASK command=allow", mode_decide(PermissionMode.DONT_ASK, "command") == "allow")

# ---------------------------------------------------------------------------
# 9. Agent extension fields
# ---------------------------------------------------------------------------
def verify_agent_fields():
    print("\n== 9. Agent Extension Fields ==")
    from codeferry.agent import Agent
    from unittest.mock import MagicMock

    agent = Agent(
        client=MagicMock(),
        registry=ToolRegistry(),
        protocol="anthropic",
    )
    check("agent_id is generated automatically", agent.agent_id is not None and len(agent.agent_id) == 12)
    check("parent_id defaults to None", agent.parent_id is None)
    check("trace_id defaults to None", agent.trace_id is None)

    agent.set_agent_catalog("## Agents\n- Explore: search")
    check("set_agent_catalog takes effect", "Explore" in agent._agent_catalog)

# ---------------------------------------------------------------------------
# 10. AgentTool parameter model
# ---------------------------------------------------------------------------
def verify_agent_tool():
    print("\n== 10. AgentTool Parameters and Schema ==")
    from codeferry.tools.agent_tool import AgentTool, AgentToolParams

    params = AgentToolParams(
        prompt="Explore project structure",
        description="Code exploration",
        subagent_type="Explore",
        model="haiku",
        run_in_background=True,
        name="my-explore",
    )
    check("Required parameter prompt", params.prompt == "Explore project structure")
    check("Required parameter description", params.description == "Code exploration")
    check("Optional subagent_type", params.subagent_type == "Explore")
    check("Optional model", params.model == "haiku")
    check("Optional run_in_background", params.run_in_background is True)
    check("Optional name", params.name == "my-explore")

    # Schema verification.
    schema = AgentToolParams.model_json_schema()
    required = schema.get("required", [])
    check("prompt is required", "prompt" in required)
    check("description is required", "description" in required)
    check("subagent_type is not required", "subagent_type" not in required)

    # worktree is not implemented.
    params_wt = AgentToolParams(
        prompt="test", description="test", isolation="worktree"
    )
    check("isolation parameter can be set", params_wt.isolation == "worktree")

# ===========================================================================
# Main flow
# ===========================================================================
async def main():
    global passed, failed

    print("=" * 60)
    print("  SubAgent System Verification (Chapter 12)")
    print("=" * 60)

    loader = verify_loader()
    verify_tool_filter(loader)
    verify_fork()
    verify_trace()
    await verify_task_manager()
    verify_notification()
    verify_config()
    verify_permission()
    verify_agent_fields()
    verify_agent_tool()

    print("\n" + "=" * 60)
    total = passed + failed
    if failed == 0:
        print(f"  \033[32mAll passed: {passed}/{total}\033[0m")
    else:
        print(f"  \033[31mFailed: {failed}/{total}\033[0m")
    print("=" * 60)

    return failed == 0

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
