import json
import tempfile
import unittest
from pathlib import Path

from organizer import vision
from organizer.config import Config
from organizer.index import connect
from organizer.scanner import scan
from organizer.sorting import build_sort_plan, image_candidates
from tests.test_audit import write


def look(**kw):
    base = {"w": 1000, "h": 1000, "alpha": False, "colors": 3000, "labels": [], "text": "",
            "faces": 0}
    base.update(kw)
    return base


class DecideTest(unittest.TestCase):
    def dest(self, **kw):
        d = vision.decide_from_vision(look(**kw))
        return None if d is None else d.dest

    def test_categories(self):
        self.assertEqual(self.dest(text="MEMOBRICK", alpha=True, colors=12),
                         "A Brand & Identity/Logos & Graphics")
        self.assertEqual(self.dest(text="Memo Brick", colors=30), "A Brand & Identity/Logos & Graphics")
        self.assertEqual(self.dest(alpha=True, colors=20), "A Brand & Identity/Logos & Graphics")
        self.assertEqual(self.dest(text="Holiday sale 30% OFF your custom mosaic"),
                         "F Creative Assets/Ad Creatives")
        self.assertEqual(self.dest(colors=28), "B Product Development/Mosaic Designs")
        self.assertEqual(self.dest(text=" ".join(["word"] * 15)), "F Creative Assets/Graphics & Flyers")
        self.assertEqual(self.dest(labels=[["toy", "0.80"], ["indoor", "0.5"]]),
                         "F Creative Assets/Product Photos")
        self.assertEqual(self.dest(labels=[["picture_frame", "0.6"]]), "F Creative Assets/Product Photos")
        self.assertEqual(self.dest(faces=2, labels=[["people", "0.9"]]),
                         "F Creative Assets/Photos with People")
        self.assertEqual(self.dest(labels=[["document", "0.9"]]), "")
        self.assertIsNone(self.dest(labels=[["sky", "0.9"], ["outdoor", "0.8"]]))
        self.assertIsNone(vision.decide_from_vision({"path": "x", "error": "unreadable"}))

    def test_small_or_faces_never_mosaic(self):
        self.assertNotEqual(self.dest(colors=20, w=100, h=100), "B Product Development/Mosaic Designs")
        self.assertEqual(self.dest(colors=20, faces=1), "F Creative Assets/Photos with People")

    def test_low_confidence_label_ignored(self):
        self.assertIsNone(self.dest(labels=[["toy", "0.15"]]))

    def test_tool_not_built_off_mac(self):
        with tempfile.TemporaryDirectory() as tmp:
            if not vision.sys.platform == "darwin":
                self.assertIsNone(vision.ensure_tool(Path(tmp), lambda m: None))


class AnalyzeAndSortTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name) / "home"
        dl = home / "Downloads"
        self.logo = write(dl / "final.png", b"a")
        self.people = write(dl / "IMG_2000.jpg", b"bb")
        self.plain = write(dl / "IMG_2001.jpg", b"ccc")
        self.named = write(dl / "memobrick logo dark.png", b"dddd")
        write(dl / "sub/inner.jpg", b"e")
        self.cfg = Config(roots=[dl], state_dir=Path(self.tmp.name) / "state",
                          loose_photos_are_business=True)
        self.cfg.intake_folders = [dl]
        self.cfg.business_root = home / "Documents" / "01 MEMOBRICK BUSINESS"
        self.conn = connect(self.cfg.db_path)
        self.scan_id = scan(self.conn, self.cfg)
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, paths):
        self.calls.append(list(paths))
        answers = {
            str(self.logo): look(text="memobrick", alpha=True, colors=10),
            str(self.people): look(faces=1),
            str(self.plain): look(labels=[["sky", "0.9"]]),
            str(self.named): look(faces=3),
        }
        for p in paths:
            yield json.dumps({"path": p, **answers.get(p, look())}) + "\n"
        yield "not json\n"

    def test_candidates_cache_and_plan(self):
        rows = image_candidates(self.conn, self.scan_id, self.cfg)
        self.assertEqual(sorted(Path(r["path"]).name for r in rows),
                         ["IMG_2000.jpg", "IMG_2001.jpg", "final.png", "memobrick logo dark.png"])
        looks = vision.analyze_images(self.conn, None, rows, lambda m: None, runner=self.runner)
        self.assertEqual(len(looks), 4)
        again = vision.analyze_images(self.conn, None, rows, lambda m: None, runner=self.runner)
        self.assertEqual(len(self.calls), 1, "cache not used on the second run")
        self.assertEqual(again[str(self.people)]["faces"], 1)

        plan = build_sort_plan(self.conn, self.scan_id, self.cfg, 0, looks)
        got = {Path(i["path"]).name: i["section"] for i in plan["items"]}
        biz = "01 MEMOBRICK BUSINESS/"
        self.assertEqual(got["final.png"], biz + "A Brand & Identity/Logos & Graphics")
        self.assertEqual(got["IMG_2000.jpg"], biz + "F Creative Assets/Photos with People")
        self.assertTrue(got["IMG_2001.jpg"].startswith(biz + "F Creative Assets/Photos/"))
        # A clear name rule wins over what the picture shows.
        self.assertEqual(got["memobrick logo dark.png"], biz + "A Brand & Identity/Logos & Graphics")
        self.assertEqual(plan["images_analyzed"], 4)
        self.assertEqual(plan["image_types"]["Logos & Graphics"], 1)


if __name__ == "__main__":
    unittest.main()
