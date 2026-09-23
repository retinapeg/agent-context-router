# Routing

Which files to read for which task. The table below is generated from `routes.json` (the only executable map); do not edit it by hand.

**Default read set** for any substantial session: `vault/CURRENT_STATE.md`, `vault/TASKS.md`, `ROUTING.md`. Then pick one route. Never read the whole vault.

Command: `python3 memory.py context <route> [<slug>]`

<!-- BEGIN GENERATED ROUTES (python3 memory.py routes --write) -->
| route | task | entity | extra files after default set | exclusions |
| --- | --- | --- | --- | --- |
| `job` | Specific job status or follow-up | `<slug>` → `vault/jobs/<slug>.md`; omitted → `INDEX.md` | — | No unrelated jobs or career evidence by default. |
| `job-prep` | Job application or interview preparation | `<slug>` → `vault/jobs/<slug>.md`; omitted → `INDEX.md` | `vault/career/EVIDENCE_BANK.md` | No other jobs or unverified career claims. Fails if the canonical evidence bank is unavailable. |
| `cv-evidence` | CV evidence work | none | `vault/career/EVIDENCE_BANK.md`, `vault/career/CV_VARIANTS.md` | No CV drafting or placeholder CVs. Fails if canonical sources are unavailable. |
| `idea` | Idea capture or development | `<slug>` → `vault/ideas/<slug>.md`; omitted → `INDEX.md` | — | No job history; do not implement the idea automatically. |
| `project` | Project or research work | `<slug>` → `vault/projects/<slug>.md`; omitted → `INDEX.md` | — | Follow named links only on request; no recursive import. |
| `session` | Session recovery | `<slug>` → `vault/sessions/<slug>.md`; omitted → `INDEX.md` | — | No chat history; load the referenced entity only via a second explicit command. |
| `planning` | General planning | none | `vault/DECISIONS.md` | No all-vault scan; request a category index explicitly if needed. |
<!-- END GENERATED ROUTES -->

**Rules**
- Entity omitted → category `INDEX.md` only. Several entries fit → ask one targeted question. Never guess a job, person or project.
- No recursive link following. To load a linked note, run a second explicit command.
- Examples (agent choice, not script NLP): "Any update on Example Co?" → `job <slug>`; "Prepare for Example Co" → `job-prep <slug>`; "Develop the comet idea" → `idea synthetic-comet-idea`; "Continue the benchmark" → `project <slug>`.
