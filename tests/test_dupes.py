import os
import tempfile
import unittest
from pathlib import Path

from organizer import trash
from organizer.config import Config
from organizer.duplicates import find_duplicates
from organizer.dupes import build_duplicates_plan, render_duplicates_preview
from organizer.index import connect
from organizer.scanner import scan
from tests.test_audit import write
from tests.test_orders_trash import FakeFinder


class DuplicatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name) / "home"
        self.home = home
        biz = home / "Documents" / "01 MEMOBRICK BUSINESS"
        a, b, c = os.urandom(300_000), os.urandom(5000), os.urandom(7000)
        self.p = {
            "logo_biz": write(biz / "A Brand & Identity/Logos & Graphics/logo.png", a),
            "logo_dl": write(home / "Downloads/logo.png", a),
            "logo_desk": write(home / "Desktop/logo copy.png", a),
            "doc_copy": write(home / "Documents/plan copy.pdf", b, age_days=1),
            "doc_clean": write(home / "Documents/plan.pdf", b, age_days=5),
            "x_new": write(home / "Downloads/x.jpg", c, age_days=1),
            "x_old": write(home / "Downloads/x (1).jpg", c, age_days=9),
            "unique": write(home / "Downloads/unique.jpg", os.urandom(5000)),
        }
        self.cfg = Config(roots=[home / "Documents", home / "Downloads", home / "Desktop"],
                          state_dir=Path(self.tmp.name) / "state")
        self.cfg.business_root = biz
        self.cfg.intake_folders = [home / "Downloads", home / "Desktop"]
        self.conn = connect(self.cfg.db_path)
        self.scan_id = scan(self.conn, self.cfg)
        find_duplicates(self.conn, self.scan_id)
        self.plan = build_duplicates_plan(self.conn, self.scan_id, self.cfg, 0)
        self.finder = FakeFinder(Path(self.tmp.name) / "Trash")

    def tearDown(self):
        self.tmp.cleanup()

    def test_keepers(self):
        keeps = {Path(s["keep"]).name: s["keep"] for s in self.plan["sets"]}
        self.assertEqual(keeps["logo.png"], str(self.p["logo_biz"]))   # organized folder wins
        self.assertEqual(keeps["plan.pdf"], str(self.p["doc_clean"]))  # clean name wins
        self.assertEqual(keeps["x.jpg"], str(self.p["x_new"]))          # clean name beats older
        trashed = sorted(Path(i["path"]).name for i in self.plan["items"])
        self.assertEqual(trashed, ["logo copy.png", "logo.png", "plan copy.pdf", "x (1).jpg"])
        self.assertNotIn(str(self.p["unique"]), [i["path"] for i in self.plan["items"]])
        html = render_duplicates_preview(self.plan, Path("p.json"))
        self.assertIn("✓ keep", html)

    def test_apply_keeps_one_of_each(self):
        journal = self.cfg.state_dir / "journal" / "d.jsonl"
        counts = trash.apply_trash(self.plan, journal, runner=self.finder, say=lambda m: None)
        self.assertEqual(counts, {"trashed": 4, "skipped": 0, "failed": 0})
        for k in ("logo_biz", "doc_clean", "x_new", "unique"):
            self.assertTrue(self.p[k].exists(), k)
        trash.undo_trash(journal, runner=self.finder, say=lambda m: None)
        for p in self.p.values():
            self.assertTrue(p.exists())

    def test_extra_stays_if_keeper_gone_or_changed(self):
        self.p["logo_biz"].unlink()                      # keeper deleted after the scan
        self.p["doc_clean"].write_bytes(os.urandom(5000))  # keeper edited (same size)
        journal = self.cfg.state_dir / "journal" / "d2.jsonl"
        counts = trash.apply_trash(self.plan, journal, runner=self.finder, say=lambda m: None)
        self.assertTrue(self.p["logo_dl"].exists())
        self.assertTrue(self.p["logo_desk"].exists())
        self.assertTrue(self.p["doc_copy"].exists())
        self.assertFalse(self.p["x_old"].exists())
        self.assertEqual(counts["trashed"], 1)
        self.assertEqual(counts["skipped"], 3)


if __name__ == "__main__":
    unittest.main()
