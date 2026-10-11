import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from organizer import cli, tags, trash
from tests.test_audit import write


class FakeXattr:
    def __init__(self):
        self.store = {}

    def get(self, path):
        return self.store.get(path)

    def set(self, path, data):
        self.store[path] = data

    def remove(self, path):
        self.store.pop(path, None)


class TagsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "01 MEMOBRICK BUSINESS"
        self.imgs = [
            write(self.root / "A Brand & Identity/Logos & Graphics/logo.png", b"1"),
            write(self.root / "F Creative Assets/Photos/2026/IMG_1.HEIC", b"2"),
            write(self.root / "F Creative Assets/Screenshot 2026-10-01 at 1.png", b"3"),
        ]
        self.not_imgs = [
            write(self.root / "I Finance & Legal/invoice.pdf", b"4"),
            write(self.root / "C Website & Technology/Editor & Studio Builds/editor/package.json", b"{}"),
            write(self.root / "C Website & Technology/Editor & Studio Builds/editor/assets/icon.png", b"5"),
            write(self.root / "F Creative Assets/.hidden.png", b"6"),
        ]
        self.xa = FakeXattr()

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_keeps_existing_tags_and_remove(self):
        p = str(self.imgs[0])
        self.xa.set(p, plistlib.dumps(["Blue\n4", "monalisa"], fmt=plistlib.FMT_BINARY))
        self.assertTrue(tags.add_tag(p, "Red", self.xa))
        self.assertFalse(tags.add_tag(p, "Red", self.xa))
        self.assertEqual(tags.read_tags(p, self.xa), ["Blue\n4", "monalisa", "Red\n6"])
        self.assertTrue(tags.remove_tag(p, "Red", self.xa))
        self.assertEqual(tags.read_tags(p, self.xa), ["Blue\n4", "monalisa"])
        q = str(self.imgs[1])
        tags.add_tag(q, "Red", self.xa)
        tags.remove_tag(q, "Red", self.xa)
        self.assertNotIn(q, self.xa.store, "empty tag list should remove the attribute")

    def test_finds_only_memobrick_images(self):
        found = sorted(tags.images_under(self.root))
        self.assertEqual(found, sorted(str(p) for p in self.imgs))

    def test_apply_and_undo(self):
        tags.add_tag(str(self.imgs[0]), "Red", self.xa)  # already red: left as is
        found = tags.untagged_images(self.root, "Red", self.xa)
        self.assertEqual(len(found["already"]), 1)
        journal = Path(self.tmp.name) / "j.jsonl"
        counts = tags.apply_tags(found["todo"], "Red", journal, self.xa, say=lambda m: None)
        self.assertEqual(counts, {"tagged": 2, "failed": 0})
        for p in self.imgs:
            self.assertTrue(tags.has_tag(tags.read_tags(str(p), self.xa), "Red"))
        entries = trash.read_journal(journal)
        tags.undo_tags(entries, self.xa, say=lambda m: None)
        self.assertTrue(tags.has_tag(tags.read_tags(str(self.imgs[0]), self.xa), "Red"),
                        "undo removed a tag it didn't add")
        self.assertFalse(tags.read_tags(str(self.imgs[1]), self.xa))

    def test_cli_tag_needs_confirmation(self):
        cfg = Path(self.tmp.name) / "config.json"
        cfg.write_text(json.dumps({"approved_folders": [str(self.root)],
                                   "business_root": str(self.root),
                                   "state_dir": str(Path(self.tmp.name) / "state")}))
        args = cli.build_parser().parse_args(["tag", "--config", str(cfg)])
        with mock.patch.object(tags, "backend", return_value=self.xa):
            self.assertEqual(cli.cmd_tag(args, ask=lambda q: "no"), 1)
            self.assertFalse(self.xa.store)
            self.assertEqual(cli.cmd_tag(args, ask=lambda q: "tag"), 0)
            self.assertEqual(len(self.xa.store), 3)
            journal = next((Path(self.tmp.name) / "state/journal").glob("tag-red_*.jsonl"))
            self.assertEqual(cli.main(["undo", str(journal)]), 0)
            self.assertFalse(self.xa.store)

    def test_auto_tag_after_sort(self):
        from organizer.config import Config
        cfg = Config(roots=[], state_dir=Path(self.tmp.name) / "state")
        cfg.business_root = self.root
        journal = Path(self.tmp.name) / "s.jsonl"
        journal.write_text("\n".join(json.dumps(e) for e in [
            {"status": "moved", "path": "/x/a.png", "dest": str(self.imgs[0])},
            {"status": "moved", "path": "/x/b.pdf", "dest": str(self.not_imgs[0])},
            {"status": "moved", "path": "/x/shoot", "dest": str(self.root / "F Creative Assets/Photos")},
            {"status": "moved", "path": "/x/c.png", "dest": "/elsewhere/c.png"},
        ]) + "\n")
        with mock.patch.object(tags, "backend", return_value=self.xa):
            n = cli._auto_tag(cfg, journal)
        self.assertEqual(n, 2)  # logo.png + IMG_1.HEIC inside the moved folder
        self.assertNotIn(str(self.not_imgs[0]), self.xa.store)

    def test_backend_only_on_mac(self):
        if tags.sys.platform != "darwin":
            self.assertIsNone(tags.backend())


if __name__ == "__main__":
    unittest.main()
