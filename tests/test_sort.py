import json
import os
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

from organizer import cli, moves, trash
from organizer.classify import classify_file, peek_text
from organizer.config import Config
from organizer.index import connect
from organizer.scanner import scan
from organizer.sorting import build_sort_plan, render_sort_preview
from tests.test_audit import write


def row(name, kind=None, business=0, mtime=1767225600.0):  # 2026-01-01
    from organizer.kinds import kind_for
    return {"name": name, "kind": kind or kind_for(name), "business": business,
            "mtime": mtime, "ext": os.path.splitext(name)[1].lower()}


def make_pdf(path: Path, text: str, compress=True) -> Path:
    content = f"BT /F1 12 Tf ({text}) Tj ET".encode()
    body = zlib.compress(content) if compress else content
    data = (b"%%PDF-1.4\n1 0 obj << /Length %d >>\nstream\n" % len(body)
            + body + b"\nendstream\nendobj\n%%EOF")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_docx(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f"<w:document><w:p><w:t>{text}</w:t></w:p></w:document>")
    return path


class ClassifyTest(unittest.TestCase):
    def dest(self, name, text="", loose=True, **kw):
        return classify_file(row(name, **kw), text, loose).dest

    def test_name_rules(self):
        cases = {
            "S-6SUKU6NGJR-photo.jpg": "G Customers & Orders/Customer Photos/S-6SUKU6NGJR",
            "orders_export_1.csv": "G Customers & Orders/Order Exports",
            "products_export.csv": "C Website & Technology/Shopify Exports",
            "memobrick-size-zoom-test-latest (2).zip": "C Website & Technology/Editor & Studio Builds",
            "Memobrick_Logo_white.png": "A Brand & Identity/Logos & Graphics",
            "Montserrat-Bold.ttf": "A Brand & Identity/Fonts",
            "Invoice-0042.pdf": "I Finance & Legal/Invoices & Receipts",
            "packing slip 21074.pdf": "H Operations/Shipping",
            "Inventory October.xlsx": "H Operations/Inventory",
            "Alibaba quote baseplates.pdf": "H Operations/Suppliers & Purchasing",
            "Assembly Instructions 30x30.pdf": "B Product Development/Assembly Instructions",
            "frame mockup.png": "B Product Development/Mockups",
            "Brick colors chart.pdf": "B Product Development/Brick Colors & Parts",
            "DALL·E 2026-01-02 mosaic.png": "F Creative Assets/AI-Generated",
            "holiday reel v2.mp4": "E Social Media/Posts & Videos",
            "Black Friday ad 1080x1080.png": "F Creative Assets/Ad Creatives",
            "Christmas campaign plan.docx": "D Marketing/Campaigns",
            "Untitled design (4).png": "F Creative Assets/Designs (Canva)",
            "IMG_1234.HEIC": "F Creative Assets/Photos/2026",
        }
        for name, want in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.dest(name), want)

    def test_review_and_false_friends(self):
        for name in ["Creative Cloud Installer.dmg", "random.zip", "notes.txt",
                     "Dropbox.pdf", "wholesale terms.pdf", "Test results.pdf"]:
            with self.subTest(name=name):
                self.assertEqual(self.dest(name), "")
        self.assertEqual(self.dest("IMG_1234.jpg", loose=False), "")
        # Not a customer ID (no digits): treated as an ordinary loose photo.
        self.assertEqual(self.dest("S-shaped curve.png"), "F Creative Assets/Photos/2026")
        self.assertEqual(self.dest("Screenshot 2026-01-01 at 1.png"), "")

    def test_content_rules(self):
        self.assertEqual(self.dest("Test results.pdf", "let's build your memobrick! step 1"),
                         "B Product Development/Assembly Instructions")
        self.assertEqual(self.dest("scan0001.pdf", "invoice no 5 total due $40 total"),
                         "I Finance & Legal/Invoices & Receipts")
        self.assertEqual(self.dest("Safari.pdf", "usps ground advantage postage paid"),
                         "H Operations/Shipping")

    def test_peek_text_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_pdf(Path(tmp) / "a.pdf", "Let's build your MemoBrick!")
            self.assertIn("let's build your memobrick", peek_text(str(p), ".pdf", p.stat().st_size))
            p = make_pdf(Path(tmp) / "b.pdf", "USPS postage paid", compress=False)
            self.assertIn("usps postage paid", peek_text(str(p), ".pdf", p.stat().st_size))
            p = make_docx(Path(tmp) / "c.docx", "Factura total 100")
            self.assertIn("factura total 100", peek_text(str(p), ".docx", p.stat().st_size))
            bad = write(Path(tmp) / "broken.docx", b"not a zip")
            self.assertEqual(peek_text(str(bad), ".docx", 9), "")


class SortPlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name) / "home"
        self.home = home
        dl, desk = home / "Downloads", home / "Desktop"
        self.files = {
            "customer": write(dl / "S-6SUKU6NGJR-photo.jpg", b"jpg"),
            "logo": write(dl / "memobrick logo.png", b"png"),
            "instructions": make_pdf(dl / "Test results.pdf", "Let's build your MemoBrick!"),
            "invoice": make_docx(dl / "document (3).docx", "Invoice total due 40 total"),
            "photo": write(desk / "IMG_1234.jpg", b"img"),
        }
        self.stay = [
            write(dl / "Installer.dmg", b"dmg"),
            write(dl / "notes.txt", b"hello"),
            write(dl / "bank passwords.pdf", b"secret"),
        ]
        write(dl / "Holiday shoot/a.jpg", b"1")
        write(dl / "Holiday shoot/b.jpg", b"2")
        write(dl / "memobrick-editor/package.json", b"{}")
        write(dl / "memobrick-editor/src/app.js", b"x")
        write(dl / "misc stuff/readme.txt", b"r")
        self.cfg = Config(roots=[dl, desk], state_dir=Path(self.tmp.name) / "state",
                          loose_photos_are_business=True)
        self.cfg.business_root = home / "Documents" / "01 MEMOBRICK BUSINESS"
        (home / "Documents").mkdir()
        self.cfg.intake_folders = [dl, desk]
        self.conn = connect(self.cfg.db_path)
        self.scan_id = scan(self.conn, self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_contents(self):
        plan = build_sort_plan(self.conn, self.scan_id, self.cfg, 0)
        got = {os.path.basename(i["path"]): os.path.relpath(i["dest"], self.cfg.business_root)
               for i in plan["items"]}
        self.assertEqual(got["S-6SUKU6NGJR-photo.jpg"],
                         "G Customers & Orders/Customer Photos/S-6SUKU6NGJR/S-6SUKU6NGJR-photo.jpg")
        self.assertEqual(got["memobrick logo.png"], "A Brand & Identity/Logos & Graphics/memobrick logo.png")
        self.assertEqual(got["Test results.pdf"],
                         "B Product Development/Assembly Instructions/Test results.pdf")
        self.assertEqual(got["document (3).docx"],
                         "I Finance & Legal/Invoices & Receipts/document (3).docx")
        self.assertTrue(got["IMG_1234.jpg"].startswith("F Creative Assets/Photos/"))
        self.assertTrue(got["Holiday shoot"].startswith("F Creative Assets/Photos/"))
        self.assertEqual(got["memobrick-editor"],
                         "C Website & Technology/Editor & Studio Builds/memobrick-editor")
        staying = {os.path.basename(r["path"]): r["reason"] for r in plan["review"]}
        for name in ["Installer.dmg", "notes.txt", "bank passwords.pdf", "misc stuff"]:
            self.assertIn(name, staying)
        self.assertIn("private", staying["bank passwords.pdf"])
        html = render_sort_preview(plan, Path("p.json"))
        self.assertIn("Nothing has happened yet", html)
        self.assertIn("Need your decision", html)

    def test_apply_and_undo_including_folders(self):
        plan = build_sort_plan(self.conn, self.scan_id, self.cfg, 0)
        journal = self.cfg.state_dir / "journal" / "s.jsonl"
        counts = moves.apply_moves(plan, journal, say=lambda m: None)
        self.assertEqual(counts["failed"], 0)
        self.assertEqual(counts["moved"], len(plan["items"]))
        editor = self.cfg.business_root / "C Website & Technology/Editor & Studio Builds/memobrick-editor"
        self.assertTrue((editor / "src/app.js").exists(), "project folder not moved whole")
        for p in self.files.values():
            self.assertFalse(p.exists())
        for p in self.stay:
            self.assertTrue(p.exists())
        moves.undo_moves(journal, say=lambda m: None)
        for p in self.files.values():
            self.assertTrue(p.exists())
        self.assertTrue((self.home / "Downloads/memobrick-editor/src/app.js").exists())

    def test_changed_folder_is_left_alone(self):
        plan = build_sort_plan(self.conn, self.scan_id, self.cfg, 0)
        shoot = self.home / "Downloads/Holiday shoot"
        write(shoot / "c.jpg", b"new")
        os.utime(shoot, (0, 0))  # folder mtime now differs from the plan
        counts = moves.apply_moves(plan, self.cfg.state_dir / "journal" / "x.jsonl",
                                   say=lambda m: None)
        self.assertGreaterEqual(counts["skipped"], 1)
        self.assertTrue((shoot / "c.jpg").exists())

    def test_cli_sort_end_to_end(self):
        cfg_path = Path(self.tmp.name) / "config.json"
        cfg_path.write_text(json.dumps({
            "approved_folders": [str(p) for p in self.cfg.roots],
            "intake_folders": [str(p) for p in self.cfg.intake_folders],
            "state_dir": str(self.cfg.state_dir),
            "business_root": str(self.cfg.business_root),
            "loose_photos_are_business": True,
        }))
        self.assertEqual(cli.main(["audit", "--config", str(cfg_path), "--no-hash"]), 0)
        self.assertEqual(cli.main(["sort", "--config", str(cfg_path)]), 0)
        plan_path = next((self.cfg.state_dir / "plans").glob("sort-memobrick_*.json"))
        args = cli.build_parser().parse_args(["apply", str(plan_path), "--config", str(cfg_path)])
        with mock.patch.object(trash, "latest_backup_age_hours", return_value=1.0):
            self.assertEqual(cli.cmd_apply(args, ask=lambda q: "move"), 0)
        self.assertTrue((self.cfg.business_root / "A Brand & Identity/Logos & Graphics/memobrick logo.png").exists())


if __name__ == "__main__":
    unittest.main()
