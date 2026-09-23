#!/usr/bin/env python3
"""Deterministic context-selection eval: router vs simple baselines on a synthetic vault.

No model is called. Every number here measures a retrieval policy over fixed
files and fixed labels, not a model's behaviour.

Conditions (all return the same packet format, via memory.assemble_packet):
  router_oracle   memory.py with the labelled route and slug. Measures the packet
                  policy given a correct choice; perfect recall is by construction.
  router_lexical  memory.py with route and slug chosen by a label-free lexical
                  picker over the category INDEX rows (a stand-in for the agent's choice).
  bm25_top1/3     BM25 over the full text of every candidate note, top k, plus the
                  same default set.
  full_dump       every file in the vault plus ROUTING.md.

  python3 eval/run_eval.py            # writes eval/results/{raw.jsonl,summary.json,summary.md}
"""
import hashlib
import json
import math
import platform
import re
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))
import memory  # noqa: E402

ROOT = HERE / "fixture_root"
REQUESTS = HERE / "requests.jsonl"
RESULTS = HERE / "results"

# Fixed before the first run; not tuned on these labels.
PARAMS = {"bm25_k1": 1.2, "bm25_b": 0.75, "picker_min_score": 2.0, "picker_margin": 1.25,
          "latency_repeats": 20, "cli_repeats": 3}
STOPWORDS = set("""a an and are as at be by did do does for from get got had has have how i i'm in is it its
last left me my next of on or so that the their them then there this to up was we were what whats when where
which who why will with you your s""".split())
SCAFFOLD_NAMES = {"INDEX.md", "_TEMPLATE.md", "CURRENT_STATE.md", "TASKS.md"}
CATEGORY_ROUTE = {"ideas": "idea", "jobs": "job", "projects": "project", "sessions": "session"}
NO_LIMIT = 10**9


def tokens(text):
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]


class BM25:
    def __init__(self, docs, k1, b):
        self.ids = list(docs)
        self.tf = [Counter(tokens(docs[i])) for i in self.ids]
        self.len = [sum(tf.values()) for tf in self.tf]
        self.avg = sum(self.len) / len(self.len)
        df = Counter(t for tf in self.tf for t in tf)
        n = len(self.ids)
        self.idf = {t: math.log((n - d + 0.5) / (d + 0.5) + 1) for t, d in df.items()}
        self.k1, self.b = k1, b

    def scores(self, query):
        q = tokens(query)
        out = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            out.append((s, self.ids[i]))
        return sorted(out, key=lambda x: (-x[0], x[1]))  # ties broken by path, deterministically


def vault_files(root):
    return sorted(p.relative_to(root).as_posix() for p in (root / "vault").rglob("*.md"))


def candidate_notes(root):
    """Notes a request could be about: everything except indexes, templates and the default set."""
    return [r for r in vault_files(root) if Path(r).name not in SCAFFOLD_NAMES]


def index_rows(root):
    rows = {}
    for category in CATEGORY_ROUTE:
        for line in (root / "vault" / category / "INDEX.md").read_text().splitlines():
            m = re.match(r"\| (.+?) \| `(\w+)/([a-z0-9-]+)\.md` \| (.+?) \|$", line)
            if m:
                rows[f"vault/{m.group(2)}/{m.group(3)}.md"] = f"{m.group(1)} {m.group(4)}"
    return rows


def lexical_pick(picker, query):
    """Label-free stand-in for the agent: one note, or abstain to the category index."""
    ranked = picker.scores(query)
    (s1, top), (s2, _) = ranked[0], ranked[1]
    category = top.split("/")[1]
    if s1 == 0:
        return "planning", None, "no index row matched; planning route"
    if s1 < PARAMS["picker_min_score"] or s1 < PARAMS["picker_margin"] * s2:
        return CATEGORY_ROUTE[category], None, f"abstain (top {s1:.2f}, second {s2:.2f}); {category} index only"
    return CATEGORY_ROUTE[category], Path(top).stem, f"picked {top} (top {s1:.2f}, second {s2:.2f})"


def run_condition(name, req, cfg, picker, bm25, all_files):
    """Return (packet, detail) for one request under one condition."""
    if name == "router_oracle":
        return memory.build_packet(ROOT, req["route"], req["entity"])[0], f"route {req['route']} {req['entity'] or ''}".strip()
    if name == "router_lexical":
        route, slug, why = lexical_pick(picker, req["text"])
        return memory.build_packet(ROOT, route, slug)[0], why
    if name.startswith("bm25_top"):
        k = int(name[-1])
        top = [p for _, p in bm25.scores(req["text"])[:k]]
        rels = list(cfg["default_set"]) + top
        return memory.assemble_packet(ROOT, cfg, rels, name, f"top {k}", NO_LIMIT)[0], "top: " + ", ".join(top)
    if name == "full_dump":
        rels = ["ROUTING.md"] + all_files
        return memory.assemble_packet(ROOT, cfg, rels, name, "all files", NO_LIMIT)[0], "all files"
    raise ValueError(name)


CONDITIONS = ["router_oracle", "router_lexical", "bm25_top1", "bm25_top3", "full_dump"]
MANIFEST_RE = re.compile(r"^===== BEGIN FILE: (\S+) ", re.M)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def main():
    cfg, reqs, cands, all_files = load_inputs()

    t0 = time.perf_counter()
    bm25 = BM25({p: (ROOT / p).read_text() for p in cands}, PARAMS["bm25_k1"], PARAMS["bm25_b"])
    bm25_build_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    picker = BM25(index_rows(ROOT), PARAMS["bm25_k1"], PARAMS["bm25_b"])
    picker_build_ms = (time.perf_counter() - t0) * 1000

    rows = evaluate(cfg, reqs, cands, all_files, picker, bm25, PARAMS["latency_repeats"])
    summary = summarise(reqs, cands, all_files, rows, bm25_build_ms, picker_build_ms)
    RESULTS.mkdir(exist_ok=True)
    with open(RESULTS / "raw.jsonl", "w") as f:
        for x in rows:
            f.write(json.dumps(x) + "\n")
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (RESULTS / "summary.md").write_text(render_md(summary, rows))
    print((RESULTS / "summary.md").read_text())


def load_inputs():
    cfg = memory.load_routes(ROOT)
    reqs = [json.loads(line) for line in REQUESTS.read_text().splitlines() if line.strip()]
    cands = candidate_notes(ROOT)
    all_files = vault_files(ROOT)
    for r in reqs:  # labels must point at real candidate notes
        for p in r["relevant"] + r["acceptable"]:
            assert p in cands, (r["id"], p)
    return cfg, reqs, cands, all_files


def evaluate(cfg, reqs, cands, all_files, picker, bm25, repeats):
    rows = []
    for cond in CONDITIONS:
        for r in reqs:
            try:
                packet, detail = run_condition(cond, r, cfg, picker, bm25, all_files)
                error = None
            except memory.MemoryError_ as e:
                packet, detail, error = "", "", str(e)
            times = []
            for _ in range(repeats):
                t0 = time.perf_counter()
                try:
                    run_condition(cond, r, cfg, picker, bm25, all_files)
                except memory.MemoryError_:
                    pass
                times.append((time.perf_counter() - t0) * 1000)
            loaded = MANIFEST_RE.findall(packet)
            got = [p for p in loaded if p in cands]
            rel, acc = set(r["relevant"]), set(r["acceptable"])
            missing = sorted(rel - set(got))
            unrelated = sorted(set(got) - rel - acc)
            rows.append({
                "condition": cond, "id": r["id"], "type": r["type"], "text": r["text"],
                "relevant": r["relevant"], "retrieved_notes": got, "missing": missing, "unrelated": unrelated,
                "recall": (len(rel) - len(missing)) / len(rel) if rel else None,
                "abstained": not got if not rel else None,
                "packet_chars": len(packet), "files_loaded": len(loaded),
                "latency_ms_median": round(statistics.median(times), 4), "detail": detail, "error": error,
            })

    return rows


def summarise(reqs, cands, all_files, rows, bm25_build_ms, picker_build_ms):
    cli = []  # what an agent pays per call: a fresh python3 process running memory.py
    for r in reqs:
        cmd = [sys.executable, str(REPO / "memory.py"), "--root", str(ROOT), "context", r["route"]]
        cmd += [r["entity"]] if r["entity"] else []
        ts = []
        for _ in range(PARAMS["cli_repeats"]):
            t0 = time.perf_counter()
            subprocess.run(cmd, capture_output=True, check=True)
            ts.append((time.perf_counter() - t0) * 1000)
        cli.append(statistics.median(ts))

    summary = {"params": PARAMS, "n_requests": len(reqs), "n_candidate_notes": len(cands),
               "request_types": dict(Counter(r["type"] for r in reqs)),
               "inputs_sha256_12": {"requests.jsonl": sha(REQUESTS), "run_eval.py": sha(Path(__file__)),
                                    "memory.py": sha(REPO / "memory.py"),
                                    "fixture_root": hashlib.sha256("".join(sha(ROOT / p) for p in
                                                                   ["routes.json", "ROUTING.md"] + all_files).encode()).hexdigest()[:12]},
               "environment": {"python": platform.python_version(), "platform": platform.platform(),
                               "machine": platform.machine()},
               "index_build_ms": {"bm25_full_text": round(bm25_build_ms, 3), "lexical_picker": round(picker_build_ms, 3)},
               "router_cli_ms": {"median": round(statistics.median(cli), 1), "max": round(max(cli), 1)},
               "conditions": {}}
    for cond in CONDITIONS:
        rs = [x for x in rows if x["condition"] == cond]
        ans = [x for x in rs if x["relevant"]]
        amb = [x for x in rs if not x["relevant"]]
        lat = sorted(x["latency_ms_median"] for x in rs)
        summary["conditions"][cond] = {
            "requests_all_relevant_retrieved": f"{sum(not x['missing'] for x in ans)}/{len(ans)}",
            "relevant_note_recall": round(sum(x["recall"] for x in ans) / len(ans), 3),
            "requests_with_unrelated": f"{sum(bool(x['unrelated']) for x in rs)}/{len(rs)}",
            "unrelated_notes_mean": round(statistics.mean(len(x["unrelated"]) for x in rs), 2),
            "unrelated_notes_total": sum(len(x["unrelated"]) for x in rs),
            "ambiguous_abstained": f"{sum(x['abstained'] for x in amb)}/{len(amb)}",
            "packet_chars_median": int(statistics.median(x["packet_chars"] for x in rs)),
            "packet_chars_max": max(x["packet_chars"] for x in rs),
            "errors": sum(x["error"] is not None for x in rs),
            "latency_ms_median": round(statistics.median(lat), 3),
            "latency_ms_p95": round(lat[min(len(lat) - 1, math.ceil(0.95 * len(lat)) - 1)], 3),
            "by_type_all_relevant": {t: f"{sum(not x['missing'] for x in ans if x['type'] == t)}/"
                                        f"{sum(1 for x in ans if x['type'] == t)}"
                                     for t in sorted({x['type'] for x in ans})},
        }

    return summary


def render_md(s, rows):
    out = ["# Eval results (generated by eval/run_eval.py; do not edit)", "",
           f"{s['n_requests']} labelled requests, {s['n_candidate_notes']} candidate notes, no model calls. "
           f"Python {s['environment']['python']} on {s['environment']['platform']}.", "",
           "| condition | all relevant retrieved | relevant-note recall | requests with unrelated notes | "
           "unrelated notes / request | ambiguous: abstained | packet chars median (max) | latency ms median (p95) | errors |",
           "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for c, m in s["conditions"].items():
        out.append(f"| `{c}` | {m['requests_all_relevant_retrieved']} | {m['relevant_note_recall']} | "
                   f"{m['requests_with_unrelated']} | {m['unrelated_notes_mean']} | {m['ambiguous_abstained']} | "
                   f"{m['packet_chars_median']:,} ({m['packet_chars_max']:,}) | "
                   f"{m['latency_ms_median']} ({m['latency_ms_p95']}) | {m['errors']} |")
    out += ["", "All-relevant-retrieved by request type:", "",
            "| condition | " + " | ".join(next(iter(s["conditions"].values()))["by_type_all_relevant"]) + " |",
            "| --- |" + " ---: |" * len(next(iter(s["conditions"].values()))["by_type_all_relevant"])]
    for c, m in s["conditions"].items():
        out.append(f"| `{c}` | " + " | ".join(m["by_type_all_relevant"].values()) + " |")
    out += ["", f"Router via CLI (fresh `python3 memory.py` process, what an agent pays per call): "
            f"median {s['router_cli_ms']['median']} ms, max {s['router_cli_ms']['max']} ms. "
            f"Index builds (once per session): BM25 {s['index_build_ms']['bm25_full_text']} ms, "
            f"picker {s['index_build_ms']['lexical_picker']} ms.", "",
            "## Failure cases", "",
            "Every request where a condition missed a relevant note, loaded a note on an ambiguous request, "
            "or errored. `bm25_top3` and `full_dump` unrelated-note inclusions are counted above, not listed.", ""]
    for c in ["router_oracle", "router_lexical", "bm25_top1", "bm25_top3", "full_dump"]:
        fails = [x for x in rows if x["condition"] == c and (x["missing"] or x["error"] or x["abstained"] is False
                 or (x["unrelated"] and c in ("router_oracle", "router_lexical", "bm25_top1")))]
        out.append(f"### `{c}`: {len(fails)} failing request(s)")
        if fails:
            out += ["", "| id | type | request | missing | unrelated loaded | detail |", "| --- | --- | --- | --- | --- | --- |"]
            for x in fails:
                short = lambda ps: (", ".join(Path(p).stem for p in ps) if len(ps) <= 5
                                    else f"{len(ps)} notes (see raw.jsonl)") or "—"
                detail = (x["error"] or x["detail"]).replace("|", "/")
                out.append(f"| {x['id']} | {x['type']} | {x['text']} | {short(x['missing'])} | "
                           f"{short(x['unrelated'])} | {detail} |")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    main()
