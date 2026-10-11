"""Finder tags (the colored dots), read and written as the extended attribute
com.apple.metadata:_kMDItemUserTags. Only metadata changes — never the file's
contents, name, or dates.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import plistlib
import sys
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from organizer.config import PACKAGE_EXTENSIONS, is_sensitive_name
from organizer.kinds import kind_for
from organizer.scanner import _is_project, is_dataless

ATTR = "com.apple.metadata:_kMDItemUserTags"
COLORS = {"Gray": 1, "Green": 2, "Purple": 3, "Blue": 4, "Yellow": 5, "Red": 6, "Orange": 7}
IMAGE_KINDS = ("image", "screenshot")
XATTR_NOFOLLOW = 0x0001
ENOATTR = 93  # macOS "attribute not found"


class MacXattr:
    """getxattr/setxattr/removexattr from libc (macOS signatures)."""

    def __init__(self):
        self.libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        self.libc.getxattr.restype = ctypes.c_ssize_t
        self.libc.setxattr.restype = ctypes.c_int
        self.libc.removexattr.restype = ctypes.c_int
        self.libc.getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
        self.libc.setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
        self.libc.removexattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]

    def get(self, path: str) -> Optional[bytes]:
        p, n = os.fsencode(path), ATTR.encode()
        size = self.libc.getxattr(p, n, None, 0, 0, XATTR_NOFOLLOW)
        if size < 0:
            err = ctypes.get_errno()
            if err == ENOATTR:
                return None
            raise OSError(err, os.strerror(err), path)
        buf = ctypes.create_string_buffer(size)
        got = self.libc.getxattr(p, n, buf, size, 0, XATTR_NOFOLLOW)
        if got < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), path)
        return buf.raw[:got]

    def set(self, path: str, data: bytes) -> None:
        if self.libc.setxattr(os.fsencode(path), ATTR.encode(), data, len(data), 0,
                              XATTR_NOFOLLOW) != 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), path)

    def remove(self, path: str) -> None:
        if self.libc.removexattr(os.fsencode(path), ATTR.encode(), XATTR_NOFOLLOW) != 0:
            err = ctypes.get_errno()
            if err != ENOATTR:  # already gone is fine
                raise OSError(err, os.strerror(err), path)


def backend():
    return MacXattr() if sys.platform == "darwin" else None


def read_tags(path: str, xa) -> List[str]:
    raw = xa.get(path)
    if not raw:
        return []
    try:
        value = plistlib.loads(raw)
    except Exception:
        return []
    return [t for t in value if isinstance(t, str)] if isinstance(value, list) else []


def has_tag(tags: List[str], name: str) -> bool:
    return any(t.split("\n")[0] == name for t in tags)


def add_tag(path: str, name: str, xa) -> bool:
    """Add a Finder tag, keeping existing ones. False if it was already there."""
    tags = read_tags(path, xa)
    if has_tag(tags, name):
        return False
    tags.append(f"{name}\n{COLORS.get(name, 0)}")
    xa.set(path, plistlib.dumps(tags, fmt=plistlib.FMT_BINARY))
    return True


def remove_tag(path: str, name: str, xa) -> bool:
    tags = read_tags(path, xa)
    kept = [t for t in tags if t.split("\n")[0] != name]
    if len(kept) == len(tags):
        return False
    if kept:
        xa.set(path, plistlib.dumps(kept, fmt=plistlib.FMT_BINARY))
    else:
        xa.remove(path)
    return True


def is_memobrick_image(path: str) -> bool:
    return kind_for(os.path.basename(path)) in IMAGE_KINDS


def images_under(root: Path) -> Iterable[str]:
    """Image files under root; skips hidden items, symlinks, bundles, code folders."""
    stack = [str(root)]
    while stack:
        cur = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError:
            continue
        for e in entries:
            if e.name.startswith(".") or e.is_symlink():
                continue
            if e.is_dir(follow_symlinks=False):
                if Path(e.name).suffix.lower() in PACKAGE_EXTENSIONS or _is_project(e.path):
                    continue
                stack.append(e.path)
            elif e.is_file(follow_symlinks=False) and is_memobrick_image(e.path) \
                    and not is_sensitive_name(e.name):
                yield e.path


def untagged_images(root: Path, name: str, xa) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {"todo": [], "already": [], "icloud": []}
    for p in images_under(root):
        try:
            if is_dataless(os.lstat(p)):
                out["icloud"].append(p)
            elif has_tag(read_tags(p, xa), name):
                out["already"].append(p)
            else:
                out["todo"].append(p)
        except OSError:
            continue
    return out


def apply_tags(paths: Iterable[str], name: str, journal: Path, xa,
               say: Callable[[str], None] = print) -> Dict[str, int]:
    counts = {"tagged": 0, "failed": 0}
    journal.parent.mkdir(parents=True, exist_ok=True)
    with open(journal, "a", encoding="utf-8") as j:
        os.chmod(journal, 0o600)
        for p in paths:
            try:
                if add_tag(p, name, xa):
                    counts["tagged"] += 1
                    j.write(json.dumps({"status": "tagged", "path": p, "tag": name,
                                        "at": time.time()}) + "\n")
                    j.flush()
            except OSError as e:
                counts["failed"] += 1
                j.write(json.dumps({"status": "failed", "path": p, "why": str(e),
                                    "at": time.time()}) + "\n")
    return counts


def undo_tags(entries: List[Dict], xa, say: Callable[[str], None] = print) -> Dict[str, int]:
    counts = {"restored": 0, "failed": 0}
    for e in entries:
        if e.get("status") != "tagged":
            continue
        try:
            if os.path.lexists(e["path"]):
                remove_tag(e["path"], e["tag"], xa)
                counts["restored"] += 1
        except OSError as err:
            counts["failed"] += 1
            say(f"! Could not remove the tag from {e['path']}: {err}")
    return counts
