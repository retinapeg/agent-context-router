"""Interoperability check: the official MCP Python SDK client talks to bridge.py over HTTP.

Needs the dev-only SDK in .venv (not a runtime dependency of the bridge):
  uv venv .venv && uv pip install --python .venv/bin/python "mcp==2.2.0"
  .venv/bin/python -m unittest -v test_bridge_sdk
Skipped under plain python3, where `mcp` is not installed.
"""
import asyncio
import json
import unittest

from test_bridge import TOKEN, BridgeFixture

try:
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client
except ImportError:  # plain python3 without the dev venv
    Client = None


@unittest.skipIf(Client is None, "official mcp SDK not installed (dev-only .venv)")
class TestOfficialClient(BridgeFixture):
    def run_client(self, work, mode="auto", headers=None, path="/mcp"):
        url = f"http://127.0.0.1:{self.port}{path}"

        async def main():
            http = create_mcp_http_client(headers=headers or {})
            async with http:
                async with Client(streamable_http_client(url, http_client=http), mode=mode) as client:
                    return client.protocol_version, await work(client)
        return asyncio.run(main())

    async def read_and_write(self, client):
        tools = await client.list_tools()
        fetched = await client.call_tool("fetch", {"id": "synthetic-alpha"})
        outside = await client.call_tool("fetch", {"id": "real-idea"})
        payload = json.loads(fetched.content[0].text)
        written = await client.call_tool("update_idea", {
            "id": "synthetic-alpha", "expected_sha256": payload["metadata"]["sha256"],
            "content": payload["text"].replace("SYN-ALPHA-1", "SYN-ALPHA-SDK")})
        return tools, fetched, outside, written

    def check(self, version, expected_version, result):
        tools, fetched, outside, written = result
        self.assertEqual(version, expected_version)
        self.assertEqual({t.name for t in tools.tools}, {"search", "fetch", "create_idea", "update_idea"})
        self.assertFalse(fetched.is_error)
        self.assertIn("SYN-ALPHA-1", fetched.structured_content["text"])
        self.assertTrue(outside.is_error)
        self.assertFalse(written.is_error, written.content[0].text)
        self.assertIn("SYN-ALPHA-SDK", (self.v / "ideas" / "synthetic-alpha.md").read_text())

    def test_auto_mode_negotiates_2026_protocol_with_bearer_header(self):
        version, result = self.run_client(self.read_and_write, headers={"Authorization": f"Bearer {TOKEN}"})
        self.check(version, "2026-07-28", result)
        eras = {e.get("era") for e in self.audit() if e.get("event") == "rpc"}
        self.assertEqual(eras, {"2026"})  # no fallback to the handshake

    def test_legacy_mode_with_x_bridge_token_header(self):
        version, result = self.run_client(self.read_and_write, mode="legacy", headers={"X-Bridge-Token": TOKEN})
        self.check(version, "2025-11-25", result)

    def test_path_token_mode(self):
        version, result = self.run_client(self.read_and_write, path=f"/mcp/{TOKEN}")
        self.check(version, "2026-07-28", result)

    def test_sdk_without_valid_token_cannot_connect(self):
        async def work(client):
            return await client.list_tools()
        for kwargs in ({}, {"headers": {"Authorization": "Bearer wrong-" + TOKEN}}, {"path": "/mcp/wrong"}):
            with self.assertRaises(BaseException):
                self.run_client(work, **kwargs)
        self.assertFalse(any(e.get("event") == "rpc" for e in self.audit()))


if __name__ == "__main__":
    unittest.main()
