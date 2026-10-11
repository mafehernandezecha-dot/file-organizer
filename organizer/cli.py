"""Command line: python3 -m organizer <command>

`audit`, `report` and `orders` never change your files; they only write the
index, reports, and plans inside the state dir (~/.file-organizer). `apply`
acts only on a saved plan after backup and typed confirmation; `undo`
reverses it.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from organizer import __version__
from organizer.analysis import build_audit, human
from organizer.config import load_config
from organizer.duplicates import find_duplicates
from organizer.index import connect, latest_scan_id
from organizer.plans import (
    DEFAULT_ORDER_PATTERN, build_trash_plan, find_order_files, load_plan, parse_skip,
    render_trash_preview, save_plan, write_preview,
)
from organizer import trash
from organizer.report import write_reports
from organizer.scanner import scan


def _say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def cmd_audit(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    missing = [r for r in cfg.roots if not r.exists()]
    for r in missing:
        _say(f"! Approved folder not found (drive unplugged?), skipping: {r}")
    cfg.roots = [r for r in cfg.roots if r.exists()]
    if not cfg.roots:
        _say("No approved folders are available. Nothing scanned.")
        return 1

    conn = connect(cfg.db_path)
    _say("Scanning (read-only):")
    for r in cfg.roots:
        _say(f"  • {r}")
    scan_id = scan(conn, cfg, progress=lambda n: _say(f"  … {n:,} items"))
    if cfg.hash_duplicates and not args.no_hash:
        _say("Checking for exact duplicates (only same-size files are read)…")
        find_duplicates(conn, scan_id)
    audit = build_audit(conn, scan_id, cfg)
    html_path, md_path = write_reports(audit, cfg.reports_dir)
    _say("")
    _say(f"Indexed {audit.total_items:,} items, {human(audit.total_bytes)}.")
    _say(f"Duplicate sets: {len(audit.duplicate_groups)} · Version families: "
         f"{len(audit.version_families)} · Memobrick items: {audit.business_total:,}")
    _say(f"Report: {html_path}")
    _say(f"        {md_path}")
    if args.open and sys.platform == "darwin":
        subprocess.run(["open", str(html_path)], check=False)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    audit = build_audit(conn, latest_scan_id(conn), cfg)
    html_path, _ = write_reports(audit, cfg.reports_dir)
    print(html_path)
    if args.open and sys.platform == "darwin":
        subprocess.run(["open", str(html_path)], check=False)
    return 0


def cmd_orders(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    scan_id = latest_scan_id(conn)
    scan_row = conn.execute("SELECT finished_at FROM scans WHERE id=?", (scan_id,)).fetchone()
    pattern = cfg.order_file_pattern or DEFAULT_ORDER_PATTERN
    rows = find_order_files(conn, scan_id, pattern)
    if not rows:
        _say("No order files found (names starting with a 5–6 digit order number).")
        return 0
    plan = build_trash_plan(rows, "customer order files", scan_row["finished_at"])
    plan_path = save_plan(plan, cfg.state_dir / "plans", "trash-orders")
    preview = write_preview(render_trash_preview(plan, plan_path, pattern),
                            cfg.reports_dir, plan_path)
    _say(f"Found {len(rows):,} order file(s), {human(sum(r['size'] for r in rows))}.")
    _say("Nothing has been moved. Review the preview:")
    _say(f"  {preview}")
    _say("When it looks right, approve with:")
    _say(f"  python3 -m organizer apply {plan_path}")
    if args.open and sys.platform == "darwin":
        subprocess.run(["open", str(preview)], check=False)
    return 0


def _confirm_backup(ask) -> bool:
    age = trash.latest_backup_age_hours()
    if age is not None and age <= trash.BACKUP_MAX_AGE_HOURS:
        _say(f"✓ Time Machine backup found from {age:.0f} hour(s) ago.")
        return True
    if age is not None:
        _say(f"! The latest Time Machine backup is {age / 24:.1f} day(s) old.")
    else:
        _say("! macOS didn't report the latest Time Machine backup to Terminal.")
    _say("  Click the Time Machine icon in the menu bar (or System Settings → General →")
    _say("  Time Machine) and check \"Latest backup\". If it's not from today, choose")
    _say("  \"Back Up Now\", wait for it to finish, then come back.")
    return ask("Is your latest backup from today? Type yes to continue: ").strip().lower() == "yes"


def cmd_apply(args: argparse.Namespace, ask=input) -> int:
    if not trash.is_macos():
        _say("apply only runs on macOS.")
        return 2
    cfg = load_config(args.config)
    plan = load_plan(args.plan)
    skip, bad = parse_skip(args.skip)
    if bad:
        _say(f"Error: couldn't read --skip value(s): {', '.join(bad)}")
        return 2
    count, size = trash.summarize(plan, skip)
    if not count:
        _say("Nothing to do: every item is skipped.")
        return 0
    if not _confirm_backup(ask):
        _say("Stopped. Nothing was changed.")
        return 1
    _say(f"About to move {count:,} file(s) ({human(size)}) to the Trash"
         f"{f', keeping {len(skip)} you skipped' if skip else ''}.")
    _say("macOS may ask to let Terminal control Finder — click OK.")
    if ask("Type TRASH to continue: ").strip() != "TRASH":
        _say("Stopped. Nothing was changed.")
        return 1
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    journal = cfg.state_dir / "journal" / f"{Path(args.plan).stem}_{stamp}.jsonl"
    counts = trash.apply_trash(plan, journal, skip, say=_say)
    _say("")
    _say(f"Moved to Trash: {counts['trashed']:,} · left alone: {counts['skipped']:,}"
         f" · failed: {counts['failed']:,}")
    _say("To put everything back:")
    _say(f"  python3 -m organizer undo {journal}")
    return 0 if not counts["failed"] else 1


def cmd_undo(args: argparse.Namespace) -> int:
    if not trash.is_macos():
        _say("undo only runs on macOS.")
        return 2
    counts = trash.undo_trash(args.journal, say=_say)
    _say(f"Restored: {counts['restored']:,} · could not restore: {counts['failed']:,}")
    if counts["failed"]:
        _say("Anything not restored is still in the Trash — use Finder's “Put Back”.")
    return 0 if not counts["failed"] else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="organizer", description="Smart File Organization Agent")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("audit", help="Read-only scan of approved folders + audit report")
    a.add_argument("--config", type=Path, default=Path("config.json"))
    a.add_argument("--no-hash", action="store_true", help="skip duplicate hashing (faster)")
    a.add_argument("--open", action="store_true", help="open the report when done (macOS)")
    a.set_defaults(func=cmd_audit)

    r = sub.add_parser("report", help="Re-render the report from the latest scan")
    r.add_argument("--config", type=Path, default=Path("config.json"))
    r.add_argument("--open", action="store_true")
    r.set_defaults(func=cmd_report)

    o = sub.add_parser("orders", help="Preview customer order files to move to Trash")
    o.add_argument("--config", type=Path, default=Path("config.json"))
    o.add_argument("--open", action="store_true")
    o.set_defaults(func=cmd_orders)

    ap = sub.add_parser("apply", help="Carry out a reviewed plan (moves to Trash)")
    ap.add_argument("plan", type=Path)
    ap.add_argument("--skip", help="item numbers to keep, e.g. 3,17,20-25")
    ap.add_argument("--config", type=Path, default=Path("config.json"))
    ap.set_defaults(func=cmd_apply)

    u = sub.add_parser("undo", help="Put back everything a run moved to Trash")
    u.add_argument("journal", type=Path)
    u.set_defaults(func=cmd_undo)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, LookupError) as e:
        _say(f"Error: {e}")
        return 2
