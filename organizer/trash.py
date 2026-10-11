"""Execute an approved trash plan, and undo it.

Safety:
  * Runs only from a saved plan the user reviewed; items can be skipped.
  * Requires a recent Time Machine backup (or the user's typed confirmation
    when macOS won't report it) and a typed confirmation to proceed.
  * Re-checks every file just before acting: if it moved, changed size, or
    was modified since the scan, it is left alone.
  * Uses Finder to move files to the Trash — never permanent deletion — so
    Finder's "Put Back" works and no Full Disk Access is needed.
  * Writes a journal line per file *as it goes*, so an interrupted run is
    still fully undoable.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

BACKUP_MAX_AGE_HOURS = 24
CHUNK = 50

# Each argv entry is a POSIX path. Prints one line per path:
# OK<TAB>path<TAB>name-in-trash   or   ERR<TAB>path<TAB>message
TRASH_SCRIPT = r'''
on run argv
  set out to {}
  repeat with a in argv
    set pth to contents of a
    try
      tell application "Finder"
        set t to delete (POSIX file pth as alias)
        set nm to name of t
      end tell
      set end of out to "OK" & tab & pth & tab & nm
    on error e
      set end of out to "ERR" & tab & pth & tab & e
    end try
  end repeat
  set AppleScript's text item delimiters to linefeed
  return out as text
end run
'''

# argv: pairs of (name-in-trash, destination folder).
RESTORE_SCRIPT = r'''
on run argv
  set out to {}
  repeat with i from 1 to (count of argv) by 2
    set nm to item i of argv
    set dest to item (i + 1) of argv
    try
      tell application "Finder"
        move (item nm of trash) to (POSIX file dest as alias)
      end tell
      set end of out to "OK" & tab & nm
    on error e
      set end of out to "ERR" & tab & nm & tab & e
    end try
  end repeat
  set AppleScript's text item delimiters to linefeed
  return out as text
end run
'''

Runner = Callable[[str, List[str]], str]


def osascript(script: str, args: List[str]) -> str:
    res = subprocess.run(
        ["osascript", "-", *args], input=script, capture_output=True, text=True, check=False
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or "osascript failed")
    return res.stdout.strip()


def latest_backup_age_hours(now: Optional[float] = None) -> Optional[float]:
    """Hours since the latest Time Machine backup, or None if macOS won't say."""
    try:
        res = subprocess.run(["tmutil", "latestbackup"], capture_output=True, text=True,
                             timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})", res.stdout)
    if res.returncode != 0 or not m:
        return None
    ts = dt.datetime(*map(int, m.groups())).timestamp()
    return ((now or time.time()) - ts) / 3600


def still_matches(item: Dict) -> Tuple[bool, str]:
    try:
        st = os.lstat(item["path"])
    except FileNotFoundError:
        return False, "no longer there"
    except OSError as e:
        return False, f"unreadable: {e.strerror}"
    if not os.path.isfile(item["path"]) or os.path.islink(item["path"]):
        return False, "no longer a regular file"
    if st.st_size != item["size"] or abs(st.st_mtime - item["mtime"]) > 1:
        return False, "changed since the scan"
    return True, ""


def apply_trash(
    plan: Dict,
    journal_path: Path,
    skip: Iterable[int] = (),
    runner: Runner = osascript,
    say: Callable[[str], None] = print,
) -> Dict[str, int]:
    skip = set(skip)
    todo, counts = [], {"trashed": 0, "skipped": 0, "failed": 0}
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(journal_path.parent, 0o700)
    with open(journal_path, "a", encoding="utf-8") as j:
        os.chmod(journal_path, 0o600)

        def log(entry: Dict) -> None:
            entry["at"] = time.time()
            j.write(json.dumps(entry) + "\n")
            j.flush()
            os.fsync(j.fileno())

        for it in plan["items"]:
            if it["n"] in skip:
                counts["skipped"] += 1
                log({"status": "skipped", "path": it["path"], "why": "you chose to keep it"})
                continue
            ok, why = still_matches(it)
            if not ok:
                counts["skipped"] += 1
                log({"status": "skipped", "path": it["path"], "why": why})
                continue
            todo.append(it["path"])

        for i in range(0, len(todo), CHUNK):
            batch = todo[i:i + CHUNK]
            try:
                out = runner(TRASH_SCRIPT, batch)
            except RuntimeError as e:
                for p in batch:
                    counts["failed"] += 1
                    log({"status": "failed", "path": p, "why": str(e)})
                say(f"! Finder refused: {e}")
                break
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) == 3 and parts[0] == "OK":
                    counts["trashed"] += 1
                    log({"status": "trashed", "path": parts[1], "trash_name": parts[2]})
                elif len(parts) >= 2:
                    counts["failed"] += 1
                    log({"status": "failed", "path": parts[1], "why": "\t".join(parts[2:])})
            say(f"  … {min(i + CHUNK, len(todo)):,} / {len(todo):,}")
    return counts


def read_journal(path: Path) -> List[Dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def undo_trash(journal_path: Path, runner: Runner = osascript,
               say: Callable[[str], None] = print) -> Dict[str, int]:
    log = read_journal(journal_path)
    done = {e["path"] for e in log if e["status"] == "restored"}
    entries = [e for e in log if e["status"] == "trashed" and e["path"] not in done]
    counts = {"restored": 0, "failed": 0}
    pairs: List[Tuple[Dict, str, str]] = []
    for e in entries:
        dest = os.path.dirname(e["path"])
        if os.path.lexists(e["path"]):
            counts["failed"] += 1
            say(f"! Not restoring (a file is already there): {e['path']}")
            continue
        os.makedirs(dest, exist_ok=True)
        pairs.append((e, e["trash_name"], dest))
    restored = set()
    for i in range(0, len(pairs), CHUNK):
        batch = pairs[i:i + CHUNK]
        args = [x for _, nm, dest in batch for x in (nm, dest)]
        out = runner(RESTORE_SCRIPT, args)
        for line in out.splitlines():
            parts = line.split("\t")
            if parts[0] == "OK":
                restored.add(parts[1])
            else:
                say(f"! Could not restore {parts[1] if len(parts) > 1 else '?'}: "
                    f"{parts[2] if len(parts) > 2 else ''}")
    with open(journal_path, "a", encoding="utf-8") as j:
        for e, nm, _ in pairs:
            if nm in restored:
                counts["restored"] += 1
                j.write(json.dumps({"status": "restored", "path": e["path"],
                                    "at": time.time()}) + "\n")
            else:
                counts["failed"] += 1
    return counts


def summarize(plan: Dict, skip: Iterable[int]) -> Tuple[int, int]:
    skip = set(skip)
    chosen = [i for i in plan["items"] if i["n"] not in skip]
    return len(chosen), sum(i["size"] for i in chosen)


def is_macos() -> bool:
    return sys.platform == "darwin"

