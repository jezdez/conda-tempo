#!/usr/bin/env python3
"""Exploratory bench: py-rattler extract vs cps extract.

The cps author has expressed interest in folding the cph API into cps
and deprecating cph. A related but separate question is: could cps
itself delegate its extract path to a Rust implementation? py-rattler
(https://pypi.org/project/py-rattler/) is the natural candidate — it
ships ``rattler.package_streaming.extract(path, dest)`` as a thin
Python wrapper over the Rust ``rattler_package_streaming`` crate,
already used by the mamba ecosystem.

This bench compares:

  * cps_current       conda_package_streaming.extract.extract(pkg, dest)
  * rattler           rattler.package_streaming.extract(pkg, dest)
  * rattler_also      rattler (second run to surface warm-file-cache effects)

Measured on the same 5 real .conda archives used by S8 / S15.

Prerequisites: ``pip install py-rattler`` into the pixi env (the
pixi-installed cps env doesn't have py-rattler by default).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
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
        raise RuntimeError("No .conda packages in cache.")
    tmp_root = Path(os.environ.get("CONDA_BENCH_TMPDIR", tempfile.gettempdir()))
    dest_root = tmp_root / f"s16-dest-{n}"
    dest_root.mkdir(parents=True, exist_ok=True)
    _FIXTURE_CACHE[n] = (packages, dest_root)
    return packages, dest_root


def _clear_dests(dest_root: Path, count: int) -> list[str]:
    try:
        shutil.rmtree(dest_root)
    except FileNotFoundError:
        pass
    dest_root.mkdir(parents=True, exist_ok=True)
    dests = []
    for i in range(count):
        d = dest_root / f"pkg-{i:03d}"
        d.mkdir(parents=True, exist_ok=True)
        dests.append(str(d))
    return dests


def _bench_cps(packages, dest_root):
    from conda_package_streaming.extract import extract

    dests = _clear_dests(dest_root, len(packages))
    for pkg, dst in zip(packages, dests):
        extract(pkg, dest_dir=dst)


def _bench_rattler(packages, dest_root):
    from rattler.package_streaming import extract

    dests = _clear_dests(dest_root, len(packages))
    for pkg, dst in zip(packages, dests):
        extract(pkg, dst)


def register_memray(n: int) -> None:
    packages, dest_root = _setup(n)
    _bench_rattler(packages, dest_root)


def main() -> int:
    import pyperf

    def _forward(cmd, args):
        cmd.extend(("-N", str(args.count)))

    runner = pyperf.Runner(add_cmdline_args=_forward)
    runner.argparser.add_argument(
        "-N", "--count", type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "5")),
        help="number of .conda archives per sample (default: 5)",
    )
    args = runner.parse_args()
    n = args.count
    packages, dest_root = _setup(n)

    runner.metadata["s16_n"] = str(n)
    runner.bench_func(
        f"s16_cps_current_n{n}",
        _bench_cps, packages, dest_root,
    )
    runner.bench_func(
        f"s16_rattler_n{n}",
        _bench_rattler, packages, dest_root,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
