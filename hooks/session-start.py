"""
SessionStart hook - injects knowledge base context into every conversation.

This is the "context injection" layer. When Claude Code starts a session,
this hook reads the knowledge base index and recent daily log, then injects
them as additional context so Claude always "remembers" what it has learned.

Configure in .claude/settings.json:
{
    "hooks": {
        "SessionStart": [{
            "matcher": "",
            "command": "uv run python hooks/session-start.py"
        }]
    }
}
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Paths relative to project root
ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = ROOT / "knowledge"
DAILY_DIR = ROOT / "daily"
INDEX_FILE = KNOWLEDGE_DIR / "index.md"
PENDING_FILE = ROOT / "mcp_pending.txt"

MAX_CONTEXT_CHARS = 20_000
MAX_LOG_LINES = 30


def get_pending_reminder() -> str:
    """Pending MCP-approval reminder — only rendered when something awaits a decision."""
    if not PENDING_FILE.exists():
        return ""
    items = [ln.strip() for ln in PENDING_FILE.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")]
    if not items:
        return ""
    lst = "\n".join(f"- {a}" for a in items[:30])
    extra = f"\n- … (+{len(items) - 30} weitere)" if len(items) > 30 else ""
    return (
        f"## ⏳ MCP-Freigabe ausstehend ({len(items)})\n\n"
        f"Diese kompilierten OT-Artikel warten auf deine Freigabe für den ot-knowledge MCP:\n"
        f"{lst}{extra}\n\n"
        f"Freigeben: `uv run python scripts/curate.py approve <name>` (oder `--all`) · "
        f"Verwerfen: `… exclude <name>` · Danach: `uv run python scripts/push_to_ot_knowledge.py`"
    )


def get_recent_log() -> str:
    """Read the most recent daily log (today or yesterday)."""
    today = datetime.now(timezone.utc).astimezone()

    for offset in range(2):
        date = today - timedelta(days=offset)
        log_path = DAILY_DIR / f"{date.strftime('%Y-%m-%d')}.md"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").splitlines()
            # Return last N lines to keep context small
            recent = lines[-MAX_LOG_LINES:] if len(lines) > MAX_LOG_LINES else lines
            return "\n".join(recent)

    return "(no recent daily log)"


def build_context() -> str:
    """Assemble the context to inject into the conversation."""
    parts = []

    # Today's date
    today = datetime.now(timezone.utc).astimezone()
    parts.append(f"## Today\n{today.strftime('%A, %B %d, %Y')}")

    # Pending MCP-approval reminder (only when non-empty)
    reminder = get_pending_reminder()
    if reminder:
        parts.append(reminder)

    # Knowledge base index (the core retrieval mechanism)
    if INDEX_FILE.exists():
        index_content = INDEX_FILE.read_text(encoding="utf-8")
        parts.append(f"## Knowledge Base Index\n\n{index_content}")
    else:
        parts.append("## Knowledge Base Index\n\n(empty - no articles compiled yet)")

    # Recent daily log
    recent_log = get_recent_log()
    parts.append(f"## Recent Daily Log\n\n{recent_log}")

    context = "\n\n---\n\n".join(parts)

    # Truncate if too long
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n\n...(truncated)"

    return context


def main():
    context = build_context()

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }

    print(json.dumps(output))


if __name__ == "__main__":
    main()
