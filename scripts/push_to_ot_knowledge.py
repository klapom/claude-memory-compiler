"""Push ALLOWLISTED knowledge articles into the ot-knowledge MCP (internal_ingest_docs).

Default-deny gate: only ids in mcp_allowlist.txt are pushed. Every article is
secret-scanned + auto-redacted in memory before upload. Incremental: a source's
sha is tracked in mcp_push_state.json — unchanged articles are skipped.

Self-contained (urllib only) so it runs on the VM (--base-url to the DGX) and on
the Spark (localhost). Idempotent server-side (sha256(source) dedup).

Usage:
    python scripts/push_to_ot_knowledge.py --dry-run
    python scripts/push_to_ot_knowledge.py                          # localhost:33320
    python scripts/push_to_ot_knowledge.py --base-url http://192.168.178.10:33320/mcp
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE = ROOT / "knowledge"
ALLOWLIST = ROOT / "mcp_allowlist.txt"
STATE = ROOT / "scripts" / "mcp_push_state.json"


def _resolve_user() -> str:
    """Ingest user for the X-Pommer-User header — kept out of the (public) repo.

    Order: env POMMER_INGEST_USER -> gitignored mcp_user.txt -> placeholder.
    """
    env = os.environ.get("POMMER_INGEST_USER")
    if env:
        return env.strip()
    uf = ROOT / "mcp_user.txt"
    if uf.exists():
        return uf.read_text(encoding="utf-8").strip()
    return "internal@example.invalid"


USER = _resolve_user()

_SECRET = re.compile(
    "|".join("(?:%s)" % p for p in [
        r"ghp_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}",
        r"-----BEGIN (?:OPENSSH|RSA|EC) PRIVATE KEY-----.*?-----END [^-]*-----",
        r"[A-Za-z0-9]{30,}\.access", r"client_secret['\"\s:=]+[A-Za-z0-9._-]{16,}",
        r"(?:api[_-]?key|token|password|secret)['\"\s:=]+[A-Za-z0-9._/+-]{16,}",
        r"\b[0-9a-f]{40}\b", r"\b[0-9a-f]{64}\b",
    ]),
    re.DOTALL | re.IGNORECASE,
)


def _redact(text: str) -> tuple[str, int]:
    return _SECRET.subn("[REDACTED-SECRET]", text)


# ── minimal Streamable-HTTP MCP client ──────────────────────────────────
class _Mcp:
    def __init__(self, base_url: str):
        self.base, self.sid, self.n = base_url, None, 0

    def _post(self, payload, capture=False):
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "X-Pommer-User": USER}
        if self.sid:
            headers["Mcp-Session-Id"] = self.sid
        req = urllib.request.Request(self.base, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            if capture and r.headers.get("Mcp-Session-Id"):
                self.sid = r.headers["Mcp-Session-Id"]
            raw = r.read().decode()
        for ln in raw.splitlines():
            ln = ln.strip()
            if ln.startswith("data:"):
                ln = ln[5:].strip()
            if ln.startswith("{"):
                try:
                    return json.loads(ln)
                except json.JSONDecodeError:
                    pass
        return {"raw": raw[:200]}

    def _id(self):
        self.n += 1
        return self.n

    def initialize(self):
        self._post({"jsonrpc": "2.0", "id": self._id(), "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "kb-push", "version": "1.0"}}}, capture=True)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def ingest(self, collection, docs):
        return self._post({"jsonrpc": "2.0", "id": self._id(), "method": "tools/call",
                           "params": {"name": "internal_ingest_docs",
                                      "arguments": {"collection": collection, "docs": docs}}})


def _allow():
    if not ALLOWLIST.exists():
        return []
    return [ln.strip() for ln in ALLOWLIST.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # Per-machine MCP target: mcp_target.txt (Spark=localhost default, VM=DGX URL)
    target_file = ROOT / "mcp_target.txt"
    default_url = target_file.read_text(encoding="utf-8").strip() if target_file.exists() else "http://localhost:33320/mcp"
    ap.add_argument("--collection", default="pommer_internal_global")
    ap.add_argument("--base-url", default=default_url)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore incremental state")
    args = ap.parse_args()

    state = json.loads(STATE.read_text()) if STATE.exists() and not args.force else {}
    docs, total_red, skipped = [], 0, 0
    for aid in _allow():
        f = KNOWLEDGE / aid
        if not f.exists():
            print(f"  ⚠ allowlisted but missing: {aid}")
            continue
        content, nred = _redact(f.read_text(encoding="utf-8"))
        total_red += nred
        sha = hashlib.sha256(content.encode()).hexdigest()[:16]
        if state.get(aid) == sha and not args.force:
            skipped += 1
            continue
        docs.append({"source": aid, "content": content,
                     "metadata": {"source_type": "ot_knowledge", "origin": "compiled_kb", "filename": aid}})
        state[aid] = sha

    print(f"allowlist={len(_allow())} | to-push={len(docs)} | unchanged-skipped={skipped} | redactions={total_red} -> {args.collection}")
    for d in docs:
        print(f"  + {d['source']}")
    if args.dry_run:
        print("DRY-RUN: nothing pushed.")
        return 0
    if not docs:
        print("nothing new to push.")
        return 0

    mcp = _Mcp(args.base_url)
    mcp.initialize()
    res = mcp.ingest(args.collection, docs)
    print("result:", json.dumps(res)[:300])
    STATE.write_text(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
