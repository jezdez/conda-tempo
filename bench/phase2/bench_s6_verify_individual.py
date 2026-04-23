#!/usr/bin/env python3
"""S6 microbenchmark: ``UnlinkLinkTransaction._verify_individual_level``.

Background (Phase-1 cProfile for W1):

    ncalls  tottime  cumtime  filename:lineno(function)
         1   0.004    5.516   link.py:620(_verify_individual_level)
       200   0.022    4.971   path_actions.py:558(PrefixReplaceLinkAction.verify)
       200   0.009    4.682   portability.py:67(update_prefix)

The method at ``link.py:620-642`` is::

    error_results = []
    for axn in all_actions:
        if axn.verified:
            continue
        error_result = axn.verify()
        ...

A bare ``for`` loop inside a single prefix. Each call to
``PrefixReplaceLinkAction.verify()`` does a copy + chmod + prefix
rewrite + sha256 — all embarrassingly parallelizable I/O work.

This microbenchmark isolates the serial fan-out cost at parameterized
M (number of prefix-replace actions in one prefix). Expected Phase-2
confirmation: wall time scales linearly with M; parallelization with
the existing ``verify_executor`` should scale inversely with available
cores (bounded by disk throughput).

``register_pyperf(runner, m)`` — _verify_individual_level wall time.
``register_memray(m)``        — 1 invocation, for allocation profiling.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from fixtures import (
    reset_actions_and_tempdir,
    synthetic_prefix_replace_actions,
)

# Worker-lifetime caches. pyperf spawns a fresh worker subprocess per
# sample batch, so these survive across inner-loop iterations of one
# sample but not across pyperf runs.
_ACTIONS_CACHE: dict = {}


def _setup(m: int):
    """Build (or reuse) the M-action fixture for the current worker."""
    if m in _ACTIONS_CACHE:
        return _ACTIONS_CACHE[m]
    tmp_root = Path(
        os.environ.get("CONDA_BENCH_TMPDIR", tempfile.gettempdir())
    )
    tmp_root.mkdir(parents=True, exist_ok=True)
    ctx = synthetic_prefix_replace_actions(m, tmpdir=tmp_root)
    _ACTIONS_CACHE[m] = ctx
    return ctx


def _bench_verify(actions, temp_dir: str) -> None:
    """The unit of work: reset state, then run _verify_individual_level once."""
    from conda.core.link import ActionGroup, UnlinkLinkTransaction

    reset_actions_and_tempdir(actions, temp_dir)

    # _verify_individual_level iterates:
    #   for action_groups in prefix_action_group:
    #       for axngroup in action_groups:
    #           yield axngroup.actions
    # so we wrap in [ [ActionGroup(...)] ].
    group = ActionGroup(
        type="link",
        pkg_data=None,
        actions=actions,
        target_prefix="",
    )
    UnlinkLinkTransaction._verify_individual_level([[group]])


def register_memray(m: int) -> None:
    """Single invocation of _verify_individual_level for allocation profiling."""
    actions, _ctx, _target, _pkg, temp_dir = _setup(m)
    _bench_verify(actions, temp_dir)


def main() -> int:
    import pyperf

    def _forward_records(cmd, args):
        cmd.extend(("-N", str(args.count)))

    runner = pyperf.Runner(add_cmdline_args=_forward_records)
    runner.argparser.add_argument(
        "-N", "--count", type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "200")),
        help="number of PrefixReplaceLinkAction fixtures (default: 200)",
    )
    args = runner.parse_args()
    m = args.count

    runner.metadata["s6_m"] = str(m)
    actions, _ctx, _target, _pkg, temp_dir = _setup(m)

    runner.bench_func(
        f"s6_verify_individual_level_m{m}",
        _bench_verify,
        actions,
        temp_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
