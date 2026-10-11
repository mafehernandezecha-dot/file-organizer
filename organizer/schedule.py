"""Turn the weekly check on or off with a per-user LaunchAgent.

The agent doesn't run the scan itself: it opens a small .command file in
Terminal, so the scan uses the folder permissions Terminal already has and
you can see it happen. No Full Disk Access, no hidden background process.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

LABEL = "com.memobrick.file-organizer.weekly"
DAYS = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4,
        "friday": 5, "saturday": 6}


def agent_path(agents_dir: Optional[Path] = None) -> Path:
    return (agents_dir or Path.home() / "Library" / "LaunchAgents") / f"{LABEL}.plist"


def write_command_file(state_dir: Path, repo_dir: Path, config: Path) -> Path:
    cmd = state_dir / "weekly.command"
    cmd.parent.mkdir(parents=True, exist_ok=True)
    cmd.write_text(
        "#!/bin/zsh\n"
        "# Weekly tidy-up check (read-only). Opened by the LaunchAgent "
        f"{LABEL}.\n"
        f"cd {shlex.quote(str(repo_dir))} || exit 1\n"
        f"{shlex.quote(sys.executable)} -m organizer weekly --config {shlex.quote(str(config))} --open\n"
        "echo\necho 'Done. You can close this window.'\n",
        encoding="utf-8",
    )
    os.chmod(cmd, 0o700)
    return cmd


def build_agent(command_file: Path, weekday: int, hour: int, minute: int, log: Path) -> Dict:
    return {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/open", "-a", "Terminal", str(command_file)],
        "StartCalendarInterval": {"Weekday": weekday, "Hour": hour, "Minute": minute},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "RunAtLoad": False,
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)


def install(state_dir: Path, repo_dir: Path, config: Path, weekday: int, hour: int, minute: int,
            agents_dir: Optional[Path] = None, load: bool = True) -> Path:
    cmd = write_command_file(state_dir, repo_dir, config)
    plist = agent_path(agents_dir)
    plist.parent.mkdir(parents=True, exist_ok=True)
    if load and plist.exists():
        _launchctl("bootout", f"gui/{os.getuid()}", str(plist))
    with open(plist, "wb") as f:
        plistlib.dump(build_agent(cmd, weekday, hour, minute, state_dir / "weekly.log"), f)
    if load:
        res = _launchctl("bootstrap", f"gui/{os.getuid()}", str(plist))
        if res.returncode != 0:
            _launchctl("load", "-w", str(plist))  # older macOS
    return plist


def uninstall(agents_dir: Optional[Path] = None, load: bool = True) -> bool:
    plist = agent_path(agents_dir)
    if not plist.exists():
        return False
    if load:
        res = _launchctl("bootout", f"gui/{os.getuid()}", str(plist))
        if res.returncode != 0:
            _launchctl("unload", "-w", str(plist))
    plist.unlink()
    return True


def status(agents_dir: Optional[Path] = None) -> Optional[Dict]:
    plist = agent_path(agents_dir)
    if not plist.exists():
        return None
    with open(plist, "rb") as f:
        return plistlib.load(f)
