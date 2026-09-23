"""The eval is reproducible: fixtures rebuild byte for byte, and a rerun matches the committed raw results
on every field except timing. Also pins the router's packet policy on the labelled requests."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "eval"))
import build_fixtures  # noqa: E402
import run_eval  # noqa: E402

TIMING = {"latency_ms_median"}


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class TestEval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg, cls.reqs, cls.cands, cls.all_files = run_eval.load_inputs()
        bm25 = run_eval.BM25({p: (run_eval.ROOT / p).read_text() for p in cls.cands}, 1.2, 0.75)
        picker = run_eval.BM25(run_eval.index_rows(run_eval.ROOT), 1.2, 0.75)
        cls.rows = run_eval.evaluate(cls.cfg, cls.reqs, cls.cands, cls.all_files, picker, bm25, repeats=1)

    def test_fixtures_rebuild_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            rebuilt = build_fixtures.build(Path(tmp) / "fixture_root")
            self.assertEqual(tree(rebuilt), tree(run_eval.ROOT))

    def test_rerun_matches_committed_raw_results_except_timing(self):
        committed = [json.loads(line) for line in (run_eval.RESULTS / "raw.jsonl").read_text().splitlines()]
        strip = lambda rows: [{k: v for k, v in r.items() if k not in TIMING} for r in rows]
        self.assertEqual(strip(self.rows), strip(committed))

    def test_router_policy_with_labelled_route_and_slug(self):
        oracle = [r for r in self.rows if r["condition"] == "router_oracle"]
        self.assertEqual(len(oracle), len(self.reqs))
        for r in oracle:
            self.assertIsNone(r["error"], r["id"])
            self.assertEqual(r["missing"], [], r["id"])
            self.assertEqual(r["unrelated"], [], r["id"])
            self.assertLessEqual(r["packet_chars"], self.cfg["max_chars"], r["id"])
            if not r["relevant"]:
                self.assertTrue(r["abstained"], r["id"])

    def test_labels_are_well_formed(self):
        ids = [r["id"] for r in self.reqs]
        self.assertEqual(len(ids), len(set(ids)))
        for r in self.reqs:
            self.assertIn(r["route"], self.cfg["routes"])
            self.assertEqual(r["entity"] is None, not r["relevant"] or self.cfg["routes"][r["route"]]["entity"] == "none")


if __name__ == "__main__":
    unittest.main()
