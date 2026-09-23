"""Tests for bridge.py against a live local server on a temporary synthetic fixture."""
import http.client
import json
import os
import shutil
import socket
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import bridge
import memory

REPO = Path(__file__).resolve().parent
TOKEN = "t" * 20 + "-synthetic-test-token-0123456789"
SECRETS = ("REAL-IDEA-SECRET", "JOB-SECRET", "STATE-SECRET", "OUTSIDE-SECRET", "PROJECT-SECRET", "CASE-SECRET",
           "HARDLINK-SECRET")
MODERN = "2026-07-28"


def note(note_id, marker, provenance="fixture"):
    return (f"---\nid: idea-{note_id}\ntitle: {note_id.replace('-', ' ').title()}\ncreated: x\nupdated: x\n"
            f"status: captured\nprovenance: {provenance}\n---\n# {note_id}\n\n## Next action\n{marker}\n")


def modern_meta():
    return {"_meta": {bridge.META_VERSION: MODERN, bridge.META_CAPABILITIES: {}}}


class BridgeFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "repo"
        self.root.mkdir()
        shutil.copy(REPO / "routes.json", self.root)
        shutil.copy(REPO / "ROUTING.md", self.root)
        v = self.root / "vault"
        for cat in ["ideas", "jobs", "projects", "sessions"]:
            (v / cat).mkdir(parents=True)
            shutil.copy(REPO / "vault" / cat / "_TEMPLATE.md", v / cat)
            (v / cat / "INDEX.md").write_text(f"# {cat}\n| title | path | description |\n| --- | --- | --- |\n"
                                              f"| Real | `{cat}/real.md` | keep this row |\n")
        (v / "CURRENT_STATE.md").write_text("STATE-SECRET\n")
        (v / "TASKS.md").write_text("tasks\n")
        (v / "ideas" / "synthetic-alpha.md").write_text(note("synthetic-alpha", "SYN-ALPHA-1"))
        (v / "ideas" / "synthetic-beta.md").write_text(note("synthetic-beta", "SYN-BETA-1"))
        (v / "ideas" / "real-idea.md").write_text(note("real-idea", "REAL-IDEA-SECRET"))
        (v / "jobs" / "synthetic-job.md").write_text("JOB-SECRET\n")
        (v / "projects" / "synthetic-project.md").write_text("PROJECT-SECRET\n")
        (self.tmp / "outside.md").write_text("OUTSIDE-SECRET\n")
        os.symlink(v / "jobs" / "synthetic-job.md", v / "ideas" / "synthetic-link-job.md")
        os.symlink(self.tmp / "outside.md", v / "ideas" / "synthetic-link-outside.md")
        self.v = v
        self.start_server()
        self.bodies = []  # every response body, checked for leaks in tearDown

    def start_server(self):
        self.server = bridge.BridgeServer(self.root, TOKEN, 0)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for body in self.bodies:
            for secret in SECRETS:
                self.assertNotIn(secret, body, "private fixture content leaked through the bridge")
        shutil.rmtree(self.tmp)

    def http(self, method="POST", path="/mcp", body=None, token=TOKEN, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if token is not None:
            hdrs["Authorization"] = f"Bearer {token}"
        hdrs.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        text = resp.read().decode()
        conn.close()
        self.bodies.append(text)
        return resp.status, dict(resp.getheaders()), text

    def rpc(self, method, params=None, req_id=1, expect_status=200, **kw):
        status, _, text = self.http(body={"jsonrpc": "2.0", "id": req_id, "method": method,
                                          "params": params or {}}, **kw)
        self.assertEqual(status, expect_status, text)
        return json.loads(text)

    def modern(self, method, params=None, headers=None, expect_status=200):
        params = {**(params or {}), **modern_meta()}
        hdrs = {"MCP-Protocol-Version": MODERN, "Mcp-Method": method}
        if method == "tools/call":
            hdrs["Mcp-Name"] = params.get("name", "")
        hdrs.update(headers or {})
        return self.rpc(method, params, headers=hdrs, expect_status=expect_status)

    def call(self, name, arguments):
        """Return (payload dict or error text, isError)."""
        res = self.rpc("tools/call", {"name": name, "arguments": arguments})
        self.assertIn("result", res, res)
        result = res["result"]
        if result["isError"]:
            return {"error": result["content"][0]["text"]}, True
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        return result["structuredContent"], False

    def audit(self):
        self.server.auditor.close()
        path = self.root / "out" / "bridge-audit.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def files(self, category="ideas"):
        return sorted(p.name for p in (self.v / category).iterdir())


class TestAuthAndTransport(BridgeFixture):
    def test_refuses_weak_or_missing_tokens(self):
        for bad in (None, "", "short-token", "a" * 40, "password" * 5, "x" * 20 + "!@#$%^&*()" * 2):
            with self.assertRaises(SystemExit):
                bridge.BridgeServer(self.root, bad, 0)

    def test_token_file_must_be_private_and_consistent(self):
        path = self.tmp / "token"
        path.write_text(TOKEN + "\n")
        os.chmod(path, 0o644)
        with self.assertRaises(SystemExit):
            bridge.load_token(path)
        os.chmod(path, 0o600)
        with mock.patch.dict(os.environ, {bridge.TOKEN_ENV: ""}):
            self.assertEqual(bridge.load_token(path), TOKEN)
        with mock.patch.dict(os.environ, {bridge.TOKEN_ENV: TOKEN + "x"}):
            with self.assertRaises(SystemExit):
                bridge.load_token(path)
        os.symlink(path, self.tmp / "link")
        with self.assertRaises(SystemExit):
            bridge.load_token(self.tmp / "link")

    def test_init_token_writes_private_file_and_never_prints_it(self):
        path = self.tmp / "cfg" / "token"
        with mock.patch("sys.stdout") as out:
            bridge.main(["--token-file", str(path), "init-token"])
        printed = "".join(c.args[0] for c in out.write.call_args_list)
        token = path.read_text().strip()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertNotIn(token, printed)
        bridge.check_token(token)

    def test_binds_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_every_method_and_path_requires_the_token(self):
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        paths = ["/mcp", "/", "/mcp/", "/mcp/wrong", f"/mcp/{TOKEN}x", f"/mcp/{TOKEN[:-1]}", f"/MCP/{TOKEN}",
                 f"/mcp?t={TOKEN}", "/.well-known/oauth-protected-resource", f"/mcp//{TOKEN}"]
        for method in ("POST", "GET", "DELETE", "PUT", "OPTIONS"):
            for path in paths:
                for token in (None, "wrong-" + TOKEN, TOKEN + "x"):
                    status, headers, text = self.http(method, path, body=init if method == "POST" else None,
                                                      token=token)
                    self.assertEqual(status, 404, (method, path, token))
                    self.assertNotIn("WWW-Authenticate", headers)
                    self.assertNotIn("result", text)

    def test_three_ways_to_present_the_token(self):
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        self.assertEqual(self.http(body=ping)[0], 200)
        self.assertEqual(self.http(body=ping, token=None, headers={"X-Bridge-Token": TOKEN})[0], 200)
        self.assertEqual(self.http(path=f"/mcp/{TOKEN}", body=ping, token=None)[0], 200)
        # wrong scheme, duplicated headers
        self.assertEqual(self.http(body=ping, token=None, headers={"Authorization": f"Basic {TOKEN}"})[0], 404)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest("POST", "/mcp")
        for value in (f"Bearer {TOKEN}", "Bearer other"):
            conn.putheader("Authorization", value)
        data = json.dumps(ping).encode()
        conn.putheader("Content-Length", str(len(data)))
        conn.endheaders(data)
        self.assertEqual(conn.getresponse().status, 404)
        conn.close()

    def test_failed_auth_is_aggregated_and_redacted(self):
        for _ in range(200):
            self.http(path=f"/mcp/guess-{TOKEN[:5]}", body={}, token=None)
        entries = self.audit()
        self.assertLessEqual(len(entries), 2)
        self.assertEqual(sum(e["count"] for e in entries if e["event"] == "auth_failed"), 200)
        log = json.dumps(entries)
        self.assertIn("/mcp/<redacted>", log)
        self.assertNotIn("guess-", log)
        self.assertNotIn(TOKEN, log)

    def test_audit_file_is_size_capped(self):
        with mock.patch.object(bridge, "AUDIT_MAX_BYTES", 2000):
            for _ in range(40):
                self.call("fetch", {"id": "synthetic-alpha"})
        path = self.root / "out" / "bridge-audit.jsonl"
        self.assertLess(path.stat().st_size, 3000)
        self.assertIn("audit_cap_reached", path.read_text())

    def test_slow_clients_do_not_block_authorised_requests(self):
        drip = socket.create_connection(("127.0.0.1", self.port))
        drip.sendall(b"P")
        silent = [socket.create_connection(("127.0.0.1", self.port)) for _ in range(3)]
        start = time.monotonic()
        self.assertEqual(self.http(body={"jsonrpc": "2.0", "id": 1, "method": "ping"})[0], 200)
        self.assertLess(time.monotonic() - start, 2)
        for s in [drip, *silent]:
            s.close()

    def test_connection_deadline_closes_dripping_clients(self):
        self.server.shutdown()
        self.server.server_close()
        with mock.patch.object(bridge, "CONNECTION_DEADLINE", 1):
            self.start_server()
            s = socket.create_connection(("127.0.0.1", self.port))
            s.settimeout(5)
            start = time.monotonic()
            closed = False
            while time.monotonic() - start < 4:
                try:
                    s.sendall(b"P")
                    if s.recv(1024) == b"":
                        closed = True
                        break
                except socket.timeout:
                    continue
                except OSError:
                    closed = True
                    break
            s.close()
            self.assertTrue(closed)
            self.assertLess(time.monotonic() - start, 3)

    def test_connection_cap(self):
        self.server.shutdown()
        self.server.server_close()
        self.server = bridge.BridgeServer(self.root, TOKEN, 0, max_connections=2)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        held = [socket.create_connection(("127.0.0.1", self.port)) for _ in range(2)]
        time.sleep(0.2)
        extra = socket.create_connection(("127.0.0.1", self.port))
        extra.settimeout(3)
        self.assertEqual(extra.recv(10), b"")  # closed immediately
        for s in [*held, extra]:
            s.close()

    def test_token_in_near_miss_path_never_reaches_the_audit_log(self):
        for path in (f"/mcp{TOKEN}", f"/MCP/{TOKEN}", f"/{TOKEN}", f"/mcp%2F{TOKEN}", f"/sse/{TOKEN}",
                     f"/.well-known/oauth-protected-resource/mcp/{TOKEN}", f"/mcp/{TOKEN}x"):
            self.assertEqual(self.http(path=path, body={}, token=None)[0], 404)
        log = json.dumps(self.audit())
        self.assertNotIn(TOKEN[:12], log)
        self.assertIn("<other>", log)

    def test_unauthenticated_404_has_no_server_banner(self):
        status, headers, _ = self.http(token=None, body={})
        self.assertEqual(status, 404)
        self.assertNotIn("context-router", headers.get("Server", "").lower())
        self.assertNotIn("python", headers.get("Server", "").lower())

    def test_lone_surrogate_in_echoed_fields_still_gets_a_reply(self):
        raw = ('{"jsonrpc":"2.0","id":"r\\ud800","method":"tools/call","params":{"name":"create_idea",'
               '"arguments":{"title":"Synthetic surrogate id","description":"d","goal":"g","next_action":"n"}}}')
        status, _, text = self.http(raw=raw.encode())
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(text)["result"]["isError"])
        for raw in ('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"x\\ud800","arguments":{}}}',
                    '{"jsonrpc":"2.0","id":1,"method":"m\\ud800"}'):
            status, _, text = self.http(raw=raw.encode())
            self.assertEqual(status, 200, raw)
            json.loads(text)

    def test_handshake_era_initialize_and_version_negotiation(self):
        res = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "t", "version": "0"}})
        self.assertEqual(res["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", res["result"]["capabilities"])
        self.assertLessEqual(len(res["result"]["instructions"]), 512)
        res = self.rpc("initialize", {"protocolVersion": "1999-01-01"})
        self.assertEqual(res["result"]["protocolVersion"], "2025-11-25")

    def test_2026_era_discover_list_and_call(self):
        res = self.modern("server/discover")["result"]
        self.assertIn(MODERN, res["supportedVersions"])
        self.assertEqual((res["resultType"], res["ttlMs"], res["cacheScope"]), ("complete", 0, "private"))
        self.assertEqual(res["_meta"][bridge.META_SERVER_INFO]["name"], "context-router-bridge")
        tools = self.modern("tools/list")["result"]
        self.assertEqual(tools["resultType"], "complete")
        self.assertEqual({t["name"] for t in tools["tools"]}, {"search", "fetch", "create_idea", "update_idea"})
        call = self.modern("tools/call", {"name": "fetch", "arguments": {"id": "synthetic-alpha"}})["result"]
        self.assertEqual(call["resultType"], "complete")
        self.assertFalse(call["isError"])
        self.assertIn("SYN-ALPHA-1", call["structuredContent"]["text"])
        self.assertEqual(self.audit()[-1]["era"], "2026")

    def test_2026_era_envelope_ladder(self):
        # missing _meta
        res = self.rpc("tools/list", {}, headers={"MCP-Protocol-Version": MODERN, "Mcp-Method": "tools/list"},
                       expect_status=400)
        self.assertEqual(res["error"]["code"], -32602)
        # header/body disagreement
        res = self.modern("tools/list", headers={"Mcp-Method": "tools/call"}, expect_status=400)
        self.assertEqual(res["error"]["code"], -32020)
        res = self.modern("tools/call", {"name": "fetch", "arguments": {"id": "synthetic-alpha"}},
                          headers={"Mcp-Name": "search"}, expect_status=400)
        self.assertEqual(res["error"]["code"], -32020)
        # unsupported modern version: -32022 with a supported list that lets clients fall back
        res = self.rpc("tools/list", {"_meta": {bridge.META_VERSION: "2099-01-01", bridge.META_CAPABILITIES: {}}},
                       headers={"MCP-Protocol-Version": "2099-01-01", "Mcp-Method": "tools/list"}, expect_status=400)
        self.assertEqual(res["error"]["code"], -32022)
        self.assertIn("2025-11-25", res["error"]["data"]["supported"])
        # unknown method: 404 + -32601 in the 2026 era, 200 + -32601 in the handshake era
        self.assertEqual(self.modern("resources/list", expect_status=404)["error"]["code"], -32601)
        self.assertEqual(self.modern("ping", expect_status=404)["error"]["code"], -32601)
        self.assertEqual(self.rpc("server/discover")["error"]["code"], -32601)

    def test_notification_and_client_response_get_202(self):
        for body in ({"jsonrpc": "2.0", "method": "notifications/initialized"},
                     {"jsonrpc": "2.0", "result": {}}):
            status, _, text = self.http(body=body)
            self.assertEqual((status, text), (202, ""))

    def test_protocol_errors(self):
        status, _, text = self.http(raw=b"{not json")
        self.assertEqual((status, json.loads(text)["error"]["code"]), (400, -32700))
        for raw in (b'"x"', b"[1]", json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"}]).encode(),
                    b'{"jsonrpc":"2.0","id":true,"method":"ping"}', b'{"jsonrpc":"2.0","id":[1],"method":"ping"}',
                    b'{"jsonrpc":"2.0","id":1,"method":7}', b'{"jsonrpc":"1.0","id":1,"method":"ping"}'):
            status, _, text = self.http(raw=raw)
            self.assertEqual((status, json.loads(text)["error"]["code"]), (400, -32600), raw)
        self.assertEqual(self.rpc("ping", params=None)["result"], {})
        self.assertEqual(self.rpc("tools/list", params=[1])["error"]["code"], -32602)
        self.assertEqual(self.http("GET")[0], 405)
        self.assertEqual(self.http(raw=b"[" * 60000)[0], 400)  # deep nesting under the size cap
        self.assertEqual(self.rpc("ping")["result"], {})  # still alive

    def test_oversize_and_chunked_bodies_rejected(self):
        self.assertEqual(self.http(raw=b"x" * (bridge.MAX_BODY_BYTES + 1))[0], 413)
        self.assertEqual(self.http(raw=b"{}", headers={"Transfer-Encoding": "chunked"})[0], 411)

    def test_foreign_origin_rejected(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        self.assertEqual(self.http(body=body, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.http(body=body, headers={"Origin": "https://chatgpt.com"})[0], 200)


class TestSurface(BridgeFixture):
    def test_tool_list_is_exactly_four_without_delete_or_paths(self):
        tools = self.rpc("tools/list")["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, {"search", "fetch", "create_idea", "update_idea"})
        banned = {"path", "file", "filename", "dir", "directory", "category", "kind", "root", "route"}
        for t in tools:
            self.assertNotRegex(t["name"], "delete|remove|rm|unlink|move|rename")
            self.assertFalse(t["inputSchema"]["additionalProperties"])
            self.assertFalse(banned & set(t["inputSchema"]["properties"]), t["name"])
            self.assertEqual(t["annotations"]["readOnlyHint"], t["name"] in ("search", "fetch"))
            self.assertFalse(t["annotations"]["openWorldHint"])

    def test_delete_and_unknown_tools_rejected(self):
        for name in ("delete_idea", "delete", "remove", "write_file", "read_file", 7, None, ["fetch"]):
            res = self.rpc("tools/call", {"name": name, "arguments": {"id": "synthetic-alpha"}})
            self.assertTrue(res["result"]["isError"], name)
        self.assertIn("synthetic-alpha.md", self.files())

    def test_bad_arguments_rejected_and_audited(self):
        for args in ({"id": "synthetic-alpha", "path": "vault/jobs/synthetic-job.md"},
                     {"id": "synthetic-alpha", "category": "jobs"}, {"id": ["synthetic-alpha"]}, {},
                     {"id": "x" * 10000}, {"id": "synthetic-\udcff"}, "synthetic-alpha", ["synthetic-alpha"]):
            payload, is_error = self.call("fetch", args)
            self.assertTrue(is_error, repr(args)[:80])
        entries = [e for e in self.audit() if e.get("tool") == "fetch"]
        self.assertEqual(len(entries), 8)
        self.assertFalse(any(e["ok"] for e in entries))


class TestReads(BridgeFixture):
    def test_search_lists_only_synthetic_regular_idea_notes(self):
        payload, is_error = self.call("search", {"query": ""})
        self.assertFalse(is_error)
        self.assertEqual([r["id"] for r in payload["results"]], ["synthetic-alpha", "synthetic-beta"])
        payload, _ = self.call("search", {"query": "SYN-BETA-1"})
        self.assertEqual([r["id"] for r in payload["results"]], ["synthetic-beta"])
        for q in ("REAL-IDEA-SECRET", "JOB-SECRET", "STATE-SECRET", "real", "job"):
            payload, _ = self.call("search", {"query": q})
            self.assertEqual(payload["results"], [], q)

    def test_fetch_returns_content_path_and_revision(self):
        payload, is_error = self.call("fetch", {"id": "synthetic-alpha"})
        self.assertFalse(is_error)
        text = (self.v / "ideas" / "synthetic-alpha.md").read_text()
        self.assertEqual(payload["text"], text)
        self.assertNotIn("SYN-BETA-1", payload["text"])
        self.assertEqual(payload["metadata"]["path"], "vault/ideas/synthetic-alpha.md")
        self.assertEqual(payload["metadata"]["sha256"], memory.file_sha(text))
        self.assertNotIn(str(self.tmp), json.dumps(payload))

    def test_fetch_sees_disk_edit_immediately(self):
        (self.v / "ideas" / "synthetic-alpha.md").write_text(note("synthetic-alpha", "SYN-ALPHA-2"))
        payload, _ = self.call("fetch", {"id": "synthetic-alpha"})
        self.assertIn("SYN-ALPHA-2", payload["text"])
        self.assertNotIn("SYN-ALPHA-1", payload["text"])

    def test_fetch_rejects_everything_outside_scope(self):
        attacks = ["real-idea", "synthetic-link-job", "synthetic-link-outside", "synthetic-missing",
                   "../jobs/synthetic-job", "synthetic-alpha/../../jobs/synthetic-job", "synthetic-alpha.md",
                   "vault/ideas/synthetic-alpha.md", "/etc/passwd", "~/secret", "INDEX", "_TEMPLATE",
                   "synthetic-job", "synthetic-project", "../CURRENT_STATE", "Synthetic-Alpha",
                   "synthetic-alpha\n", "synthetic-alpha%2f..", "synthetic-", "synthetic-alpha\x00"]
        for note_id in attacks:
            payload, is_error = self.call("fetch", {"id": note_id})
            self.assertTrue(is_error, note_id)
            self.assertNotIn("text", payload)

    def test_case_folded_and_lookalike_names_are_not_reachable(self):
        ideas = self.v / "ideas"
        (ideas / "Synthetic-Budget.md").write_text(note("synthetic-budget", "CASE-SECRET"))
        (ideas / "synthetic-Key.md").write_text(note("synthetic-key", "CASE-SECRET"))
        (ideas / "ſynthetic-plan.md").write_text(note("synthetic-plan", "CASE-SECRET"))
        for note_id in ("synthetic-budget", "synthetic-key", "synthetic-plan"):
            payload, is_error = self.call("fetch", {"id": note_id})
            self.assertTrue(is_error, note_id)
            payload, is_error = self.call("update_idea", {"id": note_id, "expected_sha256": "0" * 12,
                                                          "content": note(note_id, "X")})
            self.assertTrue(is_error, note_id)
        payload, _ = self.call("search", {"query": ""})
        self.assertEqual([r["id"] for r in payload["results"]], ["synthetic-alpha", "synthetic-beta"])

    def test_symlinked_ideas_dir_and_hardlinks_are_refused(self):
        os.link(self.v / "jobs" / "synthetic-job.md", self.v / "ideas" / "synthetic-hard.md")
        self.assertTrue(self.call("fetch", {"id": "synthetic-hard"})[1])
        os.remove(self.v / "ideas" / "synthetic-hard.md")
        (self.v / "jobs" / "synthetic-leak.md").write_text(note("synthetic-leak", "HARDLINK-SECRET"))
        shutil.move(self.v / "ideas", self.tmp / "ideas-real")
        os.symlink(self.v / "jobs", self.v / "ideas")
        payload, is_error = self.call("fetch", {"id": "synthetic-leak"})
        self.assertTrue(is_error)
        self.assertTrue(self.call("search", {"query": ""})[1])


class TestWrites(BridgeFixture):
    def create(self, title="Synthetic kite test", **kw):
        args = {"title": title, "description": "Bridge write test.", "goal": "Check GPT can create notes.",
                "next_action": "Fly marker GPT-KITE-TEST", **kw}
        return self.call("create_idea", args)

    def test_create_idea_via_memory_new_and_update(self):
        payload, is_error = self.create()
        self.assertFalse(is_error, payload)
        path = self.v / "ideas" / "synthetic-kite-test.md"
        text = path.read_text()
        self.assertEqual(payload["path"], "vault/ideas/synthetic-kite-test.md")
        self.assertEqual(payload["sha256"], memory.file_sha(text))
        front = text.split("---\n")[1]
        self.assertIn("provenance: created by chatgpt via memory.py", front)
        self.assertIn("last_writer: chatgpt", front)
        self.assertIn("## Goal\nCheck GPT can create notes.", text)
        self.assertIn("## Next action\nFly marker GPT-KITE-TEST", text)
        index = (self.v / "ideas" / "INDEX.md").read_text()
        self.assertIn("| Bridge write test. |", index)
        self.assertIn("keep this row", index)
        self.assertIn("GPT-KITE-TEST", self.call("fetch", {"id": "synthetic-kite-test"})[0]["text"])
        created = [e for e in self.audit() if e.get("tool") == "create_idea"]
        self.assertEqual(created[-1]["id"], "synthetic-kite-test")

    def test_create_refuses_non_synthetic_duplicate_and_bad_fields(self):
        before_files, before_index = self.files(), (self.v / "ideas" / "INDEX.md").read_text()
        for title in ("My real idea", "Real synthetic idea", "-synthetic x", "Synthetic | pipe",
                      "Synthetic\nnewline", "Synthetic `tick`", "   ", "Synthetic \udcff", "S" * 121):
            self.assertTrue(self.create(title)[1], repr(title))
        for field, value in (("description", "a|b"), ("description", "bad \udcff"), ("goal", "G" * 2001),
                             ("next_action", "N" * 1001), ("goal", " "), ("goal", "x\udcff")):
            self.assertTrue(self.create(**{field: value})[1], (field, value[:10]))
        self.assertEqual(self.files(), before_files)
        self.assertEqual((self.v / "ideas" / "INDEX.md").read_text(), before_index)
        before = (self.v / "ideas" / "synthetic-alpha.md").read_text()
        payload, is_error = self.create("Synthetic alpha")
        self.assertTrue(is_error)
        self.assertIn("refusing to overwrite", payload["error"])
        self.assertEqual((self.v / "ideas" / "synthetic-alpha.md").read_text(), before)

    def test_create_places_text_in_body_only(self):
        payload, is_error = self.create("Synthetic (one or two sentences) ## Next action",
                                        goal="provenance: owner-reported", next_action="GPT-ACTION-X")
        self.assertFalse(is_error, payload)
        text = (self.v / "ideas" / payload["path"].split("/")[-1]).read_text()
        front, body = text.split("---\n")[1], text.split("---\n", 2)[2]
        self.assertIn("provenance: created by chatgpt", front)
        self.assertNotIn("owner-reported", front)
        self.assertIn("## Goal\nprovenance: owner-reported", body)
        self.assertIn("## Next action\nGPT-ACTION-X", body)
        self.assertTrue(self.create("Synthetic other", goal="x\n## Next action\n(one exact, concrete action)")[1])

    def test_update_with_current_revision(self):
        fetched, _ = self.call("fetch", {"id": "synthetic-alpha"})
        new = fetched["text"].replace("SYN-ALPHA-1", "SYN-ALPHA-GPT")
        payload, is_error = self.call("update_idea", {"id": "synthetic-alpha",
                                                      "expected_sha256": fetched["metadata"]["sha256"],
                                                      "content": new})
        self.assertFalse(is_error, payload)
        text = (self.v / "ideas" / "synthetic-alpha.md").read_text()
        self.assertIn("SYN-ALPHA-GPT", text)
        self.assertIn("last_writer: chatgpt", text.split("---\n")[1])
        self.assertEqual(payload["previous_sha256"], fetched["metadata"]["sha256"])
        self.assertEqual(payload["sha256"], memory.file_sha(text))

    def test_update_stamps_writer_even_if_updated_line_dropped(self):
        fetched, _ = self.call("fetch", {"id": "synthetic-alpha"})
        new = fetched["text"].replace("updated: x\n", "").replace("SYN-ALPHA-1", "NO-UPDATED-LINE")
        payload, is_error = self.call("update_idea", {"id": "synthetic-alpha",
                                                      "expected_sha256": fetched["metadata"]["sha256"],
                                                      "content": new})
        self.assertFalse(is_error, payload)
        front = (self.v / "ideas" / "synthetic-alpha.md").read_text().split("---\n")[1]
        self.assertIn("last_writer: chatgpt", front)
        self.assertRegex(front, r"updated: \d{4}-")

    def test_update_cannot_forge_attribution(self):
        fetched, _ = self.call("fetch", {"id": "synthetic-alpha"})
        sha, text = fetched["metadata"]["sha256"], fetched["text"]
        forgeries = [text.replace("provenance: fixture", "provenance: owner-reported"),
                     text.replace("created: x", "created: 2020-01-01"),
                     text.replace("provenance: fixture\n", ""),
                     text.replace("id: idea-synthetic-alpha", "id: idea-synthetic-beta"),
                     text.replace("---\n", "", 2),
                     "---\nid: idea-synthetic-alpha\n---\n" + text]
        for content in forgeries:
            payload, is_error = self.call("update_idea", {"id": "synthetic-alpha", "expected_sha256": sha,
                                                          "content": content})
            self.assertTrue(is_error, content[:60])
        self.assertEqual((self.v / "ideas" / "synthetic-alpha.md").read_text(), text)

    def test_update_lone_cr_cannot_forge_frontmatter(self):
        fetched, _ = self.call("fetch", {"id": "synthetic-alpha"})
        text = fetched["text"]
        forged = ("---\ntitle: x\rid: idea-synthetic-alpha\rcreated: 1999-01-01\rprovenance: owner-reported\r---\r"
                  "<!--\n" + text.split("---\n")[1] + "---\n-->\nbody\n")
        payload, is_error = self.call("update_idea", {"id": "synthetic-alpha",
                                                      "expected_sha256": fetched["metadata"]["sha256"],
                                                      "content": forged})
        self.assertTrue(is_error)
        self.assertEqual((self.v / "ideas" / "synthetic-alpha.md").read_text(), text)

    def test_update_yaml_tricks_cannot_change_protected_keys(self):
        fetched, _ = self.call("fetch", {"id": "synthetic-alpha"})
        sha, text = fetched["metadata"]["sha256"], fetched["text"]
        for extra in ('"provenance": owner-reported\n', "? provenance\n", "  provenance: owner-reported\n",
                      "provenance : owner-reported\n"):
            content = text.replace("status: captured\n", "status: captured\n" + extra)
            self.assertTrue(self.call("update_idea", {"id": "synthetic-alpha", "expected_sha256": sha,
                                                      "content": content})[1], extra)
        self.assertEqual((self.v / "ideas" / "synthetic-alpha.md").read_text(), text)

    def test_create_quotes_title_and_limits_id_length(self):
        payload, is_error = self.create("Synthetic: colon # hash")
        self.assertFalse(is_error, payload)
        fetched, _ = self.call("fetch", {"id": payload["id"]})
        self.assertEqual(fetched["title"], "Synthetic: colon # hash")
        self.assertTrue(self.create("Synthetic " + "m" * 105)[1])

    def test_oversized_note_is_not_served(self):
        (self.v / "ideas" / "synthetic-huge.md").write_text(note("synthetic-huge", "x" * (bridge.MAX_NOTE_BYTES + 1)))
        self.assertTrue(self.call("fetch", {"id": "synthetic-huge"})[1])

    def test_update_refuses_stale_revision(self):
        fetched, _ = self.call("fetch", {"id": "synthetic-alpha"})
        (self.v / "ideas" / "synthetic-alpha.md").write_text(note("synthetic-alpha", "EDITED-IN-OBSIDIAN"))
        payload, is_error = self.call("update_idea", {
            "id": "synthetic-alpha", "expected_sha256": fetched["metadata"]["sha256"],
            "content": fetched["text"].replace("SYN-ALPHA-1", "STALE-GPT")})
        self.assertTrue(is_error)
        self.assertIn("conflict", payload["error"])
        self.assertIn("EDITED-IN-OBSIDIAN", (self.v / "ideas" / "synthetic-alpha.md").read_text())

    def test_update_rejects_out_of_scope_and_malformed(self):
        real = (self.v / "ideas" / "real-idea.md").read_text()
        job = (self.v / "jobs" / "synthetic-job.md").read_text()
        good = (self.v / "ideas" / "synthetic-alpha.md").read_text()
        sha = memory.file_sha(good)
        cases = [
            {"id": "real-idea", "expected_sha256": memory.file_sha(real), "content": real + "x"},
            {"id": "synthetic-link-job", "expected_sha256": memory.file_sha(job), "content": "OVERWRITE"},
            {"id": "../jobs/synthetic-job", "expected_sha256": memory.file_sha(job), "content": "OVERWRITE"},
            {"id": "synthetic-missing", "expected_sha256": sha, "content": good},
            {"id": "synthetic-alpha", "expected_sha256": "abc", "content": good},
            {"id": "synthetic-alpha", "expected_sha256": "zz" * 8, "content": good},
            {"id": "synthetic-alpha", "expected_sha256": sha, "content": "no frontmatter"},
            {"id": "synthetic-alpha", "expected_sha256": sha, "content": good + "x" * bridge.MAX_NOTE_CHARS},
            {"id": "synthetic-alpha", "expected_sha256": sha, "content": good + "\udcff"},
        ]
        for args in cases:
            payload, is_error = self.call("update_idea", args)
            self.assertTrue(is_error, args["id"] + " " + args["expected_sha256"])
        self.assertEqual((self.v / "ideas" / "real-idea.md").read_text(), real)
        self.assertEqual((self.v / "jobs" / "synthetic-job.md").read_text(), job)
        self.assertEqual((self.v / "ideas" / "synthetic-alpha.md").read_text(), good)

    def test_audit_log_records_calls_without_content(self):
        self.call("fetch", {"id": "synthetic-alpha"})
        self.call("fetch", {"id": "real-idea"})
        entries = [e for e in self.audit() if e.get("tool")]
        self.assertEqual([(e["tool"], e["ok"]) for e in entries], [("fetch", True), ("fetch", False)])
        self.assertEqual(entries[0]["sha256"],
                         memory.file_sha((self.v / "ideas" / "synthetic-alpha.md").read_text())[:12])
        self.assertNotIn("SYN-ALPHA-1", json.dumps(entries))
        self.assertNotIn("id", entries[1])  # invalid ids are not echoed into the log


if __name__ == "__main__":
    unittest.main()
