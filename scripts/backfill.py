"""
Backfill daily logs from historical Claude Code JSONL transcripts.

Walks ~/.claude/projects/**/*.jsonl (top-level only by default, skipping
subagent transcripts under */subagents/*), extracts conversation context
per session, runs flush.run_flush() via the Claude Agent SDK, and appends
the result to daily/<session_date>.md with the session's original time.

Resumable via scripts/backfill-state.json.

Usage:
    uv run python scripts/backfill.py --dry-run
    uv run python scripts/backfill.py --limit 3
    uv run python scripts/backfill.py --project -home-admin-projects-nanoclaw
    uv run python scripts/backfill.py
    uv run python scripts/backfill.py --force
"""

from __future__ import annotations

# Recursion guard — must be set BEFORE claude_agent_sdk import so any hooks
# fired by the SDK's internal Claude Code invocation exit early.
import os
os.environ["CLAUDE_INVOKED_BY"] = "backfill"

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAILY_DIR = ROOT / "daily"
SCRIPTS_DIR = ROOT / "scripts"
STATE_FILE = SCRIPTS_DIR / "backfill-state.json"
LOG_FILE = SCRIPTS_DIR / "backfill.log"

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

MIN_TURNS = 10
MIN_CHARS = 5_000
MAX_TURNS = 200
MAX_CONTEXT_CHARS = 60_000

sys.path.insert(0, str(SCRIPTS_DIR))
from flush import append_to_daily_log, run_flush  # noqa: E402

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def sha_of(path: Path) -> str:
    h = sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def parse_jsonl_session(transcript: Path) -> tuple[str, int, str, str] | None:
    """Walk the JSONL and return (context_md, turn_count, session_date, session_time).

    session_date: YYYY-MM-DD (from first user/assistant timestamp, fallback mtime)
    session_time: HH:MM
    Returns None if no valid turns found.
    """
    turns: list[str] = []
    first_ts: str | None = None

    with open(transcript, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg = entry.get("message", {})
            if isinstance(msg, dict):
                role = msg.get("role", "")
                content = msg.get("content", "")
            else:
                role = entry.get("role", "")
                content = entry.get("content", "")

            if role not in ("user", "assistant"):
                continue

            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        parts.append(block)
                content = "\n".join(parts)

            if not isinstance(content, str) or not content.strip():
                continue

            if first_ts is None:
                first_ts = entry.get("timestamp")

            label = "User" if role == "user" else "Assistant"
            turns.append(f"**{label}:** {content.strip()}\n")

    if not turns:
        return None

    recent = turns[-MAX_TURNS:]
    context = "\n".join(recent)
    # Strip control chars (except \n\r\t) that can corrupt the Agent SDK's
    # CLI stdin pipe — observed as "Command failed with exit code 1".
    context = "".join(c for c in context if ord(c) >= 32 or c in "\n\r\t")
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[-MAX_CONTEXT_CHARS:]
        boundary = context.find("\n**")
        if boundary > 0:
            context = context[boundary + 1:]

    if first_ts:
        try:
            dt = datetime.fromisoformat(first_ts.replace("Z", "+00:00")).astimezone()
        except ValueError:
            dt = datetime.fromtimestamp(transcript.stat().st_mtime).astimezone()
    else:
        dt = datetime.fromtimestamp(transcript.stat().st_mtime).astimezone()

    return context, len(recent), dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


def discover_transcripts(include_subagents: bool, project_filter: str | None) -> list[Path]:
    all_jsonl = sorted(CLAUDE_PROJECTS_DIR.rglob("*.jsonl"))
    results = []
    for p in all_jsonl:
        parts = p.parts
        if not include_subagents and "subagents" in parts:
            continue
        if project_filter:
            # project dir is the direct child of CLAUDE_PROJECTS_DIR
            try:
                rel = p.relative_to(CLAUDE_PROJECTS_DIR)
            except ValueError:
                continue
            if rel.parts[0] != project_filter:
                continue
        results.append(p)
    return results


async def process_one(transcript: Path, dry_run: bool) -> dict:
    """Process a single JSONL. Returns status dict for state."""
    parsed = parse_jsonl_session(transcript)
    if parsed is None:
        return {"status": "skipped", "reason": "no_turns"}
    context, turn_count, session_date, session_time = parsed

    if turn_count < MIN_TURNS:
        return {"status": "skipped", "reason": f"turns<{MIN_TURNS}", "turns": turn_count}
    if len(context) < MIN_CHARS:
        return {"status": "skipped", "reason": f"chars<{MIN_CHARS}", "chars": len(context)}

    if dry_run:
        return {
            "status": "dry_run",
            "turns": turn_count,
            "chars": len(context),
            "session_date": session_date,
            "session_time": session_time,
        }

    section_label = f"Backfill: {transcript.parent.name}/{transcript.stem[:8]}"
    response = await run_flush(context)

    if "FLUSH_ERROR" in response:
        append_to_daily_log(response, "Memory Flush (backfill)",
                            date=session_date, time_str=session_time)
        return {"status": "error", "session_date": session_date,
                "error": response[:200]}
    if "FLUSH_OK" in response:
        return {"status": "ok_empty", "session_date": session_date,
                "turns": turn_count}

    append_to_daily_log(response, section_label,
                        date=session_date, time_str=session_time)
    return {
        "status": "ok",
        "session_date": session_date,
        "session_time": session_time,
        "turns": turn_count,
        "chars": len(context),
        "response_chars": len(response),
    }


async def run(args: argparse.Namespace) -> None:
    if args.file:
        transcripts = [Path(args.file)]
    else:
        transcripts = discover_transcripts(args.include_subagents, args.project)
        if args.limit:
            transcripts = transcripts[: args.limit]

    print(f"Discovered {len(transcripts)} transcripts", flush=True)
    logging.info("Backfill starting: %d transcripts, dry_run=%s, force=%s",
                 len(transcripts), args.dry_run, args.force)

    state = load_state()
    processed = state.setdefault("processed", {})

    ok = skipped = errored = dry = 0
    for i, t in enumerate(transcripts, 1):
        key = str(t)
        try:
            sha = sha_of(t)
        except OSError as e:
            print(f"[{i}/{len(transcripts)}] ERR reading {t}: {e}", flush=True)
            errored += 1
            continue

        if not args.force and key in processed and processed[key].get("sha") == sha:
            prev = processed[key]
            print(f"[{i}/{len(transcripts)}] skip (done: {prev.get('status')}) {t.name}",
                  flush=True)
            skipped += 1
            continue

        try:
            result = await process_one(t, args.dry_run)
        except Exception as e:
            logging.exception("process_one failed for %s", t)
            result = {"status": "error", "error": f"{type(e).__name__}: {e}"}

        result["sha"] = sha
        result["processed_at"] = datetime.now().isoformat(timespec="seconds")
        processed[key] = result

        status = result["status"]
        if status == "ok":
            ok += 1
        elif status == "dry_run":
            dry += 1
        elif status.startswith("ok") or status == "skipped":
            skipped += 1
        else:
            errored += 1

        print(f"[{i}/{len(transcripts)}] {status:10s} {t.name}"
              f"  turns={result.get('turns','-')} date={result.get('session_date','-')}",
              flush=True)

        if not args.dry_run:
            save_state(state)

    if args.dry_run:
        save_state(state)

    print(f"\nDone. ok={ok} skipped={skipped} dry_run={dry} errors={errored}", flush=True)
    logging.info("Backfill finished: ok=%d skipped=%d dry=%d errors=%d",
                 ok, skipped, dry, errored)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Scan only, no Agent SDK calls, no writes to daily/")
    p.add_argument("--force", action="store_true",
                   help="Reprocess transcripts even if already in state")
    p.add_argument("--limit", type=int, default=0,
                   help="Process only the first N discovered transcripts")
    p.add_argument("--project", default=None,
                   help="Restrict to one project dir under ~/.claude/projects/")
    p.add_argument("--include-subagents", action="store_true",
                   help="Include */subagents/*.jsonl (off by default)")
    p.add_argument("--file", default=None,
                   help="Process a single JSONL file (absolute path); overrides discovery")
    args = p.parse_args()

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
