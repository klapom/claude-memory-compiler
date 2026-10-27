"""
Compile daily conversation logs into structured knowledge articles.

This is the "LLM compiler" - it reads daily logs (source code) and produces
organized knowledge articles (the executable).

Usage:
    uv run python compile.py                    # compile new/changed logs only
    uv run python compile.py --all              # force recompile everything
    uv run python compile.py --file daily/2026-04-01.md  # compile a specific log
    uv run python compile.py --dry-run          # show what would be compiled
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import sys
from pathlib import Path

from config import COMPILE_MODEL, lean_cli_options, SCRIPTS_DIR, AGENTS_FILE, CONCEPTS_DIR, CONNECTIONS_DIR, DAILY_DIR, KNOWLEDGE_DIR, now_iso
from utils import (
    file_hash,
    list_raw_files,
    list_wiki_articles,
    load_state,
    read_wiki_index,
    save_state,
)

# ── Paths for the LLM to use ──────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent


async def compile_daily_log(log_path: Path, state: dict) -> float:
    """Compile a single daily log into knowledge articles.

    Returns the API cost of the compilation.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    log_content = log_path.read_text(encoding="utf-8")
    schema = AGENTS_FILE.read_text(encoding="utf-8")
    wiki_index = read_wiki_index()

    # NB: existing articles are NOT inlined into the prompt anymore.
    # The KB grew past 130+ articles (~190k tokens) and overflowed Sonnet 4.6's
    # 200k context window, causing all compiles to fail with "Fatal error in
    # message reader" since ~2026-05-22. The LLM has Read/Glob/Grep tools and
    # will pull the specific articles it needs after scanning the index below.
    article_count = len(list_wiki_articles())
    timestamp = now_iso()

    prompt = f"""You are a knowledge compiler. Your job is to read a daily conversation log
and extract knowledge into structured wiki articles.

## Schema (AGENTS.md)

{schema}

## Current Wiki Index

{wiki_index}

## Existing Wiki Articles

The knowledge base currently has **{article_count} articles** under `knowledge/`.
They are NOT included inline (would overflow the context window).
Use the Read/Glob/Grep tools to inspect specific articles you need to update or
cross-reference. The index above tells you what exists; pick 3–10 relevant ones
based on the daily log topic and read them before deciding to create vs. update.

## Daily Log to Compile

**File:** {log_path.name}

{log_content}

## Your Task

Read the daily log above and compile it into wiki articles following the schema exactly.

**Important — context-efficient workflow:**

1. Skim the wiki index above and the daily log content
2. Identify topics in the log that may already have articles → Read those specific articles via the Read tool (NOT every article)
3. Use Glob/Grep if you need to discover articles by content/filename
4. Then create new / update existing articles as needed

### Rules:

1. **Extract durable knowledge** - Only what a future session needs: decisions, measured
   facts, root causes, gotchas. Zero new articles is a valid result; prefer updating an
   existing article over creating a near-duplicate.
2. **Create concept articles** in `knowledge/concepts/` - One .md file per concept
   - Use the article format from AGENTS.md (YAML frontmatter + sections)
   - Include `sources:` in frontmatter pointing to the daily log file
   - Use `[[concepts/slug]]` wikilinks only where the log states a real relation
   - Write concise and factual; length follows the evidence, not a template
3. **Create connection articles** in `knowledge/connections/` if this log reveals non-obvious
   relationships between 2+ existing concepts
4. **Update existing articles** if this log adds new information to concepts already in the wiki
   - Read the existing article, add the new information, add the source to frontmatter
5. **Update knowledge/index.md** - Add new entries to the table
   - Each entry: `| [[path/slug]] | One-line summary | source-file | {timestamp[:10]} |`
6. **Append to knowledge/log.md** - Add a timestamped entry:
   ```
   ## [{timestamp}] compile | {log_path.name}
   - Source: daily/{log_path.name}
   - Articles created: [[concepts/x]], [[concepts/y]]
   - Articles updated: [[concepts/z]] (if any)
   ```

### File paths:
- Write concept articles to: {CONCEPTS_DIR}
- Write connection articles to: {CONNECTIONS_DIR}
- Update index at: {KNOWLEDGE_DIR / 'index.md'}
- Append log at: {KNOWLEDGE_DIR / 'log.md'}

### Evidence rules (override any length or section minimum in AGENTS.md):
- **Nothing without a source.** Every statement, number, mechanism, comparison or
  table cell must be traceable to a line in this daily log or in the existing article
  you are updating. Do not fill gaps from general knowledge, do not explain how
  something "probably works", do not estimate effects the log does not state.
- **Keep claim and evidence apart.** Where the log gives a command/measurement and its
  result, keep both. Where the log only states a conclusion or assumption without a
  measurement, write it as such and mark it "not verified".
- **Name residual uncertainty** instead of smoothing it over; a counter-check that the
  log does not mention did not happen.
- **Contradictions:** if the log contradicts an existing article, update the article
  with the newer dated statement and mark the old one as superseded (with its date) -
  never merge both silently.
- Sections without evidence stay short or are omitted; no filler paragraphs, no
  invented "Related Concepts".
- Every article must have complete YAML frontmatter and cite the daily log in Sources.
"""

    cost = 0.0

    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                cwd=str(ROOT_DIR),
                system_prompt={"type": "preset", "preset": "claude_code"},
                allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
                permission_mode="acceptEdits",
                max_turns=30,
                model=COMPILE_MODEL,
                **lean_cli_options(["Read", "Write", "Edit", "Glob", "Grep"]),
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        pass  # compilation output - LLM writes files directly
            elif isinstance(message, ResultMessage):
                # total_cost_usd is the SDK's tokens x list-price estimate. It is not
                # an actual charge when the CLI runs on a Claude subscription login.
                cost = message.total_cost_usd or 0.0
                u = message.usage or {}
                tokens_in = (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                             + u.get("cache_read_input_tokens", 0))
                print(f"  List-price equivalent: ${cost:.4f} (model {COMPILE_MODEL}, "
                      f"{message.num_turns} turns, {tokens_in} input tokens)")
    except Exception as e:
        print(f"  Error: {e}")
        return 0.0

    # Update state
    rel_path = log_path.name
    state.setdefault("ingested", {})[rel_path] = {
        "hash": file_hash(log_path),
        "compiled_at": now_iso(),
        "cost_usd": cost,
    }
    state["total_cost"] = state.get("total_cost", 0.0) + cost
    save_state(state)

    return cost


def main():
    parser = argparse.ArgumentParser(description="Compile daily logs into knowledge articles")
    parser.add_argument("--all", action="store_true", help="Force recompile all logs")
    parser.add_argument("--file", type=str, help="Compile a specific daily log file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be compiled")
    args = parser.parse_args()

    if not args.dry_run:
        # Several flushes can spawn compile.py at nearly the same moment; without a
        # lock they compiled the same logs in parallel into the same articles
        # (2026-10-26: 4 runs within 3 minutes). The lock is released on exit.
        lock_file = open(SCRIPTS_DIR / "compile.lock", "w")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"[{now_iso()}] Another compile is running - exiting.")
            return

    state = load_state()

    # Determine which files to compile
    if args.file:
        target = Path(args.file)
        if not target.is_absolute():
            target = DAILY_DIR / target.name
        if not target.exists():
            # Try resolving relative to project root
            target = ROOT_DIR / args.file
        if not target.exists():
            print(f"Error: {args.file} not found")
            sys.exit(1)
        to_compile = [target]
    else:
        all_logs = list_raw_files()
        if args.all:
            to_compile = all_logs
        else:
            to_compile = []
            for log_path in all_logs:
                rel = log_path.name
                prev = state.get("ingested", {}).get(rel, {})
                if not prev or prev.get("hash") != file_hash(log_path):
                    to_compile.append(log_path)

    if not to_compile:
        print("Nothing to compile - all daily logs are up to date.")
        return

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Files to compile ({len(to_compile)}):")
    for f in to_compile:
        print(f"  - {f.name}")

    if args.dry_run:
        return

    # Compile each file sequentially
    total_cost = 0.0
    for i, log_path in enumerate(to_compile, 1):
        print(f"\n[{i}/{len(to_compile)}] Compiling {log_path.name}...")
        cost = asyncio.run(compile_daily_log(log_path, state))
        total_cost += cost
        print(f"  Done.")

    articles = list_wiki_articles()
    print(f"\nCompilation complete. List-price equivalent: ${total_cost:.2f}")
    print(f"Knowledge base: {len(articles)} articles")


if __name__ == "__main__":
    main()
