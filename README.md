# agent-context-router

A small Python tool that gives a fresh AI agent session a bounded, citable context packet from a Markdown
notes vault, and makes the agent's edits revision-checked. It uses only the standard library.

Independent engineering project; not deployed in production or used commercially. Code was written with
Claude Code (AI-assisted) from my specification.

## Demo (Python 3.11+, macOS/Linux, no dependencies)
```sh
git clone https://github.com/retinapeg/agent-context-router && cd agent-context-router
python3 memory.py context idea synthetic-comet-idea                    # packet: manifest + 4 files, 5,263 chars
python3 memory.py context idea                                         # no slug: category index only
python3 memory.py context idea synthetic-comet-idea --max-chars 3000   # over the cap: nothing emitted, exit 2
```
Revision-checked write: the second `update` reuses the old hash and is refused.
```sh
SHA=$(python3 memory.py context idea synthetic-comet-idea | grep 'comet-idea.md |' | cut -d'|' -f4 | tr -d ' ')
sed 's/VIOLET-COMET-5802/INDIGO-COMET-7316/' vault/ideas/synthetic-comet-idea.md > /tmp/comet.md
python3 memory.py update idea synthetic-comet-idea --expect "$SHA" --from /tmp/comet.md --by me   # prints diff and new hash
python3 memory.py update idea synthetic-comet-idea --expect "$SHA" --from /tmp/comet.md --by me   # conflict, nothing written
git checkout vault/                                                                                # reset the demo
```

## How it works
- **Routing.** The agent, not the code, picks one route from [`ROUTING.md`](ROUTING.md) and one note slug from
  the category `INDEX.md`. `memory.py` does no language parsing; it maps the choice to permitted paths. It returns
  a fixed default set (`CURRENT_STATE`, `TASKS`, `ROUTING.md`) plus that one note, with a manifest giving each
  file's path, size and sha256.
  - Without a slug, it returns the category index only, and the agent should ask a question.
  - Packets over 12,000 characters are refused, never truncated.
  - Paths outside `vault/`, unknown routes and missing required sources fail with exit 2.
  - [`routes.json`](routes.json) is the only route map; the `ROUTING.md` table is generated from it.
- **Writes.**
  - `new` never overwrites.
  - `update` must quote the sha256 the writer last read. Under a file lock, `memory.py` re-checks that hash and
    replaces the file atomically. A stale write through `memory.py` is refused with nothing written.
  - There is no delete. Edits made in an editor bypass this check, but a later `update` through `memory.py` detects them.
- **Agents** follow [`AGENTS.md`](AGENTS.md), which [`CLAUDE.md`](CLAUDE.md) imports. The committed `vault/` is
  synthetic. Real notes go in the git-ignored `local/`: create it with `sh setup_local.sh`, then pass `--root local`.

## Result
The evaluation is deterministic, with no model calls: 41 labelled requests over 37 synthetic notes. It measures
a selection policy, not a model.

| condition | all required notes retrieved | requests with unrelated notes | median packet chars |
| --- | ---: | ---: | ---: |
| router, labelled route and slug | 35/35 | 0/41 | 4,378 |
| router, label-free lexical picker | 21/35 | 10/41 | 4,370 |
| BM25 top-1 | 24/35 | 10/41 | 4,355 |
| BM25 top-3 | 31/35 | 41/41 | 5,677 |
| whole vault (over the 12,000 cap; reference only) | 35/35 | 41/41 | 35,256 |

**Main failure: the router is only as good as the route and slug choice.**
- With the labelled choice it is perfect, by construction.
- When a label-free lexical picker stands in for the agent, it does worse than plain BM25. On the 31 requests
  the picker can route, it retrieves every required note on 21/31, against 24/31 for BM25 top-1. By construction
  the picker can never select the `job-prep` or `cv-evidence` routes, so those requests are excluded.
- How well a real model makes this choice has not been measured.

## Evidence
- **Evaluation:** [`eval/README.md`](eval/README.md) covers method, conditions, full metrics, limits and how
  to reproduce. [`eval/results/summary.md`](eval/results/summary.md) lists every failure case, and
  [`eval/results/raw.jsonl`](eval/results/raw.jsonl) has the per-request rows.
- **Fresh Claude sessions:** [`evidence/README.md`](evidence/README.md), with transcripts in
  [`evidence/fresh-sessions/`](evidence/fresh-sessions/).
  - Two separate Claude Code sessions recovered the demo note's next action, with the correct sha256, before and
    after a revision-checked edit.
  - Three sessions with no tools could not answer.
  - Scope: one model, one exactly named note.
- **Tests:** `python3 -m unittest` passes 80 tests. The 4 official MCP SDK interop checks skip there and pass
  separately: `pip install -r requirements-dev.txt && python3 -m unittest test_bridge_sdk`.

## Limits
- Route and slug selection by a model is unmeasured, and the eval is small, synthetic and written by the tool's author.
- Nothing enforces the protocol. An agent with broad file access can bypass the router; only its tool allowlist stops it.
- An editor that doesn't take the lock (e.g. Obsidian) can still collide with a write in a very small window.
  It runs on a single machine, and size is counted in characters, not tokens.

## Optional, not required
- **ChatGPT bridge** ([`bridge.py`](bridge.py), [`docs/chatgpt-bridge.md`](docs/chatgpt-bridge.md)): a
  token-gated, loopback-only MCP server over synthetic idea notes. It is tested locally. **ChatGPT integration is
  unverified**: no ChatGPT session has connected.
- **Claude Code skill** ([`skills/context-router/SKILL.md`](skills/context-router/SKILL.md)): restates `AGENTS.md`.
  It is optional and not separately evaluated.

MIT licence.
