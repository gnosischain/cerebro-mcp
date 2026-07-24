"""Headless Claude-agent driver for the templates benchmark suite.

Runs one instruction template through a real Claude agent (``claude -p``)
connected ONLY to the cerebro MCP server, in an empty scratch workspace (no
repo/user CLAUDE.md skews the run — representative of an end user pasting the
template into a fresh chat), and measures wall time, tokens, and cost from the
CLI's ``--output-format json`` result.

Key mechanics (verified against the installed CLI, incl. by smoke runs):
- The interactive shell aliases ``claude``/``claude-personal`` do not exist in
  subprocesses; the real binary resolves via PATH. ``CEREBRO_BENCH_CLAUDE_BIN``
  overrides it, ``CEREBRO_BENCH_CLAUDE_CONFIG_DIR`` selects the CLI config dir
  (the machine-local ``claude-personal`` alias is ``CLAUDE_CONFIG_DIR=~/.claude-personal``).
- **This CLI build defers MCP tools behind ToolSearch**: the agent must load
  cerebro tool schemas via a ToolSearch call before invoking them. Therefore
  ``--tools ""`` is FATAL (it strips ToolSearch itself → MCP unreachable; the
  model narrates tool calls as text and burns a turn). Instead: keep the
  built-in set but ``--disallowedTools`` every wandering tool, leaving
  ToolSearch as the only built-in — which matches what a real desktop user of
  the deferred-tools client experiences, so measured token costs stay honest.
- ``--allowedTools mcp__cerebro`` pre-permits the whole cerebro server — no
  ``--dangerously-skip-permissions``.
- There is NO ``--max-turns`` in this CLI build: runs are bounded by
  ``--max-budget-usd`` (CLI-enforced) plus a subprocess wall-clock timeout.
- The scratch redirect for the SPAWNED server travels via the ``env`` block of
  the generated MCP config (run.py's in-process env redirect cannot reach it).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Writable server paths -> per-run scratch subdirs (mirrors run.py _SCRATCH_ENV).
SERVER_SCRATCH_DIRS = {
    "CEREBRO_REPORT_DIR": "reports",
    "THINKING_LOG_DIR": "logs",
    "CEREBRO_RESEARCH_DIR": "research",
    "MCP_SECURITY_LOG_DIR": "security_audit",
    "CEREBRO_SAVED_QUERIES_DIR": "saved_queries",
    "ASYNC_RESULT_DIR": "query_results",
}
SERVER_SCRATCH_FILES = {"EVENT_STORE_PATH": "cerebro_state.db"}

#: Tool actions that satisfy the "charts" / "answer" deliverable checks.
CHART_ACTIONS = {"generate_charts", "generate_metric_charts", "quick_metric_chart", "quick_chart", "generate_chart"}
#: "answer" covers both data planes: warehouse (query_metrics/execute_query) and
#: point-in-time chain reads / tx decodes (chain_state_analyst, tx forensics).
ANSWER_ACTIONS = {
    "query_metrics", "execute_query", "run_saved_query",
    "contract_call_function", "contract_explore",
    "contract_decode_transaction_input", "contract_decode_receipt_logs",
    "rpc_trace_transaction", "load_graph_transactions",
}


@dataclass
class HeadlessRunRecord:
    delivered: bool = False
    fail_reason: str | None = None
    duration_ms: float | None = None
    duration_api_ms: float | None = None
    num_turns: int | None = None
    total_cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    session_id: str | None = None
    subtype: str | None = None
    is_error: bool | None = None
    result_text: str = ""
    run_dir: str = ""
    verify_detail: str = ""
    personas_adopted: list[str] = field(default_factory=list)
    review: dict[str, Any] | None = None

    def to_meta(self) -> dict[str, Any]:
        data = {k: v for k, v in self.__dict__.items() if k != "result_text"}
        data["result_chars"] = len(self.result_text)
        return data


def resolve_claude_bin() -> str:
    override = os.environ.get("CEREBRO_BENCH_CLAUDE_BIN")
    if override:
        return override
    found = shutil.which("claude")
    if not found:
        raise RuntimeError(
            "claude CLI not found on PATH; set CEREBRO_BENCH_CLAUDE_BIN to the binary"
        )
    return found


def _claude_env() -> dict[str, str]:
    env = dict(os.environ)
    config_dir = os.environ.get("CEREBRO_BENCH_CLAUDE_CONFIG_DIR")
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = str(Path(config_dir).expanduser())
    return env


def load_repo_env() -> dict[str, str]:
    """Parse the repo .env (stdlib) — the spawned server's credentials."""
    env_path = REPO_ROOT / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        key = key.strip()
        # Absolutize repo-relative paths (the spawned server's cwd is the empty
        # workspace, not the repo, so relative .env paths would break).
        candidate = REPO_ROOT / value
        if value and not value.startswith(("/", "http", "~")) and candidate.exists():
            value = str(candidate)
        values[key] = value
    return values


def build_mcp_config(run_dir: Path) -> Path:
    """Write the per-run MCP config pointing at the cerebro stdio server."""
    server_bin = os.environ.get(
        "CEREBRO_BENCH_SERVER_BIN", str(REPO_ROOT / ".venv" / "bin" / "cerebro-mcp")
    )
    env_block = load_repo_env()
    for var, sub in SERVER_SCRATCH_DIRS.items():
        target = run_dir / sub
        target.mkdir(parents=True, exist_ok=True)
        env_block[var] = str(target)
    for var, sub in SERVER_SCRATCH_FILES.items():
        env_block[var] = str(run_dir / sub)
    env_block["REPORT_AUTO_OPEN"] = "false"
    env_block["SEMANTIC_AUTOLOAD_ON_LOCAL_MTIME"] = "false"
    config = {"mcpServers": {"cerebro": {"command": server_bin, "env": env_block}}}
    path = run_dir / "mcp_config.json"
    path.write_text(json.dumps(config, indent=2) + "\n")
    return path


def fill_instructions(instructions: str, params: dict[str, str]) -> str:
    filled = instructions
    for name, value in params.items():
        filled = filled.replace("{{" + name + "}}", str(value))
    leftover = re.findall(r"\{\{([A-Z][A-Z0-9_]*)\}\}", filled)
    if leftover:
        raise ValueError(f"unfilled template params: {sorted(set(leftover))}")
    return filled


# ---------------------------------------------------------------------------
# Session-trace introspection (deliverable + persona verification)
# ---------------------------------------------------------------------------

def _iter_trace_steps(logs_dir: Path):
    for trace_file in sorted(logs_dir.glob("session_*.json")):
        try:
            data = json.loads(trace_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        steps = data.get("steps") if isinstance(data, dict) else None
        if not isinstance(steps, list):
            continue
        for step in steps:
            if isinstance(step, dict):
                yield step


def _successful_actions(logs_dir: Path) -> set[str]:
    actions: set[str] = set()
    for step in _iter_trace_steps(logs_dir):
        if step.get("success") is False:
            continue
        action = step.get("action")
        if isinstance(action, str):
            actions.add(action)
    return actions


def personas_adopted(logs_dir: Path) -> list[str]:
    """Roles actually adopted via get_agent_persona in this run's traces."""
    roles: list[str] = []
    for step in _iter_trace_steps(logs_dir):
        if step.get("action") != "get_agent_persona":
            continue
        args = step.get("tool_args") or {}
        role = args.get("role") if isinstance(args, dict) else None
        if not role:
            # Defensive: some traces summarize args as strings.
            match = re.search(r"role['\"]?[:=]\s*['\"]?([a-z_]+)", json.dumps(args))
            role = match.group(1) if match else None
        if role and role not in roles:
            roles.append(role)
    return roles


def verify_deliverable(kind: str, run_dir: Path, result_text: str) -> tuple[bool, str]:
    reports_dir = run_dir / "reports"
    logs_dir = run_dir / "logs"
    if kind == "report_file":
        found = sorted(reports_dir.glob("*.html"))
        return (bool(found), f"{len(found)} report file(s)")
    actions = _successful_actions(logs_dir)
    if kind == "charts":
        hit = sorted(actions & CHART_ACTIONS)
        return (bool(hit), f"chart calls: {hit}")
    if kind == "answer":
        hit = sorted(actions & ANSWER_ACTIONS)
        ok = bool(hit) and bool(result_text.strip())
        return (ok, f"answer calls: {hit}, result_chars={len(result_text)}")
    if kind == "export":
        exported = sorted(p.name for p in reports_dir.glob("*")) if reports_dir.exists() else []
        ok = "export_report" in actions and bool(exported)
        return (ok, f"export_report={'export_report' in actions}, files={len(exported)}")
    return (False, f"unknown verify kind {kind!r}")


# ---------------------------------------------------------------------------
# The run itself
# ---------------------------------------------------------------------------

def run_template(
    *,
    instructions: str,
    verify: str,
    verify_personas: list[str],
    run_dir: Path,
    model: str,
    timeout_s: int,
    budget_usd: float,
) -> HeadlessRunRecord:
    record = HeadlessRunRecord(run_dir=str(run_dir))
    run_dir.mkdir(parents=True, exist_ok=True)
    workspace = run_dir / "workspace"
    workspace.mkdir(exist_ok=True)
    mcp_config = build_mcp_config(run_dir)

    cmd = [
        resolve_claude_bin(),
        "-p", instructions,
        "--output-format", "json",
        "--mcp-config", str(mcp_config),
        "--strict-mcp-config",
        "--model", model,
        # MCP tools are deferred behind ToolSearch in this CLI build — keep it,
        # deny everything else built-in so the agent cannot wander.
        "--disallowedTools",
        "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,"
        "Task,TodoWrite,Skill,ScheduleWakeup,KillShell,BashOutput,"
        "EnterPlanMode,ExitPlanMode",
        "--allowedTools", "mcp__cerebro",
        "--max-budget-usd", str(budget_usd),
        "--no-session-persistence",
    ]
    (run_dir / "command.txt").write_text(" ".join(cmd[:2]) + " ...\n" + instructions + "\n")
    try:
        proc = subprocess.run(
            cmd, cwd=workspace, env=_claude_env(),
            capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        record.fail_reason = f"timeout after {timeout_s}s"
        return record
    (run_dir / "stdout.json").write_text(proc.stdout or "")
    if proc.stderr:
        (run_dir / "stderr.txt").write_text(proc.stderr)

    payload: dict[str, Any] | None = None
    try:
        payload = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        record.fail_reason = f"unparseable CLI output (exit {proc.returncode})"
        return record

    # Defensive field extraction — CLI result fields drift across versions.
    record.duration_ms = payload.get("duration_ms")
    record.duration_api_ms = payload.get("duration_api_ms")
    record.num_turns = payload.get("num_turns")
    record.total_cost_usd = payload.get("total_cost_usd", payload.get("cost_usd"))
    record.session_id = payload.get("session_id")
    record.subtype = payload.get("subtype")
    record.is_error = payload.get("is_error")
    usage = payload.get("usage") or {}
    record.input_tokens = usage.get("input_tokens")
    record.output_tokens = usage.get("output_tokens")
    record.cache_creation_input_tokens = usage.get("cache_creation_input_tokens")
    record.cache_read_input_tokens = usage.get("cache_read_input_tokens")
    record.result_text = str(payload.get("result") or "")

    if record.is_error:
        record.fail_reason = f"CLI reported error (subtype={record.subtype})"
        return record

    delivered, detail = verify_deliverable(verify, run_dir, record.result_text)
    record.verify_detail = detail
    record.personas_adopted = personas_adopted(run_dir / "logs")
    missing = [r for r in verify_personas if r not in record.personas_adopted]
    if delivered and missing:
        # Delivered but skipped its mandated personas: the template failed to
        # execute as designed — counts as NOT delivered.
        record.fail_reason = f"personas not adopted: {missing}"
        return record
    if not delivered:
        record.fail_reason = f"deliverable check failed ({detail})"
        return record
    record.delivered = True
    return record


def extract_deliverable_content(run_dir: Path, verify: str, result_text: str, *, limit: int = 40_000) -> str:
    """Deliverable content handed to the adversarial reviewers."""
    reports_dir = run_dir / "reports"
    if verify in {"report_file", "export"}:
        parts: list[str] = []
        for path in sorted(reports_dir.glob("*.html")):
            try:
                from cerebro_mcp.tools.visualization.charts import _extract_structured_from_html
                structured = _extract_structured_from_html(path.read_text())
                parts.append(json.dumps(structured, default=str)[: limit // 2])
            except Exception:
                # Fallback: strip tags crudely.
                text = re.sub(r"<script.*?</script>", " ", path.read_text(), flags=re.S)
                text = re.sub(r"<[^>]+>", " ", text)
                parts.append(re.sub(r"\s+", " ", text)[: limit // 2])
        parts.append(result_text)
        return "\n\n".join(parts)[:limit]
    # charts / answer tiers: the final reply plus chart/query outputs from traces.
    parts = [result_text]
    for step in _iter_trace_steps(run_dir / "logs"):
        if step.get("action") in CHART_ACTIONS | ANSWER_ACTIONS:
            summary = step.get("output_summary") or step.get("tool_result") or ""
            if isinstance(summary, (dict, list)):
                summary = json.dumps(summary, default=str)
            parts.append(str(summary)[:4000])
    return "\n\n".join(parts)[:limit]
