"""Render an Audit as a self-contained HTML page and a Markdown file.

Reports stay in the local state dir (owner-only permissions) because they
contain file paths. Nothing is sent anywhere.
"""

from __future__ import annotations

import datetime as dt
import html
import os
from pathlib import Path
from typing import List, Tuple

from organizer.analysis import Audit, human

HOME = str(Path.home())


def short(path: str) -> str:
    return "~" + path[len(HOME):] if path.startswith(HOME) else path


def date(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _sections(a: Audit) -> List[Tuple[str, List[str], List[List[str]], str]]:
    """(title, header, rows, note) tuples shared by both renderers."""
    s = []
    s.append(("Storage by approved folder", ["Folder", "Items", "Size"],
              [[short(r), f"{v['items']:,}", human(v["bytes"])] for r, v in a.by_root.items()], ""))
    s.append(("Storage by file type", ["Type", "Items", "Size"],
              [[k["kind"], f"{k['items']:,}", human(k["bytes"])] for k in a.by_kind], ""))
    s.append(("Largest folders", ["Folder", "Items", "Size"],
              [[short(f["path"]), f"{f['items']:,}", human(f["bytes"])] for f in a.largest_folders], ""))
    s.append(("Largest files", ["File", "Size", "Modified"],
              [[short(r["path"]), human(r["size"]), date(r["mtime"])] for r in a.largest_files], ""))

    dup_rows = []
    for g in a.duplicate_groups[:40]:
        first = True
        for r in g:
            dup_rows.append([
                human(g[0]["size"]) + f" × {len(g)}" if first else "",
                short(r["path"]), date(r["mtime"]),
            ])
            first = False
    s.append(("Exact duplicates", ["Set", "Copy", "Modified"], dup_rows,
              "Byte-for-byte identical (SHA-256). Recommendation only: nothing is deleted. "
              "Resized or re-exported versions are not exact duplicates and are not listed here."))

    ver_rows = []
    for fam in a.version_families[:30]:
        first = True
        for r in fam:
            ver_rows.append([fam[0]["base_name"] if first else "", short(r["path"]),
                             date(r["mtime"]), human(r["size"])])
            first = False
    s.append(("Version families", ["Family", "File", "Modified", "Size"], ver_rows,
              "Files that look like versions of one document. The newest is not assumed "
              "to be the approved one; Phase 3 compares contents before suggesting a primary."))

    biz_rows = [[short(root), str(sum(c.values())),
                 ", ".join(f"{k} {n}" for k, n in c.most_common(5))]
                for root, c in a.business_by_root.items()]
    s.append(("Memobrick items by location", ["Location", "Items", "Mostly"], biz_rows,
              "Matched by keywords in the folder or file name (memobrick, mosaic, brick …). "
              "Content-based recognition comes in Phase 3."))

    s.append(("Not opened or modified recently", ["File", "Size", "Last touched"],
              [[short(r["path"]), human(r["size"]), date(max(r["mtime"], r["atime"] or 0))]
               for r in a.stale_examples],
              f"{a.stale_count:,} files ({human(a.stale_bytes)}) untouched for over a year. "
              "Old does not mean disposable — this is only for awareness."))

    s.append(("Software projects (kept intact)", ["Project", "Size", "Modified"],
              [[short(r["path"]), human(r["size"]), date(r["mtime"])] for r in a.projects[:30]],
              "Folders with code markers (.git, package.json, Shopify theme …). "
              "Treated as one unit and never reorganized from the inside."))
    s.append(("Libraries & bundles (kept intact)", ["Item", "Size", "Modified"],
              [[short(r["path"]), human(r["size"]), date(r["mtime"])] for r in a.packages[:30]],
              "Photos libraries, video projects, Keynote/Pages files and apps are single "
              "items to macOS and are never opened or split."))
    s.append(("Skipped during scan", ["Reason", "Count"],
              [[k, f"{v:,}"] for k, v in a.skipped.most_common()],
              "Hidden files, system folders, and anything that looks like credentials are "
              "never scanned. 'Permission denied' means macOS privacy controls blocked it."))
    return s


def render_markdown(a: Audit) -> str:
    out = [f"# File Audit — {date(a.finished_at)}", "",
           "Read-only audit. **No files were moved, renamed, or deleted.**", "",
           f"- Items indexed: **{a.total_items:,}**",
           f"- Total size: **{human(a.total_bytes)}**",
           f"- Duplicate sets: **{len(a.duplicate_groups)}** "
           f"({human(a.duplicate_reclaimable)} in extra copies)",
           f"- Memobrick-related items: **{a.business_total:,}**",
           f"- Sensitive-looking files (not opened): **{a.sensitive_count}**", "",
           "## Organization problems found", ""]
    out += [f"- {o}" for o in a.observations] or ["- None detected."]
    for title, header, rows, note in _sections(a):
        out += ["", f"## {title}", ""]
        if note:
            out += [f"_{note}_", ""]
        if not rows:
            out.append("None.")
            continue
        out.append("| " + " | ".join(header) + " |")
        out.append("|" + "---|" * len(header))
        for row in rows:
            out.append("| " + " | ".join(c.replace("|", "\\|") for c in row) + " |")
    return "\n".join(out) + "\n"


CSS = """
:root{--bg:#fbfaf8;--fg:#1d1d1f;--muted:#6e6e73;--card:#fff;--line:#e5e2dc;--accent:#c2410c}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#f2f2f2;--muted:#a1a1a6;
--card:#1f1f1f;--line:#333;--accent:#fb923c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif}
main{max-width:1080px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:28px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 8px}
.sub{color:var(--muted);margin:0 0 24px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.tile b{display:block;font-size:24px;font-variant-numeric:tabular-nums}
.tile span{color:var(--muted);font-size:13px}
.safe{border-left:4px solid var(--accent);padding:10px 14px;background:var(--card);
border-radius:8px;margin:20px 0}
ul.obs li{margin:4px 0}.note{color:var(--muted);font-size:13px;margin:0 0 8px}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:12px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:7px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}tr:last-child td{border-bottom:0}
td:first-child{word-break:break-all}td{font-variant-numeric:tabular-nums}
details summary{cursor:pointer;color:var(--muted)}
"""


def render_html(a: Audit) -> str:
    e = html.escape
    tiles = [
        (f"{a.total_items:,}", "items indexed"),
        (human(a.total_bytes), "total size"),
        (str(len(a.duplicate_groups)), f"duplicate sets · {human(a.duplicate_reclaimable)} extra"),
        (str(len(a.version_families)), "version families"),
        (f"{a.business_total:,}", "Memobrick items"),
        (f"{a.stale_count:,}", "untouched > 1 year"),
    ]
    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        f"<title>File Audit</title><style>{CSS}</style></head><body><main>",
        f"<h1>File Audit</h1><p class=sub>{e(date(a.finished_at))} · "
        f"{e(', '.join(short(r) for r in a.roots))}</p>",
        "<div class=safe><b>Read-only.</b> Nothing was moved, renamed, or deleted. "
        "This report stays on your Mac.</div>",
        "<div class=tiles>" + "".join(
            f"<div class=tile><b>{e(v)}</b><span>{e(l)}</span></div>" for v, l in tiles) + "</div>",
        "<h2>Organization problems found</h2><ul class=obs>",
        "".join(f"<li>{e(o)}</li>" for o in a.observations) or "<li>None detected.</li>",
        "</ul>",
    ]
    for title, header, rows, note in _sections(a):
        parts.append(f"<h2>{e(title)}</h2>")
        if note:
            parts.append(f"<p class=note>{e(note)}</p>")
        if not rows:
            parts.append("<p class=note>None.</p>")
            continue
        table = ("<div class=wrap><table><thead><tr>" +
                 "".join(f"<th>{e(h)}</th>" for h in header) + "</tr></thead><tbody>" +
                 "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in row) + "</tr>"
                         for row in rows) + "</tbody></table></div>")
        parts.append(table if len(rows) <= 30 else
                     f"<details><summary>Show {len(rows)} rows</summary>{table}</details>")
    parts.append("</main></body></html>")
    return "".join(parts)


def write_reports(a: Audit, reports_dir: Path) -> Tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(reports_dir, 0o700)
    stamp = dt.datetime.fromtimestamp(a.finished_at).strftime("%Y-%m-%d_%H%M")
    html_path = reports_dir / f"audit_{stamp}.html"
    md_path = reports_dir / f"audit_{stamp}.md"
    for p, text in ((html_path, render_html(a)), (md_path, render_markdown(a))):
        p.write_text(text, encoding="utf-8")
        os.chmod(p, 0o600)
    return html_path, md_path
