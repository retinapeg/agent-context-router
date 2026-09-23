# Optional ChatGPT bridge (`bridge.py`): UNVERIFIED with ChatGPT

**Status: built and tested locally; never connected to ChatGPT.** No ChatGPT read or write has been run.
Nothing in this repository shows that ChatGPT can use it. The rest of the project does not depend on it.

## What it is
`bridge.py` is a stdlib-only MCP server (Streamable HTTP, stateless, JSON responses) that lets a remote
agent read and write a narrow slice of the vault through `memory.py`. It binds to `127.0.0.1:8765`,
serves `/mcp`, and speaks both the 2026-07-28 protocol (`server/discover`, per-request `_meta`) and the
older `initialize` handshake (2025-11-25, 2025-06-18, 2025-03-26).

| property | how it is enforced | tests (in `test_bridge.py` unless noted) |
| --- | --- | --- |
| requires authentication | One token, as `Authorization: Bearer`, `X-Bridge-Token`, or the path `/mcp/<token>`. Anything else gets a plain 404 with no Server banner. Refuses to start with a weak, world-readable, symlinked or conflicting token. Failed-auth paths are logged only as fixed labels. | `test_every_method_and_path_requires_the_token`, `test_three_ways_to_present_the_token`, `test_token_in_near_miss_path_never_reaches_the_audit_log`, `test_refuses_weak_or_missing_tokens`; SDK `test_sdk_without_valid_token_cannot_connect` |
| accepts no file paths | Tools take an `id` that must fully match `synthetic-[a-z0-9-]+` (≤100 chars) and exist under that exact name. Closed schemas; unknown or extra arguments, non-strings and bad UTF-8 are rejected. | `test_fetch_rejects_everything_outside_scope`, `test_bad_arguments_rejected_and_audited`, `test_tool_list_is_exactly_four_without_delete_or_paths` |
| exposes one category only | Only regular, single-link files named exactly `vault/ideas/synthetic-*.md`, ≤256 KB. Symlinked folders or notes, hardlinks, FIFOs and case-folded or look-alike names are refused. Never serves CURRENT_STATE, TASKS, DECISIONS, INDEX or templates. | `test_case_folded_and_lookalike_names_are_not_reachable`, `test_symlinked_ideas_dir_and_hardlinks_are_refused`, `test_oversized_note_is_not_served` |
| no delete | Four tools: `search`, `fetch`, `create_idea`, `update_idea`. | `test_delete_and_unknown_tools_rejected` |
| writes only via `memory.py` | `create_idea` runs `memory.py new --by chatgpt`; `update_idea` runs `memory.py update --expect <sha256>` (file lock, atomic replace, revision re-checked before the rename). The remote writer cannot change `id`, `created` or `provenance`. | `test_update_refuses_stale_revision`, `test_update_cannot_forge_attribution`, `test_update_lone_cr_cannot_forge_frontmatter`, `test_update_yaml_tricks_cannot_change_protected_keys` |

Interop: `test_bridge_sdk.py` drives the bridge with the **official MCP Python SDK client** (`mcp==2.2.0`)
in both protocol eras: 4/4 pass in a dev venv (`pip install -r requirements-dev.txt`). This shows
conformance only as far as that client exercises it. It says nothing about ChatGPT's client.

## Why a hand-written server instead of the SDK server
- The runtime path has no third-party packages. The SDK server would add pydantic, httpx, anyio, starlette and uvicorn.
- The security-relevant code (auth gate, scope checks, write guards, connection cap, audit log) would be custom either way, as ASGI middleware around the SDK.
- Cost of this choice: protocol changes are tracked by hand; no SSE, sessions, resources or OAuth; base64 `Mcp-Name` values are not decoded.

## Review history
The bridge and `memory.py` went through two rounds of AI-assisted adversarial review (Claude subagents
with separate lenses; each finding re-checked by an independent proof-of-concept verifier). Round 1 had 24 findings,
23 confirmed. Among them: a lone surrogate could wipe `INDEX.md`; concurrent writers could lose updates;
case-folded names were reachable. Round 2 had 25 new confirmed findings, for example a lone CR forging frontmatter. The fixes each
have a regression test. **Known and not fixed:**
- Unauthenticated clients that can open raw TCP to the port can hold all 8 connection slots (denial of service).
- `search` is O(N²) under the global lock; about 2,000 notes would stall it.
- An editor that does not take the lock (e.g. Obsidian) can still race a write in a very small window.
- Low severity: the entropy heuristic in `check_token` accepts some patterned tokens; stdlib 4xx/5xx replies to malformed requests come before auth; a symlinked `_TEMPLATE.md` would be read; one unreadable note breaks `search`; the audit log stops at 5 MB; `create_idea` reveals whether a non-exposed `synthetic-*` name exists.

## How it would be connected (not done)
ChatGPT developer mode supports custom MCP apps, but OpenAI's documentation and Help Center disagree
about writes on personal plans. The documented auth options are OAuth, No Auth and Mixed; static API keys are not listed.
The route I would try is OpenAI's Secure MCP Tunnel, with `tunnel-client` injecting `X-Bridge-Token`
from a file, so there is no public URL and no token in a URL. None of this has been installed or tested.

```sh
python3 bridge.py init-token   # token at ~/.config/agent-context-router/bridge-token (0600), never printed
python3 bridge.py exposure     # lists exactly what would be exposed
python3 bridge.py serve        # http://127.0.0.1:8765/mcp, token required
```

## Acceptance test (defined, NOT RUN)
1. Fresh ChatGPT chat: "What is the next action for the synthetic comet idea? Give the path and revision."
   PASS if the reply has the current marker and sha256, the lantern control marker does not appear, and the audit log shows only the comet fetch.
2. Edit the note locally; new chat, same question. PASS if the new marker and new sha256 are reported.
3. Ask ChatGPT to create "Synthetic gpt kite idea". PASS if the note appears with
   `provenance: created by chatgpt via memory.py` and a fresh Claude session reads it back.

Paste-in instructions for a ChatGPT session are in [`chatgpt-instructions.md`](chatgpt-instructions.md).
