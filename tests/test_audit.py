import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from organizer.analysis import build_audit
from organizer.cli import main
from organizer.config import Config
from organizer.duplicates import EDGE, find_duplicates
from organizer.index import connect
from organizer.kinds import base_name, kind_for
from organizer.scanner import scan


def write(path: Path, data: bytes = b"x", age_days: float = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if age_days:
        t = time.time() - age_days * 86400
        os.utime(path, (t, t))
    return path


def snapshot(root: Path):
    """Every path with its bytes hash, size, and mtime — to prove nothing changed."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            p = Path(dirpath, name)
            st = p.lstat()
            digest = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else None
            out[str(p)] = (st.st_size, st.st_mtime, digest)
    return out


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.state = base / "state"
        d = self.home
        big = os.urandom(3 * EDGE)
        write(d / "Downloads/Memobrick_logo.png", big)
        write(d / "Desktop/Memobrick_logo copy.png", big)          # exact duplicate
        write(d / "Desktop/same_size_different.png", os.urandom(3 * EDGE))
        write(d / "Documents/Proposal_FINAL.docx", b"a" * 10)
        write(d / "Documents/Proposal v2.docx", b"b" * 11)
        write(d / "Documents/old_tax.pdf", b"old", age_days=800)
        write(d / "Downloads/Installer.dmg", b"dmg" * 100)
        write(d / "Documents/passwords.txt", b"secret" * 50)       # sensitive: never read
        write(d / "Documents/passwords copy.txt", b"secret" * 50)
        write(d / "Documents/.hidden_file", b"h")
        write(d / "Documents/Library/thing.txt", b"lib")
        write(d / "Documents/Shop Theme/package.json", b"{}")       # software project
        write(d / "Documents/Shop Theme/src/app.js", b"x" * 100)
        write(d / "Pictures/Photos Library.photoslibrary/database/Photos.sqlite", b"db" * 100)
        write(d / "Documents/secrets/key.txt", b"k")
        os.symlink(d / "Documents", d / "Desktop/Docs alias")
        self.cfg = Config(roots=[d / "Desktop", d / "Downloads", d / "Documents", d / "Pictures"],
                          state_dir=self.state)

    def tearDown(self):
        self.tmp.cleanup()

    def run_audit(self):
        conn = connect(self.cfg.db_path)
        scan_id = scan(conn, self.cfg)
        find_duplicates(conn, scan_id)
        return conn, scan_id, build_audit(conn, scan_id, self.cfg)

    def test_audit_never_modifies_user_files(self):
        before = snapshot(self.home)
        self.run_audit()
        self.assertEqual(before, snapshot(self.home))

    def test_exact_duplicates_found_and_same_size_unique_ignored(self):
        _, _, a = self.run_audit()
        self.assertEqual(len(a.duplicate_groups), 1)
        names = sorted(r["name"] for r in a.duplicate_groups[0])
        self.assertEqual(names, ["Memobrick_logo copy.png", "Memobrick_logo.png"])
        self.assertEqual(a.duplicate_reclaimable, 3 * EDGE)

    def test_sensitive_files_not_hashed(self):
        conn, scan_id, _ = self.run_audit()
        rows = conn.execute(
            "SELECT quick_hash, full_hash, sensitive FROM files WHERE name LIKE 'passwords%'"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r["sensitive"], 1)
            self.assertIsNone(r["quick_hash"])
            self.assertIsNone(r["full_hash"])

    def test_exclusions_packages_projects_symlinks(self):
        conn, scan_id, a = self.run_audit()
        paths = {r["path"]: r for r in conn.execute("SELECT * FROM files WHERE scan_id=?", (scan_id,))}
        self.assertFalse(any(".hidden_file" in p for p in paths))
        self.assertFalse(any("/Library/" in p for p in paths))
        self.assertFalse(any("/secrets/" in p for p in paths))
        self.assertFalse(any(p.endswith("app.js") for p in paths), "project internals indexed")
        self.assertFalse(any(p.endswith("Photos.sqlite") for p in paths), "package internals indexed")
        project = [r for r in paths.values() if r["item_type"] == "project"]
        self.assertEqual([r["name"] for r in project], ["Shop Theme"])
        self.assertEqual(project[0]["size"], 102)
        pkg = [r for r in paths.values() if r["item_type"] == "package"]
        self.assertEqual([r["name"] for r in pkg], ["Photos Library.photoslibrary"])
        link = [r for r in paths.values() if r["item_type"] == "symlink"]
        self.assertEqual(len(link), 1)
        self.assertEqual(a.skipped["excluded folder"], 1)

    def test_versions_stale_business_observations(self):
        _, _, a = self.run_audit()
        fams = [sorted(r["name"] for r in f) for f in a.version_families]
        self.assertIn(["Proposal v2.docx", "Proposal_FINAL.docx"], fams)
        self.assertEqual([r["name"] for r in a.stale_examples], ["old_tax.pdf"])
        self.assertEqual(a.business_total, 2)
        text = " ".join(a.observations)
        self.assertIn("installer", text)
        self.assertIn("Memobrick", text)

    def test_cli_writes_private_reports(self):
        cfg_path = Path(self.tmp.name) / "config.json"
        cfg_path.write_text(json.dumps({
            "approved_folders": [str(r) for r in self.cfg.roots] + [str(self.home / "Missing")],
            "state_dir": str(self.state),
        }))
        self.assertEqual(main(["audit", "--config", str(cfg_path)]), 0)
        reports = list((self.state / "reports").iterdir())
        self.assertEqual(sorted(p.suffix for p in reports), [".html", ".md"])
        for p in reports:
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        html = next(p for p in reports if p.suffix == ".html").read_text()
        self.assertIn("Nothing was moved", html)
        self.assertEqual(main(["report", "--config", str(cfg_path)]), 0)

    def test_no_approved_folders_is_an_error(self):
        cfg_path = Path(self.tmp.name) / "config.json"
        cfg_path.write_text(json.dumps({"approved_folders": []}))
        self.assertEqual(main(["audit", "--config", str(cfg_path)]), 2)


class LoosePhotosTest(unittest.TestCase):
    def test_loose_photos_count_as_business_but_screenshots_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "Downloads"
            write(d / "IMG_1234.jpg", b"p")
            write(d / "Screenshot 2026-10-01 at 10.00.png", b"s")
            write(d / "notes.pdf", b"n")
            for flag, expected in ((False, set()), (True, {"IMG_1234.jpg"})):
                cfg = Config(roots=[d], state_dir=Path(tmp) / f"state{flag}",
                             loose_photos_are_business=flag)
                conn = connect(cfg.db_path)
                scan_id = scan(conn, cfg)
                got = {r["name"] for r in conn.execute(
                    "SELECT name FROM files WHERE scan_id=? AND business=1", (scan_id,))}
                self.assertEqual(got, expected)


class KindsTest(unittest.TestCase):
    def test_base_name_groups_versions_not_years(self):
        self.assertEqual(base_name("Proposal_FINAL2.pdf"), base_name("Proposal v3 (1).pdf"))
        self.assertEqual(base_name("Logo copy 2.png"), "logo.png")
        self.assertNotEqual(base_name("Report_2024.xlsx"), base_name("Report_2025.xlsx"))
        self.assertEqual(base_name("Proposal_newsletter.pdf"), "proposal newsletter.pdf")

    def test_kind_for(self):
        self.assertEqual(kind_for("IMG_0001.HEIC"), "image")
        self.assertEqual(kind_for("reel.MOV"), "video")
        self.assertEqual(kind_for("mystery"), "other")


if __name__ == "__main__":
    unittest.main()
