import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from organizer import cli, trash
from organizer.config import Config
from organizer.index import connect
from organizer.plans import (
    build_trash_plan, find_order_files, load_plan, parse_skip, render_trash_preview, save_plan,
)
from organizer.scanner import scan
from tests.test_audit import write


class FakeFinder:
    """Stands in for osascript/Finder: moves files into a fake Trash folder."""

    def __init__(self, trash_dir: Path, fail_names=()):
        self.trash = trash_dir
        self.trash.mkdir(parents=True, exist_ok=True)
        self.fail_names = set(fail_names)
        self.calls = 0

    def __call__(self, script, args):
        self.calls += 1
        out = []
        if script is trash.TRASH_SCRIPT:
            for p in args:
                name = os.path.basename(p)
                if name in self.fail_names:
                    out.append(f"ERR\t{p}\tlocked")
                    continue
                dest = self.trash / name
                n = 2
                while dest.exists():  # Finder renames clashes in the Trash
                    stem, ext = os.path.splitext(name)
                    dest = self.trash / f"{stem} {n}{ext}"
                    n += 1
                shutil.move(p, dest)
                out.append(f"OK\t{p}\t{dest.name}")
        else:
            for nm, folder in zip(args[::2], args[1::2]):
                shutil.move(str(self.trash / nm), os.path.join(folder, nm))
                out.append(f"OK\t{nm}")
        return "\n".join(out)


class OrdersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        d = self.home / "Downloads"
        self.orders = [
            write(d / "21074 Riley Sherman 16x16.pdf", b"a" * 100),
            write(d / "21074 Riley Sherman proof.png", b"b" * 50),
            write(d / "#21100_Jane Doe.pdf", b"c" * 30),
            write(self.home / "Desktop/21099 - Corporate order.pdf", b"d" * 20),
        ]
        self.keep = [
            write(d / "2024 Taxes.pdf", b"t"),          # year, not an order
            write(d / "IMG_21074.jpg", b"i"),
            write(d / "21074.pdf", b"n"),                # no separator after number
            write(d / "12345678 invoice.pdf", b"x"),     # 8 digits
            write(d / "21074 passwords.txt", b"p"),      # sensitive: never touched
        ]
        self.cfg = Config(roots=[d, self.home / "Desktop"], state_dir=base / "state")
        self.conn = connect(self.cfg.db_path)
        self.scan_id = scan(self.conn, self.cfg)
        self.finder = FakeFinder(base / "Trash")

    def tearDown(self):
        self.tmp.cleanup()

    def make_plan(self):
        rows = find_order_files(self.conn, self.scan_id)
        plan = build_trash_plan(rows, "customer order files", 0)
        path = save_plan(plan, self.cfg.state_dir / "plans", "trash-orders")
        return load_plan(path), path

    def test_detects_only_order_files(self):
        names = sorted(r["name"] for r in find_order_files(self.conn, self.scan_id))
        self.assertEqual(names, sorted(p.name for p in self.orders))

    def test_plan_and_preview_change_nothing(self):
        plan, path = self.make_plan()
        html = render_trash_preview(plan, path, "order files", "Order", lambda p: "21074")
        self.assertIn("Nothing has happened yet", html)
        self.assertIn("21074", html)
        for p in self.orders + self.keep:
            self.assertTrue(p.exists())

    def test_apply_skip_changed_and_undo(self):
        plan, _ = self.make_plan()
        by_name = {os.path.basename(i["path"]): i["n"] for i in plan["items"]}
        skip = {by_name["#21100_Jane Doe.pdf"]}
        self.orders[3].write_bytes(b"changed since scan!!!")  # different size now
        journal = self.cfg.state_dir / "journal" / "run.jsonl"
        counts = trash.apply_trash(plan, journal, skip, runner=self.finder, say=lambda m: None)
        self.assertEqual(counts, {"trashed": 2, "skipped": 2, "failed": 0})
        self.assertFalse(self.orders[0].exists())
        self.assertFalse(self.orders[1].exists())
        self.assertTrue(self.orders[2].exists(), "skipped file was trashed")
        self.assertTrue(self.orders[3].exists(), "changed file was trashed")
        for p in self.keep:
            self.assertTrue(p.exists())
        self.assertEqual(journal.stat().st_mode & 0o777, 0o600)

        counts = trash.undo_trash(journal, runner=self.finder, say=lambda m: None)
        self.assertEqual(counts, {"restored": 2, "failed": 0})
        self.assertEqual(self.orders[0].read_bytes(), b"a" * 100)
        self.assertTrue(self.orders[1].exists())
        # A second undo is a no-op, not an error.
        self.assertEqual(trash.undo_trash(journal, runner=self.finder, say=lambda m: None),
                         {"restored": 0, "failed": 0})

    def test_failures_are_journaled_and_undo_restores_the_rest(self):
        plan, _ = self.make_plan()
        finder = FakeFinder(Path(self.tmp.name) / "Trash2", fail_names={"#21100_Jane Doe.pdf"})
        journal = self.cfg.state_dir / "journal" / "run2.jsonl"
        counts = trash.apply_trash(plan, journal, runner=finder, say=lambda m: None)
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(counts["trashed"], 3)
        statuses = [json.loads(l)["status"] for l in journal.read_text().splitlines()]
        self.assertEqual(statuses.count("failed"), 1)
        trash.undo_trash(journal, runner=finder, say=lambda m: None)
        for p in self.orders:
            self.assertTrue(p.exists())

    def test_undo_never_overwrites(self):
        plan, _ = self.make_plan()
        journal = self.cfg.state_dir / "journal" / "run3.jsonl"
        trash.apply_trash(plan, journal, runner=self.finder, say=lambda m: None)
        self.orders[0].write_bytes(b"new file in the same place")
        counts = trash.undo_trash(journal, runner=self.finder, say=lambda m: None)
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(self.orders[0].read_bytes(), b"new file in the same place")

    def test_cli_apply_needs_backup_and_typed_confirmation(self):
        _, path = self.make_plan()
        cfg_path = Path(self.tmp.name) / "config.json"
        cfg_path.write_text(json.dumps({"approved_folders": [str(r) for r in self.cfg.roots],
                                        "state_dir": str(self.cfg.state_dir)}))
        args = cli.build_parser().parse_args(["apply", str(path), "--config", str(cfg_path)])
        with mock.patch.object(trash, "is_macos", return_value=True), \
             mock.patch.object(trash, "latest_backup_age_hours", return_value=None), \
             mock.patch.object(trash, "apply_trash") as do_it:
            self.assertEqual(cli.cmd_apply(args, ask=lambda q: "no"), 1)
            answers = iter(["yes", "trash"])  # wrong case: must be exactly TRASH
            self.assertEqual(cli.cmd_apply(args, ask=lambda q: next(answers)), 1)
            do_it.assert_not_called()
        with mock.patch.object(trash, "is_macos", return_value=True), \
             mock.patch.object(trash, "latest_backup_age_hours", return_value=3.0), \
             mock.patch.object(trash, "apply_trash",
                               return_value={"trashed": 4, "skipped": 0, "failed": 0}) as do_it:
            self.assertEqual(cli.cmd_apply(args, ask=lambda q: "TRASH"), 0)
            do_it.assert_called_once()

    def test_cli_orders_writes_preview_only(self):
        cfg_path = Path(self.tmp.name) / "config.json"
        cfg_path.write_text(json.dumps({"approved_folders": [str(r) for r in self.cfg.roots],
                                        "state_dir": str(self.cfg.state_dir)}))
        self.assertEqual(cli.main(["orders", "--config", str(cfg_path)]), 0)
        previews = list((self.cfg.state_dir / "reports").glob("preview_*.html"))
        self.assertEqual(len(previews), 1)
        for p in self.orders:
            self.assertTrue(p.exists())

    def test_parse_skip(self):
        self.assertEqual(parse_skip("3, 17,20-22"), ({3, 17, 20, 21, 22}, []))
        self.assertEqual(parse_skip("x,5"), ({5}, ["x"]))
        self.assertEqual(parse_skip(None), (set(), []))


if __name__ == "__main__":
    unittest.main()


class LabelsTest(unittest.TestCase):
    def test_label_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "Downloads"
            labels = ["shipping_labels_thermal.pdf", "Shipping Label 21074.pdf", "USPS_label.pdf",
                      "fedex-ground.pdf", "UPS label 3.png", "label_ups.pdf", "Postage receipt.pdf",
                      "PirateShip-labels.pdf", "order.zpl", "shippo_2026-10-01.pdf"]
            others = ["backups.pdf", "groups.pdf", "Label design.ai", "labels sheet.docx",
                      "Test results.pdf", "S-6SUKU6NGJR-photo.jpg", "ups and downs.pdf"]
            for n in labels + others:
                write(d / n, b"x")
            cfg = Config(roots=[d], state_dir=Path(tmp) / "state")
            conn = connect(cfg.db_path)
            scan_id = scan(conn, cfg)
            from organizer.plans import find_label_files
            got = {r["name"] for r in find_label_files(conn, scan_id)}
            # order.zpl has no label word in its name, so name-only matching skips it.
            self.assertEqual(got, set(labels) - {"order.zpl"})

    def test_cli_labels_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "Downloads"
            f = write(d / "shipping_labels_thermal.pdf", b"x" * 10)
            state = Path(tmp) / "state"
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps({"approved_folders": [str(d)], "state_dir": str(state)}))
            self.assertEqual(cli.main(["audit", "--config", str(cfg_path), "--no-hash"]), 0)
            self.assertEqual(cli.main(["labels", "--config", str(cfg_path)]), 0)
            preview = next((state / "reports").glob("preview_trash-labels_*.html")).read_text()
            self.assertIn("Preview: move shipping labels to Trash", preview)
            self.assertIn("shipping_labels_thermal.pdf", preview)
            self.assertTrue(f.exists())
            plan = load_plan(next((state / "plans").glob("trash-labels_*.json")))
            self.assertEqual(plan["reason"], "shipping labels")
