# agent-context-router

Can a fresh AI agent session load only the project notes it needs, within a fixed character budget, and be prevented from overwriting a note that changed since it last read it?

**Result:** The loading, budget and stale-write protection work and are tested. The retrieval idea did not beat the baseline: a keyword picker standing in for the agent's note choice retrieved every required note on 21 of 31 routable requests, against 24 of 31 for plain BM25 top-1 (21/35 and 24/35 over the 35 of 41 labelled requests that have a required note). Given the correct choice, the router is perfect by construction, so the hard part is the choice, and this repo does not solve it.

**Why it matters:** Context loading for agents is an engineering problem (what gets loaded, how much, and whether a concurrent edit is lost) before it is a retrieval problem. The engineering here holds up; the fancy routing lost to a 30-year-old ranking function, and the README keeps that answer.

**Status:** Complete (tag `v1.0.0`; later commits are documentation only). Not deployed or used commercially.

- `memory.py context <route> [<note>]` prints one Markdown packet: the chosen note plus a few fixed project files, each with its character count and a 12-character sha256 prefix. Packets over the budget (12,000 characters by default) are refused with a non-zero exit, not truncated.
- `memory.py update --expect <sha>` is a compare-and-swap under a file lock. An edit against a stale hash is refused and nothing is written.
- Two separate Claude Code sessions, given the exact note name, each read the demo note with the correct hash, one before and one after an edit made between them; the stale retry was refused. Three controls with no file access could not answer.
- Unmeasured: how well a real model chooses the route and note. Unverified: the optional ChatGPT bridge, which has never been connected.

## Evidence

| Claim | Where to check |
|---|---|
| 21/35 vs 24/35 (21/31 vs 24/31 on routable requests); oracle 35/35; BM25 top-3 31/35 but unrelated notes on 41/41 | [`eval/results/summary.md`](eval/results/summary.md), [`eval/results/summary.json`](eval/results/summary.json), per-request rows in [`eval/results/raw.jsonl`](eval/results/raw.jsonl) |
| Method, conditions, what "routable" excludes (3 job-prep and 1 CV request the picker can never select) | [`eval/README.md`](eval/README.md) |
| Fresh-session recovery and the refused stale edit | [`evidence/README.md`](evidence/README.md), transcripts in [`evidence/fresh-sessions/`](evidence/fresh-sessions) |
| Budget refusal, path and symlink checks, locked compare-and-swap | `assemble_packet` and `cmd_update` in [`memory.py`](memory.py); `test_memory.py` |

## Reproduce

Python 3.11+, macOS or Linux, standard library only, no model calls:

```bash
python3 -m unittest                               # 84 tests; 4 skip without the optional mcp package, 1 more on case-sensitive filesystems
python3 memory.py context idea synthetic-comet-idea   # prints a packet with its manifest
python3 eval/run_eval.py                          # re-runs the 5-condition evaluation; rewrites eval/results/ (raw.jsonl matches except latency; summary.* also records the local Python and platform)
```

`test_eval.py` asserts that a rerun reproduces the committed `raw.jsonl` apart from timings.

## What this does not show

- That any model picks notes well with this router. The picker in the evaluation is lexical, not a model.
- That the two-session recovery generalises: one model, one prompt that named the note exactly, and each control made one server-side advisor call.
- Anything about the ChatGPT bridge beyond its unit tests.

[Technical details →](docs/GUIDE.md)
