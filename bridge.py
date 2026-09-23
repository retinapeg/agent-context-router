#!/usr/bin/env python3
"""Local MCP bridge: synthetic idea notes only (V0 test surface for ChatGPT).

Scope
  Exposes only vault/ideas/synthetic-*.md: exact lowercase names, regular files,
  no symlinks, no hardlinks. Tools: search, fetch, create_idea, update_idea.
  No delete, no paths, no other categories, no default-set files.
Writes
  Go through `memory.py new` and `memory.py update --expect <sha256>` (revision
  checked, file-locked), run as a subprocess with an argument list (no shell).
  ChatGPT cannot change a note's id, created or provenance lines.
Auth and network
  One secret token, presented as `Authorization: Bearer <token>`,
  `X-Bridge-Token: <token>`, or the URL path `/mcp/<token>`. Anything else gets a
  plain 404. The server refuses to start without a strong token. It binds
  127.0.0.1 only; reaching it from ChatGPT needs a tunnel, which this file never
  starts.
Protocol
  MCP Streamable HTTP, stateless, JSON responses. Speaks both the 2026-07-28
  per-request envelope (server/discover) and the initialize handshake
  (2025-11-25, 2025-06-18, 2025-03-26).

Commands
  python3 bridge.py init-token     create the token file (0600) outside the repo; never prints it
  python3 bridge.py exposure       list exactly which notes the bridge would expose
  python3 bridge.py serve [--port 8765]
"""
import argparse
import datetime as dt
import hmac
import http.server
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import memory

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
ENDPOINT = "/mcp"
MEMORY_PY = Path(memory.__file__).resolve()
TOKEN_ENV = "MEMORY_BRIDGE_TOKEN"
DEFAULT_TOKEN_FILE = Path.home() / ".config" / "agent-context-router" / "bridge-token"  # outside the repo, never committed
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{32,256}")
MAX_BODY_BYTES = 64 * 1024
MAX_NOTE_BYTES = 256 * 1024  # fetch refuses anything larger
MAX_ID_CHARS = 100
MAX_NOTE_CHARS = 6000  # keeps `memory.py context idea <id>` under its 12,000-char packet cap
MAX_CONNECTIONS = 8
RECV_TIMEOUT = 5  # seconds per socket read
CONNECTION_DEADLINE = 20  # seconds from accept to close, whatever the client does
AUDIT_MAX_BYTES = 5 * 1024 * 1024
AUTH_FAIL_WINDOW = 60  # seconds; failed-auth events are counted, then written as one line
ID_RE = re.compile(r"synthetic-[a-z0-9]+(?:-[a-z0-9]+)*")  # always fullmatch
SHA_RE = re.compile(r"[0-9a-f]{12,64}")
WRITER = "chatgpt"
PROTECTED_FRONTMATTER = ("id", "created", "provenance")
MODERN_VERSIONS = ("2026-07-28",)
HANDSHAKE_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
MODERN_STATUS = {-32700: 400, -32600: 400, -32602: 400, -32020: 400, -32022: 400, -32601: 404}
KNOWN_PATHS = ("/", ENDPOINT, "/.well-known/oauth-protected-resource", "/.well-known/oauth-authorization-server",
               "/.well-known/openid-configuration")  # failed-auth paths are logged only as one of these
ALLOWED_ORIGINS = ("https://chatgpt.com", "https://chat.openai.com")
SERVER_INFO = {"name": "context-router-bridge", "version": "0.2.0"}
INSTRUCTIONS = ("Context vault, synthetic idea notes only. Use search to find an id, fetch to read it. "
                "Answer from fetched text and cite the id, path and sha256 from fetch metadata. To edit, fetch "
                "first and pass its sha256 as expected_sha256 to update_idea; on a conflict, fetch again and "
                "merge. Keep the id, created and provenance lines. There is no delete.")

TOOLS = [
    {
        "name": "search",
        "title": "Search synthetic idea notes",
        "description": "Find synthetic idea notes by words in their id, title or text. Empty query lists all. "
                       "Returns ids to pass to fetch.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "maxLength": 200}},
                        "required": ["query"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
                        "openWorldHint": False},
    },
    {
        "name": "fetch",
        "title": "Fetch one synthetic idea note",
        "description": "Return the full Markdown of one synthetic idea note by id, with path, sha256 and mtime.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "minLength": 1, "maxLength": MAX_ID_CHARS}},
                        "required": ["id"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
                        "openWorldHint": False},
    },
    {
        "name": "create_idea",
        "title": "Create a synthetic idea note",
        "description": "Create a new idea note. The title must start with 'Synthetic' (V0 test scope). "
                       "Never overwrites an existing note.",
        "inputSchema": {"type": "object", "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 120},
            "description": {"type": "string", "minLength": 1, "maxLength": 200},
            "goal": {"type": "string", "minLength": 1, "maxLength": 2000},
            "next_action": {"type": "string", "minLength": 1, "maxLength": 1000},
        }, "required": ["title", "description", "goal", "next_action"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
                        "openWorldHint": False},
    },
    {
        "name": "update_idea",
        "title": "Replace a synthetic idea note (revision checked)",
        "description": "Replace the full Markdown of an existing synthetic idea note. expected_sha256 must be "
                       "the sha256 from your latest fetch; the write is refused if the note changed since. "
                       "Keep the frontmatter, including its id, created and provenance lines.",
        "inputSchema": {"type": "object", "properties": {
            "id": {"type": "string", "minLength": 1, "maxLength": MAX_ID_CHARS},
            "expected_sha256": {"type": "string", "minLength": 12, "maxLength": 64},
            "content": {"type": "string", "minLength": 1, "maxLength": MAX_NOTE_CHARS},
        }, "required": ["id", "expected_sha256", "content"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False,
                        "openWorldHint": False},
    },
]
TOOL_PROPS = {t["name"]: t["inputSchema"]["properties"] for t in TOOLS}


class ToolError(Exception):
    """Reported to the client as a tool result with isError: true."""


# ---------------------------------------------------------------- note access

def ideas_dir(root):
    cfg = memory.load_routes(root)
    try:
        return memory.category_dir(root, cfg, "ideas"), cfg
    except memory.MemoryError_:
        raise ToolError("ideas folder unavailable")


def is_exposable(folder, name):
    st = os.lstat(folder / name)
    return stat.S_ISREG(st.st_mode) and st.st_nlink == 1


def exposed_ids(root):
    """Exact on-disk names only: lowercase synthetic-*.md, regular, single link."""
    folder, _ = ideas_dir(root)
    return [name[:-3] for name in sorted(os.listdir(folder))
            if name.endswith(".md") and ID_RE.fullmatch(name[:-3]) and is_exposable(folder, name)]


def read_note(root, note_id):
    if not isinstance(note_id, str) or not ID_RE.fullmatch(note_id):
        raise ToolError("id must look like 'synthetic-<words>'; only synthetic idea notes are exposed")
    if note_id not in exposed_ids(root):
        raise ToolError(f"no such synthetic idea note: {note_id}")
    folder, cfg = ideas_dir(root)
    fd = os.open(folder / f"{note_id}.md", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)  # check the file actually opened, not the name
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise ToolError(f"no such synthetic idea note: {note_id}")
        if st.st_size > MAX_NOTE_BYTES:
            raise ToolError(f"note too large to serve: {note_id}")
        chunks = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
    finally:
        os.close(fd)
    try:
        text = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError:
        raise ToolError(f"note is not valid UTF-8: {note_id}")
    m = re.search(r"(?m)^title: (.+)$", text)
    title = m.group(1).strip() if m else note_id
    if title.startswith('"'):
        try:
            title = json.loads(title)
        except ValueError:
            pass
    return {
        "id": note_id,
        "title": title,
        "text": text,
        "path": f"{cfg['vault_dir']}/ideas/{note_id}.md",
        "sha256": memory.file_sha(text),
        "mtime": dt.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"),
    }


def note_url(note_id):
    return f"obsidian://open?vault=vault&file=ideas%2F{note_id}"


def frontmatter_lines(text, key):
    m = memory.FRONTMATTER_RE.match(text)
    if not m:
        return None
    return [ln for ln in m.group(1).split("\n") if re.match(rf"\s*{key}\s*:", ln)]


# ---------------------------------------------------------------- tools

def tool_search(root, args):
    words = args["query"].lower().split()
    results = []
    for note_id in exposed_ids(root):
        note = read_note(root, note_id)
        hay = f"{note_id} {note['title']} {note['text']}".lower()
        if all(w in hay for w in words):
            results.append({"id": note_id, "title": note["title"], "url": note_url(note_id)})
    return {"results": results}


def tool_fetch(root, args):
    note = read_note(root, args["id"])
    return {"id": note["id"], "title": note["title"], "text": note["text"], "url": note_url(note["id"]),
            "metadata": {"path": note["path"], "sha256": note["sha256"], "mtime": note["mtime"],
                         "chars": len(note["text"])}}


def run_memory(root, *argv):
    """Run memory.py with an argument list (never a shell). Returns stdout or raises ToolError."""
    proc = subprocess.run([sys.executable, str(MEMORY_PY), "--root", str(root), *argv],
                          capture_output=True, text=True, timeout=30)
    if proc.returncode == 0:
        return proc.stdout
    if proc.returncode == 2 and proc.stderr.startswith("error: "):
        message = proc.stderr.strip().removeprefix("error: ")
        for prefix in {str(root), str(Path(root).resolve())}:
            message = message.replace(prefix, "<repo>")
        raise ToolError(message)
    sys.stderr.write(f"bridge: memory.py exited {proc.returncode}:\n{proc.stderr}\n")
    raise ToolError("internal error in memory.py (details in the local terminal)")


def single_line(value, field):
    if not value.strip() or re.search(r"[\x00-\x1f\x7f|`]", value):
        raise ToolError(f"{field} must be one non-empty line, without | or `")
    return value.strip()


def fill_sections(text, goal, action):
    """Fill the template's Goal and Next action placeholders; exactly one of each, body only."""
    text, n_goal = re.subn(r"(?m)^## Goal\n\(one or two sentences\)$", lambda _: "## Goal\n" + goal, text)
    text, n_action = re.subn(r"(?m)^## Next action\n\(one exact, concrete action\)$",
                             lambda _: "## Next action\n" + action, text)
    if (n_goal, n_action) != (1, 1):
        raise ToolError("could not place goal/next_action in the template (remove headings from the text)")
    return text


def replace_with_revision_check(root, note_id, expected_sha, content):
    with tempfile.TemporaryDirectory(prefix="bridge-") as tmp:  # outside the vault, removed afterwards
        src = Path(tmp) / "note.md"
        src.write_text(content, encoding="utf-8")
        run_memory(root, "update", "idea", note_id, f"--expect={expected_sha}", f"--from={src}", f"--by={WRITER}")


def tool_create_idea(root, args):
    title = single_line(args["title"], "title")
    description = single_line(args["description"], "description")
    if not title[0].isalpha():
        raise ToolError("title must start with a letter")
    try:
        note_id = memory.slugify(title)
    except memory.MemoryError_ as e:
        raise ToolError(str(e))
    if not ID_RE.fullmatch(note_id):
        raise ToolError("V0 scope: title must start with 'Synthetic' so the note id starts with 'synthetic-'")
    if len(note_id) > MAX_ID_CHARS:
        raise ToolError(f"title too long: its id would exceed {MAX_ID_CHARS} characters")
    goal = re.sub(r"\r\n?", "\n", args["goal"]).strip()
    action = re.sub(r"\r\n?", "\n", args["next_action"]).strip()
    if not goal or not action or "\x00" in goal + action:
        raise ToolError("goal and next_action must be non-empty text")
    # Validate the filled note in memory before anything touches disk.
    folder, _ = ideas_dir(root)
    preview = fill_sections((folder / "_TEMPLATE.md").read_text(encoding="utf-8"), goal, action)
    if len(preview) + len(title) + 200 > MAX_NOTE_CHARS:
        raise ToolError(f"note would exceed {MAX_NOTE_CHARS} chars")
    run_memory(root, "new", "idea", title, f"--by={WRITER}", f"--description={description}")
    created = read_note(root, note_id)
    try:
        filled = fill_sections(created["text"], goal, action)
        replace_with_revision_check(root, note_id, created["sha256"], filled)
    except ToolError as e:
        raise ToolError(f"created {created['path']} but could not fill it: {e}")
    note = read_note(root, note_id)
    return {"id": note_id, "path": note["path"], "sha256": note["sha256"], "created": True}


def tool_update_idea(root, args):
    note_id, expected, content = args["id"], args["expected_sha256"].lower(), args["content"]
    current = read_note(root, note_id)  # scope check first
    if not SHA_RE.fullmatch(expected):
        raise ToolError("expected_sha256 must be 12-64 hex chars from your latest fetch")
    content = re.sub(r"\r\n?", "\n", content)
    if "\x00" in content:
        raise ToolError("content must be text")
    for key in PROTECTED_FRONTMATTER:
        if frontmatter_lines(content, key) != frontmatter_lines(current["text"], key):
            raise ToolError(f"content must keep the frontmatter and its '{key}:' line unchanged")
    replace_with_revision_check(root, note_id, expected, content)
    note = read_note(root, note_id)
    return {"id": note_id, "path": note["path"], "previous_sha256": current["sha256"], "sha256": note["sha256"]}


HANDLERS = {"search": tool_search, "fetch": tool_fetch,
            "create_idea": tool_create_idea, "update_idea": tool_update_idea}


def validate_args(name, args):
    props = TOOL_PROPS[name]
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    extra, missing = set(args) - set(props), set(props) - set(args)
    if extra or missing:
        raise ToolError(f"bad arguments: unexpected {sorted(map(str, extra))[:5]}, missing {sorted(missing)}")
    for key, value in args.items():
        if not isinstance(value, str):
            raise ToolError(f"{key} must be a string")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ToolError(f"{key} must be valid UTF-8 text")
        lo, hi = props[key].get("minLength", 0), props[key]["maxLength"]
        if not lo <= len(value) <= hi:
            raise ToolError(f"{key} must be {lo}-{hi} characters")


def call_tool(root, name, args):
    """Validate strictly, run the tool, return (payload dict, is_error). Never raises."""
    try:
        validate_args(name, args)
        return HANDLERS[name](root, args), False
    except ToolError as e:
        return {"error": str(e)}, True
    except Exception:
        sys.stderr.write(f"bridge: internal error in {name}:\n{traceback.format_exc()}")
        return {"error": "internal error (details in the local terminal)"}, True


# ---------------------------------------------------------------- JSON-RPC / MCP

class RpcError(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def modern_version(headers, msg):
    """Validate the 2026-07-28 per-request envelope (SDK ladder order); return the version."""
    params = msg.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    if not isinstance(meta, dict) or META_VERSION not in meta or META_CAPABILITIES not in meta:
        raise RpcError(-32602, f"params._meta must carry {META_VERSION!r} and {META_CAPABILITIES!r}")
    for header in ("MCP-Protocol-Version", "Mcp-Method", "Mcp-Name"):
        if len(headers.get_all(header) or []) > 1:
            raise RpcError(-32020, f"{header} header appears more than once")
    version = meta[META_VERSION]
    if headers.get("MCP-Protocol-Version") != version:
        raise RpcError(-32020, "MCP-Protocol-Version header does not match the request envelope's protocol version")
    if headers.get("Mcp-Method") != msg["method"]:
        raise RpcError(-32020, "Mcp-Method header does not match the request body's method")
    if msg["method"] == "tools/call" and params.get("name") is not None and headers.get("Mcp-Name") != params["name"]:
        raise RpcError(-32020, "Mcp-Name header does not match the request body's 'name' parameter")
    if not isinstance(version, str):
        raise RpcError(-32602, "protocol version must be a string")
    if version not in MODERN_VERSIONS:
        raise RpcError(-32022, "Unsupported protocol version",
                       {"supported": [*MODERN_VERSIONS, *HANDSHAKE_VERSIONS], "requested": version})
    return version


def tool_result(root, params, audit_entry):
    name = params.get("name")
    args = params.get("arguments", {})
    if not isinstance(name, str) or name not in HANDLERS:
        shown = name[:40] if isinstance(name, str) else type(name).__name__
        audit_entry.update(tool=shown, ok=False, error="unknown tool")
        return {"content": [{"type": "text", "text": f"Unknown tool: {shown}"}], "isError": True}
    payload, is_error = call_tool(root, name, args)
    audit_entry.update(tool=name, ok=not is_error)
    if isinstance(args, dict) and isinstance(args.get("id"), str) and ID_RE.fullmatch(args["id"]):
        audit_entry["id"] = args["id"]
    if name == "create_idea" and isinstance(args, dict) and isinstance(args.get("title"), str):
        try:
            audit_entry["id"] = memory.slugify(args["title"])[:100]
        except (memory.MemoryError_, UnicodeError):
            pass
    for key in ("sha256", "previous_sha256"):
        if key in payload:
            audit_entry[key] = payload[key][:12]
    if "metadata" in payload:
        audit_entry["sha256"] = payload["metadata"]["sha256"][:12]
    if "results" in payload:
        audit_entry["result_ids"] = [r["id"] for r in payload["results"]]
    if is_error:
        audit_entry["error"] = payload["error"][:200]
        return {"content": [{"type": "text", "text": payload["error"]}], "isError": True}
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "structuredContent": payload, "isError": False}


def dispatch(root, headers, msg, audit_entry):
    """Return (http_status, response dict) for a validated JSON-RPC request."""
    method, params = msg["method"], msg.get("params")
    header_version = headers.get("MCP-Protocol-Version")
    modern = header_version is not None and header_version not in HANDSHAKE_VERSIONS
    audit_entry.update(method=method[:60], era="2026" if modern else "handshake")
    try:
        if params is not None and not isinstance(params, dict):
            raise RpcError(-32602, "params must be an object")
        params = params or {}
        if modern:
            audit_entry["version"] = modern_version(headers, msg)
            extra = {"resultType": "complete", "_meta": {META_SERVER_INFO: SERVER_INFO}}
            cache = {"ttlMs": 0, "cacheScope": "private"}
            if method == "server/discover":
                result = {"supportedVersions": [*MODERN_VERSIONS, *HANDSHAKE_VERSIONS],
                          "capabilities": {"tools": {"listChanged": False}},
                          "instructions": INSTRUCTIONS, **cache, **extra}
            elif method == "tools/list":
                result = {"tools": TOOLS, **cache, **extra}
            elif method == "tools/call":
                result = {**tool_result(root, params, audit_entry), **extra}
            else:
                raise RpcError(-32601, f"Method not found: {method[:60]}")
        else:
            if method == "initialize":
                asked = params.get("protocolVersion")
                version = asked if asked in HANDSHAKE_VERSIONS else HANDSHAKE_VERSIONS[0]
                audit_entry["version"] = version
                result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = tool_result(root, params, audit_entry)
            else:
                raise RpcError(-32601, f"Method not found: {method[:60]}")
        return 200, {"jsonrpc": "2.0", "id": msg["id"], "result": result}
    except RpcError as e:
        audit_entry.update(ok=False, error=f"rpc {e.code}")
        error = {"code": e.code, "message": e.message}
        if e.data is not None:
            error["data"] = e.data
        return (MODERN_STATUS.get(e.code, 400) if modern else 200), {"jsonrpc": "2.0", "id": msg["id"], "error": error}


# ---------------------------------------------------------------- audit

class Auditor:
    """Append-only JSONL audit log: no note content, no tokens, bounded size."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.failed = {}  # redacted path -> count since last flush
        self.last_flush = float("-inf")
        self.capped = False

    def _append(self, entry):
        if self.capped:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > AUDIT_MAX_BYTES:
                self.capped = True
                entry = {"event": "audit_cap_reached", "max_bytes": AUDIT_MAX_BYTES}
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": memory.now_iso(), **entry}) + "\n")
        except OSError as e:
            sys.stderr.write(f"bridge: audit write failed: {type(e).__name__}\n")

    def _flush_failures(self):
        if self.failed:
            self._append({"event": "auth_failed", "count": sum(self.failed.values()), "paths": self.failed})
            self.failed = {}
        self.last_flush = time.monotonic()

    def record(self, entry):
        with self.lock:
            self._flush_failures()
            self._append(entry)

    def auth_failed(self, path):
        bare = path.split("?")[0]
        shown = bare if bare in KNOWN_PATHS else f"{ENDPOINT}/<redacted>" if bare.startswith(ENDPOINT + "/") else "<other>"
        with self.lock:
            self.failed[shown] = self.failed.get(shown, 0) + 1
            if time.monotonic() - self.last_flush >= AUTH_FAIL_WINDOW:
                self._flush_failures()

    def close(self):
        with self.lock:
            self._flush_failures()


# ---------------------------------------------------------------- HTTP

class Handler(http.server.BaseHTTPRequestHandler):
    server: "BridgeServer"
    timeout = RECV_TIMEOUT

    def version_string(self):  # no Server banner, even on the unauthenticated 404
        return ""

    def log_message(self, format, *args):  # no default access log (paths may carry the token)
        del format, args

    def _send(self, status, body=None, headers=None):
        data = b"" if body is None else json.dumps(body).encode("ascii")  # \u-escapes; never fails to encode
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _authorised(self):
        token = self.server.token.encode("utf-8")
        if self.path == ENDPOINT:
            presented = []
            auth = self.headers.get_all("Authorization") or []
            if len(auth) == 1:
                scheme, _, value = auth[0].partition(" ")
                if scheme.lower() == "bearer":
                    presented.append(value.strip())
            bridge_token = self.headers.get_all("X-Bridge-Token") or []
            if len(bridge_token) == 1:
                presented.append(bridge_token[0].strip())
            return any(hmac.compare_digest(p.encode("utf-8"), token) for p in presented)
        if self.path.startswith(ENDPOINT + "/"):
            return hmac.compare_digest(self.path[len(ENDPOINT) + 1:].encode("utf-8"), token)
        return False

    def _gate(self):
        """Auth first (plain 404 on failure, so ChatGPT is not sent into OAuth discovery), then Origin."""
        if not self._authorised():
            self.server.auditor.auth_failed(self.path)
            self._send(404, {"error": "not found"})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            self._send(403, {"error": "origin not allowed"})
            return False
        return True

    def do_POST(self):
        if not self._gate():
            return
        if self.headers.get("Transfer-Encoding"):
            return self._send(411, {"error": "Content-Length required"})
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._send(411, {"error": "Content-Length required"})
        if length < 0 or length > MAX_BODY_BYTES:
            return self._send(413, {"error": f"body over {MAX_BODY_BYTES} bytes"})
        raw = self.rfile.read(length)
        try:
            msg = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError):  # includes JSON and UTF-8 decode errors
            return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        if isinstance(msg, dict) and "id" not in msg and ("method" in msg or "result" in msg or "error" in msg):
            return self._send(202)  # notification or client response: acknowledged, nothing to return
        req_id = msg.get("id") if isinstance(msg, dict) else None
        if (not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str)
                or not (isinstance(req_id, str) or (isinstance(req_id, int) and not isinstance(req_id, bool)))):
            return self._send(400, {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32600, "message": "Invalid request (batches not supported)"}})
        entry = {"event": "rpc", "ua": (self.headers.get("User-Agent") or "")[:80]}
        if self.headers.get("Origin"):
            entry["origin"] = self.headers["Origin"][:60]
        with self.server.tool_lock:  # one request at a time: writes are serialised
            status, response = dispatch(self.server.root, self.headers, msg, entry)
        self.server.auditor.record(entry)
        self._send(status, response)

    def _not_allowed(self):
        if self._gate():
            self._send(405, {"error": "use POST"}, {"Allow": "POST"})

    do_GET = do_DELETE = do_PUT = do_PATCH = do_HEAD = do_OPTIONS = _not_allowed


def check_token(token):
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token) or len(set(token)) < 16:
        raise SystemExit("refusing to start: token must be 32+ chars of [A-Za-z0-9_-] with 16+ distinct "
                         "characters (run `python3 bridge.py init-token`)")


class BridgeServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root, token, port, max_connections=MAX_CONNECTIONS):
        check_token(token)
        self.root = Path(root).resolve()
        self.token = token
        self.auditor = Auditor(self.root / "out" / "bridge-audit.jsonl")
        self.tool_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(max_connections)
        super().__init__((HOST, port), Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)  # over capacity: close at once
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        def expire():
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(CONNECTION_DEADLINE, expire)
        timer.daemon = True
        timer.start()
        try:
            super().process_request_thread(request, client_address)
        finally:
            timer.cancel()
            self.slots.release()

    def handle_error(self, request, client_address):
        # One line instead of a traceback: dropped or expired connections are routine.
        sys.stderr.write(f"bridge: connection error: {sys.exc_info()[0].__name__}\n")

    def server_close(self):
        super().server_close()
        self.auditor.close()


# ---------------------------------------------------------------- CLI

def load_token(token_file):
    env = (os.environ.get(TOKEN_ENV) or "").strip() or None
    from_file = None
    if token_file.exists() or token_file.is_symlink():
        st = os.lstat(token_file)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise SystemExit(f"refusing token file {token_file}: must be a regular file you own with mode 600")
        from_file = token_file.read_text(encoding="utf-8").strip() or None
    if env and from_file and env != from_file:
        raise SystemExit(f"refusing to start: ${TOKEN_ENV} and {token_file} hold different tokens")
    return env or from_file


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help=argparse.SUPPRESS)
    ap.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-token")
    sub.add_parser("exposure")
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args(argv)
    root = Path(args.root).resolve() if args.root else memory.SCRIPT_ROOT

    if args.cmd == "init-token":
        path = args.token_file
        if path.exists() or path.is_symlink():
            print(f"{path} already exists; not changed")
            return 0
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_urlsafe(32) + "\n")
        print(f"wrote {path} (mode 600); view it only when entering it in the connector settings")
        return 0
    if args.cmd == "exposure":
        ids = exposed_ids(root)
        print(f"tools: {', '.join(t['name'] for t in TOOLS)} (no delete)")
        print(f"scope: vault/ideas/synthetic-*.md, exact names, regular single-link files; {len(ids)} note(s):")
        for note_id in ids:
            note = read_note(root, note_id)
            print(f"  {note['path']}  sha256 {note['sha256'][:12]}  {len(note['text'])} chars")
        return 0
    server = BridgeServer(root, load_token(args.token_file), args.port)
    print(f"serving {len(exposed_ids(root))} synthetic idea note(s) at http://{HOST}:{server.server_port}{ENDPOINT} "
          f"(token required; audit log out/bridge-audit.jsonl). Ctrl-C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
