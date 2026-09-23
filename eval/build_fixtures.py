#!/usr/bin/env python3
"""Generate the synthetic evaluation vault in eval/fixture_root/ (deterministic).

Every note is invented. Companies, projects and ideas are fictional. The
generated files are committed so the eval can be rerun without this script;
rerunning it must reproduce them byte for byte (checked in test_eval.py).

  python3 eval/build_fixtures.py
"""
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "fixture_root"
REPO = HERE.parent
STAMP = "2026-08-15T09:00:00+01:00"

# (category, slug, title, one-line INDEX description, body sections)
NOTES = [
    # ------------------------------------------------------------------ ideas
    ("ideas", "tide-clock-widget", "Tide clock widget", "Home-screen widget showing the next high tide.",
     {"Goal": "A phone widget that shows the time of the next high and low water for a chosen harbour.",
      "Current state": "- Prototype reads a public tide-prediction feed and renders a dial.\n- Harbour picker not started.",
      "Next action": "Add a harbour picker backed by the station list (marker TIDECLOCK-31)."}),
    ("ideas", "tide-gauge-data-cleaner", "Tide gauge data cleaner", "Clean noisy sensor readings from a tide gauge.",
     {"Goal": "Remove spikes and gaps from raw water-level sensor logs before analysis.",
      "Current state": "- Median filter removes single-sample spikes.\n- Gap filling is still linear interpolation.",
      "Next action": "Compare linear gap filling with harmonic fitting on one month of logs (marker GAUGE-17)."}),
    ("ideas", "recipe-scaler", "Recipe scaler", "Scale recipe quantities and convert units.",
     {"Goal": "Scale a recipe to any number of servings and convert cups, grams and ounces.",
      "Current state": "- Parser handles fractions like 1 1/2.\n- Density table covers 40 ingredients.",
      "Next action": "Handle ingredients with no density entry by asking for weight (marker RECIPE-08)."}),
    ("ideas", "bike-commute-planner", "Bike commute planner", "Plan cycle routes that avoid busy roads.",
     {"Goal": "Suggest a daily route to the office that trades a little distance for quieter streets.",
      "Current state": "- Uses open street data with a traffic-weighted cost.\n- No elevation yet.",
      "Next action": "Add hill penalty from an elevation model (marker BIKE-44)."}),
    ("ideas", "plant-watering-reminder", "Plant watering reminder", "Remind me when each houseplant needs water.",
     {"Goal": "Per-plant reminders based on species, pot size and season.",
      "Current state": "- Manual schedule for six plants in a spreadsheet.",
      "Next action": "Move the schedule into a calendar feed (marker PLANT-02)."}),
    ("ideas", "board-game-rules-explainer", "Board game rules explainer", "Answer rules questions from a rulebook.",
     {"Goal": "Ask a question about a board game and get an answer quoted from the official rulebook.",
      "Current state": "- Two rulebooks converted to text.\n- Answers must quote a page.",
      "Next action": "Write ten rules questions with known answers to test it (marker RULES-66)."}),
    ("ideas", "podcast-chapter-splitter", "Podcast chapter splitter", "Split long audio into topic chapters.",
     {"Goal": "Turn a two-hour episode into chapters with titles, using the transcript.",
      "Current state": "- Transcripts from a speech-to-text model.\n- Topic boundaries picked by hand for one episode.",
      "Next action": "Try embedding-similarity boundaries against the hand-labelled episode (marker POD-29)."}),
    ("ideas", "receipt-splitter", "Receipt splitter", "Split restaurant bills among friends.",
     {"Goal": "Photograph a receipt, assign items to people, and compute who owes what including tip.",
      "Current state": "- Line-item extraction works on printed receipts.\n- Shared dishes not handled.",
      "Next action": "Support splitting one item between several people (marker RECEIPT-53)."}),
    ("ideas", "solar-panel-yield-forecaster", "Solar panel yield forecaster", "Forecast tomorrow's rooftop solar output.",
     {"Goal": "Predict next-day generation from a weather forecast and past inverter data.",
      "Current state": "- One year of inverter data exported.\n- Baseline: yesterday's output.",
      "Next action": "Fit a regression on cloud cover and compare with the persistence baseline (marker SOLAR-71)."}),
    ("ideas", "language-flashcard-generator", "Language flashcard generator", "Generate spaced-repetition cards.",
     {"Goal": "Create vocabulary cards from articles I read in the language I am learning.",
      "Current state": "- Word frequency list built from 30 articles.\n- Card export format chosen.",
      "Next action": "Filter out words already known before export (marker CARDS-12)."}),
    # ------------------------------------------------------------------- jobs
    ("jobs", "northwind-robotics-ml-engineer", "Northwind Robotics ML engineer", "Machine learning engineer, perception team.",
     {"Role and stage": "Northwind Robotics, ML engineer (perception). Technical screen done 2026-08-04.",
      "Current state": "- Waiting for on-site scheduling.\n- Recruiter asked for two reference contacts.",
      "Next action": "Send the two reference contacts to the recruiter (marker NWROBO-5)."}),
    ("jobs", "northwind-logistics-data-analyst", "Northwind Logistics data analyst", "Data analyst, warehouse operations.",
     {"Role and stage": "Northwind Logistics, data analyst. Applied 2026-08-02, no reply yet.",
      "Current state": "- Application submitted through the careers site.",
      "Next action": "Follow up by email if there is no reply by 2026-08-20 (marker NWLOG-9)."}),
    ("jobs", "helios-labs-research-engineer", "Helios Labs research engineer", "Research engineer, training infrastructure.",
     {"Role and stage": "Helios Labs, research engineer. First interview booked for 2026-08-22.",
      "Current state": "- Interview covers distributed training and debugging.",
      "Next action": "Review the gradient-accumulation bug story for the interview (marker HELIOS-3)."}),
    ("jobs", "quarry-health-applied-scientist", "Quarry Health applied scientist", "Applied scientist, clinical NLP.",
     {"Role and stage": "Quarry Health, applied scientist. Take-home received 2026-08-10.",
      "Current state": "- Take-home: classify discharge summaries; due in seven days.",
      "Next action": "Finish the error analysis section of the take-home (marker QUARRY-6)."}),
    ("jobs", "brightwater-bank-quant-developer", "Brightwater Bank quant developer", "Quant developer, rates desk.",
     {"Role and stage": "Brightwater Bank, quant developer. Second round passed 2026-08-08.",
      "Current state": "- Final round is a pricing-library code review.",
      "Next action": "Practise reading an unfamiliar curve-building codebase (marker BWB-4)."}),
    ("jobs", "fernleaf-games-gameplay-programmer", "Fernleaf Games gameplay programmer", "Gameplay programmer, C++.",
     {"Role and stage": "Fernleaf Games, gameplay programmer. Rejected after portfolio review 2026-08-01.",
      "Current state": "- Closed. Feedback: wanted shipped titles.",
      "Next action": "None; archive the note (marker FERN-0)."}),
    ("jobs", "orbital-freight-forecasting-scientist", "Orbital Freight forecasting scientist", "Forecasting scientist, demand planning.",
     {"Role and stage": "Orbital Freight, forecasting scientist. Application drafted, not sent.",
      "Current state": "- Cover letter needs a demand-forecasting example.",
      "Next action": "Choose which forecasting project to cite in the cover letter (marker ORBIT-7)."}),
    ("jobs", "cobalt-ai-evaluation-engineer", "Cobalt AI evaluation engineer", "Engineer building LLM evaluation harnesses.",
     {"Role and stage": "Cobalt AI, evaluation engineer. Recruiter call 2026-08-12.",
      "Current state": "- Role builds benchmark suites and graders for language models.",
      "Next action": "Prepare a five-minute walkthrough of a grader I built (marker COBALT-1)."}),
    # --------------------------------------------------------------- projects
    ("projects", "retrieval-benchmark-v1", "Retrieval benchmark v1", "First retrieval benchmark; finished.",
     {"Objective and checkpoint": "Compare lexical and dense retrieval on 200 labelled queries. Finished 2026-07-01.",
      "Current state": "- Final: BM25 recall@10 0.71, dense 0.78 (synthetic numbers).",
      "Next action": "None; results frozen (marker RBV1-99)."}),
    ("projects", "retrieval-benchmark-v2", "Retrieval benchmark v2", "Second retrieval benchmark with harder queries.",
     {"Objective and checkpoint": "Extend v1 with multi-hop queries and a reranker.",
      "Current state": "- 120 of 300 multi-hop queries labelled.\n- Reranker not integrated.",
      "Next action": "Label the next 60 multi-hop queries (marker RBV2-42)."}),
    ("projects", "kalman-tracker-demo", "Kalman tracker demo", "Constant-velocity Kalman filter tracking demo.",
     {"Objective and checkpoint": "Track a simulated target through sensor dropouts and show covariance growth.",
      "Current state": "- Filter consistent on simulated data.\n- Plot of covariance ellipses done.",
      "Next action": "Add a dropout scenario longer than ten seconds (marker KALMAN-5)."}),
    ("projects", "home-energy-dashboard", "Home energy dashboard", "Dashboard of household electricity use.",
     {"Objective and checkpoint": "Show half-hourly smart-meter consumption with cost per day.",
      "Current state": "- Meter data importer works.\n- Tariff table hard-coded.",
      "Next action": "Load the tariff from a config file (marker ENERGY-23)."}),
    ("projects", "thesis-literature-map", "Thesis literature map", "Map of papers cited in my thesis.",
     {"Objective and checkpoint": "Graph of cited papers grouped by method, for the dissertation's related-work chapter.",
      "Current state": "- 85 papers imported with citation links.",
      "Next action": "Cluster the papers and name each cluster (marker LITMAP-14)."}),
    ("projects", "sparse-autoencoder-replication", "Sparse autoencoder replication", "Reproduce a published SAE result on a small model.",
     {"Objective and checkpoint": "Replicate a dictionary-learning interpretability result on a two-layer transformer.",
      "Current state": "- Activations cached for 10M tokens.\n- First SAE trained; many dead features.",
      "Next action": "Add feature resampling to reduce dead features (marker SAE-38)."}),
    ("projects", "chess-engine-in-rust", "Chess engine in Rust", "Alpha-beta chess engine written in Rust.",
     {"Objective and checkpoint": "A UCI engine that beats a 1500-rated bot.",
      "Current state": "- Move generation passes perft to depth 5.\n- Search has no transposition table.",
      "Next action": "Add a transposition table and measure nodes per second (marker CHESS-61)."}),
    ("projects", "weather-station-firmware", "Weather station firmware", "Firmware for a garden weather station.",
     {"Objective and checkpoint": "Microcontroller firmware that logs temperature, humidity and wind every minute.",
      "Current state": "- Sensors read correctly.\n- Deep sleep drains the battery faster than expected.",
      "Next action": "Measure current draw in deep sleep with the radio off (marker WX-19)."}),
    ("projects", "personal-finance-ledger", "Personal finance ledger", "Plain-text double-entry ledger.",
     {"Objective and checkpoint": "Import bank CSVs into a double-entry ledger and reconcile monthly.",
      "Current state": "- Two bank importers written.\n- Categorisation rules cover 80% of transactions.",
      "Next action": "Write rules for the remaining uncategorised transactions (marker LEDGER-27)."}),
    ("projects", "tokenizer-benchmark", "Tokenizer benchmark", "Compare tokenizers on compression and speed.",
     {"Objective and checkpoint": "Measure bytes per token and throughput for four tokenizers on a mixed corpus.",
      "Current state": "- Corpus assembled.\n- Throughput harness done; compression table pending.",
      "Next action": "Fill the compression table for all four tokenizers (marker TOKEN-50)."}),
    # --------------------------------------------------------------- sessions
    ("sessions", "2026-08-01-retrieval-benchmark-v2-checkpoint", "2026-08-01 retrieval benchmark v2 checkpoint",
     "Checkpoint: multi-hop labelling progress.",
     {"Entity": "`project retrieval-benchmark-v2`",
      "What changed this session": "- Labelled 40 multi-hop queries.\n- Agreed a two-annotator check on 10%.",
      "Next action": "Run the agreement check on the first 40 labels (marker SESS-RB-1)."}),
    ("sessions", "2026-08-03-northwind-robotics-prep-checkpoint", "2026-08-03 northwind robotics prep checkpoint",
     "Checkpoint: technical screen preparation.",
     {"Entity": "`job northwind-robotics-ml-engineer`",
      "What changed this session": "- Rehearsed the perception pipeline explanation.",
      "Next action": "Time the explanation to under three minutes (marker SESS-NW-1)."}),
    ("sessions", "2026-08-05-kalman-tracker-checkpoint", "2026-08-05 kalman tracker checkpoint",
     "Checkpoint: consistency test results.",
     {"Entity": "`project kalman-tracker-demo`",
      "What changed this session": "- NEES test passed on 500 simulated runs.",
      "Next action": "Write up the consistency result in the README (marker SESS-KF-1)."}),
    ("sessions", "2026-08-07-tide-clock-checkpoint", "2026-08-07 tide clock checkpoint",
     "Checkpoint: dial rendering.",
     {"Entity": "`idea tide-clock-widget`",
      "What changed this session": "- Dial renders on two screen sizes.",
      "Next action": "Decide between a list and a map for the harbour picker (marker SESS-TC-1)."}),
    ("sessions", "2026-08-09-chess-engine-checkpoint", "2026-08-09 chess engine checkpoint",
     "Checkpoint: perft results.",
     {"Entity": "`project chess-engine-in-rust`",
      "What changed this session": "- Fixed en passant bug; perft depth 5 now matches.",
      "Next action": "Profile move generation before adding the transposition table (marker SESS-CH-1)."}),
    ("sessions", "2026-08-11-energy-dashboard-checkpoint", "2026-08-11 energy dashboard checkpoint",
     "Checkpoint: importer done.",
     {"Entity": "`project home-energy-dashboard`",
      "What changed this session": "- Smart-meter importer handles daylight-saving changes.",
      "Next action": "Add unit tests for the daylight-saving edge case (marker SESS-EN-1)."}),
]

FIXED_FILES = {
    "vault/CURRENT_STATE.md": """# Current state

**Synthetic evaluation vault.** Every note is invented.

## Focus
Job search for ML roles, several side projects and a backlog of small ideas.

## Constraints
- Only saved notes are recoverable.
- Do not guess an entity; ask one question when it is unclear.
""",
    "vault/TASKS.md": """# Tasks

## NOW
- Prepare for the Helios Labs interview.
- Finish the Quarry Health take-home.

## NEXT
- Label more multi-hop queries for retrieval benchmark v2.
""",
    "vault/DECISIONS.md": """# Decisions

| date | decision |
| --- | --- |
| 2026-08-01 | Notes are written only through memory.py; edits need the sha256 last read. |
| 2026-08-02 | Apply only to roles with a named hiring manager or a referral. |
| 2026-08-06 | Weekly priority order: interviews, then take-homes, then side projects. |
| 2026-08-09 | Side projects get at most one evening per week until the job search ends. |
""",
    "vault/career/EVIDENCE_BANK.md": """# Evidence bank (synthetic)

| claim | evidence | verified |
| --- | --- | --- |
| Built a Kalman tracker with a passing consistency test | project kalman-tracker-demo, session 2026-08-05 | yes |
| Ran a lexical vs dense retrieval benchmark | project retrieval-benchmark-v1 | yes |
| Trained a sparse autoencoder on cached activations | project sparse-autoencoder-replication | partly (dead features) |
""",
    "vault/career/CV_VARIANTS.md": """# CV variants (synthetic)

| variant | emphasis | last edited |
| --- | --- | --- |
| research-engineer | training infrastructure, debugging | 2026-08-05 |
| applied-scientist | NLP, evaluation, error analysis | 2026-08-10 |
""",
}

HEADINGS = {"ideas": "idea", "jobs": "job", "projects": "project", "sessions": "session"}


def note_text(category, slug, title, sections):
    kind = HEADINGS[category]
    lines = ["---", f"id: {kind}-{slug}", f"title: {json.dumps(title)}", f"created: {STAMP}",
             f"updated: {STAMP}", "provenance: synthetic eval fixture", "---", f"# {title}", ""]
    for heading, text in sections.items():
        lines += [f"## {heading}", text, ""]
    return "\n".join(lines)


def build(out=OUT):
    if out.exists():
        shutil.rmtree(out)
    (out / "vault").mkdir(parents=True)
    shutil.copy(REPO / "routes.json", out / "routes.json")
    shutil.copy(REPO / "ROUTING.md", out / "ROUTING.md")
    for rel, text in FIXED_FILES.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    index_rows = {c: [] for c in HEADINGS}
    for category, slug, title, desc, sections in NOTES:
        folder = out / "vault" / category
        folder.mkdir(exist_ok=True)
        (folder / f"{slug}.md").write_text(note_text(category, slug, title, sections), encoding="utf-8")
        index_rows[category].append(f"| {title} | `{category}/{slug}.md` | {desc} |")
    for category, rows in index_rows.items():
        head = [f"# {category} index", "", "Title, path and one-line description only. Status lives in the note itself.",
                "", "| title | path | description |", "| --- | --- | --- |"]
        (out / "vault" / category / "INDEX.md").write_text("\n".join(head + rows) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    build()
    print(f"wrote {len(NOTES)} notes + {len(FIXED_FILES)} fixed files under {OUT.relative_to(REPO)}")
