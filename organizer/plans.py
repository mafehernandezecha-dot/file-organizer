"""Plans: an exact, reviewable list of proposed actions, saved before anything runs.

A plan is a JSON file in <state>/plans/. Nothing in this module touches user
files; it only reads the index and writes the plan plus an HTML preview.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from organizer.analysis import human
from organizer.report import CSS, date, short

# Customer order files start with a 5–6 digit order number followed by a
# separator, e.g. "21074 Riley Sherman 16x16.pdf" or "#21074_proof.png".
# 4-digit numbers are excluded so years ("2024 Taxes.pdf") never match.
DEFAULT_ORDER_PATTERN = r"^#?(\d{5,6})[ _\-]"


def find_order_files(
    conn: sqlite3.Connection, scan_id: int, pattern: str = DEFAULT_ORDER_PATTERN
) -> List[sqlite3.Row]:
    rx = re.compile(pattern)
    rows = conn.execute(
        """SELECT * FROM files WHERE scan_id=? AND item_type='file' AND sensitive=0
           ORDER BY name""",
        (scan_id,),
    ).fetchall()
    return [r for r in rows if rx.match(r["name"])]


def order_number(name: str, pattern: str = DEFAULT_ORDER_PATTERN) -> str:
    m = re.match(pattern, name)
    return m.group(1) if m and m.groups() else ""


def build_trash_plan(rows: List[sqlite3.Row], reason: str, scan_finished: float) -> Dict:
    return {
        "version": 1,
        "action": "trash",
        "reason": reason,
        "created_at": time.time(),
        "scan_finished_at": scan_finished,
        "items": [
            {"n": i, "path": r["path"], "size": r["size"], "mtime": r["mtime"],
             "dataless": bool(r["dataless"])}
            for i, r in enumerate(rows, 1)
        ],
    }


def save_plan(plan: Dict, plans_dir: Path, slug: str) -> Path:
    plans_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(plans_dir, 0o700)
    stamp = dt.datetime.fromtimestamp(plan["created_at"]).strftime("%Y-%m-%d_%H%M%S")
    path = plans_dir / f"{slug}_{stamp}.json"
    path.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def load_plan(path: Path) -> Dict:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if plan.get("version") != 1 or plan.get("action") not in ("trash",):
        raise ValueError(f"not a plan file this version understands: {path}")
    return plan


def render_trash_preview(plan: Dict, plan_path: Path, pattern: str) -> str:
    e = html.escape
    items = plan["items"]
    total = sum(i["size"] for i in items)
    groups: Dict[str, List[Dict]] = defaultdict(list)
    for it in items:
        groups[order_number(os.path.basename(it["path"]), pattern) or "?"].append(it)
    by_folder: Dict[str, int] = defaultdict(int)
    for it in items:
        by_folder[short(os.path.dirname(it["path"]))] += 1

    rows = []
    for order in sorted(groups):
        first = True
        for it in groups[order]:
            rows.append(
                "<tr>"
                f"<td>{it['n']}</td>"
                f"<td>{e(order) if first else ''}</td>"
                f"<td>{e(os.path.basename(it['path']))}</td>"
                f"<td>{e(short(os.path.dirname(it['path'])))}</td>"
                f"<td>{e(human(it['size']))}</td>"
                f"<td>{e(date(it['mtime']))}{' · iCloud only' if it['dataless'] else ''}</td>"
                "</tr>"
            )
            first = False
    where = "".join(f"<li>{n} in {e(f)}</li>" for f, n in
                    sorted(by_folder.items(), key=lambda kv: -kv[1])[:10])
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Preview: Order Files</title><style>{CSS}</style></head><body><main>"
        "<h1>Preview: move order files to Trash</h1>"
        f"<p class=sub>Based on the scan from {e(date(plan['scan_finished_at']))}</p>"
        "<div class=safe><b>Nothing has happened yet.</b> This is only a list. "
        "Files go to the Trash only after you approve, and can be restored with "
        "Finder's “Put Back” or the <code>undo</code> command.</div>"
        "<div class=tiles>"
        f"<div class=tile><b>{len(items):,}</b><span>files</span></div>"
        f"<div class=tile><b>{len(groups):,}</b><span>orders</span></div>"
        f"<div class=tile><b>{e(human(total))}</b><span>total size</span></div>"
        "</div>"
        "<h2>Where they are</h2><ul class=obs>" + where + "</ul>"
        "<h2>How to review</h2><ul class=obs>"
        "<li>Check the list for anything that is <b>not</b> an order file.</li>"
        "<li>To keep some, note their numbers (#) and add "
        "<code>--skip 3,17,42</code> when approving.</li>"
        f"<li>Approve with: <code>python3 -m organizer apply {e(str(plan_path))}</code></li>"
        "</ul>"
        "<h2>Files</h2><div class=wrap><table><thead><tr><th>#</th><th>Order</th>"
        "<th>File</th><th>Folder</th><th>Size</th><th>Modified</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table></div></main></body></html>"
    )


def write_preview(text: str, reports_dir: Path, plan_path: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"preview_{plan_path.stem}.html"
    out.write_text(text, encoding="utf-8")
    os.chmod(out, 0o600)
    return out


def parse_skip(spec: Optional[str]) -> Tuple[set, List[str]]:
    """'3,17,20-25' -> {3,17,20..25}. Returns (numbers, bad tokens)."""
    nums, bad = set(), []
    for tok in (spec or "").replace(" ", "").split(","):
        if not tok:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", tok)
        if not m:
            bad.append(tok)
            continue
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        nums.update(range(min(a, b), max(a, b) + 1))
    return nums, bad
