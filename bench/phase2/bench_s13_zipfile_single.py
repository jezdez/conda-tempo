#!/usr/bin/env python3
"""S13 microbenchmark: ``stream_conda_component`` double ZipFile parse.

Background (from Track B suspects):

    S13 | stream_conda_component calls zipfile.ZipFile twice per .conda
          (once per component: pkg and info)
          conda_package_streaming/package_streaming.py:138

For every .conda file, cps constructs ``zipfile.ZipFile(fileobj)``
once to stream the ``pkg-<stem>`` component and again to stream the
``info-<stem>`` component. ZipFile's constructor seeks to the
end-of-file record and parses the central directory — constant work
per file but duplicated across components.

This bench measures the cost of opening a ZipFile against a real
.conda archive and compares single-parse vs double-parse patterns.
"""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from fixtures import conda_packages_from_cache

_FIXTURE_CACHE: dict = {}


def _setup(n: int):
    """Return up to ``n`` real .conda packages for the bench fixture."""
    if n in _FIXTURE_CACHE:
        return _FIXTURE_CACHE[n]
    packages = conda_packages_from_cache(max_count=n)
    if not packages:
        raise RuntimeError(
            "No .conda packages found in the package cache. "
            "Run `conda create -n tmp -c conda-forge -y python=3.13 pandas` first.",
        )
    _FIXTURE_CACHE[n] = packages
    return packages


def _bench_current_double(packages: list[str]) -> int:
    """Open each package's ZipFile twice (cps shipping pattern)."""
    count = 0
    for pkg in packages:
        with zipfile.ZipFile(pkg) as zf1:
            count += len(zf1.namelist())
        with zipfile.ZipFile(pkg) as zf2:
            count += len(zf2.namelist())
    return count


def _bench_proposed_single(packages: list[str]) -> int:
    """Open each package's ZipFile once, reuse for both components."""
    count = 0
    for pkg in packages:
        with zipfile.ZipFile(pkg) as zf:
            names = zf.namelist()
            count += len(names)
            count += len(names)
    return count


def register_memray(n: int) -> None:
    packages = _setup(n)
    _bench_current_double(packages)


def main() -> int:
    import pyperf

    def _forward(cmd, args):
        cmd.extend(("-N", str(args.count)))

    runner = pyperf.Runner(add_cmdline_args=_forward)
    runner.argparser.add_argument(
        "-N", "--count", type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "10")),
        help="number of .conda archives to open (default: 10)",
    )
    args = runner.parse_args()
    n = args.count
    packages = _setup(n)

    a = _bench_current_double(packages)
    b = _bench_proposed_single(packages)
    assert a == b, (a, b)

    runner.metadata["s13_n"] = str(n)
    runner.bench_func(
        f"s13_double_zipfile_n{n}",
        _bench_current_double, packages,
    )
    runner.bench_func(
        f"s13_single_zipfile_n{n}",
        _bench_proposed_single, packages,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
