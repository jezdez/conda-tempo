#!/usr/bin/env python3
"""S1 microbenchmark: ``diff_for_unlink_link_precs`` sort key cost.

Background (from Track B suspects):

    S1 | Quadratic sorts in diff_for_unlink_link_precs
        solve.py:1465-1468

The tail of diff_for_unlink_link_precs does::

    unlink_precs = reversed(sorted(unlink_precs,
                                   key=lambda x: previous_records.index(x)))
    link_precs = sorted(link_precs, key=lambda x: final_precs.index(x))

``tuple.index`` is O(k); called from a sort key that's O(n log n),
each sort is O(n * k * log n). For a 50 k-record prefix with 2 k
unlink items, that was ~4 M position scans per sort.

B1 replaces the key with a precomputed ``{record: position}`` dict;
the sort becomes O(n log n) total.

This bench measures the wall time for the sorting tail of
``diff_for_unlink_link_precs`` at parameterised N (size of
``previous_records``) with a fixed k = N // 25 (i.e. 4 % of records
change on a typical ``update --all`` run).

``register_pyperf(runner, n)`` — one pyperf bench per N.
``register_memray(n)``        — single sort invocation.
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from fixtures import synthetic_prefix_records

_FIXTURE_CACHE: dict = {}


def _setup(n: int):
    if n in _FIXTURE_CACHE:
        return _FIXTURE_CACHE[n]
    # Build N records (DAG, to avoid S2 cycle-handling dominating).
    records = synthetic_prefix_records(n)
    # Choose k = n // 25 random records to be "unlinked" and a separate
    # k to be "linked" (distinct from previous).
    k = max(1, n // 25)
    rng = random.Random(7)
    unlink_indices = set(rng.sample(range(n), k))
    unlink_set = {records[i] for i in unlink_indices}
    # "final_precs" = previous minus unlink + a fresh k synthetic records.
    # For the sort test we only need final_precs as a sequence; any
    # PrefixRecord-like object works since the key is identity.
    final_precs = tuple(r for r in records if r not in unlink_set) + tuple(
        # Simple new records; we just need them to be unique-identity.
        # Reusing existing records as stand-ins keeps the fixture simple.
        records[(i + n // 2) % n] for i in range(k)
    )
    _FIXTURE_CACHE[n] = (records, unlink_set, final_precs)
    return _FIXTURE_CACHE[n]


def _bench_sort_current(records, unlink_set, final_precs):
    """The shipping implementation: ``key=lambda x: tuple.index(x)``."""
    previous_records = records
    unlink_precs = tuple(
        reversed(sorted(unlink_set, key=lambda x: previous_records.index(x)))
    )
    # The S1 fix also affects the link sort; skip it here to isolate.
    return unlink_precs


def _bench_sort_b1(records, unlink_set, final_precs):
    """The B1 implementation: precompute a {record: position} dict."""
    previous_records = records
    position = {rec: i for i, rec in enumerate(previous_records)}
    unlink_precs = tuple(reversed(sorted(unlink_set, key=position.__getitem__)))
    return unlink_precs


def register_memray(n: int) -> None:
    records, unlink_set, final_precs = _setup(n)
    _bench_sort_current(records, unlink_set, final_precs)


def main() -> int:
    import pyperf

    def _forward(cmd, args):
        cmd.extend(("-N", str(args.records)))

    runner = pyperf.Runner(add_cmdline_args=_forward)
    runner.argparser.add_argument(
        "-N", "--records", type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "5000")),
        help="size of previous_records (default: 5000)",
    )
    args = runner.parse_args()
    n = args.records
    records, unlink_set, final_precs = _setup(n)

    runner.metadata["s1_n"] = str(n)
    runner.metadata["s1_k"] = str(len(unlink_set))
    runner.bench_func(
        f"s1_diff_sort_current_n{n}",
        _bench_sort_current, records, unlink_set, final_precs,
    )
    runner.bench_func(
        f"s1_diff_sort_b1_n{n}",
        _bench_sort_b1, records, unlink_set, final_precs,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
