# Fresh Claude Code sessions: what they show and what they do not

These are end-to-end smoke tests of the protocol with a real model. There are few runs, one model and
synthetic markers. They are **not** a measure of retrieval quality; that is `eval/`, and it uses no model.

## Run on this public copy (2026-09-23), transcripts in `fresh-sessions/`
Claude Code 2.1.280, model `claude-opus-5-5`, `claude -p` non-interactive, no session persistence, no MCP
servers, tools limited to `Bash` and `Read`, and Bash allowlisted to `python3 memory.py:*`. Prompt:
"What is the next action for the synthetic comet idea? Cite paths and revisions."
These runs used the first published `AGENTS.md`. The later paragraph sending real notes to `local/` was added
afterwards; it doesn't change the retrieval steps, because the demo checkout has no `local/`.

| run | setup | tool calls | answer | markers in the whole transcript |
| --- | --- | --- | --- | --- |
| `fresh1` | repo root | Read `ROUTING.md` → `context idea` (index) → `context idea synthetic-comet-idea` | `VIOLET-COMET-5802`, sha256 `f9bae8bc8978` (correct) | VIOLET 3, INDIGO 0, lantern control 0 |
| edit | `memory.py update --expect f9bae8bc8978 --by owner` → `15feb0572660`; the same stale `--expect` retried → `conflict … Nothing written` (`edit.txt`) | — | — | — |
| `fresh2` | new process, no shared chat | same three calls | `INDIGO-COMET-7316`, sha256 `15feb0572660` (correct) | VIOLET 0, INDIGO 3, lantern control 0 |
| `control-1..3` | neutral temp dir, `--tools ""` (init shows `tools: []`, `mcp_servers: []`) | 0 local tool calls; 1 server-side `advisor` call each (a reviewer model that sees only the conversation, i.e. the prompt; its result is encrypted and omitted) | "I can't answer" | no `COMET-` marker in any answer |

Sanitising (`sanitize_transcripts.py`) rebuilds each transcript from an allowlist. It keeps the init fields that
matter (cwd, tools, MCP servers, model, permission mode, CLI version), tool calls with their inputs, tool results,
the assistant's text and the final answer. Tool-call ids become `call-1`, `call-2`, … so calls still pair with
results. Everything else is dropped: session, request, message and event ids, timestamps, signed thinking, encrypted
advisor content, usage and the local skill/command lists. Local paths become `<repo>`, `<tmp>` and `~`. The script
refuses to write output if a UUID-, id- or signature-like string survives.

## Earlier run in the private working copy (2026-09-22), transcripts not published
Same design, different markers: a fresh session recovered `TEAL-COMET-4417`; after an edit, a second
fresh session recovered `VIOLET-COMET-5802` with the old marker and the lantern control absent. A write session
created a note with `new --by claude`, filled it with `update --expect` (compare-and-swap), and a separate
fresh session read the written marker back with `last_writer: claude`. The first no-access baseline there was
**inconclusive**: the session guessed the vault path from its working-directory name and tried to grep it.
That is why the control now runs in a neutral directory with no tools.

## What this shows
- A fresh Claude Code session given only `AGENTS.md`/`CLAUDE.md` follows the protocol unprompted. It chose
  the right route and slug, cited revisions that match the files, and picked up an edit made between sessions.
- It loaded the category index before the note, which cost one extra call. The lantern control note was never loaded.
- A stale write is refused and leaves the note unchanged.
- The markers cannot be produced without the files: the no-tools controls, including their advisor call, did not produce them.

## What this does not show
- Anything about ChatGPT: no ChatGPT session has used this system.
- Selection accuracy: the prompt named the entity exactly ("synthetic comet idea"). It was not paraphrased, ambiguous or confusable.
  How well a model picks route and slug on harder requests is **unmeasured**. `eval/` only shows how much that choice matters.
- Robustness: 2 retrieval runs + 3 controls here and a similar set earlier. One model, one prompt.
- Enforcement: the tool allowlist, not the router, stops an agent from reading files directly.

## Reproduce
```sh
cp -R . /tmp/acr && cd /tmp/acr
claude -p "What is the next action for the synthetic comet idea? Cite paths and revisions." \
  --tools Bash Read --allowedTools "Bash(python3 memory.py:*)" "Read" \
  --strict-mcp-config --no-session-persistence --output-format stream-json --verbose > fresh1.jsonl
# edit the note's next action through memory.py update --expect <sha256>, then rerun as fresh2.jsonl
cd "$(mktemp -d)" && claude -p "<same prompt>" --tools "" --strict-mcp-config --no-session-persistence \
  --output-format stream-json --verbose > control-1.jsonl
```
