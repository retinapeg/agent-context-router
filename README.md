# agent-context-router

I wanted a fresh AI agent session to load only the project notes it needs, and to be unable to overwrite a note that changed since it last read it.

**Result:** The router is only as good as the choice of note: a keyword picker standing in for the agent did worse than plain BM25 search (21/31 vs 24/31).

**Status:** Complete (v1.0.0).

- Returns the chosen note plus a few fixed project files, capped at 12,000 characters, with a sha256 for each. Edits against a stale hash are refused.
- Two separate Claude Code sessions recovered a demo note with the correct hash. Controls with no file access could not.
- How well a real model chooses notes is unmeasured, and the ChatGPT integration is unverified.

[Technical details →](docs/GUIDE.md)
