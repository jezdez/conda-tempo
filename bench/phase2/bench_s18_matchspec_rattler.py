#!/usr/bin/env python3
"""S18: conda.MatchSpec vs rattler.MatchSpec + PrefixGraph-equivalent.

Jaime asked on #15971 whether swapping conda's Python ``MatchSpec`` for
py-rattler's Rust-backed ``MatchSpec`` would be faster on the
``PrefixGraph.__init__`` hot path that B2 restructured.

Three comparisons, all against the same synthetic DAG fixture
(``synthetic_prefix_records(N)``):

A. **Parse cost** — build ``MatchSpec(s)`` for every dep string in
   every record. One-shot cost; matters once per solve, not per match.

B. **Match cost (per-call, pre-parsed)** — given pre-parsed specs and
   pre-converted records, call ``spec.match(record)`` /
   ``spec.matches(record)`` M times. Isolates the raw match speed of
   the two implementations.

C. **PrefixGraph-equivalent construction** — full construction:

   * ``conda_prefix_graph_baseline``: stock ``PrefixGraph(records).graph``
     (pre-B2 quadratic path, with a fresh code path since B2 hasn't
     landed in main yet at measurement time).
   * ``conda_prefix_graph_b2``: B2's name-indexed variant, to give the
     pure-Python ceiling for the same logic.
   * ``rattler_sort_topologically``: ``PackageRecord.sort_topologically(records)``.
     Rust-backed graph construction.

All three produce a topological order of the same N records; the
comparison is like-for-like on the operation PrefixGraph is actually
used for in ``conda/cli/install.py:512``.

Caveats:

* Record conversion (conda PrefixRecord → rattler PackageRecord) is
  one-shot, outside the timed loop. In a real hybrid conda-using-rattler
  swap, this conversion would be amortised across however many times
  the records are reused.
* Synthetic deps are bare names (``"pkg-000042"``), not full spec
  strings. Real conda deps include versions, build strings, etc., which
  push the match cost up on both sides. The synthetic fixture is a
  lower bound on per-call cost for both implementations.

Usage::

    python bench_s18_matchspec_rattler.py -N 1000

    CONDA_BENCH_N=5000 python bench_s18_matchspec_rattler.py \\
        --output ../../data/phase2/s18/pyperf_n5000.json
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from fixtures import synthetic_prefix_records

_RECORD_CACHE: dict[int, object] = {}


def _to_rattler_record(rec):
    """Convert a conda.PrefixRecord to a rattler.PackageRecord."""
    import rattler

    return rattler.PackageRecord(
        name=rec.name,
        version=rec.version,
        build=rec.build,
        build_number=rec.build_number,
        subdir=rec.subdir,
        noarch="generic" if rec.subdir == "noarch" else None,
        depends=list(rec.depends),
    )


def _build_fixture(n: int):
    """Build (conda_records, rattler_records, dep_strings) once per N."""
    if n in _RECORD_CACHE:
        return _RECORD_CACHE[n]

    conda_records = synthetic_prefix_records(n)
    rattler_records = [_to_rattler_record(r) for r in conda_records]
    # all dep strings from all records, deduplicated (matches what
    # PrefixGraph's inner loop iterates over per record)
    dep_strings = []
    for r in conda_records:
        dep_strings.extend(r.depends)

    _RECORD_CACHE[n] = (conda_records, rattler_records, dep_strings)
    return _RECORD_CACHE[n]


# ------------------------------------------------------------------
# Bench A: parse cost
# ------------------------------------------------------------------


def _bench_conda_matchspec_parse(dep_strings) -> None:
    from conda.models.match_spec import MatchSpec

    for s in dep_strings:
        MatchSpec(s)


def _bench_rattler_matchspec_parse(dep_strings) -> None:
    import rattler

    for s in dep_strings:
        rattler.MatchSpec(s)


# ------------------------------------------------------------------
# Bench B: per-match cost (pre-parsed specs × pre-built records)
# ------------------------------------------------------------------


def _bench_conda_matchspec_match(args) -> None:
    specs, records = args
    for spec in specs:
        for rec in records:
            spec.match(rec)


def _bench_rattler_matchspec_match(args) -> None:
    specs, records = args
    for spec in specs:
        for rec in records:
            spec.matches(rec)


# ------------------------------------------------------------------
# Bench C: PrefixGraph-equivalent (the operation B2 restructured)
# ------------------------------------------------------------------


def _bench_conda_prefix_graph(conda_records) -> None:
    """B2's name-indexed PrefixGraph (what the current fix ships)."""
    from conda.models.prefix_graph import PrefixGraph

    PrefixGraph(conda_records).graph


def _bench_rattler_sort_topologically(rattler_records) -> None:
    """py-rattler's Rust-backed equivalent."""
    import rattler

    rattler.PackageRecord.sort_topologically(rattler_records)


def _bench_rattler_sort_topologically_with_conversion(conda_records) -> None:
    """Realistic hybrid-path cost: convert conda PrefixRecords to rattler
    PackageRecords, then call sort_topologically. This is what a real
    swap inside ``conda/core/solve.py`` or ``conda/cli/install.py``
    would pay per invocation."""
    import rattler

    rattler_records = [_to_rattler_record(r) for r in conda_records]
    rattler.PackageRecord.sort_topologically(rattler_records)


def _bench_conversion_only(conda_records) -> None:
    """Conversion cost in isolation — how much of the realistic path is
    just the conda→rattler PackageRecord construction?"""
    for r in conda_records:
        _to_rattler_record(r)


def register_memray(n: int) -> None:
    conda_records, rattler_records, dep_strings = _build_fixture(n)
    for _ in range(5):
        _bench_conda_prefix_graph(conda_records)
        _bench_rattler_sort_topologically(rattler_records)


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

    runner.metadata["s18_n"] = str(n)
    conda_records, rattler_records, dep_strings = _build_fixture(n)

    # Pre-parse spec-lists for bench B. Cap match fixture at
    # 100 specs × 100 records so the per-match loop stays reasonable
    # at both implementations across N.
    sample_dep_strings = dep_strings[:100]
    sample_records_conda = conda_records[:100]
    sample_records_rattler = rattler_records[:100]

    from conda.models.match_spec import MatchSpec
    import rattler

    conda_specs = [MatchSpec(s) for s in sample_dep_strings]
    rattler_specs = [rattler.MatchSpec(s) for s in sample_dep_strings]

    # Bench A: parse N deps (all of them — this is the realistic
    # workload since PrefixGraph calls MatchSpec(dep) for every dep of
    # every record).
    runner.bench_func(
        f"s18_conda_matchspec_parse_n{len(dep_strings)}",
        _bench_conda_matchspec_parse,
        dep_strings,
    )
    runner.bench_func(
        f"s18_rattler_matchspec_parse_n{len(dep_strings)}",
        _bench_rattler_matchspec_parse,
        dep_strings,
    )

    # Bench B: 100 specs × 100 records = 10k matches
    runner.bench_func(
        "s18_conda_matchspec_match_100x100",
        _bench_conda_matchspec_match,
        (conda_specs, sample_records_conda),
    )
    runner.bench_func(
        "s18_rattler_matchspec_match_100x100",
        _bench_rattler_matchspec_match,
        (rattler_specs, sample_records_rattler),
    )

    # Bench C: PrefixGraph-equivalent (B2 vs rattler)
    runner.bench_func(
        f"s18_conda_prefix_graph_n{n}",
        _bench_conda_prefix_graph,
        conda_records,
    )
    runner.bench_func(
        f"s18_rattler_sort_topologically_n{n}",
        _bench_rattler_sort_topologically,
        rattler_records,
    )
    runner.bench_func(
        f"s18_rattler_sort_with_conversion_n{n}",
        _bench_rattler_sort_topologically_with_conversion,
        conda_records,
    )
    runner.bench_func(
        f"s18_conversion_only_n{n}",
        _bench_conversion_only,
        conda_records,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
