"""What didn't get organized, and why. Read-only.

Combines (1) items skipped during the latest approve/apply (from journals),
(2) items the latest sort plan deliberately kept in place, and (3) what is
still sitting in the intake folders right now.
"""

from __future__ import annotations

import html
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List

from organizer.analysis import human
from organizer.config import Config
from organizer.kinds import kind_for
from organizer.report import CSS, date, short
from organizer.scanner import is_dataless

WHY_HELP = {
    "stored only in iCloud (download it first)":
        "In Finder, right-click → Download Now, then run the weekly check again.",
    "changed since the scan": "It was edited after the scan; the next check picks it up.",
    "folder contents changed since the scan": "Picked up by the next check.",
    "no longer there": "Moved or deleted since the scan — nothing to do.",
    "a file with that name is already at the destination":
        "A file with the same name is already there; compare the two and keep one.",
    "looks private — never touched": "Kept on purpose (passwords, keys…). Move it by hand if you like.",
    "app or library bundle": "Apps stay where they are. Drag to Applications if it's an app you use.",
    "software folder — not touched without your say-so": "Code folders are never moved automatically.",
    "contains private-looking files — never touched": "Kept on purpose. Move it by hand if you like.",
}


def latest_run_journals(state_dir: Path, window_s: float = 900) -> List[Path]:
    """Journals written in the most recent approve/apply session."""
    jdir = state_dir / "journal"
    if not jdir.is_dir():
        return []
    js = sorted(jdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not js:
        return []
    newest = js[0].stat().st_mtime
    return [p for p in js if newest - p.stat().st_mtime <= window_s]


def skipped_items(journals: List[Path]) -> List[Dict]:
    out = []
    for j in journals:
        entries = [json.loads(l) for l in j.read_text(encoding="utf-8").splitlines() if l.strip()]
        done = {e["path"] for e in entries if e["status"] in ("moved", "trashed", "tagged")}
        for e in entries:
            if e["status"] in ("skipped", "failed") and e["path"] not in done:
                why = e.get("why", "")
                if why.startswith("you chose") or why == "no longer there":
                    continue  # your choice, or already handled by an earlier step
                out.append({"path": e["path"], "why": why or e["status"], "step": j.stem})
    return out


def kept_in_place(state_dir: Path) -> List[Dict]:
    plans = sorted((state_dir / "plans").glob("*sort*.json"), key=lambda p: p.stat().st_mtime)
    if not plans:
        return []
    plan = json.loads(plans[-1].read_text(encoding="utf-8"))
    return [{"path": r["path"], "why": r["reason"]} for r in plan.get("review", [])
            if os.path.lexists(r["path"])]


def still_here(cfg: Config) -> List[Dict]:
    organized = {str(cfg.business_root), str(cfg.personal_root), str(cfg.intake_root)}
    out = []
    folders = [(p, True) for p in cfg.intake_folders] + [(p, False) for p in cfg.loose_file_folders]
    for folder, include_dirs in folders:
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            if e.name.startswith(".") or e.path in organized or e.name in ("Icon\r",):
                continue
            is_dir = e.is_dir(follow_symlinks=False)
            if is_dir and not include_dirs:
                continue  # your own folders in Documents are fine where they are
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            out.append({"path": e.path, "kind": "folder" if is_dir else kind_for(e.name),
                        "size": st.st_size, "mtime": st.st_mtime,
                        "icloud": is_dataless(st), "where": short(str(folder))})
    return sorted(out, key=lambda r: (r["where"], r["path"]))


def build_leftovers(cfg: Config) -> Dict:
    return {
        "skipped": skipped_items(latest_run_journals(cfg.state_dir)),
        "kept": kept_in_place(cfg.state_dir),
        "still_here": still_here(cfg),
    }


def render_leftovers(data: Dict) -> str:
    e = html.escape

    def by_reason(items):
        groups = defaultdict(list)
        for it in items:
            groups[it["why"]].append(it)
        parts = []
        for why, its in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            tip = WHY_HELP.get(why, "")
            rows = "".join(f"<tr><td>{e(os.path.basename(i['path']))}</td>"
                           f"<td>{e(short(os.path.dirname(i['path'])))}</td></tr>" for i in its)
            parts.append(f"<h3>{e(why)} ({len(its)})</h3>"
                         + (f"<p class=note>{e(tip)}</p>" if tip else "")
                         + "<div class=wrap><table><thead><tr><th>Item</th><th>Folder</th></tr>"
                         "</thead><tbody>" + rows + "</tbody></table></div>")
        return "".join(parts) or "<p class=note>None.</p>"

    still = data["still_here"]
    rows = "".join(
        f"<tr><td>{e(os.path.basename(r['path']))}</td><td>{e(r['where'])}</td><td>{e(r['kind'])}</td>"
        f"<td>{e(human(r['size'])) if r['kind'] != 'folder' else ''}</td>"
        f"<td>{e(date(r['mtime']))}{' · iCloud only' if r['icloud'] else ''}</td></tr>"
        for r in still)
    counts = Counter(r["where"] for r in still)
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Not Yet Organized</title><style>{CSS}h3{{font-size:15px;margin:22px 0 4px}}</style>"
        "</head><body><main><h1>What isn't organized yet</h1>"
        "<div class=safe><b>Read-only.</b> This page only lists items; nothing was changed.</div>"
        "<div class=tiles>"
        f"<div class=tile><b>{len(data['skipped']):,}</b><span>left alone during the last approval</span></div>"
        f"<div class=tile><b>{len(data['kept']):,}</b><span>kept in place on purpose</span></div>"
        f"<div class=tile><b>{len(still):,}</b><span>still in Downloads / Desktop / Documents</span></div>"
        "</div>"
        "<h2>Left alone during the last approval</h2>" + by_reason(data["skipped"])
        + "<h2>Kept in place on purpose</h2>" + by_reason(data["kept"])
        + "<h2>Still sitting in Downloads, Desktop and loose in Documents</h2>"
        + "<ul class=obs>" + "".join(f"<li>{n} in {e(w)}</li>" for w, n in counts.most_common()) + "</ul>"
        + ("<div class=wrap><table><thead><tr><th>Item</th><th>Where</th><th>Type</th><th>Size</th>"
           "<th>Modified</th></tr></thead><tbody>" + rows + "</tbody></table></div>" if still
           else "<p class=note>Nothing — all clear. 🎉</p>")
        + "<h2>Next step</h2><ul class=obs><li>Run <code>python3 -m organizer weekly --open</code> "
        "then <code>python3 -m organizer approve</code> to pick up what can be organized now.</li></ul>"
        "</main></body></html>"
    )
