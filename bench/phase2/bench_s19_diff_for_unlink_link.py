#!/usr/bin/env python3
"""S19: direct microbenchmark of ``diff_for_unlink_link_precs``.

``conda.core.solve.diff_for_unlink_link_precs`` is the post-solve
function that compares the solver's proposed ``final_precs`` to the
current prefix state and produces the (unlink, link) tuples the
transaction executor uses. Its cost is dominated by two ``PrefixGraph``
calls:

1. ``PrefixGraph(PrefixData(prefix).iter_records()).graph`` on the
   full prefix (``solve.py:1424``). O(N) in records, loads conda-meta
   from disk, and internally runs the MatchSpec-match loop for every
   dep of every record.
2. Implicit sort over ``previous_records`` inside the final
   ``unlink_precs`` / ``link_precs`` tuple builder
   (``solve.py:1465-1468``). B1 fixed the quadratic shape of this
   sort.

This microbench exercises (1) and (2) in isolation, with no solver,
no network, no fetch/extract. That lets us A/B the pure-Python
``PrefixGraph`` path against the rattler-backed swap (Trial 3) on
the exact operation where PrefixGraph should dominate wall time.

Fixture:

* A synthetic prefix of N records on disk under ``$CONDA_BENCH_TMPDIR``
  using the realistic generator from ``seed_big_prefix`` (exponential
  fan-out, version-constrained deps, varied builds/subdirs).
* A "final solution" constructed as ``prefix_records`` plus K new
  records and minus a handful of existing records, so
  ``diff_for_unlink_link_precs`` has non-trivial unlink + link sets.

Usage::

    python bench_s19_diff_for_unlink_link.py -N 5000

    CONDA_BENCH_N=50000 python bench_s19_diff_for_unlink_link.py \\
        --output ../../data/phase2/s19/pyperf_n50000.json
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from seed_big_prefix import seed as _seed  # noqa: E402

_FIXTURE_CACHE: dict = {}


def _build_fixture(n: int):
    """Build (prefix_path, prefix_records, final_precs) once per worker.

    The prefix is materialised on disk under ``$CONDA_BENCH_TMPDIR``
    because ``diff_for_unlink_link_precs`` calls ``PrefixData(prefix)``
    which reads conda-meta JSONs. After the initial read, we monkey-
    patch ``PrefixData.iter_records`` to return the pre-loaded records
    directly so successive samples don't re-read the disk. What we
    want to measure is ``PrefixGraph.__init__`` + the diff/sort work,
    not the I/O cost of reloading 50 000 JSONs per sample.
    """
    if n in _FIXTURE_CACHE:
        return _FIXTURE_CACHE[n]

    tmp_root = Path(os.environ.get("CONDA_BENCH_TMPDIR", tempfile.gettempdir()))
    tmp_root.mkdir(parents=True, exist_ok=True)
    prefix = tmp_root / f"s19-prefix-{n}"
    prefix.mkdir(parents=True, exist_ok=True)

    _seed(prefix, n, simple_deps=False)

    # Load records via PrefixData once, then patch iter_records to
    # skip disk reads on subsequent calls.
    from conda.core.prefix_data import PrefixData

    pd = PrefixData(str(prefix))
    prefix_records = tuple(pd.iter_records())

    # Replace iter_records on the class so every PrefixData instance
    # for this prefix returns the cached tuple.
    _cached = prefix_records

    def _cached_iter_records(self):
        if str(self.prefix_path) == str(prefix):
            return iter(_cached)
        return _original_iter_records(self)

    _original_iter_records = PrefixData.iter_records
    PrefixData.iter_records = _cached_iter_records

    # Construct final_precs: keep 95 % of prefix, drop a handful, add
    # a handful of new records (simulating an install). The overlap
    # guarantees the symmetric-difference paths
    # (``unlink_precs``/``link_precs``) do real work.
    from conda.models.records import PackageRecord

    kept = prefix_records[: int(len(prefix_records) * 0.95)]
    new_count = max(10, len(prefix_records) // 100)  # ~1 %
    new_records = []
    for i in range(new_count):
        name = f"tempo-s19-new-pkg-{i:04d}"
        new_records.append(
            PackageRecord(
                name=name,
                version="1.0.0",
                build="py313_0",
                build_number=0,
                channel="synthetic",
                subdir="noarch",
                platform=None,
                depends=(),
                md5="0" * 32,
                sha256="0" * 64,
                size=0,
                timestamp=0,
                fn=f"{name}-1.0.0-py313_0.conda",
                url="",
            )
        )
    final_precs = tuple(list(kept) + new_records)

    _FIXTURE_CACHE[n] = (str(prefix), prefix_records, final_precs)
    return _FIXTURE_CACHE[n]


def _bench_diff(args) -> None:
    prefix, _records, final_precs = args
    from conda.core.solve import diff_for_unlink_link_precs

    diff_for_unlink_link_precs(prefix, final_precs)


def register_memray(n: int) -> None:
    args = _build_fixture(n)
    for _ in range(3):
        _bench_diff(args)


def main() -> int:
    import pyperf

    def _forward_records(cmd, args):
        cmd.extend(("-N", str(args.records)))

    runner = pyperf.Runner(add_cmdline_args=_forward_records)
    runner.argparser.add_argument(
        "-N",
        "--records",
        type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "1000")),
        help="synthetic prefix size (default: 1000, or $CONDA_BENCH_N)",
    )
    args = runner.parse_args()
    n = args.records

    runner.metadata["s19_n"] = str(n)
    fixture = _build_fixture(n)
    runner.bench_func(
        f"s19_diff_for_unlink_link_precs_n{n}",
        _bench_diff,
        fixture,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
