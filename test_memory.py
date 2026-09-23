"""Tests for memory.py. All run against temporary synthetic fixtures, never the real vault."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import memory

REPO = Path(__file__).resolve().parent
SCRIPT = REPO / "memory.py"


def run(root, *args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), *args],
                          capture_output=True, text=True, cwd=cwd)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "repo"
        self.root.mkdir()
        shutil.copy(REPO / "routes.json", self.root)
        shutil.copy(REPO / "ROUTING.md", self.root)
        v = self.root / "vault"
        for rel in ["CURRENT_STATE.md", "TASKS.md", "DECISIONS.md"]:
            (v / rel).parent.mkdir(parents=True, exist_ok=True)
            (v / rel).write_text(f"# {rel}\nfixture\n")
        for cat in ["ideas", "jobs", "projects", "sessions"]:
            (v / cat).mkdir()
            shutil.copy(REPO / "vault" / cat / "_TEMPLATE.md", v / cat)
            (v / cat / "INDEX.md").write_text(f"# {cat} index\n| title | path | description |\n| --- | --- | --- |\n")
        (v / "ideas" / "alpha.md").write_text("# Alpha\nNext action: MARKER-ALPHA-1\n")
        (v / "ideas" / "beta.md").write_text("# Beta\nNext action: MARKER-BETA-UNRELATED\n")
        (v / "jobs" / "example-co.md").write_text("# Example Co\nstage: MARKER-JOB-EX\n")
        (v / "jobs" / "other-co.md").write_text("# Other Co\nstage: MARKER-JOB-OTHER\n")
        self.v = v

    def tearDown(self):
        shutil.rmtree(self.tmp)


class TestRoutes(Fixture):
    def test_idea_includes_named_excludes_unrelated(self):
        packet, files = memory.build_packet(self.root, "idea", "alpha")
        paths = [f["path"] for f in files]
        self.assertEqual(paths, ["vault/CURRENT_STATE.md", "vault/TASKS.md", "ROUTING.md", "vault/ideas/alpha.md"])
        self.assertIn("MARKER-ALPHA-1", packet)
        self.assertNotIn("MARKER-BETA-UNRELATED", packet)
        self.assertNotIn("MARKER-JOB", packet)

    def test_job_status_excludes_other_jobs_and_career(self):
        packet, files = memory.build_packet(self.root, "job", "example-co")
        self.assertIn("MARKER-JOB-EX", packet)
        self.assertNotIn("MARKER-JOB-OTHER", packet)
        self.assertFalse(any("career" in f["path"] for f in files))

    def test_job_prep_fails_closed_without_evidence_bank(self):
        with self.assertRaisesRegex(memory.MemoryError_, "unavailable.*EVIDENCE_BANK"):
            memory.build_packet(self.root, "job-prep", "example-co")

    def test_job_prep_includes_evidence_bank_when_present(self):
        (self.v / "career").mkdir()
        (self.v / "career" / "EVIDENCE_BANK.md").write_text("SYNTHETIC-BANK\n")
        packet, files = memory.build_packet(self.root, "job-prep", "example-co")
        self.assertIn("SYNTHETIC-BANK", packet)
        self.assertNotIn("MARKER-JOB-OTHER", packet)

    def test_unspecified_entity_loads_index_only(self):
        packet, files = memory.build_packet(self.root, "idea")
        self.assertEqual(files[-1]["path"], "vault/ideas/INDEX.md")
        self.assertNotIn("MARKER-ALPHA-1", packet)

    def test_missing_entity(self):
        with self.assertRaisesRegex(memory.MemoryError_, "unavailable.*nope.md"):
            memory.build_packet(self.root, "idea", "nope")

    def test_unknown_route(self):
        with self.assertRaisesRegex(memory.MemoryError_, "unknown route"):
            memory.build_packet(self.root, "everything")

    def test_traversal_rejected(self):
        for bad in ["../x", "a/b", "..", "INDEX.md"]:
            with self.assertRaises(memory.MemoryError_):
                memory.build_packet(self.root, "idea", bad)

    def test_symlink_escape_rejected(self):
        outside = self.tmp / "secret.md"
        outside.write_text("SECRET-OUTSIDE\n")
        os.symlink(outside, self.v / "ideas" / "leak.md")
        with self.assertRaisesRegex(memory.MemoryError_, "escapes"):
            memory.build_packet(self.root, "idea", "leak")

    def test_manifest_dedup_and_size_reported(self):
        packet, files = memory.build_packet(self.root, "idea", "alpha")
        self.assertEqual(len({f["path"] for f in files}), len(files))
        self.assertIn(f"total packet chars: {len(packet)}", packet)

    def test_routing_md_is_generated_from_routes_json(self):
        self.assertEqual((REPO / "ROUTING.md").read_text(), memory.render_routing(REPO))


class TestCli(Fixture):
    def test_new_creates_note_and_index_row_and_refuses_duplicate(self):
        r = run(self.root, "new", "idea", "Synthetic comet idea")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "vault/ideas/synthetic-comet-idea.md")
        note = (self.v / "ideas" / "synthetic-comet-idea.md").read_text()
        self.assertIn("id: idea-synthetic-comet-idea", note)
        self.assertNotIn("{{", note)
        self.assertIn("ideas/synthetic-comet-idea.md", (self.v / "ideas" / "INDEX.md").read_text())
        r2 = run(self.root, "new", "idea", "Synthetic comet idea")
        self.assertEqual(r2.returncode, 2)
        self.assertIn("refusing to overwrite", r2.stderr)
        self.assertEqual((self.v / "ideas" / "synthetic-comet-idea.md").read_text(), note)

    def test_new_records_writer(self):
        r = run(self.root, "new", "idea", "Gpt idea", "--by", "chatgpt", "--description", "Test | desc")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("provenance: created by chatgpt", (self.v / "ideas" / "gpt-idea.md").read_text())
        self.assertIn("| Test / desc |", (self.v / "ideas" / "INDEX.md").read_text())
        r = run(self.root, "new", "idea", "Bad writer", "--by", "x\\1\n")
        self.assertEqual(r.returncode, 2)

    def test_update_with_matching_revision(self):
        note = self.v / "ideas" / "alpha.md"
        note.write_text("---\nid: idea-alpha\nupdated: old\n---\nNext action: MARKER-ALPHA-1\n")
        sha = memory.file_sha(note.read_text())
        src = self.tmp / "new.md"
        src.write_text("---\nid: idea-alpha\nupdated: old\n---\nNext action: MARKER-ALPHA-2\n")
        r = run(self.root, "update", "idea", "alpha", "--expect", sha[:12], "--from", str(src), "--by", "claude")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = note.read_text()
        self.assertIn("MARKER-ALPHA-2", text)
        self.assertIn("last_writer: claude", text)
        self.assertNotIn("updated: old", text)
        self.assertIn("+Next action: MARKER-ALPHA-2", r.stdout)

    def test_update_refuses_stale_revision(self):
        note = self.v / "ideas" / "alpha.md"
        stale = memory.file_sha(note.read_text())[:12]
        note.write_text("# Alpha\nNext action: EDITED-IN-OBSIDIAN\n")  # concurrent edit
        src = self.tmp / "new.md"
        src.write_text("---\nid: idea-alpha\n---\n# Alpha\nNext action: FROM-STALE-WRITER\n")
        r = run(self.root, "update", "idea", "alpha", "--expect", stale, "--from", str(src))
        self.assertEqual(r.returncode, 2)
        self.assertIn("conflict", r.stderr)
        self.assertIn("EDITED-IN-OBSIDIAN", note.read_text())

    def test_update_refuses_symlink_into_other_category(self):
        os.symlink(self.v / "jobs" / "example-co.md", self.v / "ideas" / "sneaky.md")
        sha = memory.file_sha((self.v / "jobs" / "example-co.md").read_text())
        src = self.tmp / "new.md"
        src.write_text("---\nid: idea-sneaky\n---\nOVERWRITTEN\n")
        r = run(self.root, "update", "idea", "sneaky", "--expect", sha[:12], "--from", str(src))
        self.assertEqual(r.returncode, 2)
        self.assertIn("symlink", r.stderr)
        self.assertIn("MARKER-JOB-EX", (self.v / "jobs" / "example-co.md").read_text())

    def test_update_normalises_frontmatter_only(self):
        note = self.v / "ideas" / "alpha.md"
        note.write_text("---\nid: idea-alpha\ncreated: c\nupdated: old\n---\nbody\n")
        sha = memory.file_sha(note.read_text())
        src = self.tmp / "new.md"
        # writer dropped updated:, forged last_writer, and has look-alike lines in the body
        src.write_text("---\nid: idea-alpha\ncreated: c\nlast_writer: owner\n---\nupdated: body-line\nlast_writer: body-line\n")
        r = run(self.root, "update", "idea", "alpha", "--expect", sha[:12], "--from", str(src), "--by", "chatgpt")
        self.assertEqual(r.returncode, 0, r.stderr)
        front, body = note.read_text().split("---\n")[1:3]
        self.assertIn("last_writer: chatgpt", front)
        self.assertNotIn("last_writer: owner", front)
        self.assertRegex(front, r"updated: \d{4}-")
        self.assertEqual(body, "updated: body-line\nlast_writer: body-line\n")

    def test_update_rejects_bad_frontmatter(self):
        note = self.v / "ideas" / "alpha.md"
        sha = memory.file_sha(note.read_text())
        src = self.tmp / "new.md"
        for text in ("no frontmatter\n", "---\n---\nbody\n", "---\nid: idea-beta\n---\n",
                     "---\nid: idea-alpha\nid: idea-alpha\n---\n", "body\n---\nid: idea-alpha\n---\n"):
            src.write_text(text)
            r = run(self.root, "update", "idea", "alpha", "--expect", sha[:12], "--from", str(src))
            self.assertEqual(r.returncode, 2, text)
        self.assertEqual(memory.file_sha(note.read_text()), sha)

    def test_concurrent_updates_with_same_revision_only_one_wins(self):
        note = self.v / "ideas" / "alpha.md"
        note.write_text("---\nid: idea-alpha\n---\nv0\n")
        sha = memory.file_sha(note.read_text())
        procs = []
        for i in range(6):
            src = self.tmp / f"new{i}.md"
            src.write_text(f"---\nid: idea-alpha\n---\nWRITER-{i}\n")
            procs.append(subprocess.Popen([sys.executable, str(SCRIPT), "--root", str(self.root), "update", "idea",
                                           "alpha", "--expect", sha[:12], "--from", str(src)],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        codes = [p.wait() for p in procs]
        for p in procs:
            p.stdout.close(); p.stderr.close()
        self.assertEqual(sorted(codes), [0] + [2] * 5)
        self.assertEqual(len(re.findall(r"WRITER-\d", note.read_text())), 1)
        self.assertEqual([p.name for p in (self.v / "ideas").iterdir() if p.name.endswith(".tmp")], [])

    def test_concurrent_new_keeps_every_index_row(self):
        procs = [subprocess.Popen([sys.executable, str(SCRIPT), "--root", str(self.root), "new", "idea",
                                   f"Parallel idea {i}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for i in range(8)]
        for p in procs:
            self.assertEqual(p.wait(), 0)
            p.stdout.close(); p.stderr.close()
        index = (self.v / "ideas" / "INDEX.md").read_text()
        self.assertEqual(sum(f"parallel-idea-{i}.md" in index for i in range(8)), 8)

    def test_lone_surrogate_never_truncates_index_or_leaves_empty_note(self):
        index = self.v / "ideas" / "INDEX.md"
        before = index.read_text()
        for title, desc in (("Surrogate idea", "bad \udcff"), ("Bad \udcff title", "ok")):
            with self.assertRaises(memory.MemoryError_):
                memory.cmd_new(self.root, "idea", title, description=desc)
        self.assertEqual(index.read_text(), before)
        self.assertEqual(sorted(p.name for p in (self.v / "ideas").iterdir()),
                         ["INDEX.md", "_TEMPLATE.md", "alpha.md", "beta.md"])

    def test_update_requires_exact_filename_case(self):
        (self.v / "ideas" / "Mixed-Case.md").write_text("---\nid: idea-mixed-case\n---\nx\n")
        if not (self.v / "ideas" / "mixed-case.md").exists():
            self.skipTest("case-sensitive filesystem")
        sha = memory.file_sha((self.v / "ideas" / "Mixed-Case.md").read_text())
        src = self.tmp / "new.md"
        src.write_text("---\nid: idea-mixed-case\n---\nCHANGED\n")
        r = run(self.root, "update", "idea", "mixed-case", "--expect", sha[:12], "--from", str(src))
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("CHANGED", (self.v / "ideas" / "Mixed-Case.md").read_text())

    def test_update_rejects_yaml_tricks_and_lone_cr(self):
        note = self.v / "ideas" / "alpha.md"
        note.write_text("---\nid: idea-alpha\nprovenance: fixture\n---\nbody\n")
        sha = memory.file_sha(note.read_text())
        src = self.tmp / "new.md"
        for text in ('---\nid: idea-alpha\nprovenance: fixture\n"provenance": owner-reported\n---\n',
                     "---\nid: idea-alpha\nprovenance: fixture\n? provenance\n---\n",
                     "---\nid: idea-alpha\nprovenance: fixture\n  continued\n---\n",
                     "---\nid: idea-alpha\nprovenance: fixture\nprovenance: owner-reported\n---\n",
                     "---\nid: idea-alpha\nprovenance: |\n---\n",
                     "---\nid: idea-alpha\n  updated: x\n---\n"):
            src.write_text(text)
            r = run(self.root, "update", "idea", "alpha", "--expect", sha[:12], "--from", str(src))
            self.assertEqual(r.returncode, 2, text)
        # a lone CR is a line break for memory.py too, so what is checked is what is stored
        src.write_bytes(b"---\rid: idea-alpha\rprovenance: fixture\r---\rbody\r")
        r = run(self.root, "update", "idea", "alpha", "--expect", sha[:12], "--from", str(src))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("\r", note.read_text())

    def test_new_quotes_title_in_frontmatter(self):
        r = run(self.root, "new", "idea", "Idea: with # hash")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.v / "ideas" / "idea-with-hash.md").read_text()
        self.assertIn('title: "Idea: with # hash"', text)
        self.assertIn("# Idea: with # hash", text)

    def test_lock_file_symlink_is_not_followed(self):
        outside = self.tmp / "outside-lock"
        (self.root / "out").mkdir(exist_ok=True)
        os.symlink(outside, self.root / "out" / ".vault.lock")
        r = run(self.root, "new", "idea", "Lock test")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(outside.exists())

    def test_update_detects_edit_landing_during_write(self):
        note = self.v / "ideas" / "alpha.md"
        note.write_text("---\nid: idea-alpha\n---\nv0\n")
        sha = memory.file_sha(note.read_text())
        src = self.tmp / "new.md"
        src.write_text("---\nid: idea-alpha\n---\nAGENT-EDIT\n")
        real_fsync = os.fsync
        def obsidian_saves(fd):  # an unlocked editor saves while the temp file is being synced
            real_fsync(fd)
            note.write_text("---\nid: idea-alpha\n---\nOBSIDIAN-EDIT\n")
        with mock.patch.object(memory.os, "fsync", side_effect=obsidian_saves):
            with self.assertRaisesRegex(memory.MemoryError_, "changed while writing"):
                memory.cmd_update(self.root, "idea", "alpha", sha[:12], str(src))
        self.assertIn("OBSIDIAN-EDIT", note.read_text())
        self.assertEqual([p.name for p in (self.v / "ideas").iterdir() if p.name.endswith(".tmp")], [])

    def test_new_keeps_index_row_added_during_create(self):
        index = self.v / "ideas" / "INDEX.md"
        real_create = memory.create_exclusive
        def editor_adds_row(path, data):
            real_create(path, data)
            index.write_text(index.read_text() + "| Owner manual | `ideas/manual.md` | typed in Obsidian |\n")
        with mock.patch.object(memory, "create_exclusive", side_effect=editor_adds_row), \
                mock.patch("sys.stdout"):
            memory.cmd_new(self.root, "idea", "Race idea")
        text = index.read_text()
        self.assertIn("Owner manual", text)
        self.assertIn("race-idea.md", text)

    def test_update_rejects_missing_and_bad_input(self):
        src = self.tmp / "new.md"
        src.write_text("x\n")
        for args in (["idea", "nope", "--expect", "0" * 12], ["idea", "../alpha", "--expect", "0" * 12],
                     ["idea", "alpha", "--expect", "abc"]):
            r = run(self.root, "update", *args, "--from", str(src))
            self.assertEqual(r.returncode, 2, args)

    def test_new_unknown_kind(self):
        r = run(self.root, "new", "person", "Someone")
        self.assertEqual(r.returncode, 2)

    def test_oversize_fails_without_partial_output(self):
        out = self.tmp / "out" / "p.md"
        r = run(self.root, "context", "idea", "alpha", "--output", str(out), "--max-chars", "200")
        self.assertEqual(r.returncode, 2)
        self.assertIn("over limit 200", r.stderr)
        self.assertIn("vault/ideas/alpha.md=", r.stderr)
        self.assertFalse(out.exists())
        self.assertFalse(out.parent.exists() and any(out.parent.iterdir()))

    def test_output_inside_vault_refused(self):
        r = run(self.root, "context", "idea", "alpha", "--output", str(self.v / "packet.md"))
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.v / "packet.md").exists())

    def test_works_from_other_cwd(self):
        r = run(self.root, "context", "idea", "alpha", cwd=str(self.tmp))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("MARKER-ALPHA-1", r.stdout)

    def test_new_process_sees_changed_next_action(self):
        r1 = run(self.root, "context", "idea", "alpha")
        self.assertIn("MARKER-ALPHA-1", r1.stdout)
        (self.v / "ideas" / "alpha.md").write_text("# Alpha\nNext action: MARKER-ALPHA-2\n")
        r2 = run(self.root, "context", "idea", "alpha")
        self.assertIn("MARKER-ALPHA-2", r2.stdout)
        self.assertNotIn("MARKER-ALPHA-1", r2.stdout)


if __name__ == "__main__":
    unittest.main()
