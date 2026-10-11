"""Gather Memobrick videos that are already filed elsewhere in the business
folder into the one Videos folder (including the old Videos/<year> folders). Walks the folder directly (so it's right
even if the index is older than the last moves); writes only the plan.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Dict, List

from organizer.classify import VIDEOS
from organizer.config import PACKAGE_EXTENSIONS, Config, is_sensitive_name
from organizer.kinds import kind_for
from organizer.moves import unique_destinations
from organizer.scanner import _is_project, is_dataless


def videos_elsewhere(cfg: Config) -> List[str]:
    root = cfg.business_root
    target = str(root / VIDEOS)
    out, stack = [], [str(root)]
    while stack:
        cur = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError:
            continue
        for e in entries:
            if e.name.startswith(".") or e.is_symlink():
                continue
            if e.is_dir(follow_symlinks=False):
                if cur == target and not re.fullmatch(r"\d{4}", e.name):
                    continue  # your own subfolders inside Videos stay as they are
                if Path(e.name).suffix.lower() in PACKAGE_EXTENSIONS or _is_project(e.path):
                    continue
                stack.append(e.path)
            elif e.is_file(follow_symlinks=False) and kind_for(e.name) == "video" \
                    and not is_sensitive_name(e.name) and cur != target:
                out.append(e.path)
    return sorted(out)


def build_videos_plan(cfg: Config) -> Dict:
    dest_root = cfg.business_root / VIDEOS
    paths = videos_elsewhere(cfg)
    pairs = unique_destinations([(p, str(dest_root / os.path.basename(p))) for p in paths])
    items = []
    for i, (src, dest) in enumerate(pairs, 1):
        st = os.lstat(src)
        items.append({"n": i, "path": src, "dest": dest, "size": st.st_size,
                      "mtime": st.st_mtime, "dataless": is_dataless(st), "unit": "file"})
    now = time.time()
    return {"version": 1, "action": "move", "reason": "Memobrick videos",
            "dest_root": str(dest_root), "created_at": now, "scan_finished_at": now,
            "items": items}
