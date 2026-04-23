#!/usr/bin/env python3
"""S15 microbenchmark: ``cph.api.extract`` dispatch overhead.

Background: S8 measures extract-pool concurrency (multiple .conda
archives extracted in parallel). This benchmark isolates a single
``cph.api.extract`` call and measures how much wall time is spent in
cph's dispatch layer (argument normalisation, format.extract()
lookup, makedirs check) vs. the actual
``conda_package_streaming`` work.

Comparison:

  * cph_api        - ``conda_package_handling.api.extract(pkg, dest)``
                     The full conda-installed path.
  * cps_direct     - ``conda_package_streaming.extract.extract(pkg, dest)``
                     Skip cph, go straight to cps.

If the two are within noise, cph dispatch is effectively free and no
fix is warranted. If cph adds material overhead per call, it's worth
refactoring the dispatch.

``register_pyperf(runner, n)`` — n real .conda archives to extract
(fresh dest dir per iteration).
``register_memray(n)``        — 1 cph_api extract.
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
        raise RuntimeError(
            "No .conda packages in the package cache. Run "
            "`conda create -n tmp -c conda-forge -y python=3.13 pandas` first.",
        )
    tmp_root = Path(os.environ.get("CONDA_BENCH_TMPDIR", tempfile.gettempdir()))
    dest_root = tmp_root / f"s15-dest-{n}"
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


def _bench_via_cph_api(packages, dest_root):
    from conda_package_handling.api import extract

    dests = _clear_dests(dest_root, len(packages))
    for pkg, dst in zip(packages, dests):
        extract(pkg, dest_dir=dst)


def _bench_via_cps_direct(packages, dest_root):
    from conda_package_streaming.extract import extract

    dests = _clear_dests(dest_root, len(packages))
    for pkg, dst in zip(packages, dests):
        extract(pkg, dest_dir=dst)


def register_memray(n: int) -> None:
    packages, dest_root = _setup(n)
    _bench_via_cph_api(packages, dest_root)


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

    runner.metadata["s15_n"] = str(n)
    runner.bench_func(
        f"s15_cph_api_n{n}",
        _bench_via_cph_api, packages, dest_root,
    )
    runner.bench_func(
        f"s15_cps_direct_n{n}",
        _bench_via_cps_direct, packages, dest_root,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
