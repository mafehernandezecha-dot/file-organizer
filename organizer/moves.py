"""Execute an approved move plan, and undo it.

Safety:
  * Same-volume renames only: a rename never copies data, keeps every date,
    tag, and extended attribute, and can't leave a half-written file. Moves
    that would cross drives are refused.
  * Never overwrites: if the destination name is taken, the file is left alone.
  * iCloud-only placeholders are skipped (moving them would force a download).
  * Each file is re-checked against the scan before moving, verified after,
    and journaled immediately, so interrupted runs are fully undoable.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List

from organizer.scanner import is_dataless
from organizer.trash import read_journal, still_matches


def unique_destinations(pairs: List[tuple]) -> List[tuple]:
    """Give each (src, dest) a dest that is unused on disk and within the plan.

    'Screenshot.png' becomes 'Screenshot (2).png' if needed. Final collision
    checks still happen at apply time.
    """
    taken = set()
    out = []
    for src, dest in pairs:
        stem, ext = os.path.splitext(dest)
        candidate, n = dest, 2
        while candidate in taken or os.path.lexists(candidate):
            candidate = f"{stem} ({n}){ext}"
            n += 1
        taken.add(candidate)
        out.append((src, candidate))
    return out


def _nearest_existing(path: str) -> str:
    p = os.path.dirname(path)
    while p and not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p


def apply_moves(
    plan: Dict, journal_path: Path, skip: Iterable[int] = (),
    say: Callable[[str], None] = print,
) -> Dict[str, int]:
    skip = set(skip)
    counts = {"moved": 0, "skipped": 0, "failed": 0}
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(journal_path.parent, 0o700)
    with open(journal_path, "a", encoding="utf-8") as j:
        os.chmod(journal_path, 0o600)

        def log(entry: Dict) -> None:
            entry["at"] = time.time()
            j.write(json.dumps(entry) + "\n")
            j.flush()
            os.fsync(j.fileno())

        def skipped(it: Dict, why: str) -> None:
            counts["skipped"] += 1
            log({"status": "skipped", "path": it["path"], "why": why})

        total = len(plan["items"])
        for idx, it in enumerate(plan["items"], 1):
            src, dest = it["path"], it["dest"]
            if it["n"] in skip:
                skipped(it, "you chose to keep it where it is")
                continue
            ok, why = still_matches(it)
            if not ok:
                skipped(it, why)
                continue
            st = os.lstat(src)
            if is_dataless(st):
                skipped(it, "stored only in iCloud (download it first)")
                continue
            if os.path.lexists(dest):
                skipped(it, "a file with that name is already at the destination")
                continue
            anchor = _nearest_existing(dest)
            if os.stat(anchor).st_dev != st.st_dev:
                skipped(it, "destination is on a different drive")
                continue
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if os.path.lexists(dest):  # re-check right before the rename
                    skipped(it, "a file with that name is already at the destination")
                    continue
                os.rename(src, dest)
            except OSError as e:
                counts["failed"] += 1
                log({"status": "failed", "path": src, "why": e.strerror or str(e)})
                continue
            moved_st = os.lstat(dest)
            if moved_st.st_size != st.st_size or moved_st.st_ino != st.st_ino:
                counts["failed"] += 1
                log({"status": "failed", "path": src, "dest": dest,
                     "why": "verification after move did not match"})
                continue
            counts["moved"] += 1
            log({"status": "moved", "path": src, "dest": dest, "size": st.st_size})
            if idx % 100 == 0:
                say(f"  … {idx:,} / {total:,}")
    return counts


def undo_moves(journal_path: Path, say: Callable[[str], None] = print) -> Dict[str, int]:
    log = read_journal(journal_path)
    done = {e["path"] for e in log if e["status"] == "restored"}
    counts = {"restored": 0, "failed": 0}
    with open(journal_path, "a", encoding="utf-8") as j:
        for e in reversed(log):
            if e["status"] != "moved" or e["path"] in done:
                continue
            src, dest = e["path"], e["dest"]
            if os.path.lexists(src):
                counts["failed"] += 1
                say(f"! Not restoring (a file is already there): {src}")
                continue
            if not os.path.lexists(dest):
                counts["failed"] += 1
                say(f"! Can't find it where it was moved to: {dest}")
                continue
            try:
                os.makedirs(os.path.dirname(src), exist_ok=True)
                os.rename(dest, src)
            except OSError as err:
                counts["failed"] += 1
                say(f"! Could not restore {src}: {err.strerror or err}")
                continue
            counts["restored"] += 1
            j.write(json.dumps({"status": "restored", "path": src, "at": time.time()}) + "\n")
            j.flush()
    return counts


def journal_kind(journal_path: Path) -> str:
    statuses = {e["status"] for e in read_journal(journal_path)}
    if "moved" in statuses:
        return "move"
    if "trashed" in statuses:
        return "trash"
    return "empty"
