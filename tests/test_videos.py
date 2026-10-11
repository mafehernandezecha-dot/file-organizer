import json
import os
import tempfile
import unittest
from pathlib import Path

from organizer import cli, moves
from organizer.config import Config
from organizer.index import connect
from organizer.scanner import scan
from organizer.sorting import build_sort_plan
from organizer.videos import build_videos_plan
from tests.test_audit import write

VIDEOS = "01 MEMOBRICK BUSINESS/F Creative Assets/Videos"


class VideosTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.docs = self.home / "Documents"
        self.biz = self.docs / "01 MEMOBRICK BUSINESS"

    def tearDown(self):
        self.tmp.cleanup()

    def cfg(self, roots):
        c = Config(roots=roots, state_dir=self.home / "state", loose_photos_are_business=True)
        c.business_root = self.biz
        return c

    def test_new_videos_all_go_to_videos(self):
        dl = self.home / "Downloads"
        write(dl / "holiday reel v2.mp4", b"1")              # was Social Media
        write(dl / "Black Friday ad.mov", b"2")               # was Ad Creatives
        write(dl / "memobrick unboxing.mp4", b"3")            # business by name
        write(dl / "holiday ad.png", b"4")                    # image: stays with images
        write(dl / "Memobrick shoot/a.mp4", b"5")
        write(dl / "Memobrick shoot/b.mov", b"6")
        write(dl / "Memobrick shoot/c.jpg", b"7")
        write(dl / "family trip.mp4", b"8")                   # not Memobrick
        self.docs.mkdir(parents=True)
        cfg = self.cfg([dl])
        cfg.intake_folders = [dl]
        conn = connect(cfg.db_path)
        plan = build_sort_plan(conn, scan(conn, cfg), cfg, 0)
        got = {Path(i["path"]).name: os.path.dirname(os.path.relpath(i["dest"], self.docs))
               for i in plan["items"]}
        for name in ("holiday reel v2.mp4", "Black Friday ad.mov", "memobrick unboxing.mp4",
                     "Memobrick shoot"):
            self.assertEqual(got[name], VIDEOS, name)
        self.assertEqual(got["holiday ad.png"], "01 MEMOBRICK BUSINESS/F Creative Assets/Ad Creatives")
        self.assertEqual(got["family trip.mp4"], "05 INTAKE/Videos")

    def test_gather_existing_videos(self):
        v = [
            write(self.biz / "E Social Media/Posts & Videos/reel.mp4", b"r"),
            write(self.biz / "F Creative Assets/Ad Creatives/ad.mov", b"a"),
            write(self.biz / "F Creative Assets/Videos/2026/old.mp4", b"o"),
            write(self.biz / "F Creative Assets/AI-Generated/reel.mp4", b"x"),  # name clash
        ]
        stay = [
            write(self.biz / "F Creative Assets/Videos/already.mp4", b"k"),
            write(self.biz / "F Creative Assets/Videos/My Picks/fav.mp4", b"f"),  # your subfolder
            write(self.biz / "F Creative Assets/Ad Creatives/ad.png", b"i"),
            write(self.biz / "C Website & Technology/Editor & Studio Builds/ed/package.json", b"{}"),
            write(self.biz / "C Website & Technology/Editor & Studio Builds/ed/demo.mp4", b"d"),
        ]
        plan = build_videos_plan(self.cfg([self.docs]))
        dests = sorted(os.path.relpath(i["dest"], self.docs) for i in plan["items"])
        self.assertEqual(dests, [VIDEOS + "/ad.mov", VIDEOS + "/old.mp4", VIDEOS + "/reel (2).mp4",
                                 VIDEOS + "/reel.mp4"])
        journal = self.home / "j.jsonl"
        self.assertEqual(moves.apply_moves(plan, journal, say=lambda m: None)["moved"], 4)
        for p in stay:
            self.assertTrue(p.exists())
        moves.undo_moves(journal, say=lambda m: None)
        for p in v:
            self.assertTrue(p.exists())

    def test_cli_videos(self):
        write(self.biz / "E Social Media/Posts & Videos/reel.mp4", b"r")
        cfg_path = self.home / "config.json"
        cfg_path.write_text(json.dumps({"approved_folders": [str(self.docs)],
                                        "business_root": str(self.biz),
                                        "state_dir": str(self.home / "state")}))
        self.assertEqual(cli.main(["videos", "--config", str(cfg_path)]), 0)
        self.assertTrue(list((self.home / "state/plans").glob("move-videos_*.json")))


if __name__ == "__main__":
    unittest.main()
