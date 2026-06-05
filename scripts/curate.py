"""Curation gate for KB -> ot-knowledge MCP sync.

Default-deny: only articles on the allowlist are ever pushed. `propose` finds
NEW pure-OMNITRACKER articles not yet decided and parks them as "pending" — the
SessionStart hook surfaces them so you get reminded to approve/exclude.

State files (repo root, one "subdir/name.md" id per line):
    mcp_allowlist.txt   approved -> eligible for push
    mcp_pending.txt     proposed, awaiting your decision (drives the hook reminder)
    mcp_excluded.txt    deliberately out -> never re-proposed

Commands:
    python scripts/curate.py propose
    python scripts/curate.py approve concepts/omnitracker-actlog-parsing.md [...]
    python scripts/curate.py approve --all
    python scripts/curate.py exclude concepts/foo.md [...]
    python scripts/curate.py status
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE = ROOT / "knowledge"
SUBDIRS = ("concepts", "connections", "qa")
ALLOWLIST = ROOT / "mcp_allowlist.txt"
PENDING = ROOT / "mcp_pending.txt"
EXCLUDED = ROOT / "mcp_excluded.txt"

# "Eng: nur Pure-OMNITRACKER" — strict filename gate (Klaus 2026-06-05).
PURE_OT = re.compile(r"^(omnitracker|ot-|crystaltoll|gear-ot|gear-cmdb)", re.IGNORECASE)


def _read(p: Path) -> list[str]:
    if not p.exists():
        return []
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]


def _write(p: Path, items) -> None:
    p.write_text("\n".join(sorted(set(items))) + ("\n" if items else ""), encoding="utf-8")


def _all_articles() -> list[str]:
    out = []
    for sub in SUBDIRS:
        d = KNOWLEDGE / sub
        if d.exists():
            out += [f"{sub}/{p.name}" for p in d.glob("*.md")]
    return out


def _pure_ot_candidates() -> list[str]:
    return [a for a in _all_articles() if PURE_OT.match(a.split("/", 1)[1])]


def cmd_propose() -> None:
    allow, excl = set(_read(ALLOWLIST)), set(_read(EXCLUDED))
    pending = sorted(a for a in _pure_ot_candidates() if a not in allow and a not in excl)
    _write(PENDING, pending)
    print(f"propose: {len(pending)} pending (pure-OT, not yet decided)")
    for a in pending:
        print(f"  ⏳ {a}")


def cmd_approve(names) -> None:
    allow, pending = set(_read(ALLOWLIST)), set(_read(PENDING))
    if names == ["--all"]:
        names = sorted(pending)
    for n in names:
        allow.add(n)
        pending.discard(n)
    _write(ALLOWLIST, allow)
    _write(PENDING, pending)
    print(f"approved {len(names)} -> allowlist now {len(allow)}; pending {len(pending)}")


def cmd_exclude(names) -> None:
    excl, pending = set(_read(EXCLUDED)), set(_read(PENDING))
    for n in names:
        excl.add(n)
        pending.discard(n)
    _write(EXCLUDED, excl)
    _write(PENDING, pending)
    print(f"excluded {len(names)} -> excluded now {len(excl)}; pending {len(pending)}")


def cmd_status() -> None:
    print(f"allowlist: {len(_read(ALLOWLIST))} | pending: {len(_read(PENDING))} | excluded: {len(_read(EXCLUDED))}")
    print(f"pure-OT candidates in knowledge/: {len(_pure_ot_candidates())} | total articles: {len(_all_articles())}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "propose":
        cmd_propose()
    elif cmd == "approve":
        cmd_approve(args)
    elif cmd == "exclude":
        cmd_exclude(args)
    elif cmd == "status":
        cmd_status()
    else:
        print(f"unknown command: {cmd}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
