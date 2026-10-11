"""Configuration: which folders are approved, and what is always off-limits."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

DEFAULT_STATE_DIR = Path.home() / ".file-organizer"

# Directory names never descended into, anywhere. Hidden entries (".*") are
# skipped separately. These cover system folders, app data, sync internals,
# credentials, and build caches.
ALWAYS_EXCLUDED_NAMES = {
    "Library",
    "Applications",
    "System",
    "Volumes",
    "private",
    "node_modules",
    "__pycache__",
    "venv",
    ".venv",
    "Pods",
    "DerivedData",
    "$RECYCLE.BIN",
    "System Volume Information",
}

# Name fragments that mark credentials/secrets. Matching folders are skipped
# entirely; matching files are counted but never opened or hashed.
SENSITIVE_NAME_FRAGMENTS = (
    "keychain",
    "password",
    "passwd",
    "credential",
    "secret",
    "private key",
    "id_rsa",
    "id_ed25519",
    ".pem",
    ".p12",
    ".pfx",
    ".kdbx",
    ".1pux",
    ".env",
)

# macOS "packages": folders that Finder shows as a single file. The agent
# treats them as one opaque item and never looks inside, so libraries and
# editing projects can't be broken.
PACKAGE_EXTENSIONS = {
    ".app", ".photoslibrary", ".photolibrary", ".imovielibrary", ".fcpbundle",
    ".fcpxbundle", ".logicx", ".band", ".prproj", ".aep", ".rtfd", ".pages",
    ".numbers", ".key", ".bundle", ".framework", ".plugin", ".kext",
    ".musiclibrary", ".tvlibrary", ".xcodeproj", ".xcworkspace", ".sparsebundle",
    ".pkg", ".mpkg", ".lrlibrary", ".lrdata", ".cocatalog", ".sketch",
}

# Files whose presence marks a folder as an active software project. The
# project root is recorded as one unit and its contents are not reorganized.
PROJECT_MARKERS = {
    ".git", "package.json", "pyproject.toml", "requirements.txt", "Gemfile",
    "Cargo.toml", "go.mod", "composer.json", "Podfile", "pom.xml",
    "build.gradle", "shopify.theme.toml", "config/settings_schema.json",
    "Makefile", "setup.py",
}


@dataclass
class Config:
    roots: List[Path]
    state_dir: Path = DEFAULT_STATE_DIR
    extra_excluded: List[str] = field(default_factory=list)
    stale_days: int = 365
    large_file_mb: int = 500
    hash_duplicates: bool = True
    # Personal photos live in Apple Photos; loose photo files are Memobrick
    # assets. When true, loose images count as business. Screenshots are a
    # separate kind and never count.
    loose_photos_are_business: bool = False
    order_file_pattern: str = ""
    label_file_pattern: str = ""
    screenshots_folder: Path = Path.home() / "Pictures" / "Screenshots"
    business_keywords: List[str] = field(
        default_factory=lambda: ["memobrick", "mosaic", "brick", "baseplate", "shopify"]
    )

    @property
    def db_path(self) -> Path:
        return self.state_dir / "index.db"

    @property
    def reports_dir(self) -> Path:
        return self.state_dir / "reports"


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p))).resolve()


def load_config(path: Path) -> Config:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    roots = [_expand(r) for r in data.get("approved_folders", [])]
    if not roots:
        raise ValueError("config has no 'approved_folders' — nothing is authorized to scan")
    cfg = Config(roots=roots)
    if "state_dir" in data:
        cfg.state_dir = _expand(data["state_dir"])
    cfg.extra_excluded = list(data.get("excluded_folders", []))
    cfg.stale_days = int(data.get("stale_days", cfg.stale_days))
    cfg.large_file_mb = int(data.get("large_file_mb", cfg.large_file_mb))
    cfg.hash_duplicates = bool(data.get("hash_duplicates", True))
    cfg.loose_photos_are_business = bool(data.get("loose_photos_are_business", False))
    cfg.order_file_pattern = str(data.get("order_file_pattern", ""))
    cfg.label_file_pattern = str(data.get("label_file_pattern", ""))
    if "screenshots_folder" in data:
        cfg.screenshots_folder = _expand(data["screenshots_folder"])
    if "business_keywords" in data:
        cfg.business_keywords = [k.lower() for k in data["business_keywords"]]
    return cfg


def is_sensitive_name(name: str) -> bool:
    low = name.lower()
    return any(frag in low for frag in SENSITIVE_NAME_FRAGMENTS)
