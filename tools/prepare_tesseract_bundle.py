#!/usr/bin/env python3
"""Prepare a minimal Tesseract runtime bundle for PyInstaller."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = PROJECT_ROOT / "tesseract_bin"
DEFAULT_OUTPUT = PROJECT_ROOT / "build_assets" / "tesseract_bin"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def collect_dependencies(executable: Path, source_dir: Path) -> dict[str, Path]:
    try:
        import pefile
    except ImportError as exc:
        raise SystemExit(
            "pefile is required to prepare the bundle: pip install pefile"
        ) from exc

    available = {
        path.name.lower(): path
        for path in source_dir.iterdir()
        if path.is_file()
    }
    required: dict[str, Path] = {}
    queue = [executable.name]

    while queue:
        name = queue.pop()
        key = name.lower()
        if key in required or key not in available:
            continue

        path = available[key]
        required[key] = path
        pe = None
        try:
            pe = pefile.PE(str(path), fast_load=True)
            directories = [
                index
                for index in (
                    pefile.DIRECTORY_ENTRY.get("IMAGE_DIRECTORY_ENTRY_IMPORT"),
                    pefile.DIRECTORY_ENTRY.get("IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"),
                )
                if index is not None
            ]
            pe.parse_data_directories(directories=directories)
            for attribute in (
                "DIRECTORY_ENTRY_IMPORT",
                "DIRECTORY_ENTRY_DELAY_IMPORT",
            ):
                for entry in getattr(pe, attribute, []) or []:
                    dependency = entry.dll.decode("ascii", errors="ignore")
                    if dependency.lower() in available:
                        queue.append(dependency)
        except Exception as exc:
            print(f"Warning: could not inspect {path.name}: {exc}", file=sys.stderr)
        finally:
            if pe is not None:
                pe.close()

    return required


def main() -> int:
    args = parse_args()
    source = args.source.resolve()
    output = args.output.resolve()

    executable = source / "tesseract.exe"
    trained_data = source / "tessdata" / "eng.traineddata"
    if not executable.is_file():
        raise SystemExit(f"Missing Tesseract executable: {executable}")
    if not trained_data.is_file():
        raise SystemExit(f"Missing English language data: {trained_data}")
    if output == PROJECT_ROOT or source in output.parents or output in source.parents:
        raise SystemExit(f"Refusing to use unsafe output directory: {output}")

    dependencies = collect_dependencies(executable, source)
    if output.exists():
        shutil.rmtree(output)
    (output / "tessdata").mkdir(parents=True)

    copied = 0
    for path in dependencies.values():
        shutil.copy2(path, output / path.name)
        copied += 1
    shutil.copy2(trained_data, output / "tessdata" / trained_data.name)

    for license_name in ("LICENSE", "AUTHORS"):
        license_path = source / "doc" / license_name
        if license_path.is_file():
            target_dir = output / "doc"
            target_dir.mkdir(exist_ok=True)
            shutil.copy2(license_path, target_dir / license_name)

    total_bytes = sum(
        path.stat().st_size for path in output.rglob("*") if path.is_file()
    )
    print(
        f"Prepared {copied} runtime files + eng.traineddata "
        f"({total_bytes / 1024 / 1024:.1f} MiB) in {output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
