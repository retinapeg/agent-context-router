#!/usr/bin/env python3
"""Rebuild raw `claude -p --output-format stream-json` transcripts from an allowlist of fields.

Only the fields needed to check the claims in evidence/README.md are copied; everything
else is dropped by default. Kept:
  init       cwd, tools, mcp_servers, model, permissionMode, claude_code_version
  assistant  text blocks; tool_use name + input; server-side tool name only
  user       tool_result text + is_error
  result     result (the final answer), is_error, num_turns, duration_ms
Tool-call ids are replaced by local sequence numbers (call-1, call-2, ...) so calls still
pair with their results. Dropped: session, request, message and event ids/UUIDs, timestamps,
signed or encrypted thinking and advisor content, usage/cost, and the local skill,
command, agent and plugin lists. Local paths become <repo>, <tmp> and ~.

  python3 evidence/sanitize_transcripts.py RAW_DIR OUT_DIR REPO_PATH [HOME]
"""
import json
import re
import sys
from pathlib import Path

INIT_FIELDS = ("cwd", "tools", "mcp_servers", "model", "permissionMode", "claude_code_version")
RESULT_FIELDS = ("result", "is_error", "num_turns", "duration_ms")
ID_PATTERNS = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|"
                         r"\b(?:toolu|srvtoolu|msg|req)_[A-Za-z0-9]+|\"signature\"|encrypted_content")


def text_of(content):
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content if c.get("type") == "text")


def rebuild(events):
    calls = {}

    def call_id(raw):
        return calls.setdefault(raw, f"call-{len(calls) + 1}")

    out = []
    for ev in events:
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            out.append({"type": "init", **{k: ev.get(k) for k in INIT_FIELDS}})
        elif kind == "assistant":
            blocks = []
            for c in ev["message"]["content"]:
                if c["type"] == "text":
                    blocks.append({"type": "text", "text": c["text"]})
                elif c["type"] == "tool_use":
                    blocks.append({"type": "tool_use", "call": call_id(c["id"]), "name": c["name"], "input": c["input"]})
                elif c["type"] == "server_tool_use":
                    blocks.append({"type": "server_tool_use", "call": call_id(c["id"]), "name": c["name"],
                                   "note": "server-side tool; its result is encrypted and omitted"})
                # thinking, advisor results and anything unknown are dropped
            if blocks:
                out.append({"type": "assistant", "model": ev["message"].get("model"), "content": blocks})
        elif kind == "user" and isinstance(ev["message"]["content"], list):
            blocks = [{"type": "tool_result", "call": call_id(c["tool_use_id"]), "is_error": bool(c.get("is_error")),
                       "text": text_of(c.get("content", ""))}
                      for c in ev["message"]["content"] if c.get("type") == "tool_result"]
            if blocks:
                out.append({"type": "user", "content": blocks})
        elif kind == "result":
            out.append({"type": "result", **{k: ev.get(k) for k in RESULT_FIELDS}})
    return out


def main(raw_dir, out_dir, repo, home):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for src in sorted(Path(raw_dir).glob("*.jsonl")):
        events = [json.loads(line) for line in src.read_text().splitlines() if line.strip()]
        lines = []
        for ev in rebuild(events):
            text = json.dumps(ev, ensure_ascii=False)
            text = text.replace(repo, "<repo>").replace("/private/tmp/claude-501", "<tmp>").replace(home, "~")
            lines.append(text)
        body = "\n".join(lines) + "\n"
        leak = ID_PATTERNS.search(body)
        if leak:
            raise SystemExit(f"{src.name}: identifier-like text survived: {leak.group(0)!r}")
        (out / src.name).write_text(body)
        print(f"{src.name}: {len(lines)} events")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else str(Path.home()))
