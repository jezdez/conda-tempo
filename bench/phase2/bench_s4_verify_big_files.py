#!/usr/bin/env python3
"""S4 microbenchmark: ``PrefixReplaceLinkAction.verify`` on large binaries.

Background (from Track B suspects):

    S4 | PrefixReplaceLinkAction.verify always SHA-256s the rewritten file
        path_actions.py:601

Phase-1 W1 verified ~200 small files at 0.76 ms each (S6 fixture is
4 KB). For a prefix with large binaries (libLLVM, libtorch, etc.,
50-200 MB), each verify does a copy + prefix-rewrite + SHA-256, where
SHA-256 on a 50 MB file is ~25 ms alone (see S14 null result) —
15-30x the per-file cost of the small synthetic fixture.

This benchmark parameterises the file size and measures per-action
wall time. Reuses ``fixtures.synthetic_prefix_replace_actions`` with
a large ``file_size`` argument; each action is a real
``PrefixReplaceLinkAction`` pointing at a multi-MB file with a
placeholder embedded.

B4 proposal: gate the ``sha256_in_prefix`` computation on
``context.extra_safety_checks`` (see the conda suspects table).
Measure first: if the hash is already a small fraction of wall time,
skipping it wins proportionally little.

``register_pyperf(runner, n)`` — n in megabytes per file.
``register_memray(n)``        — 1 invocation of _verify for 10 files.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from fixtures import reset_actions_and_tempdir, synthetic_prefix_replace_actions

_FIXTURE_CACHE: dict = {}


def _setup(mb: int, *, m: int = 3):
    """Build ``m`` PrefixReplaceLinkAction instances with ``mb``-MB files each.

    Keep ``m`` small so each benchmark iteration isn't dominated by the
    per-file fixed cost — we want the sweep to reflect file-size
    scaling, not action-count scaling (that's S6's territory).
    """
    key = (mb, m)
    if key in _FIXTURE_CACHE:
        return _FIXTURE_CACHE[key]
    tmp_root = Path(
        os.environ.get("CONDA_BENCH_TMPDIR", tempfile.gettempdir())
    )
    tmp_root.mkdir(parents=True, exist_ok=True)
    file_size = mb * 1024 * 1024
    ctx = synthetic_prefix_replace_actions(
        m, tmpdir=tmp_root / f"s4-{mb}mb", file_size=file_size,
    )
    _FIXTURE_CACHE[key] = ctx
    return ctx


def _bench_verify(actions, temp_dir: str) -> None:
    from conda.core.link import ActionGroup, UnlinkLinkTransaction

    reset_actions_and_tempdir(actions, temp_dir)
    group = ActionGroup(type="link", pkg_data=None, actions=actions, target_prefix="")
    UnlinkLinkTransaction._verify_individual_level([[group]])


def register_memray(mb: int) -> None:
    actions, _ctx, _target, _pkg, temp_dir = _setup(mb)
    _bench_verify(actions, temp_dir)


def main() -> int:
    import pyperf

    def _forward(cmd, args):
        cmd.extend(("-N", str(args.megabytes)))

    runner = pyperf.Runner(add_cmdline_args=_forward)
    runner.argparser.add_argument(
        "-N", "--megabytes", type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "10")),
        help="per-file size in MB (default: 10)",
    )
    args = runner.parse_args()
    mb = args.megabytes
    actions, _ctx, _target, _pkg, temp_dir = _setup(mb)

    runner.metadata["s4_mb"] = str(mb)
    runner.bench_func(
        f"s4_verify_big_files_{mb}mb",
        _bench_verify,
        actions,
        temp_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
