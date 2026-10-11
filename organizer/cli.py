"""Command line: python3 -m organizer <command>

Phase 1 commands are read-only with respect to your files. The only things
written are the index and reports inside the state dir (~/.file-organizer).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from organizer import __version__
from organizer.analysis import build_audit, human
from organizer.config import load_config
from organizer.duplicates import find_duplicates
from organizer.index import connect, latest_scan_id
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
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, LookupError) as e:
        _say(f"Error: {e}")
        return 2
