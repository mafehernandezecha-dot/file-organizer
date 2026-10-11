import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from organizer import cli, moves, trash
from organizer.config import Config
from organizer.index import connect
from organizer.plans import (
    build_move_plan, find_screen_captures, load_plan, render_move_preview, save_plan,
    screenshot_move_pairs,
)
from organizer.scanner import scan
from tests.test_audit import write


class ScreenshotMoveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.dest = self.home / "Pictures" / "Screenshots"
        self.shots = [
            write(self.home / "Desktop/Screenshot 2026-10-01 at 10.00.00.png", b"a" * 10, age_days=5),
            write(self.home / "Desktop/Screenshot 2026-10-02 at 11.00.00.png", b"b" * 11),
            write(self.home / "Downloads/Screenshot 2026-10-01 at 10.00.00.png", b"c" * 12),  # same name
            write(self.home / "Desktop/Screen Recording 2026-10-03 at 09.00.00.mov", b"d" * 13),
        ]
        self.untouched = [
            write(self.home / "Desktop/IMG_0001.jpg", b"photo"),
            write(self.dest / "Screenshot old.png", b"already sorted"),
        ]
        self.cfg = Config(roots=[self.home / "Desktop", self.home / "Downloads",
                                 self.home / "Pictures"], state_dir=base / "state")
        self.conn = connect(self.cfg.db_path)
        self.scan_id = scan(self.conn, self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self):
        rows = find_screen_captures(self.conn, self.scan_id, self.dest)
        p = build_move_plan(screenshot_move_pairs(rows, self.dest), "screenshots", self.dest, 0)
        path = save_plan(p, self.cfg.state_dir / "plans", "move-screenshots")
        return load_plan(path), path

    def test_plan_targets_and_name_clashes(self):
        plan, path = self.plan()
        dests = sorted(os.path.relpath(i["dest"], self.dest) for i in plan["items"])
        self.assertEqual(dests, [
            "Screen Recordings/Screen Recording 2026-10-03 at 09.00.00.mov",
            "Screenshot 2026-10-01 at 10.00.00 (2).png",
            "Screenshot 2026-10-01 at 10.00.00.png",
            "Screenshot 2026-10-02 at 11.00.00.png",
        ])
        self.assertEqual(len(plan["items"]), 4, "already-sorted screenshot was planned again")
        html = render_move_preview(plan, path, "screenshots and screen recordings")
        self.assertIn("Nothing has happened yet", html)
        self.assertIn("(2)", html)
        for p in self.shots:
            self.assertTrue(p.exists())

    def test_apply_keeps_dates_skips_changed_and_undo_restores(self):
        plan, _ = self.plan()
        old_mtime = self.shots[0].stat().st_mtime
        self.shots[1].write_bytes(b"edited after the scan")
        journal = self.cfg.state_dir / "journal" / "m.jsonl"
        counts = moves.apply_moves(plan, journal, say=lambda m: None)
        self.assertEqual(counts, {"moved": 3, "skipped": 1, "failed": 0})
        moved = self.dest / "Screenshot 2026-10-01 at 10.00.00.png"
        self.assertEqual(moved.read_bytes(), b"a" * 10)
        self.assertEqual(moved.stat().st_mtime, old_mtime)
        self.assertTrue((self.dest / "Screen Recordings" /
                         "Screen Recording 2026-10-03 at 09.00.00.mov").exists())
        self.assertTrue(self.shots[1].exists(), "changed file was moved")
        for p in self.untouched:
            self.assertTrue(p.exists())

        self.assertEqual(moves.undo_moves(journal, say=lambda m: None),
                         {"restored": 3, "failed": 0})
        for p in self.shots:
            self.assertTrue(p.exists())
        self.assertEqual(self.shots[2].read_bytes(), b"c" * 12)
        self.assertEqual(moves.undo_moves(journal, say=lambda m: None),
                         {"restored": 0, "failed": 0})

    def test_never_overwrites_at_apply_time(self):
        plan, _ = self.plan()
        target = Path(plan["items"][0]["dest"])
        write(target, b"appeared after preview")
        journal = self.cfg.state_dir / "journal" / "m2.jsonl"
        counts = moves.apply_moves(plan, journal, say=lambda m: None)
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(target.read_bytes(), b"appeared after preview")

    def test_skip_numbers_and_interrupted_run_undo(self):
        plan, _ = self.plan()
        journal = self.cfg.state_dir / "journal" / "m3.jsonl"
        partial = dict(plan, items=plan["items"][:2])  # as if the run stopped halfway
        moves.apply_moves(partial, journal, skip={1}, say=lambda m: None)
        self.assertEqual(moves.journal_kind(journal), "move")
        moves.undo_moves(journal, say=lambda m: None)
        for p in self.shots:
            self.assertTrue(p.exists())

    def test_cli_screenshots_apply_undo_end_to_end(self):
        cfg_path = Path(self.tmp.name) / "config.json"
        cfg_path.write_text(json.dumps({
            "approved_folders": [str(r) for r in self.cfg.roots],
            "state_dir": str(self.cfg.state_dir),
            "screenshots_folder": str(self.dest),
        }))
        self.assertEqual(cli.main(["screenshots", "--config", str(cfg_path)]), 0)
        plan_path = next((self.cfg.state_dir / "plans").glob("move-screenshots_*.json"))
        args = cli.build_parser().parse_args(["apply", str(plan_path), "--config", str(cfg_path)])
        with mock.patch.object(trash, "latest_backup_age_hours", return_value=2.0):
            self.assertEqual(cli.cmd_apply(args, ask=lambda q: "TRASH"), 1)  # wrong word
            for p in self.shots:
                self.assertTrue(p.exists())
            self.assertEqual(cli.cmd_apply(args, ask=lambda q: "MOVE"), 0)
        for p in self.shots:
            self.assertFalse(p.exists())
        journal = next((self.cfg.state_dir / "journal").glob("*.jsonl"))
        self.assertEqual(cli.main(["undo", str(journal)]), 0)
        for p in self.shots:
            self.assertTrue(p.exists())


if __name__ == "__main__":
    unittest.main()
