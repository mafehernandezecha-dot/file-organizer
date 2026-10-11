import json
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from organizer import cli, schedule, trash, weekly
from tests.test_audit import snapshot, write
from tests.test_orders_trash import FakeFinder


class WeeklyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        home = base / "home"
        self.home = home
        dl, desk, pics = home / "Downloads", home / "Desktop", home / "Pictures"
        same = os.urandom(5000)
        self.f = {
            "shot": write(desk / "Screenshot 2026-10-12 at 9.00.00.png", b"s"),
            "dup1": write(dl / "flyer.pdf", same),
            "dup2": write(dl / "flyer copy.pdf", same),
            "label": write(dl / "shipping_labels_thermal.pdf", b"l" * 10),
            "dmg": write(dl / "Some App.dmg", b"d" * 20),
            "logo": write(dl / "memobrick logo.png", b"g" * 30),
        }
        pics.mkdir(parents=True)
        (home / "Documents").mkdir()
        self.state = base / "state"
        self.cfg_path = base / "config.json"
        self.cfg_path.write_text(json.dumps({
            "approved_folders": [str(desk), str(dl), str(pics), str(home / "Documents")],
            "intake_folders": [str(dl), str(desk)],
            "state_dir": str(self.state),
            "business_root": str(home / "Documents/01 MEMOBRICK BUSINESS"),
            "screenshots_folder": str(pics / "Screenshots"),
        }))

    def tearDown(self):
        self.tmp.cleanup()

    def test_weekly_prepares_everything_and_changes_nothing(self):
        before = snapshot(self.home)
        self.assertEqual(cli.main(["weekly", "--config", str(self.cfg_path), "--no-vision"]), 0)
        self.assertEqual(before, snapshot(self.home))
        summary = weekly.load_latest(self.state)
        steps = [x["step"] for x in summary["entries"]]
        self.assertEqual(steps, ["duplicates", "cleanup", "screenshots", "labels", "sort"])
        html = Path(summary["report"]).read_text()
        self.assertIn("Nothing was changed", html)
        self.assertIn("python3 -m organizer approve", html)

    def test_approve_all(self):
        cli.main(["weekly", "--config", str(self.cfg_path), "--no-vision"])
        finder = FakeFinder(Path(self.tmp.name) / "Trash")
        real = trash.apply_trash
        args = cli.build_parser().parse_args(["approve", "--config", str(self.cfg_path)])
        patches = [
            mock.patch.object(trash, "is_macos", return_value=True),
            mock.patch.object(trash, "latest_backup_age_hours", return_value=1.0),
            mock.patch.object(trash, "apply_trash",
                              side_effect=lambda p, j, s=(), say=print: real(p, j, s, runner=finder, say=say)),
        ]
        for p in patches:
            p.start()
        try:
            self.assertEqual(cli.cmd_approve(args, ask=lambda q: "no"), 1)
            self.assertTrue(self.f["shot"].exists(), "acted without confirmation")
            self.assertEqual(cli.cmd_approve(args, ask=lambda q: "approve"), 0)
            self.assertEqual(cli.cmd_approve(args, ask=lambda q: "approve"), 0)  # nothing left
        finally:
            for p in patches:
                p.stop()
        self.assertTrue((self.home / "Pictures/Screenshots/Screenshot 2026-10-12 at 9.00.00.png").exists())
        self.assertEqual(self.f["dup1"].exists() + self.f["dup2"].exists(), 1)
        for k in ("label", "dmg", "shot", "logo"):
            self.assertFalse(self.f[k].exists(), k)
        logo = self.home / "Documents/01 MEMOBRICK BUSINESS/A Brand & Identity/Logos & Graphics/memobrick logo.png"
        self.assertTrue(logo.exists())
        self.assertTrue(all(x.get("approved") for x in weekly.load_latest(self.state)["entries"]))

    def test_schedule_files(self):
        agents = Path(self.tmp.name) / "LaunchAgents"
        plist = schedule.install(self.state, Path("/repo dir"), self.cfg_path, 1, 9, 0,
                                 agents_dir=agents, load=False)
        data = plistlib.loads(plist.read_bytes())
        self.assertEqual(data["Label"], schedule.LABEL)
        self.assertEqual(data["StartCalendarInterval"], {"Weekday": 1, "Hour": 9, "Minute": 0})
        self.assertEqual(data["ProgramArguments"][:3], ["/usr/bin/open", "-a", "Terminal"])
        cmd = Path(data["ProgramArguments"][3]).read_text()
        self.assertIn("cd '/repo dir'", cmd)
        self.assertIn("-m organizer weekly", cmd)
        self.assertNotIn("approve", cmd, "the schedule must never approve on its own")
        self.assertEqual(schedule.status(agents)["StartCalendarInterval"]["Weekday"], 1)
        self.assertTrue(schedule.uninstall(agents, load=False))
        self.assertIsNone(schedule.status(agents))
        self.assertFalse(schedule.uninstall(agents, load=False))


if __name__ == "__main__":
    unittest.main()
