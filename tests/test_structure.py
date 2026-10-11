import json
import tempfile
import unittest
from pathlib import Path

from organizer import cli, structure
from tests.test_audit import write


class StructureTest(unittest.TestCase):
    def test_create_and_undo(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp) / "Documents"
            biz = docs / "01 MEMOBRICK BUSINESS"
            keep = write(biz / "F Creative Assets/Videos/a.mp4", b"v")
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps({"approved_folders": [str(docs)],
                                            "business_root": str(biz),
                                            "state_dir": str(Path(tmp) / "state")}))
            args = cli.build_parser().parse_args(["folders", "--config", str(cfg_path)])
            self.assertEqual(cli.cmd_folders(args, ask=lambda q: "no"), 1)
            self.assertFalse((biz / "D Marketing").exists())
            self.assertEqual(cli.cmd_folders(args, ask=lambda q: "create"), 0)
            for sub in ["Facebook Ads", "Instagram Ads", "TikTok Ads", "Google Ads",
                        "Email Marketing", "Campaigns", "Strategy & Reports"]:
                self.assertTrue((biz / "D Marketing" / sub).is_dir(), sub)
            self.assertTrue((docs / "02 PERSONAL DOCUMENTS/Medical Records").is_dir())
            self.assertTrue((docs / "05 INTAKE/Needs Manual Review").is_dir())
            self.assertEqual(cli.cmd_folders(args, ask=lambda q: "create"), 0)  # nothing missing
            write(biz / "D Marketing/Campaigns/plan.docx", b"p")   # used after creation
            journal = next((Path(tmp) / "state/journal").glob("folders_*.jsonl"))
            self.assertEqual(cli.main(["undo", str(journal)]), 1)  # one folder kept (not empty)
            self.assertTrue((biz / "D Marketing/Campaigns/plan.docx").exists())
            self.assertFalse((biz / "D Marketing/TikTok Ads").exists())
            self.assertFalse((docs / "02 PERSONAL DOCUMENTS").exists())
            self.assertTrue(keep.exists())

    def test_every_sort_destination_is_in_the_plan(self):
        """Folders the sorter uses should all exist in the structure."""
        import re
        from organizer import classify
        src = Path(classify.__file__).read_text()
        used = set(re.findall(r'f"\{([A-I])\}/([^"/{]+)', src))
        letters = {k[0]: k for k in structure.BUSINESS}
        for letter, sub in used:
            self.assertIn(sub, structure.BUSINESS[letters[letter]], f"{letter}/{sub}")


if __name__ == "__main__":
    unittest.main()
