"""Installers & archives clean-up, keeping the newest versions of Memobrick
themes and editor builds.

- Shopify theme archives and Memobrick editor/studio build archives are
  grouped into families. The newest N (by modified date) of each family are
  kept together in the business folder; older versions go to the Trash.
- Every other installer file or archive inside the intake folders
  (Downloads, Desktop) goes to the Trash.
- App bundles and anything outside the intake folders are left alone.
Reads the index only; writes the plan and its preview.
"""

from __future__ import annotations

import html
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from organizer.analysis import human
from organizer.classify import C, _has
from organizer.config import Config
from organizer.moves import unique_destinations
from organizer.report import CSS, date, short

THEME, EDITOR = "Shopify theme", "Memobrick editor/studio build"
FAMILY_FOLDER = {THEME: f"{C}/Shopify Themes", EDITOR: f"{C}/Editor & Studio Builds"}


def _under(path: str, folder: Path) -> bool:
    return (path + os.sep).startswith(str(folder) + os.sep)


def family_of(row, cfg: Config) -> Optional[str]:
    if row["kind"] != "archive":
        return None
    n = row["name"].lower()
    path = row["path"]
    if _under(path, cfg.business_root / FAMILY_FOLDER[THEME]) or (
            _has(n, "theme", r"theme[\s_\-]*export") and
            (_has(n, "memobrick", "shopify", "dawn") or _under(path, cfg.business_root))):
        return THEME
    if _under(path, cfg.business_root / FAMILY_FOLDER[EDITOR]) or _has(n, "editor", "studio") \
            or _has(n, "memobrick"):
        return EDITOR
    return None


def build_cleanup_plan(conn, scan_id: int, cfg: Config, scan_finished: float,
                       keep: int = 5) -> Dict:
    rows = conn.execute(
        """SELECT * FROM files WHERE scan_id=? AND item_type='file' AND sensitive=0
             AND kind IN ('archive', 'installer') ORDER BY path""", (scan_id,)).fetchall()
    families: Dict[str, List] = defaultdict(list)
    trash_rows = []
    for r in rows:
        fam = family_of(r, cfg)
        if fam:
            families[fam].append((r, fam))
        elif any(_under(r["path"], i) for i in cfg.intake_folders):
            why = "installer" if r["kind"] == "installer" else "compressed archive"
            trash_rows.append((r, why))

    kept, moves, trash = [], [], []
    for fam, members in families.items():
        members.sort(key=lambda x: x[0]["mtime"], reverse=True)
        folder = cfg.business_root / FAMILY_FOLDER[fam]
        for rank, (r, _) in enumerate(members, 1):
            if rank <= keep:
                entry = {"path": r["path"], "size": r["size"], "mtime": r["mtime"],
                         "family": fam, "rank": rank}
                if _under(r["path"], folder):
                    kept.append(entry)
                else:
                    moves.append((entry, str(folder / r["name"])))
            else:
                trash.append({"path": r["path"], "size": r["size"], "mtime": r["mtime"],
                              "why": f"older {fam} (version {rank} of {len(members)})",
                              "group": f"Older {fam}s"})
    for r, why in trash_rows:
        trash.append({"path": r["path"], "size": r["size"], "mtime": r["mtime"], "why": why,
                      "group": "Installers" if why == "installer" else "Other archives"})

    items = []
    pairs = unique_destinations([(m["path"], dest) for m, dest in moves])
    for (m, _), (_, dest) in zip(moves, pairs):
        items.append({**m, "op": "move", "dest": dest, "dataless": False, "unit": "file"})
    for t in sorted(trash, key=lambda t: (t["group"], t["path"])):
        items.append({**t, "op": "trash", "dataless": False})
    for i, it in enumerate(items, 1):
        it["n"] = i
    return {
        "version": 1,
        "action": "cleanup",
        "reason": "installers and archives",
        "keep": keep,
        "dest_root": str(cfg.business_root),
        "created_at": time.time(),
        "scan_finished_at": scan_finished,
        "items": items,
        "kept_in_place": kept,
    }


def split_plan(plan: Dict):
    """(move plan, trash plan) in the shapes apply_moves / apply_trash expect."""
    base = {k: v for k, v in plan.items() if k != "items"}
    moves = [i for i in plan["items"] if i["op"] == "move"]
    trash = [i for i in plan["items"] if i["op"] == "trash"]
    return {**base, "action": "move", "items": moves}, {**base, "action": "trash", "items": trash}


def render_cleanup_preview(plan: Dict, plan_path: Path) -> str:
    e = html.escape
    items, kept = plan["items"], plan.get("kept_in_place", [])
    moves = [i for i in items if i["op"] == "move"]
    trash = [i for i in items if i["op"] == "trash"]

    keep_rows = "".join(
        f"<tr><td>{it['n']}</td><td>{e(it['family'])}</td><td>#{it['rank']}</td>"
        f"<td>{e(os.path.basename(it['path']))}</td><td>moves to {e(short(os.path.dirname(it['dest'])))}</td>"
        f"<td>{e(human(it['size']))}</td><td>{e(date(it['mtime']))}</td></tr>"
        for it in moves
    ) + "".join(
        f"<tr><td></td><td>{e(it['family'])}</td><td>#{it['rank']}</td>"
        f"<td>{e(os.path.basename(it['path']))}</td><td>already there</td>"
        f"<td>{e(human(it['size']))}</td><td>{e(date(it['mtime']))}</td></tr>"
        for it in kept
    )
    groups: Dict[str, List[Dict]] = defaultdict(list)
    for it in trash:
        groups[it["group"]].append(it)
    trash_html = ""
    for g in sorted(groups):
        its = groups[g]
        body = "".join(
            f"<tr><td>{it['n']}</td><td>{e(os.path.basename(it['path']))}</td>"
            f"<td>{e(short(os.path.dirname(it['path'])))}</td><td>{e(it['why'])}</td>"
            f"<td>{e(human(it['size']))}</td><td>{e(date(it['mtime']))}</td></tr>" for it in its)
        trash_html += (f"<h3>{e(g)} ({len(its):,} · {e(human(sum(i['size'] for i in its)))})</h3>"
                       "<div class=wrap><table><thead><tr><th>#</th><th>File</th><th>Folder</th>"
                       "<th>Why</th><th>Size</th><th>Modified</th></tr></thead><tbody>"
                       + body + "</tbody></table></div>")
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Preview: Installers & Archives</title><style>{CSS}h3{{font-size:15px;margin:22px 0 6px}}</style>"
        "</head><body><main>"
        "<h1>Preview: installers & archives</h1>"
        f"<p class=sub>Based on the scan from {e(date(plan['scan_finished_at']))}</p>"
        "<div class=safe><b>Nothing has happened yet.</b> The newest "
        f"{plan['keep']} versions of each Memobrick theme / editor build are kept together; "
        "everything listed under “Trash” goes to the Trash only after you approve. "
        "Finder's “Put Back” or the <code>undo</code> command restores anything.</div>"
        "<div class=tiles>"
        f"<div class=tile><b>{len(moves) + len(kept):,}</b><span>versions kept</span></div>"
        f"<div class=tile><b>{len(trash):,}</b><span>to Trash</span></div>"
        f"<div class=tile><b>{e(human(sum(i['size'] for i in trash)))}</b>"
        "<span>freed when the Trash is emptied</span></div>"
        "</div>"
        "<h2>How to review</h2><ul class=obs>"
        "<li>Check “Other archives”: a zip of customer photos or assets you still need? "
        "Note its number (#).</li>"
        "<li>Keep anything by adding <code>--skip 3,17,20-25</code> when approving.</li>"
        f"<li>Approve with: <code>python3 -m organizer apply {e(str(plan_path))}</code></li>"
        "</ul>"
        f"<h2>Kept: newest {plan['keep']} versions</h2>"
        + ("<div class=wrap><table><thead><tr><th>#</th><th>Family</th><th>Newest</th><th>File</th>"
           "<th>Where</th><th>Size</th><th>Modified</th></tr></thead><tbody>" + keep_rows
           + "</tbody></table></div>" if keep_rows else "<p class=note>No theme or editor builds found.</p>")
        + "<h2>Trash</h2>" + (trash_html or "<p class=note>Nothing to trash.</p>")
        + "</main></body></html>"
    )
