#!/usr/bin/env python3
"""S7 microbenchmark: serial vs parallel ``action.execute()`` fan-out.

Background (Phase-1 cProfile for W2):

    ncalls   tottime   cumtime  filename:lineno(function)
     29189   0.067    10.457   gateways/disk/create.py:343(create_link)
     25983   9.391     9.391   {built-in method posix.link}
       578   0.013    17.688   link.py:1045(_execute_actions)

``_execute_actions`` has a bare ``for action in axngroup.actions:
action.execute()`` at ``link.py:1070``. With ``context.execute_threads
= 1`` (the shipping default), the outer ``execute_executor`` also fans
out one package at a time. Net effect: every ``posix.link`` call
happens on the main thread in strict sequence.

S7 is the proposed fix: either bump the default of ``execute_threads``
(user-visible) or push fan-out one level down into ``_execute_actions``
(internal only).

This microbenchmark runs the same set of M hardlink actions under
four different execution strategies and reports wall time for each:

  * ``serial``     — bare for-loop (the current shipping behaviour)
  * ``threads-2``  — ``ThreadPoolExecutor(max_workers=2).map(...)``
  * ``threads-4``  — max_workers=4
  * ``threads-8``  — max_workers=8

The expected pattern: flat or near-flat scaling from 2 → 4 threads (I/O
bound on NVMe, but syscall serialization inside the kernel limits
speedup), diminishing returns at 8.

``register_pyperf(runner, m)`` — serial + three parallel variants.
``register_memray(m)``        — serial invocation (allocation profile).
"""
from __future__ import annotations

import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from fixtures import clear_target_prefix, synthetic_hardlink_actions

_FIXTURE_CACHE: dict = {}


def _setup(m: int):
    if m in _FIXTURE_CACHE:
        return _FIXTURE_CACHE[m]
    tmp_root = Path(
        os.environ.get("CONDA_BENCH_TMPDIR", tempfile.gettempdir())
    )
    tmp_root.mkdir(parents=True, exist_ok=True)
    ctx = synthetic_hardlink_actions(m, tmpdir=tmp_root)
    _FIXTURE_CACHE[m] = ctx
    return ctx


def _run_action(action) -> None:
    action.execute()


def _bench_serial(actions, target_prefix: str) -> None:
    clear_target_prefix(target_prefix, subdirs=("bin",))
    for axn in actions:
        axn.execute()


def _bench_parallel(actions, target_prefix: str, max_workers: int) -> None:
    clear_target_prefix(target_prefix, subdirs=("bin",))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        # list() to force realization of lazy map() and surface exceptions.
        list(pool.map(_run_action, actions))


def register_memray(m: int) -> None:
    actions, _ctx, target_prefix, _pkg = _setup(m)
    _bench_serial(actions, target_prefix)


def main() -> int:
    import pyperf

    def _forward_records(cmd, args):
        cmd.extend(("-N", str(args.count)))

    runner = pyperf.Runner(add_cmdline_args=_forward_records)
    runner.argparser.add_argument(
        "-N", "--count", type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "1000")),
        help="number of LinkPathAction fixtures (default: 1000)",
    )
    args = runner.parse_args()
    m = args.count

    runner.metadata["s7_m"] = str(m)
    actions, _ctx, target_prefix, _pkg = _setup(m)

    runner.bench_func(
        f"s7_link_serial_m{m}",
        _bench_serial,
        actions,
        target_prefix,
    )
    for workers in (2, 4, 8):
        runner.bench_func(
            f"s7_link_parallel_m{m}_k{workers}",
            _bench_parallel,
            actions,
            target_prefix,
            workers,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
