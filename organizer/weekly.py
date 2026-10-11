"""Weekly Review Mode: scan, prepare every clean-up preview, write one report.

Nothing here changes user files. The report lists each prepared plan; the
`approve` command carries them all out after one backup check and one typed
confirmation.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from organizer import vision as vision_mod
from organizer.analysis import build_audit, human
from organizer.cleanup import build_cleanup_plan, render_cleanup_preview
from organizer.config import Config
from organizer.duplicates import find_duplicates
from organizer.dupes import build_duplicates_plan, render_duplicates_preview
from organizer.index import connect
from organizer.plans import (
    DEFAULT_LABEL_PATTERN, build_move_plan, build_trash_plan, find_label_files,
    find_screen_captures, render_move_preview, render_trash_preview, save_plan,
    screenshot_move_pairs, write_preview,
)
from organizer.report import CSS, short, write_reports
from organizer.scanner import scan
from organizer.sorting import build_sort_plan, image_candidates, render_sort_preview

Say = Callable[[str], None]

# Order matters when approving: trash duplicates and old archives first, then
# gather screenshots, trash labels, and finally file Memobrick items.
STEPS = ["duplicates", "cleanup", "screenshots", "labels", "sort"]
TITLES = {
    "duplicates": "Extra copies of duplicates → Trash",
    "cleanup": "Installers & old archives → Trash (newest 5 theme/editor versions kept)",
    "screenshots": "New screenshots & recordings → Pictures/Screenshots",
    "labels": "Shipping labels → Trash",
    "sort": "Memobrick files → Documents/01 MEMOBRICK BUSINESS",
}


def latest_path(state_dir: Path) -> Path:
    return state_dir / "weekly" / "latest.json"


def run_weekly(cfg: Config, say: Say, use_vision: bool = True) -> Dict:
    conn = connect(cfg.db_path)
    say("Weekly check (read-only):")
    scan_id = scan(conn, cfg, progress=lambda n: say(f"  … {n:,} items"))
    if cfg.hash_duplicates:
        find_duplicates(conn, scan_id)
    finished = conn.execute("SELECT finished_at FROM scans WHERE id=?", (scan_id,)).fetchone()[0]
    audit = build_audit(conn, scan_id, cfg)
    audit_html, _ = write_reports(audit, cfg.reports_dir)

    scanned = {str(r) for r in cfg.roots}
    cfg.intake_folders = [p for p in cfg.intake_folders if str(p) in scanned]
    plans_dir = cfg.state_dir / "plans"
    entries: List[Dict] = []

    def add(step: str, plan: Dict, render) -> None:
        if not plan["items"]:
            return
        path = save_plan(plan, plans_dir, f"weekly-{step}")
        preview = write_preview(render(plan, path), cfg.reports_dir, path)
        entries.append({"step": step, "title": TITLES[step], "plan": str(path),
                        "preview": str(preview), "count": len(plan["items"]),
                        "size": sum(i["size"] for i in plan["items"])})

    add("duplicates", build_duplicates_plan(conn, scan_id, cfg, finished),
        render_duplicates_preview)
    add("cleanup", build_cleanup_plan(conn, scan_id, cfg, finished), render_cleanup_preview)
    dest = cfg.screenshots_folder.resolve()
    caps = find_screen_captures(conn, scan_id, dest)
    add("screenshots", build_move_plan(screenshot_move_pairs(caps, dest),
                                       "screenshots and screen recordings", dest, finished),
        lambda p, path: render_move_preview(p, path, "screenshots and screen recordings"))
    labels = find_label_files(conn, scan_id, cfg.label_file_pattern or DEFAULT_LABEL_PATTERN)
    add("labels", build_trash_plan(labels, "shipping labels", finished),
        lambda p, path: render_trash_preview(p, path, "shipping labels", "Folder",
                                             lambda x: short(os.path.dirname(x))))
    looks = {}
    if use_vision:
        tool = vision_mod.ensure_tool(cfg.state_dir, say)
        if tool:
            looks = vision_mod.analyze_images(conn, tool, image_candidates(conn, scan_id, cfg), say)
    add("sort", build_sort_plan(conn, scan_id, cfg, finished, looks), render_sort_preview)

    summary = {"created_at": time.time(), "scan_finished_at": finished,
               "audit_report": str(audit_html), "entries": entries}
    out = latest_path(cfg.state_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(out.parent, 0o700)
    report = out.parent / f"weekly_{dt.datetime.now():%Y-%m-%d_%H%M}.html"
    report.write_text(render_weekly(summary), encoding="utf-8")
    os.chmod(report, 0o600)
    summary["report"] = str(report)
    out.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    os.chmod(out, 0o600)
    return summary


def render_weekly(summary: Dict) -> str:
    e = html.escape
    entries = summary["entries"]
    when = dt.datetime.fromtimestamp(summary["created_at"]).strftime("%A %d %B %Y, %H:%M")
    rows = "".join(
        f"<tr><td>{e(x['title'])}</td><td>{x['count']:,}</td><td>{e(human(x['size']))}</td>"
        f"<td><a href='{e(Path(x['preview']).as_uri())}'>Open preview</a></td></tr>"
        for x in entries)
    body = (
        "<div class=wrap><table><thead><tr><th>Ready for you</th><th>Items</th><th>Size</th>"
        "<th></th></tr></thead><tbody>" + rows + "</tbody></table></div>"
        "<h2>Approve</h2><ul class=obs>"
        "<li>Open any preview to look it over. To approve <b>everything</b> at once, run in Terminal:"
        "<br><code>cd ~/file-organizer-main && python3 -m organizer approve</code></li>"
        "<li>To approve just one, run the <code>apply</code> command shown on its preview page "
        "(you can add <code>--skip</code> numbers there).</li>"
        "<li>Everything is undoable; trashed files stay in the Trash until you empty it.</li></ul>"
        if entries else "<p class=note>Nothing to tidy this week. 🎉</p>"
    )
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Weekly Tidy-Up</title><style>{CSS}</style></head><body><main>"
        f"<h1>Weekly tidy-up</h1><p class=sub>{e(when)}</p>"
        "<div class=safe><b>Nothing was changed.</b> These are prepared previews. "
        "Files move or go to the Trash only when you approve.</div>"
        + body
        + f"<p class=note><a href='{e(Path(summary['audit_report']).as_uri())}'>Full audit report</a></p>"
        "</main></body></html>"
    )


def notify(summary: Dict) -> None:
    if sys.platform != "darwin":
        return
    n = sum(x["count"] for x in summary["entries"])
    msg = f"{n} item(s) ready for review" if n else "Nothing to tidy this week"
    script = f'display notification "{msg}" with title "Weekly tidy-up"'
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def load_latest(state_dir: Path) -> Optional[Dict]:
    p = latest_path(state_dir)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
