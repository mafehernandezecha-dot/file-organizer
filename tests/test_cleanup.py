import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from organizer import cli, moves, trash
from organizer.cleanup import build_cleanup_plan, render_cleanup_preview, split_plan
from organizer.config import Config
from organizer.index import connect
from organizer.scanner import scan
from tests.test_audit import write
from tests.test_orders_trash import FakeFinder


class CleanupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name) / "home"
        biz = home / "Documents" / "01 MEMOBRICK BUSINESS"
        themes_dir = biz / "C Website & Technology" / "Shopify Themes"
        dl = home / "Downloads"
        self.themes = []
        for i in range(7):  # theme_0 is newest
            where = themes_dir if i % 2 else dl
            self.themes.append(write(where / f"theme_export__memobrick-com-v{i}.zip",
                                     b"t" * (10 + i), age_days=i + 1))
        self.editors = [write(dl / f"memobrick-size-zoom-test-latest ({i}).zip", b"e" * (20 + i),
                              age_days=i + 1) for i in range(3)]
        self.installers = [write(dl / "Claude.dmg", b"d" * 30), write(dl / "Zoom.pkg", b"p" * 31)]
        self.other_zip = write(dl / "random stuff.zip", b"z" * 40)
        self.untouched = [
            write(home / "Documents/taxes 2024.zip", b"keep me"),        # outside intake
            write(dl / "Tool.app/Contents/Info.plist", b"app"),          # app bundle
            write(dl / "notes.txt", b"n"),
        ]
        self.cfg = Config(roots=[home / "Documents", dl], state_dir=Path(self.tmp.name) / "state")
        self.cfg.business_root = biz
        self.cfg.intake_folders = [dl]
        self.conn = connect(self.cfg.db_path)
        self.scan_id = scan(self.conn, self.cfg)
        self.plan = build_cleanup_plan(self.conn, self.scan_id, self.cfg, 0, keep=5)
        self.finder = FakeFinder(Path(self.tmp.name) / "Trash")
        self.themes_dir = themes_dir

    def tearDown(self):
        self.tmp.cleanup()

    def names(self, op):
        return sorted(Path(i["path"]).name for i in self.plan["items"] if i["op"] == op)

    def test_plan(self):
        kept_in_place = sorted(Path(k["path"]).name for k in self.plan["kept_in_place"])
        self.assertEqual(kept_in_place, ["theme_export__memobrick-com-v1.zip",
                                         "theme_export__memobrick-com-v3.zip"])
        self.assertEqual(self.names("move"), [
            "memobrick-size-zoom-test-latest (0).zip", "memobrick-size-zoom-test-latest (1).zip",
            "memobrick-size-zoom-test-latest (2).zip",
            "theme_export__memobrick-com-v0.zip", "theme_export__memobrick-com-v2.zip",
            "theme_export__memobrick-com-v4.zip"])
        self.assertEqual(self.names("trash"), [
            "Claude.dmg", "Zoom.pkg", "random stuff.zip",
            "theme_export__memobrick-com-v5.zip", "theme_export__memobrick-com-v6.zip"])
        all_paths = {i["path"] for i in self.plan["items"]}
        for p in self.untouched:
            self.assertNotIn(str(p), all_paths)
        html = render_cleanup_preview(self.plan, Path("p.json"))
        self.assertIn("Older Shopify themes", html)
        self.assertIn("already there", html)

    def test_apply_then_undo(self):
        journal = self.cfg.state_dir / "journal" / "c.jsonl"
        mp, tp = split_plan(self.plan)
        moves.apply_moves(mp, journal, say=lambda m: None)
        trash.apply_trash(tp, journal, runner=self.finder, say=lambda m: None)
        kept = sorted(p.name for p in self.themes_dir.iterdir())
        self.assertEqual(kept, [f"theme_export__memobrick-com-v{i}.zip" for i in range(5)])
        for p in self.installers + [self.other_zip, self.themes[5], self.themes[6]]:
            self.assertFalse(p.exists())
        for p in self.untouched:
            self.assertTrue(p.exists())
        real_undo = trash.undo_trash
        with mock.patch.object(trash, "is_macos", return_value=True), \
             mock.patch.object(trash, "undo_trash",
                               side_effect=lambda j, say=print: real_undo(j, runner=self.finder, say=say)):
            self.assertEqual(cli.main(["undo", str(journal)]), 0)
        for p in self.themes + self.editors + self.installers + [self.other_zip]:
            self.assertTrue(p.exists(), p)

    def test_cli_apply_cleanup(self):
        cfg_path = Path(self.tmp.name) / "config.json"
        cfg_path.write_text(json.dumps({
            "approved_folders": [str(p) for p in self.cfg.roots],
            "intake_folders": [str(p) for p in self.cfg.intake_folders],
            "state_dir": str(self.cfg.state_dir), "business_root": str(self.cfg.business_root)}))
        self.assertEqual(cli.main(["cleanup", "--config", str(cfg_path)]), 0)
        plan_path = next((self.cfg.state_dir / "plans").glob("cleanup-installers-archives_*.json"))
        args = cli.build_parser().parse_args(
            ["apply", str(plan_path), "--config", str(cfg_path), "--skip", "1"])
        finder = self.finder
        real = trash.apply_trash
        with mock.patch.object(trash, "is_macos", return_value=True), \
             mock.patch.object(trash, "latest_backup_age_hours", return_value=1.0), \
             mock.patch.object(trash, "apply_trash",
                               side_effect=lambda p, j, s=(), say=print: real(p, j, s, runner=finder, say=say)):
            self.assertEqual(cli.cmd_apply(args, ask=lambda q: "trash"), 0)
        self.assertFalse(self.installers[0].exists())
        journal = next((self.cfg.state_dir / "journal").glob("*.jsonl"))
        statuses = {json.loads(l)["status"] for l in journal.read_text().splitlines()}
        self.assertTrue({"moved", "trashed", "skipped"} <= statuses)


if __name__ == "__main__":
    unittest.main()
