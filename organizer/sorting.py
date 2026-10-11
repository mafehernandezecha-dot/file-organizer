"""Build a reviewable plan that files Memobrick items from intake folders
(Downloads, Desktop) into the business folder. Reads only; writes nothing
but the plan and its preview.
"""

from __future__ import annotations

import html
import os
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from organizer.analysis import human
from organizer.classify import HIGH, Decision, classify_file, classify_folder, peek_text
from organizer.config import Config
from organizer.moves import unique_destinations
from organizer.report import CSS, date, short

PEEK_EXTS = {".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".tsv", ".txt", ".md", ".rtf"}


def _units(rows: List[sqlite3.Row], root: str):
    """Split an intake folder's rows into top-level files and top-level folders."""
    files, folders = [], defaultdict(list)
    for r in rows:
        rel = os.path.relpath(r["path"], root)
        parts = rel.split(os.sep)
        if len(parts) == 1:
            files.append(r)
        else:
            folders[parts[0]].append(r)
    return files, folders


def build_sort_plan(conn: sqlite3.Connection, scan_id: int, cfg: Config,
                    scan_finished: float) -> Dict:
    business_root = cfg.business_root
    moves: List[Tuple[Dict, str]] = []
    review: List[Dict] = []
    for intake in cfg.intake_folders:
        root = str(intake)
        rows = conn.execute("SELECT * FROM files WHERE scan_id=? AND root=?",
                            (scan_id, root)).fetchall()
        files, folders = _units(rows, root)

        for r in files:
            base = {"path": r["path"], "size": r["size"], "mtime": r["mtime"],
                    "dataless": bool(r["dataless"])}
            if r["item_type"] == "symlink":
                continue
            if r["sensitive"]:
                review.append({**base, "unit": "file", "reason": "looks private — never touched"})
                continue
            if r["item_type"] == "package":
                review.append({**base, "unit": "file", "reason": "app or library bundle"})
                continue
            if r["item_type"] == "project":
                d = classify_folder(r["name"], Counter(), True, bool(r["business"]), r["mtime"],
                                    cfg.loose_photos_are_business)
                unit = "folder"
            else:
                text = ""
                if r["ext"] in PEEK_EXTS and not r["dataless"]:
                    text = peek_text(r["path"], r["ext"], r["size"])
                d = classify_file(r, text, cfg.loose_photos_are_business)
                unit = "file"
            _place(d, {**base, "unit": unit}, business_root, moves, review)

        for name, children in folders.items():
            path = os.path.join(root, name)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            base = {"path": path, "size": sum(c["size"] for c in children),
                    "mtime": st.st_mtime, "dataless": False, "unit": "folder",
                    "files": len(children)}
            if any(c["sensitive"] for c in children):
                review.append({**base, "reason": "contains private-looking files — never touched"})
                continue
            d = classify_folder(name, Counter(c["kind"] for c in children),
                                any(c["item_type"] == "project" for c in children),
                                any(c["business"] for c in children), st.st_mtime,
                                cfg.loose_photos_are_business)
            _place(d, base, business_root, moves, review)

    pairs = unique_destinations([(m["path"], dest) for m, dest in moves])
    items = []
    for i, ((m, _), (_, dest)) in enumerate(zip(moves, pairs), 1):
        items.append({**m, "n": i, "dest": dest})
    return {
        "version": 1,
        "action": "move",
        "reason": "Memobrick files",
        "dest_root": str(business_root),
        "created_at": time.time(),
        "scan_finished_at": scan_finished,
        "items": items,
        "review": review,
    }


def _place(d: Decision, item: Dict, business_root: Path, moves, review) -> None:
    if not d.dest:
        review.append({**item, "reason": d.reason})
        return
    item = {**item, "confidence": round(d.confidence, 2), "why": d.reason,
            "section": d.dest}
    moves.append((item, str(business_root / d.dest / os.path.basename(item["path"]))))


def render_sort_preview(plan: Dict, plan_path: Path) -> str:
    e = html.escape
    items, review = plan["items"], plan.get("review", [])
    total = sum(i["size"] for i in items)
    medium = [i for i in items if i["confidence"] < HIGH]
    by_section: Dict[str, List[Dict]] = defaultdict(list)
    for it in items:
        by_section[it["section"]].append(it)

    summary = "".join(
        f"<tr><td>{e(sec)}</td><td>{len(its):,}</td><td>{e(human(sum(i['size'] for i in its)))}</td></tr>"
        for sec, its in sorted(by_section.items())
    )

    def label(it):
        return "High" if it["confidence"] >= HIGH else "<b>Check</b>"

    rows = "".join(
        "<tr>"
        f"<td>{it['n']}</td>"
        f"<td>{e(os.path.basename(it['path']))}"
        + (f" <span class=note>(folder, {it.get('files', 0)} files)</span>" if it["unit"] == "folder" else "")
        + f"</td><td>{e(short(os.path.dirname(it['path'])))}</td>"
        f"<td>{e(it['section'])}"
        + (f" as <b>{e(os.path.basename(it['dest']))}</b>"
           if os.path.basename(it["dest"]) != os.path.basename(it["path"]) else "")
        + f"</td><td>{label(it)}</td><td>{e(it['why'])}</td>"
        f"<td>{e(human(it['size']))}</td></tr>"
        for it in sorted(items, key=lambda i: (i["section"], i["n"]))
    )
    rev_reasons = Counter(r["reason"] for r in review)
    rev_rows = "".join(
        f"<tr><td>{e(os.path.basename(r['path']))}"
        + (" <span class=note>(folder)</span>" if r["unit"] == "folder" else "")
        + f"</td><td>{e(short(os.path.dirname(r['path'])))}</td><td>{e(r['reason'])}</td>"
        f"<td>{e(human(r['size']))}</td><td>{e(date(r['mtime']))}</td></tr>"
        for r in sorted(review, key=lambda r: (r["reason"], r["path"]))
    )
    table = lambda head, body: (  # noqa: E731
        "<div class=wrap><table><thead><tr>" + "".join(f"<th>{h}</th>" for h in head)
        + "</tr></thead><tbody>" + body + "</tbody></table></div>")
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Preview: Memobrick Files</title><style>{CSS}</style></head><body><main>"
        "<h1>Preview: file Memobrick items</h1>"
        f"<p class=sub>Into {e(short(plan['dest_root']))} · based on the scan from "
        f"{e(date(plan['scan_finished_at']))}</p>"
        "<div class=safe><b>Nothing has happened yet.</b> Files move only after you approve. "
        "Names, dates and quality stay the same, nothing is overwritten, and the "
        "<code>undo</code> command puts everything back.</div>"
        "<div class=tiles>"
        f"<div class=tile><b>{len(items):,}</b><span>to file · {e(human(total))}</span></div>"
        f"<div class=tile><b>{len(medium):,}</b><span>marked “Check”</span></div>"
        f"<div class=tile><b>{len(review):,}</b><span>need your decision (staying put)</span></div>"
        "</div>"
        "<h2>Where things go</h2>"
        + table(["Folder", "Items", "Size"], summary)
        + "<h2>How to review</h2><ul class=obs>"
        "<li><b>High</b> = a clear clue (name, ID, or contents). <b>Check</b> = a reasonable "
        "guess, e.g. a loose photo filed under Creative Assets by year. Skim those.</li>"
        "<li>To leave some where they are, note their numbers (#) and add "
        "<code>--skip 3,17,20-25</code> when approving.</li>"
        f"<li>Approve with: <code>python3 -m organizer apply {e(str(plan_path))}</code></li>"
        "</ul>"
        f"<h2>Files to move ({len(items):,})</h2>"
        + (f"<details><summary>Show all {len(items):,}</summary>" if len(items) > 40 else "")
        + table(["#", "Item", "From", "To", "Confidence", "Why", "Size"], rows)
        + ("</details>" if len(items) > 40 else "")
        + f"<h2>Need your decision ({len(review):,}) — not moved</h2>"
        "<ul class=obs>" + "".join(f"<li>{n} · {e(why)}</li>" for why, n in rev_reasons.most_common())
        + "</ul>"
        + (f"<details><summary>Show all {len(review):,}</summary>"
           + table(["Item", "Folder", "Why it stays", "Size", "Modified"], rev_rows)
           + "</details>" if review else "")
        + "</main></body></html>"
    )
