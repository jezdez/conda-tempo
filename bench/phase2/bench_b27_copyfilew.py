#!/usr/bin/env python3
"""B27 focused benchmark: native Windows copy-mode file creation.

This is intentionally stdlib-only so it can run on a bare Windows VM. It
compares conda's Windows copy fallback shape (``copyfileobj`` with a 4 MiB
buffer plus ``copystat``) against ``CopyFileW`` and Python 3.12's
``_winapi.CopyFile2`` wrapper on the same filesystem.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import platform
import shutil
import statistics
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

try:
    from _winapi import (
        COPY_FILE_ALLOW_DECRYPTED_DESTINATION,
        COPY_FILE_FAIL_IF_EXISTS,
        CopyFile2,
    )
except ImportError:
    CopyFile2 = None

BUFFER_SIZE = 4 * 1024 * 1024
DEFAULT_CASES = (
    ("1 file, 64 MiB", 1, 64 * 1024 * 1024),
    ("512 files, 64 KiB each", 512, 64 * 1024),
    ("2048 files, 1 KiB each", 2048, 1024),
)
SIZE_SWEEP_CASES = tuple(
    (f"512 files, {size // 1024} KiB each", 512, size)
    for size in (
        1024,
        4 * 1024,
        8 * 1024,
        16 * 1024,
        32 * 1024,
        64 * 1024,
        128 * 1024,
        256 * 1024,
        1024 * 1024,
    )
)

CopyFileW = ctypes.windll.kernel32.CopyFileW
CopyFileW.restype = wintypes.BOOL
CopyFileW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.BOOL,
)


def fsutil_volume_info(root: Path) -> str:
    drive = root.drive or root.anchor
    if not drive:
        return ""
    try:
        return subprocess.check_output(
            ("fsutil", "fsinfo", "volumeinfo", drive),
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""


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


def copyfilew_copy(src: Path, dst: Path) -> None:
    # B27 uses CopyFileW for the file data and leaves metadata parity to copystat.
    if not CopyFileW(str(src), str(dst), True):
        raise ctypes.WinError()
    shutil.copystat(src, dst)


def copyfile2_copy(src: Path, dst: Path) -> None:
    flags = COPY_FILE_ALLOW_DECRYPTED_DESTINATION | COPY_FILE_FAIL_IF_EXISTS
    CopyFile2(str(src), str(dst), flags)
    shutil.copystat(src, dst)


def time_case(src_paths: list[Path], dst_dir: Path, copy_func) -> float:
    reset_dest(dst_dir)
    start = time.perf_counter()
    for src in src_paths:
        copy_func(src, dst_dir / src.name)
    return time.perf_counter() - start


def run_case(root: Path, label: str, count: int, size: int, repeats: int) -> dict:
    src_paths = write_source_files(root / "sources" / f"{count}-{size}", count, size)
    copy_functions = [
        ("python_copy", python_copy),
        ("copyfilew", copyfilew_copy),
    ]
    if CopyFile2 is not None:
        copy_functions.append(("copyfile2", copyfile2_copy))
    samples = {name: [] for name, _ in copy_functions}
    for repeat in range(repeats):
        start = repeat % len(copy_functions)
        ordered = copy_functions[start:] + copy_functions[:start]
        for name, copy_func in ordered:
            samples[name].append(time_case(src_paths, root / f"dest-{name}", copy_func))

    medians = {name: statistics.median(times) for name, times in samples.items()}
    result = {
        "label": label,
        "files": count,
        "bytes_per_file": size,
        "total_bytes": count * size,
        "repeats": repeats,
        "python_copy_seconds": medians["python_copy"],
        "copyfilew_seconds": medians["copyfilew"],
        "speedup": medians["python_copy"] / medians["copyfilew"],
        "python_copy_samples": samples["python_copy"],
        "copyfilew_samples": samples["copyfilew"],
    }
    if CopyFile2 is not None:
        result.update(
            copyfile2_seconds=medians["copyfile2"],
            copyfile2_speedup=medians["python_copy"] / medians["copyfile2"],
            copyfile2_vs_copyfilew=medians["copyfile2"] / medians["copyfilew"],
            copyfile2_samples=samples["copyfile2"],
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--sweep", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    cases = SIZE_SWEEP_CASES if args.sweep else DEFAULT_CASES
    results = {
        "root": str(root),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "copyfile2_available": CopyFile2 is not None,
        "volume": fsutil_volume_info(root),
        "cases": [
            run_case(root, label, count, size, args.repeats)
            for label, count, size in cases
        ],
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
