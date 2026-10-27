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

# ── Timezone ───────────────────────────────────────────────────────────
TIMEZONE = "America/Chicago"


def now_iso() -> str:
    """Current time in ISO 8601 format."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today_iso() -> str:
    """Current date in ISO 8601 format."""
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
