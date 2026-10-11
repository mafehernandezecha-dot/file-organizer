"""Command line: python3 -m organizer <command>

`audit`, `report` and `orders` never change your files; they only write the
index, reports, and plans inside the state dir (~/.file-organizer). `apply`
acts only on a saved plan after backup and typed confirmation; `undo`
reverses it.
"""

from __future__ import annotations

import argparse
import os
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
    DEFAULT_LABEL_PATTERN, DEFAULT_ORDER_PATTERN, build_trash_plan, find_label_files,
    build_move_plan, find_order_files, find_screen_captures, load_plan, order_number,
    parse_skip, render_move_preview, screenshot_move_pairs,
    render_trash_preview, save_plan, write_preview,
)
from organizer import moves, trash
from organizer.cleanup import build_cleanup_plan, render_cleanup_preview, split_plan
from organizer.dupes import build_duplicates_plan, render_duplicates_preview
from organizer.sorting import build_sort_plan, image_candidates, render_sort_preview
from organizer import vision as vision_mod
from organizer.report import short, write_reports
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


def _trash_preview(args, title, slug, group_label, find, group_of, none_msg) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    scan_id = latest_scan_id(conn)
    scan_row = conn.execute("SELECT finished_at FROM scans WHERE id=?", (scan_id,)).fetchone()
    rows = find(conn, scan_id, cfg)
    if not rows:
        _say(none_msg)
        return 0
    plan = build_trash_plan(rows, title, scan_row["finished_at"])
    plan_path = save_plan(plan, cfg.state_dir / "plans", slug)
    preview = write_preview(
        render_trash_preview(plan, plan_path, title, group_label, group_of),
        cfg.reports_dir, plan_path)
    _say(f"Found {len(rows):,} {title}, {human(sum(r['size'] for r in rows))}.")
    _say("Nothing has been moved. Review the preview:")
    _say(f"  {preview}")
    _say("When it looks right, approve with:")
    _say(f"  python3 -m organizer apply {plan_path}")
    if args.open and sys.platform == "darwin":
        subprocess.run(["open", str(preview)], check=False)
    return 0


def cmd_orders(args: argparse.Namespace) -> int:
    pattern = load_config(args.config).order_file_pattern or DEFAULT_ORDER_PATTERN
    return _trash_preview(
        args, "order files", "trash-orders", "Order",
        lambda conn, scan_id, cfg: find_order_files(conn, scan_id, pattern),
        lambda path: order_number(os.path.basename(path), pattern),
        "No order files found (names starting with a 5–6 digit order number).")


def cmd_labels(args: argparse.Namespace) -> int:
    return _trash_preview(
        args, "shipping labels", "trash-labels", "Folder",
        lambda conn, scan_id, cfg: find_label_files(
            conn, scan_id, cfg.label_file_pattern or DEFAULT_LABEL_PATTERN),
        lambda path: short(os.path.dirname(path)),
        "No shipping labels found by name (shipping label, USPS, FedEx, postage …).")


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
    cfg = load_config(args.config)
    plan = load_plan(args.plan)
    is_trash = plan["action"] in ("trash", "cleanup")
    if is_trash and not trash.is_macos():
        _say("Moving to the Trash only works on macOS.")
        return 2
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
    kept = f", keeping {len(skip)} you skipped" if skip else ""
    if plan["action"] == "cleanup":
        mp, tp = split_plan(plan)
        n_move, _ = trash.summarize(mp, skip)
        n_trash, t_size = trash.summarize(tp, skip)
        _say(f"About to keep {n_move:,} newest version(s) together and move {n_trash:,} "
             f"file(s) ({human(t_size)}) to the Trash{kept}.")
        _say("macOS may ask to let Terminal control Finder — click OK.")
        word = "TRASH"
    elif is_trash:
        _say(f"About to move {count:,} file(s) ({human(size)}) to the Trash{kept}.")
        _say("macOS may ask to let Terminal control Finder — click OK.")
        word = "TRASH"
    else:
        _say(f"About to move {count:,} file(s) ({human(size)}) into "
             f"{short(plan['dest_root'])}{kept}.")
        word = "MOVE"
    if ask(f"Type {word} to continue: ").strip().upper() != word:
        _say("Stopped. Nothing was changed.")
        return 1
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    journal = cfg.state_dir / "journal" / f"{Path(args.plan).stem}_{stamp}.jsonl"
    if plan["action"] == "cleanup":
        mp, tp = split_plan(plan)
        mc = moves.apply_moves(mp, journal, skip, say=_say)
        tc = trash.apply_trash(tp, journal, skip, say=_say)
        counts = {"skipped": mc["skipped"] + tc["skipped"], "failed": mc["failed"] + tc["failed"]}
        done = f"Kept together: {mc['moved']:,} · moved to Trash: {tc['trashed']:,}"
    elif is_trash:
        counts = trash.apply_trash(plan, journal, skip, say=_say)
        done = f"Moved to Trash: {counts['trashed']:,}"
    else:
        counts = moves.apply_moves(plan, journal, skip, say=_say)
        done = f"Moved: {counts['moved']:,}"
    _say("")
    _say(f"{done} · left alone: {counts['skipped']:,} · failed: {counts['failed']:,}")
    _say("To put everything back:")
    _say(f"  python3 -m organizer undo {journal}")
    return 0 if not counts["failed"] else 1


def cmd_undo(args: argparse.Namespace) -> int:
    statuses = {e["status"] for e in trash.read_journal(args.journal)}
    if not statuses & {"moved", "trashed"}:
        _say("That run didn't change anything, so there is nothing to undo.")
        return 0
    if "trashed" in statuses and not trash.is_macos():
        _say("Restoring from the Trash only works on macOS.")
        return 2
    counts = {"restored": 0, "failed": 0}
    kind = "trash" if "trashed" in statuses else "move"
    if "trashed" in statuses:
        for k, v in trash.undo_trash(args.journal, say=_say).items():
            counts[k] += v
    if "moved" in statuses:
        for k, v in moves.undo_moves(args.journal, say=_say).items():
            counts[k] += v
    _say(f"Restored: {counts['restored']:,} · could not restore: {counts['failed']:,}")
    if counts["failed"] and kind == "trash":
        _say("Anything not restored is still in the Trash — use Finder's “Put Back”.")
    return 0 if not counts["failed"] else 1


def cmd_screenshots(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    scan_id = latest_scan_id(conn)
    scan_row = conn.execute("SELECT finished_at FROM scans WHERE id=?", (scan_id,)).fetchone()
    dest = cfg.screenshots_folder.resolve()
    rows = find_screen_captures(conn, scan_id, dest)
    if not rows:
        _say(f"No screenshots or screen recordings outside {short(str(dest))}.")
        return 0
    plan = build_move_plan(screenshot_move_pairs(rows, dest),
                           "screenshots and screen recordings", dest, scan_row["finished_at"])
    plan_path = save_plan(plan, cfg.state_dir / "plans", "move-screenshots")
    preview = write_preview(render_move_preview(plan, plan_path,
                                                "screenshots and screen recordings"),
                            cfg.reports_dir, plan_path)
    _say(f"Found {len(rows):,} screenshot(s)/recording(s), "
         f"{human(sum(r['size'] for r in rows))}, to move into {short(str(dest))}.")
    _say("Nothing has been moved. Review the preview:")
    _say(f"  {preview}")
    _say("When it looks right, approve with:")
    _say(f"  python3 -m organizer apply {plan_path}")
    if args.open and sys.platform == "darwin":
        subprocess.run(["open", str(preview)], check=False)
    return 0


def cmd_sort(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    scan_id = latest_scan_id(conn)
    scan_row = conn.execute("SELECT finished_at FROM scans WHERE id=?", (scan_id,)).fetchone()
    scanned = {str(r) for r in cfg.roots}
    cfg.intake_folders = [p for p in cfg.intake_folders if str(p) in scanned]
    if not cfg.intake_folders:
        _say("None of the intake folders (Downloads, Desktop) are in approved_folders.")
        return 1
    _say("Reading names and document text (on this Mac only)…")
    looks = {}
    if not args.no_vision:
        tool = vision_mod.ensure_tool(cfg.state_dir, _say)
        if tool:
            looks = vision_mod.analyze_images(conn, tool, image_candidates(conn, scan_id, cfg), _say)
    plan = build_sort_plan(conn, scan_id, cfg, scan_row["finished_at"], looks)
    plan_path = save_plan(plan, cfg.state_dir / "plans", "sort-memobrick")
    preview = write_preview(render_sort_preview(plan, plan_path), cfg.reports_dir, plan_path)
    n_items = len(plan["items"])
    _say(f"To file: {n_items:,} item(s), {human(sum(i['size'] for i in plan['items']))} "
         f"→ {short(plan['dest_root'])}")
    _say(f"Need your decision (staying put): {len(plan['review']):,}")
    _say("Nothing has been moved. Review the preview:")
    _say(f"  {preview}")
    if n_items:
        _say("When it looks right, approve with:")
        _say(f"  python3 -m organizer apply {plan_path}")
    if args.open and sys.platform == "darwin":
        subprocess.run(["open", str(preview)], check=False)
    return 0


def cmd_duplicates(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    scan_id = latest_scan_id(conn)
    scan_row = conn.execute("SELECT finished_at FROM scans WHERE id=?", (scan_id,)).fetchone()
    hashed = conn.execute("SELECT COUNT(*) FROM files WHERE scan_id=? AND full_hash IS NOT NULL",
                          (scan_id,)).fetchone()[0]
    if not hashed:
        _say("The latest scan didn't check duplicates. Run `python3 -m organizer audit` first.")
        return 1
    plan = build_duplicates_plan(conn, scan_id, cfg, scan_row["finished_at"])
    if not plan["items"]:
        _say("No duplicates found in the latest scan.")
        return 0
    plan_path = save_plan(plan, cfg.state_dir / "plans", "trash-duplicates")
    preview = write_preview(render_duplicates_preview(plan, plan_path), cfg.reports_dir, plan_path)
    _say(f"{len(plan['sets']):,} duplicate set(s): {len(plan['items']):,} extra copies, "
         f"{human(sum(i['size'] for i in plan['items']))}. One copy of each is kept.")
    _say("Nothing has been moved. Review the preview:")
    _say(f"  {preview}")
    _say("When it looks right, approve with:")
    _say(f"  python3 -m organizer apply {plan_path}")
    if args.open and sys.platform == "darwin":
        subprocess.run(["open", str(preview)], check=False)
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    scan_id = latest_scan_id(conn)
    scan_row = conn.execute("SELECT finished_at FROM scans WHERE id=?", (scan_id,)).fetchone()
    scanned = {str(r) for r in cfg.roots}
    cfg.intake_folders = [p for p in cfg.intake_folders if str(p) in scanned]
    plan = build_cleanup_plan(conn, scan_id, cfg, scan_row["finished_at"], keep=args.keep)
    if not plan["items"]:
        _say("Nothing to clean up: no installers or archives to trash, and theme/editor "
             "versions are already in place.")
        return 0
    plan_path = save_plan(plan, cfg.state_dir / "plans", "cleanup-installers-archives")
    preview = write_preview(render_cleanup_preview(plan, plan_path), cfg.reports_dir, plan_path)
    mp, tp = split_plan(plan)
    _say(f"Keep newest {args.keep} per theme/editor family: "
         f"{len(mp['items']) + len(plan['kept_in_place']):,} file(s). "
         f"To Trash: {len(tp['items']):,} file(s), {human(sum(i['size'] for i in tp['items']))}.")
    _say("Nothing has been moved. Review the preview:")
    _say(f"  {preview}")
    _say("When it looks right, approve with:")
    _say(f"  python3 -m organizer apply {plan_path}")
    if args.open and sys.platform == "darwin":
        subprocess.run(["open", str(preview)], check=False)
    return 0


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

    lb = sub.add_parser("labels", help="Preview shipping labels to move to Trash")
    lb.add_argument("--config", type=Path, default=Path("config.json"))
    lb.add_argument("--open", action="store_true")
    lb.set_defaults(func=cmd_labels)

    sc = sub.add_parser("screenshots", help="Preview moving screenshots into one folder")
    sc.add_argument("--config", type=Path, default=Path("config.json"))
    sc.add_argument("--open", action="store_true")
    sc.set_defaults(func=cmd_screenshots)

    so = sub.add_parser("sort", help="Preview filing Memobrick items from Downloads/Desktop")
    so.add_argument("--config", type=Path, default=Path("config.json"))
    so.add_argument("--open", action="store_true")
    so.add_argument("--no-vision", action="store_true",
                    help="don't look inside images (sort by names only)")
    so.set_defaults(func=cmd_sort)

    du = sub.add_parser("duplicates", help="Preview trashing extra copies of duplicates")
    du.add_argument("--config", type=Path, default=Path("config.json"))
    du.add_argument("--open", action="store_true")
    du.set_defaults(func=cmd_duplicates)

    cl = sub.add_parser("cleanup", help="Preview trashing installers/archives (keeps newest theme versions)")
    cl.add_argument("--config", type=Path, default=Path("config.json"))
    cl.add_argument("--keep", type=int, default=5, help="newest versions to keep per family")
    cl.add_argument("--open", action="store_true")
    cl.set_defaults(func=cmd_cleanup)

    ap = sub.add_parser("apply", help="Carry out a reviewed plan")
    ap.add_argument("plan", type=Path)
    ap.add_argument("--skip", help="item numbers to keep, e.g. 3,17,20-25")
    ap.add_argument("--config", type=Path, default=Path("config.json"))
    ap.set_defaults(func=cmd_apply)

    u = sub.add_parser("undo", help="Put back everything a run changed")
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
