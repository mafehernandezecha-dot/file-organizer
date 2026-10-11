"""Look inside images with Apple's on-device Vision framework.

A small Swift helper (vision_analyze.swift) is compiled once with the Command
Line Tools' swiftc into <state>/bin. If that isn't possible, everything still
works — images are just sorted by name and the existing rules.
Results are cached in the index by (path, size, mtime).
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from organizer.classify import A, B, F, Decision, _has

SWIFT_SRC = Path(__file__).with_name("vision_analyze.swift")

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS vision (
    path  TEXT PRIMARY KEY,
    size  INTEGER NOT NULL,
    mtime REAL NOT NULL,
    data  TEXT NOT NULL
);
"""

Say = Callable[[str], None]


def ensure_tool(state_dir: Path, say: Say) -> Optional[Path]:
    if sys.platform != "darwin":
        return None
    out = state_dir / "bin" / "vision-analyze"
    if out.exists() and out.stat().st_mtime >= SWIFT_SRC.stat().st_mtime:
        return out
    swiftc = shutil.which("swiftc")
    if not swiftc:
        say("! Image recognition needs Apple's Command Line Tools (swiftc); "
            "sorting images by name only.")
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    say("Preparing Apple image recognition (one time, about a minute)…")
    try:
        res = subprocess.run([swiftc, "-O", str(SWIFT_SRC), "-o", str(out)],
                             capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as e:
        say(f"! Couldn't prepare image recognition ({e}); sorting images by name only.")
        return None
    if res.returncode != 0:
        log = state_dir / "vision-build-error.txt"
        log.write_text(res.stderr or res.stdout or "unknown error", encoding="utf-8")
        say("! Couldn't prepare image recognition; sorting images by name only.")
        say(f"  Details saved in {log} — share it if you'd like this fixed.")
        return None
    return out


def _cache(conn: sqlite3.Connection) -> None:
    conn.executescript(CACHE_SCHEMA)


def analyze_images(
    conn: sqlite3.Connection, tool: Optional[Path], rows: Iterable[sqlite3.Row], say: Say,
    runner: Optional[Callable[[List[str]], Iterable[str]]] = None,
) -> Dict[str, Dict]:
    """path -> analysis dict. Uses the cache; runs the tool for the rest."""
    _cache(conn)
    rows = [r for r in rows if "\n" not in r["path"]]
    results: Dict[str, Dict] = {}
    todo: List[sqlite3.Row] = []
    for r in rows:
        hit = conn.execute("SELECT size, mtime, data FROM vision WHERE path=?",
                           (r["path"],)).fetchone()
        if hit and hit["size"] == r["size"] and abs(hit["mtime"] - r["mtime"]) < 1:
            results[r["path"]] = json.loads(hit["data"])
        else:
            todo.append(r)
    if not todo or (tool is None and runner is None):
        return results
    say(f"Looking inside {len(todo):,} image(s) on this Mac…")
    by_path = {r["path"]: r for r in todo}
    run = runner or (lambda paths: _run_tool(tool, paths))
    done = 0
    for line in run(list(by_path)):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        path = data.get("path")
        if path not in by_path:
            continue
        results[path] = data
        if not data.get("error"):
            r = by_path[path]
            conn.execute("INSERT OR REPLACE INTO vision (path, size, mtime, data) VALUES (?,?,?,?)",
                         (path, r["size"], r["mtime"], json.dumps(data)))
        done += 1
        if done % 50 == 0:
            say(f"  … {done:,} / {len(todo):,}")
            conn.commit()
    conn.commit()
    return results


def _run_tool(tool: Path, paths: List[str]) -> Iterable[str]:
    proc = subprocess.Popen([str(tool)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)

    def feed():
        try:
            for p in paths:
                proc.stdin.write(p + "\n")
            proc.stdin.close()
        except BrokenPipeError:
            pass

    threading.Thread(target=feed, daemon=True).start()
    for line in proc.stdout:
        yield line
    proc.wait()


# --- deciding from what the image shows ---------------------------------------

PROMO = (r"\d+\s?%\s?off", "off", "sale", "discount", "code", r"free[\s_\-]*shipping",
         r"order[\s_\-]*now", r"shop[\s_\-]*now", "gift", r"limited[\s_\-]*time",
         r"buy[\s_\-]*now", "offer", "promo", "descuento", "oferta", "envío gratis")
PRODUCT_TOKENS = {"toy", "toys", "lego", "block", "blocks", "brick", "bricks", "mosaic",
                  "frame", "frames", "puzzle", "craft", "artwork", "painting", "canvas"}
DOCUMENT_TOKENS = {"document", "receipt", "paper", "text", "printed_page", "screenshot"}


def _label_tokens(v: Dict) -> set:
    out = set()
    for pair in v.get("labels", []):
        if pair and float(pair[1]) >= 0.2:
            out.add(pair[0].lower())
            out.update(re.split(r"[_\s]+", pair[0].lower()))
    return out


def decide_from_vision(v: Dict) -> Optional[Decision]:
    if not v or v.get("error"):
        return None
    text = (v.get("text") or "").lower()
    words = len(text.split())
    colors = v.get("colors", -1)
    faces = v.get("faces", 0)
    alpha = bool(v.get("alpha"))
    tokens = _label_tokens(v)
    small_side = min(v.get("w", 0), v.get("h", 0))

    if "memobrick" in text.replace(" ", "") and (alpha or 0 < colors <= 64 or words <= 4):
        return Decision(f"{A}/Logos & Graphics", 0.8, "shows the Memobrick name as a graphic")
    if alpha and faces == 0 and 0 < colors <= 64:
        return Decision(f"{A}/Logos & Graphics", 0.7, "transparent graphic with few colors")
    if words >= 4 and _has(text, *PROMO):
        return Decision(f"{F}/Ad Creatives", 0.75, "image with promotional text")
    if faces == 0 and 0 < colors <= 40 and small_side >= 200 and words < 6:
        return Decision(f"{B}/Mosaic Designs", 0.65, "brick-style limited color palette")
    if words >= 12:
        return Decision(f"{F}/Graphics & Flyers", 0.65, "image with a lot of text")
    if faces == 0 and tokens & PRODUCT_TOKENS:
        return Decision(f"{F}/Product Photos", 0.65,
                        f"looks like a product shot ({', '.join(sorted(tokens & PRODUCT_TOKENS))})")
    if faces > 0:
        return Decision(f"{F}/Photos with People", 0.6,
                        f"photo with {faces} face(s) — no one is identified")
    if tokens & DOCUMENT_TOKENS:
        return Decision("", 0.0, "photo of a document — please decide")
    return None


def is_generic(dest: str) -> bool:
    """Destinations where a look inside the picture can do better."""
    return (not dest) or dest.startswith(f"{F}/Photos/") or dest.endswith("Designs (Canva)")

