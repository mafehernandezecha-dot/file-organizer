import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from organizer import cli
from organizer.config import Config
from organizer.leftovers import build_leftovers, render_leftovers
from tests.test_audit import snapshot, write


class LeftoversTest(unittest.TestCase):
    def test_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            dl, docs = home / "Downloads", home / "Documents"
            write(dl / "bank passwords.pdf", b"p")
            write(dl / "Tool.app/Contents/Info.plist", b"a")
            write(docs / "loose.pdf", b"l")
            write(docs / "My Folder/x.pdf", b"x")                       # own folder: fine
            write(docs / "01 MEMOBRICK BUSINESS/A Brand & Identity/l.png", b"i")
            state = home / "state"
            (state / "journal").mkdir(parents=True)
            (state / "journal/weekly-sort_x.jsonl").write_text("\n".join(json.dumps(e) for e in [
                {"status": "skipped", "path": str(dl / "big.mov"), "why": "stored only in iCloud (download it first)"},
                {"status": "skipped", "path": str(dl / "gone.pdf"), "why": "no longer there"},
                {"status": "skipped", "path": str(dl / "k.pdf"), "why": "you chose to keep it where it is"},
                {"status": "moved", "path": str(dl / "a.png"), "dest": "/x"},
            ]) + "\n")
            (state / "plans").mkdir()
            (state / "plans/weekly-sort_x.json").write_text(json.dumps({"review": [
                {"path": str(dl / "bank passwords.pdf"), "reason": "looks private — never touched"}]}))
            cfg = Config(roots=[dl, docs], state_dir=state)
            cfg.business_root = docs / "01 MEMOBRICK BUSINESS"
            cfg.intake_folders = [dl]
            before = snapshot(home / "Downloads")
            data = build_leftovers(cfg)
            self.assertEqual(before, snapshot(home / "Downloads"))
            self.assertEqual([Path(i["path"]).name for i in data["skipped"]], ["big.mov"])
            self.assertEqual([Path(i["path"]).name for i in data["kept"]], ["bank passwords.pdf"])
            names = sorted(Path(i["path"]).name for i in data["still_here"])
            self.assertEqual(names, ["Tool.app", "bank passwords.pdf", "loose.pdf"])
            html = render_leftovers(data)
            self.assertIn("Download Now", html)

            cfg_path = home / "config.json"
            cfg_path.write_text(json.dumps({"approved_folders": [str(dl), str(docs)],
                                            "intake_folders": [str(dl)],
                                            "business_root": str(cfg.business_root),
                                            "state_dir": str(state)}))
            self.assertEqual(cli.main(["leftovers", "--config", str(cfg_path)]), 0)


if __name__ == "__main__":
    unittest.main()
