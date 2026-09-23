# agent-context-router

When a new AI agent session starts, this gives it only the notes it needs from a Markdown vault. It reads
them fresh from disk, with a revision hash on each file. Agent writes go through compare-and-swap, so
concurrent edits are not lost. A reproducible evaluation checks whether the selection policy beats simple
retrieval baselines, and says where it does not.

Independent engineering project, stdlib Python only. It is not deployed in production or used commercially.
Code was written with Claude Code (AI-assisted) from my specification. I set the requirements and
acceptance checks and reviewed the evidence.

| part | status |
| --- | --- |
| Router, guarded writes, size cap (`memory.py`) | working; 33 unit tests (standard run: 80 passed, 4 optional SDK tests skipped and passed separately) |
| Evaluation on synthetic notes (`eval/`) | 41 labelled requests, 5 conditions, raw results committed; 4 reproducibility tests |
| Fresh Claude Code sessions | 2 retrieval runs + 3 no-tools controls on this copy, transcripts in `evidence/` |
| ChatGPT via MCP bridge (`bridge.py`) | **UNVERIFIED**: built and tested locally (43 tests + 4 official-SDK interop tests), never connected to ChatGPT |
| Agent skill (`skills/`) | optional; restates the protocol; not separately evaluated |

## Problem
A fresh agent session doesn't know yesterday's decisions, a project checkpoint or the next action on an
application. Pasting everything costs context and brings in unrelated material. Relying on chat history or
compaction summaries is not auditable. The agent needs a small, fresh, citable context packet. When
several agents and a person edit the same notes, nobody's edit may be silently overwritten.

## Design
```
agent ──(route, slug)──▶ memory.py context ──▶ packet: default set + one note + manifest (path, chars, sha256, mtime)
agent ──(full text, expected sha256)──▶ memory.py update ──▶ lock → compare-and-swap → atomic replace, or "conflict"
```
- **The agent chooses; the code resolves.** `memory.py` does no natural-language parsing. The agent picks one
  route from `ROUTING.md` and one slug from a category `INDEX.md`. The script maps it to permitted paths.
  Selection errors show up in the transcript rather than inside a ranker. The eval measures the cost of this choice.
- **One entity per request, or none.** Default set (`CURRENT_STATE`, `TASKS`, `ROUTING.md`) plus exactly one
  note. With no slug the agent gets the category index only and is told to ask one question. No recursion, no whole-vault scan.
- **Freshness by content hash.** Every packet is built from disk at call time. The revision is the sha256 of the
  file, not a hand-maintained `updated:` field, which editors don't bump.
- **Bounded, never truncated.** The packet is assembled in memory and checked against a character cap
  (12,000). Over the cap, nothing is emitted, because a truncated packet can silently drop the note that matters.
- **Guarded writes.** `new` refuses to overwrite. `update` needs the sha256 the writer last read, holds a file lock, re-checks
  the hash just before an atomic rename, and stamps `last_writer`. There is no delete. Frontmatter must be plain `key: value`
  lines, so a writer can't forge `id`, `created` or `provenance`.
- **Fail closed.** Missing required sources, path traversal, symlink escape and unknown routes all exit 2 with nothing emitted.
- **One executable map.** `routes.json` is the only route definition. The `ROUTING.md` table is generated from it, and a test checks the two match.

## Run the demo (Python 3.11+, macOS/Linux, no dependencies)
```sh
git clone https://github.com/retinapeg/agent-context-router && cd agent-context-router
python3 memory.py context idea synthetic-comet-idea   # bounded packet: manifest + 4 files, ~5,300 chars
python3 memory.py context idea                        # no slug: category index only
python3 memory.py context job-prep example-co         # fails closed: required source unavailable (exit 2)
python3 memory.py context idea synthetic-comet-idea --max-chars 3000   # over cap: nothing emitted (exit 2)
```
Guarded write:
```sh
SHA=$(python3 memory.py context idea synthetic-comet-idea | grep 'comet-idea.md |' | cut -d'|' -f4 | tr -d ' ')
sed 's/VIOLET-COMET-5802/INDIGO-COMET-7316/' vault/ideas/synthetic-comet-idea.md > /tmp/comet.md
python3 memory.py update idea synthetic-comet-idea --expect "$SHA" --from /tmp/comet.md --by me   # diff + new sha
python3 memory.py update idea synthetic-comet-idea --expect "$SHA" --from /tmp/comet.md --by me   # conflict, nothing written
git checkout vault/                                                                          # reset the demo
```
With an agent: run `claude` in this folder and ask "What's the next action for the synthetic comet idea?".
`CLAUDE.md` imports `AGENTS.md`, which tells it to use the router. The committed `vault/` is synthetic.
**Real notes go in `local/`, never in `vault/`.** `sh setup_local.sh` creates an empty private root at `local/`
(routes, templates, empty indexes). Then pass `--root local` to every command, e.g.
`python3 memory.py --root local new idea "My idea"`. `AGENTS.md` and the skill tell agents to do the same.
`local/` and `out/` (derived packets, lock, audit log) are git-ignored. A throwaway clone confirmed that a note created
this way is not staged by `git add -A`.

## Evaluation (deterministic; no model calls)
Every number below measures a retrieval policy over fixed files and fixed labels, **not model performance**.

- **Data:** a synthetic vault of 37 candidate notes: 10 ideas, 8 jobs, 10 projects, 6 session checkpoints,
  plus decisions and 2 career files (`eval/fixture_root/`, generated by `eval/build_fixtures.py`). There are
  41 hand-labelled requests (`eval/requests.jsonl`) of 7 types: 12 exact, 10 paraphrase, 6 confusable (near-duplicate entities),
  6 ambiguous (correct behaviour is to load no entity note), 3 planning, 3 job-prep (two files), 1 cv-evidence.
  Each has required notes and, where defensible, "acceptable" notes that are neither required nor counted as unrelated.
- **Conditions**, all rendered in the same packet format:
  - `router_oracle`: the router given the **labelled** route and slug. Perfect recall is by construction; it
    measures the packet policy given a correct choice, i.e. an upper bound on what an agent could get.
  - `router_lexical`: the router with route and slug chosen by a **label-free** lexical picker (BM25 over index
    titles and descriptions; abstains to the index if the top score is < 2.0 or < 1.25× the runner-up). It stands in for the agent.
    **By construction it can only output a category's default route (`idea`, `job`, `project`, `session`) or its
    `planning` fallback. It can never select `job-prep` or `cv-evidence`, so those 4 requests are guaranteed misses for it.**
    Excluding them, it retrieves 21/31 versus 24/31 for `bm25_top1`.
  - `bm25_top1`, `bm25_top3`: BM25 over the full text of every note, plus the same default set.
  - `full_dump`: every file. At 35,256 characters it is about 3× the router's 12,000-character cap, which the router
    would refuse. **It is a context-cost reference, not a usable bounded baseline.** The bounded comparison is the router vs BM25.
- Parameters were fixed before the first run and not tuned. The labels, fixtures and router come from the same author,
  and n = 41, so treat the differences as indicative only.

| condition | all required notes retrieved | recall | requests with unrelated notes | unrelated / request | ambiguous: abstained | packet chars, median (max) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `router_oracle` (labelled choice) | 35/35 | 1.00 | 0/41 | 0.00 | 6/6 | 4,378 (5,112) |
| `router_lexical` (label-free) | 21/35 | 0.64 | 10/41 | 0.24 | 2/6 | 4,370 (5,112) |
| `bm25_top1` | 24/35 | 0.74 | 10/41 | 0.24 | 0/6 | 4,355 (4,433) |
| `bm25_top3` | 31/35 | 0.93 | 41/41 | 1.93 | 0/6 | 5,677 (5,918) |
| `full_dump` (over the 12,000 cap; reference only) | 35/35 | 1.00 | 41/41 | 35.78 | 0/6 | 35,256 (35,256) |

Latency is not a differentiator at this scale. In-process retrieval stays under 1 ms median for the router and
BM25 conditions and 4–6 ms for the full dump. An agent calling the CLI pays 40–50 ms median per call, of which a
bare `python3 -c pass` accounts for about 19 ms. Timings vary between runs; the committed `summary.json` holds one run.

**What the results mean**
- With a correct choice, the router's packet has every required note, nothing unrelated, and the right
  abstention on all 6 ambiguous requests (by construction). It is 7–8× smaller than the whole-vault reference. Among
  the bounded conditions, `bm25_top3` gets close on recall (31/35)
  but loads about 2 unrelated notes on every request, and never abstains.
- **That benefit depends entirely on the choice.** With the label-free lexical picker, the router does *worse*
  than plain BM25 top-1 on recall (21/35 vs 24/35), with the same unrelated-note rate. The routing design moves
  the hard problem to the agent's route and slug choice. This eval does not show that a model makes that choice well.
- Failure cases (all listed in `eval/results/summary.md`):
  - The lexical picker misses 6 of 10 paraphrases ("the thing that tells me when the sea is highest").
  - job-prep 0/3 and cv-evidence 0/1 are misses by construction (see above), not an empirical finding.
  - Its "no match → planning" fallback loads `DECISIONS.md` on 4 ambiguous requests.
  - BM25 top-1 ranks a session checkpoint above the note it checkpoints three times, and loads a note on every ambiguous request.

**Reproduce:** `python3 eval/run_eval.py` rewrites `eval/results/{raw.jsonl,summary.json,summary.md}`.
`raw.jsonl` has one row per condition × request: retrieved, missing and unrelated notes, packet size, latency.
`python3 -m unittest test_eval` checks two things: the fixtures rebuild byte for byte, and a rerun matches the
committed raw results on every field except timing.

**Next experiment (not run):** replace the lexical picker with a model choosing route and slug from the same
index on the same 41 requests, repeated runs, scored with the same code. That would measure the agent's selection accuracy.

## What the fresh Claude sessions proved ([`evidence/`](evidence/README.md))
On this copy, two separate `claude -p` processes (Claude Code 2.1.280, tools limited to `Read` and `memory.py`)
were asked for the comet idea's next action:
- **Session 1** followed `AGENTS.md` unprompted (ROUTING → index → note) and answered `VIOLET-COMET-5802` with the correct sha256.
- **Between sessions,** the note was changed through `update --expect`. Replaying the old hash was refused with nothing written.
- **Session 2** answered `INDIGO-COMET-7316` with the new sha256. The old marker and the unrelated control marker appear nowhere in its transcript.
- **Controls:** three no-tools sessions in a neutral directory could not answer.

This proves the protocol works end to end for one model on an exactly named entity, and that edits made between
sessions are picked up. It does not measure selection on harder requests, robustness across runs or models,
or anything about ChatGPT.

## Tests
```sh
python3 -m unittest -v      # standard run: 80 passed (memory 33, bridge 43, eval 4); 4 optional SDK tests skipped
pip install -r requirements-dev.txt && python3 -m unittest -v test_bridge_sdk   # the 4 SDK interop tests: passed separately
```
The tests run on temporary fixture vaults and cover:
- inclusion and exclusion per route, index-only mode and fail-closed routes
- traversal, symlink and case-folding escapes
- the size cap with no partial output
- duplicate refusal, and stale-revision refusal while a simulated editor save survives
- concurrent writers, frontmatter-forgery attempts
- a new process seeing a changed note

## Limits
- The router does no language understanding. Choosing the wrong slug is the agent's error to catch, and model selection accuracy is unmeasured.
- Nothing enforces the protocol. An agent with broad file access can bypass the router; only the tool allowlist stops it.
- Packet size is counted in characters, not tokens. Every packet carries all of `ROUTING.md` (about 2,260 chars), the first thing to trim.
- An editor that doesn't take the lock (e.g. Obsidian) can still race a write in a very small window before the rename.
- Single machine, POSIX file locks, small vaults (the bridge's `search` is O(N²)). No sync, no backup, no multi-user auth.
- Obsidian compatibility means plain Markdown plus frontmatter. The Obsidian UI itself was not tested.

## Optional pieces
- **ChatGPT bridge**: `bridge.py`, a token-gated, loopback-only MCP server exposing only `vault/ideas/synthetic-*.md`
  through four tools, with no delete and no paths. **ChatGPT integration is unverified**: no ChatGPT session has connected.
  Design, review history, known unfixed issues and the unrun acceptance test are in [`docs/chatgpt-bridge.md`](docs/chatgpt-bridge.md).
- **Skill**: [`skills/context-router/SKILL.md`](skills/context-router/SKILL.md) packages the same protocol as a
  Claude Code skill (copy it to `~/.claude/skills/`). `AGENTS.md` is the primary integration.

## Layout
```
memory.py            router, guarded writes, size cap (stdlib)
routes.json          the only route map; ROUTING.md table generated from it
AGENTS.md, CLAUDE.md agent protocol (CLAUDE.md imports AGENTS.md)
vault/               synthetic demo vault
eval/                fixture builder, labelled requests, runner, committed raw results
evidence/            sanitised fresh-session transcripts and what they show
bridge.py, docs/     optional MCP bridge for ChatGPT (unverified)
skills/              optional Claude Code skill
test_*.py            unit, bridge, SDK-interop and eval-reproducibility tests
```
MIT licence.
