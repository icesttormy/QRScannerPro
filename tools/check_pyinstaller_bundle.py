#!/usr/bin/env python3
"""Fail the build if PyInstaller pulls DLLs from the Codex runtime cache."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path


FORBIDDEN_SOURCE_MARKERS = ("codex-runtimes", "poppler")
REQUIRED_ARCHIVE_SUFFIXES = (
    r"PySide6\Qt6Core.dll",
    r"PySide6\QtCore.pyd",
    r"PySide6\pyside6.abi3.dll",
    r"shiboken6\shiboken6.abi3.dll",
)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("toc", type=Path)
    args = parser.parse_args()

    tree = ast.literal_eval(args.toc.read_text(encoding="utf-8"))
    values = list(strings(tree))
    normalized = [value.replace("/", "\\") for value in values]

    bad_sources = [
        value
        for value in values
        if any(marker in value.lower() for marker in FORBIDDEN_SOURCE_MARKERS)
    ]
    if bad_sources:
        print("Bundle contains DLLs from an unsafe runtime cache:", file=sys.stderr)
        for value in bad_sources:
            print(f"  {value}", file=sys.stderr)
        return 1

    missing = [
        suffix
        for suffix in REQUIRED_ARCHIVE_SUFFIXES
        if not any(value.endswith(suffix) for value in normalized)
    ]
    if missing:
        print("Bundle is missing required Qt files:", file=sys.stderr)
        for suffix in missing:
            print(f"  {suffix}", file=sys.stderr)
        return 1

    print("PyInstaller bundle source check passed")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main())
