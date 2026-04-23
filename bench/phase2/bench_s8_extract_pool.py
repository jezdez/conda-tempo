#!/usr/bin/env python3
"""S8 microbenchmark: extract-pool concurrency.

Background (from Track B suspects):

    S8 | Extract pool fixed at min(cpu, 3)
         conda/core/package_cache_data.py:73-74
         # On the machines we tested, extraction doesn't get any
         # faster after 3 threads
         EXTRACT_THREADS = min(os.cpu_count() or 1, 3) if THREADSAFE_EXTRACT else 1

That comment is from 2020-era conda (classic tarball extract through
libarchive). The current extraction path goes through::

    conda_package_handling.api.extract()
      -> streaming._extract()
      -> conda_package_streaming.extract.extract_stream()
      -> stdlib tarfile.extractall() (with zstd decompression for .conda)

No libarchive, all Python stdlib. The three-thread cap may or may not
still be right on modern SSD + multi-core machines.

This microbenchmark extracts N real ``.conda`` packages (pulled from
the host's package cache) under different thread counts and reports
wall time per strategy:

  * serial   — one at a time
  * K=2, K=4, K=6, K=8, K=12 — ``ThreadPoolExecutor(K)``

Expected on modern Linux NVMe: speedup past K=3 up to ~cpu_count,
bounded by zstd decompression CPU. On macOS APFS: less clear.

``register_pyperf(runner, n)`` — n = max number of packages to use.
``register_memray(n)``        — single serial extraction, allocation profile.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from fixtures import conda_packages_from_cache

_FIXTURE_CACHE: dict = {}


def _setup(n: int):
    if n in _FIXTURE_CACHE:
        return _FIXTURE_CACHE[n]
    packages = conda_packages_from_cache(max_count=n)
    if not packages:
        raise RuntimeError(
            "No .conda packages found in the package cache. "
            "Run `conda create -n tmp -c conda-forge -y python=3.13 "
            "pandas scikit-learn jupyter` first to populate it.",
        )
    tmp_root = Path(
        os.environ.get("CONDA_BENCH_TMPDIR", tempfile.gettempdir())
    )
    dest_root = tmp_root / f"s8-extract-{n}"
    dest_root.mkdir(parents=True, exist_ok=True)
    _FIXTURE_CACHE[n] = (packages, dest_root)
    return packages, dest_root


def _extract_one(package_path: str, dest_dir: str) -> None:
    from conda_package_handling.api import extract

    # cph's extract writes info/ and pkg/ components underneath dest_dir
    # and returns None on success (raises on failure).
    extract(package_path, dest_dir=dest_dir)


def _clear_dests(dest_root: Path, count: int) -> list[str]:
    """Wipe + recreate per-package dest dirs for one bench iteration."""
    try:
        shutil.rmtree(dest_root)
    except FileNotFoundError:
        pass
    dest_root.mkdir(parents=True, exist_ok=True)
    dests = [str(dest_root / f"pkg-{i:03d}") for i in range(count)]
    for d in dests:
        os.makedirs(d, exist_ok=True)
    return dests


def _bench_serial(packages: list[str], dest_root: Path) -> None:
    dests = _clear_dests(dest_root, len(packages))
    for pkg, dst in zip(packages, dests):
        _extract_one(pkg, dst)


def _bench_parallel(packages: list[str], dest_root: Path, max_workers: int) -> None:
    dests = _clear_dests(dest_root, len(packages))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(_extract_one, packages, dests))


def register_memray(n: int) -> None:
    packages, dest_root = _setup(n)
    _bench_serial(packages, dest_root)


def main() -> int:
    import pyperf

    def _forward_records(cmd, args):
        cmd.extend(("-N", str(args.count)))

    runner = pyperf.Runner(add_cmdline_args=_forward_records)
    runner.argparser.add_argument(
        "-N", "--count", type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "10")),
        help="number of .conda packages to extract per sample (default: 10)",
    )
    args = runner.parse_args()
    n = args.count

    runner.metadata["s8_n"] = str(n)
    packages, dest_root = _setup(n)

    runner.bench_func(
        f"s8_extract_serial_n{n}",
        _bench_serial, packages, dest_root,
    )
    for workers in (2, 4, 6, 8, 12):
        runner.bench_func(
            f"s8_extract_parallel_n{n}_k{workers}",
            _bench_parallel, packages, dest_root, workers,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
