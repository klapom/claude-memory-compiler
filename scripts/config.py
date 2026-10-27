"""Path constants and configuration for the personal knowledge base."""

import os
from pathlib import Path
from datetime import datetime, timezone

# ── Paths ──────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent
DAILY_DIR = ROOT_DIR / "daily"
KNOWLEDGE_DIR = ROOT_DIR / "knowledge"
CONCEPTS_DIR = KNOWLEDGE_DIR / "concepts"
CONNECTIONS_DIR = KNOWLEDGE_DIR / "connections"
QA_DIR = KNOWLEDGE_DIR / "qa"
REPORTS_DIR = ROOT_DIR / "reports"
SCRIPTS_DIR = ROOT_DIR / "scripts"
HOOKS_DIR = ROOT_DIR / "hooks"
AGENTS_FILE = ROOT_DIR / "AGENTS.md"

INDEX_FILE = KNOWLEDGE_DIR / "index.md"
LOG_FILE = KNOWLEDGE_DIR / "log.md"
STATE_FILE = SCRIPTS_DIR / "state.json"

# ── Models ─────────────────────────────────────────────────────────────
# One place to bump models. Override per run/machine via environment, e.g.
#   MEMORY_COMPILER_COMPILE_MODEL=claude-opus-5-5 uv run python scripts/compile.py
# flush runs after every session (cheap + fast); compile/lint/query reason over the KB.
FLUSH_MODEL = os.environ.get("MEMORY_COMPILER_FLUSH_MODEL", "claude-haiku-5-5")
COMPILE_MODEL = os.environ.get("MEMORY_COMPILER_COMPILE_MODEL", "claude-sonnet-5-5")
QUERY_MODEL = os.environ.get("MEMORY_COMPILER_QUERY_MODEL", "claude-sonnet-5-5")
LINT_MODEL = os.environ.get("MEMORY_COMPILER_LINT_MODEL", "claude-sonnet-5-5")


def lean_cli_options(tools: list[str]) -> dict:
    """ClaudeAgentOptions kwargs that keep an SDK call free of ballast.

    By default the bundled CLI loads every user-level tool, MCP server and skill
    (measured 2026-10-27: 225 tools, 19 MCP servers, 994 skills = ~133k input
    tokens for a call that only returns text; with this: ~6k). `tools` sets the
    built-in tool set, an empty `mcp_servers` plus --strict-mcp-config drops all
    configured MCP servers; skills disappear with the Skill tool.
    """
    return {"tools": list(tools), "mcp_servers": {}, "extra_args": {"strict-mcp-config": None}}


# ── Compile schedule ───────────────────────────────────────────────────
# flush.py spawns compile.py at most once per interval (after COMPILE_AFTER_HOUR);
# compile.py itself refuses to run twice at the same time (scripts/compile.lock).
COMPILE_INTERVAL_DAYS = 7

# ── Timezone ───────────────────────────────────────────────────────────
TIMEZONE = "America/Chicago"


def now_iso() -> str:
    """Current time in ISO 8601 format."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today_iso() -> str:
    """Current date in ISO 8601 format."""
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
