"""Smart File Organization Agent — local-first, safety-first.

Phase 1 (this release) is strictly read-only: it scans approved folders,
builds a local SQLite index, and writes an audit report. It contains no
code that moves, renames, or deletes user files.
"""

__version__ = "0.1.0"
