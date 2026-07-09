#!/usr/bin/env python3
"""B28 focused benchmark: Linux FICLONE copy-mode file creation.

This is intentionally stdlib-only so it can run on a bare Ubuntu VM. It
compares conda's Linux copy fallback shape (``copyfileobj`` with a 4 MiB
buffer plus ``copystat``) against the B28 reflink shape (``FICLONE`` plus
``copystat``) on the same filesystem.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import time
from fcntl import ioctl
from pathlib import Path

FICLONE = 0x40049409
BUFFER_SIZE = 4 * 1024 * 1024
FICLONE_MIN_FILE_SIZE = 64 * 1024
DEFAULT_CASES = (
    ("1 file, 64 MiB", 1, 64 * 1024 * 1024),
    ("512 files, 64 KiB each", 512, 64 * 1024),
    ("2048 files, 1 KiB each", 2048, 1024),
)


def findmnt(path: Path) -> dict[str, str]:
    cmd = ("findmnt", "-T", str(path), "-J", "-o", "TARGET,SOURCE,FSTYPE")
    try:
        payload = json.loads(subprocess.check_output(cmd, text=True))
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return {}
    filesystems = payload.get("filesystems") or []
    return filesystems[0] if filesystems else {}


def write_source_files(src_dir: Path, count: int, size: int) -> list[Path]:
    src_dir.mkdir(parents=True, exist_ok=True)
    chunk = bytes((i % 251 for i in range(min(size, 1024 * 1024))))
    paths = []
    for index in range(count):
        path = src_dir / f"file-{index:06d}"
        if path.exists() and path.stat().st_size == size:
            paths.append(path)
            continue
        with path.open("wb") as fh:
            remaining = size
            while remaining:
                block = chunk[: min(len(chunk), remaining)]
                fh.write(block)
                remaining -= len(block)
        paths.append(path)
    return paths


def reset_dest(dst_dir: Path) -> None:
    shutil.rmtree(dst_dir, ignore_errors=True)
    dst_dir.mkdir(parents=True)


def python_copy(src: Path, dst: Path) -> None:
    with src.open("rb") as fsrc:
        with dst.open("wb") as fdst:
            shutil.copyfileobj(fsrc, fdst, BUFFER_SIZE)
    shutil.copystat(src, dst)


def ficlone_copy(src: Path, dst: Path) -> None:
    with src.open("rb") as fsrc:
        with dst.open("xb") as fdst:
            ioctl(fdst.fileno(), FICLONE, fsrc.fileno())
    shutil.copystat(src, dst)


def gated_ficlone_copy(src: Path, dst: Path) -> None:
    if src.stat().st_size < FICLONE_MIN_FILE_SIZE:
        python_copy(src, dst)
    else:
        ficlone_copy(src, dst)


def time_case(src_paths: list[Path], dst_dir: Path, copy_func) -> float:
    reset_dest(dst_dir)
    start = time.perf_counter()
    for src in src_paths:
        copy_func(src, dst_dir / src.name)
    return time.perf_counter() - start


def run_case(root: Path, label: str, count: int, size: int, repeats: int) -> dict:
    src_paths = write_source_files(root / "sources" / f"{count}-{size}", count, size)
    copy_dir = root / "dest-copy"
    reflink_dir = root / "dest-reflink"
    copy_times = []
    reflink_times = []
    gated_times = []
    for _ in range(repeats):
        copy_times.append(time_case(src_paths, copy_dir, python_copy))
        reflink_times.append(time_case(src_paths, reflink_dir, ficlone_copy))
        gated_times.append(
            time_case(src_paths, root / "dest-gated-reflink", gated_ficlone_copy)
        )
    copy_median = statistics.median(copy_times)
    reflink_median = statistics.median(reflink_times)
    gated_median = statistics.median(gated_times)
    return {
        "label": label,
        "files": count,
        "bytes_per_file": size,
        "total_bytes": count * size,
        "repeats": repeats,
        "python_copy_seconds": copy_median,
        "ficlone_seconds": reflink_median,
        "gated_ficlone_seconds": gated_median,
        "speedup": copy_median / reflink_median if reflink_median else None,
        "gated_speedup": copy_median / gated_median if gated_median else None,
        "python_copy_samples": copy_times,
        "ficlone_samples": reflink_times,
        "gated_ficlone_samples": gated_times,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    mount = findmnt(root)
    results = {
        "root": str(root),
        "mount": mount,
        "cases": [
            run_case(root, label, count, size, args.repeats)
            for label, count, size in DEFAULT_CASES
        ],
    }
    if args.json_out:
        args.json_out.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
