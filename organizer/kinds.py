"""Coarse file kinds from extensions, plus filename-pattern helpers.

Phase 1 only needs coarse kinds for the audit. Content-based classification
with confidence scores comes in Phase 3.
"""

from __future__ import annotations

import re
from pathlib import PurePath

KIND_BY_EXT = {
    "image": {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".webp", ".tif", ".tiff",
              ".bmp", ".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".svg"},
    "video": {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm", ".mts", ".m2ts", ".3gp", ".wmv"},
    "audio": {".mp3", ".m4a", ".wav", ".aiff", ".aif", ".flac", ".aac", ".ogg"},
    "pdf": {".pdf"},
    "document": {".doc", ".docx", ".pages", ".rtf", ".rtfd", ".txt", ".md", ".odt"},
    "spreadsheet": {".xls", ".xlsx", ".numbers", ".csv", ".tsv", ".ods"},
    "presentation": {".ppt", ".pptx", ".key", ".odp"},
    "design": {".psd", ".ai", ".indd", ".sketch", ".fig", ".xd", ".eps", ".afdesign",
               ".afphoto", ".procreate", ".blend", ".stl", ".obj"},
    "video_project": {".prproj", ".fcpbundle", ".fcpxbundle", ".imovielibrary", ".aep",
                      ".drp", ".veg", ".capcut"},
    "archive": {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"},
    "installer": {".dmg", ".pkg", ".mpkg", ".app", ".exe", ".msi", ".iso"},
    "code": {".html", ".htm", ".css", ".js", ".ts", ".tsx", ".jsx", ".json", ".liquid",
             ".py", ".rb", ".php", ".sh", ".xml", ".yml", ".yaml", ".scss"},
    "font": {".ttf", ".otf", ".woff", ".woff2"},
    "email": {".eml", ".msg", ".mbox"},
}

_EXT_TO_KIND = {ext: kind for kind, exts in KIND_BY_EXT.items() for ext in exts}


def kind_for(name: str) -> str:
    return _EXT_TO_KIND.get(PurePath(name).suffix.lower(), "other")


_SCREENSHOT_RE = re.compile(r"^(screen ?shot|screenshot|captura de pantalla|cleanshot)", re.I)


def is_screenshot(name: str) -> bool:
    return bool(_SCREENSHOT_RE.match(name))


# Version / copy markers people add to filenames. Stripping them yields a
# "base name" so Proposal_FINAL2.pdf and Proposal v3 (1).pdf group together.
_END = r"(?=[\s_\-]|$)"  # token must end at a separator or end of stem
_VERSION_TOKENS = re.compile(
    r"\s*\(\d+\)$"                                                  # " (1)"
    r"|[\s_\-]+copy(\s*\d+)?" + _END +                              # " copy", " copy 2"
    r"|[\s_\-]+(final[\s_\-]?final|finale|final)\d*" + _END +         # _final, final2
    r"|[\s_\-]+(new|old|updated|update|latest|edited|edit|revised|rev\d*|draft|approved)" + _END +
    r"|[\s_\-]+v(er(sion)?)?[\s_\-]?\d+(\.\d+)*" + _END +              # v2, V03, ver 1.2
    r"|[\s_\-]+\d{1,2}$",                                             # trailing " 2" (not years)
    re.I,
)

_VERSION_HINT = re.compile(
    r"(final|v(er(sion)?)?[\s_\-]?\d|\(\d+\)|copy|updated|approved|draft|revised|_new\b)", re.I
)


def base_name(name: str) -> str:
    """Normalized stem + extension used to detect likely version families."""
    p = PurePath(name)
    stem = p.stem
    prev = None
    while prev != stem:
        prev = stem
        stem = _VERSION_TOKENS.sub("", stem).strip(" _-.")
    stem = re.sub(r"[\s_\-.]+", " ", stem).strip().lower()
    return f"{stem}{p.suffix.lower()}"


def has_version_marker(name: str) -> bool:
    return bool(_VERSION_HINT.search(PurePath(name).stem))
