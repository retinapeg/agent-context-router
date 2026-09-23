# Decisions

| decision | reason |
| --- | --- |
| Plain Markdown under `vault/` is canonical; packets are derived and never written inside the vault. | One source of truth that a person can edit and diff. |
| `routes.json` is the only executable route map; the `ROUTING.md` table is generated from it. | The rules an agent reads cannot drift from the rules the code enforces. |
| Revision = sha256 of file contents (plus mtime), not the frontmatter `updated:` field. | Editor saves do not bump `updated:`. |
| The agent chooses route and slug; `memory.py` does no natural-language parsing. | Selection errors stay visible in the transcript instead of hidden in a ranker. |
| Claude and ChatGPT may both write idea notes, only through `memory.py`: `new` never overwrites, `update` is compare-and-swap on sha256, no delete. | Two writers plus a human editor must not silently lose edits. |
| Packets over the character cap fail with nothing written; no truncation. | A truncated packet can silently drop the note that matters. |
