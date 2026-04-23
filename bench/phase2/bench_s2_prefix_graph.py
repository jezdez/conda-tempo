#!/usr/bin/env python3
"""S2 microbenchmark: ``PrefixGraph.__init__`` at parameterized N records.

Background (from the Track B suspects table):

    S2 | PrefixGraph.__init__ O(N^2)

``conda/models/prefix_graph.py:46-67``::

    for node in records:
        parent_match_specs = tuple(MatchSpec(d) for d in node.depends)
        parent_nodes = {
            rec: None
            for rec in records
            if any(m.match(rec) for m in parent_match_specs)
        }
        graph[node] = parent_nodes
        ...

Two nested loops over ``records``: outer yields N nodes, inner checks
``MatchSpec.match`` against each of N records again. Total:
``O(N^2 × K)`` ``MatchSpec.match`` calls, where K is deps per record.

The Phase-0 scaling experiment (W3 `conda update --all` at N=1 000,
5 000, 10 000, 50 000) was consistent with an O(N²) term end-to-end.
The cProfile for W3 attributed that cost to
``conda_libmamba_solver.state`` (→ S11), not to ``PrefixGraph``. So
S2 may or may not actually hit on the W3 workload — worth measuring
directly.

``register_pyperf(runner, n)`` — ``PrefixGraph.__init__`` wall time.
``register_memray(n)``        — single construction, allocation profile.
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

_RECORDS_CACHE: dict = {}


def _setup(n: int):
    if n in _RECORDS_CACHE:
        return _RECORDS_CACHE[n]
    records = synthetic_prefix_records(n)
    _RECORDS_CACHE[n] = records
    return records


def _bench_prefix_graph(records) -> None:
    """The unit of work: one PrefixGraph construction."""
    from conda.models.prefix_graph import PrefixGraph

    PrefixGraph(records)


def register_memray(n: int) -> None:
    records = _setup(n)
    _bench_prefix_graph(records)


def main() -> int:
    import pyperf

    def _forward_records(cmd, args):
        cmd.extend(("-N", str(args.count)))

    runner = pyperf.Runner(add_cmdline_args=_forward_records)
    runner.argparser.add_argument(
        "-N", "--count", type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "1000")),
        help="number of PrefixRecord fixtures (default: 1000)",
    )
    args = runner.parse_args()
    n = args.count

    runner.metadata["s2_n"] = str(n)
    records = _setup(n)

    runner.bench_func(
        f"s2_prefix_graph_n{n}",
        _bench_prefix_graph,
        records,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
