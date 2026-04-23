#!/usr/bin/env python3
"""S3 microbenchmark: ``History.update()`` on long history files.

Background (from Track B suspects):

    S3 | History.update() reads and parses the entire conda-meta/history
        history.py:108-123

Every transaction's ``History.update`` reads the full history file
(which grows monotonically for the life of an env) and iterates the
full prefix to build a ``dist_str`` set.

This bench scales the synthetic prefix's ``history`` file linearly
and measures the full ``History.update()`` wall time.

``register_pyperf(runner, n)`` — n = number of lines in history.
``register_memray(n)``        — single update invocation.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from fixtures import synthetic_prefix

_FIXTURE_CACHE: dict = {}


def _setup(lines: int):
    """Build a synthetic prefix whose ``history`` file has ``lines`` entries.

    Base prefix has the default 100 synthetic conda-meta records (just
    so History.update's iter_records pass does meaningful work); the
    history file is inflated with ``lines`` `` ==>`` transaction blocks.
    """
    if lines in _FIXTURE_CACHE:
        return _FIXTURE_CACHE[lines]
    tmp_root = Path(
        os.environ.get("CONDA_BENCH_TMPDIR", tempfile.gettempdir())
    )
    tmp_root.mkdir(parents=True, exist_ok=True)
    # Fresh prefix per size so the history file is exactly right.
    prefix = synthetic_prefix(100, tmpdir=tmp_root / f"s3-{lines}")
    history_path = prefix / "conda-meta" / "history"

    # Each "transaction" in a real history file is ~5 lines:
    #     ==> 2024-01-01 00:00:00 <==
    #     # cmd: conda install ...
    #     # conda version: N.M.P
    #     +pkg-name-version-build
    #     # update specs: ['pkg-name']
    blocks_needed = lines // 5
    chunks = ["==> 2024-01-01 00:00:00 <==\n",
              "# cmd: conda install -n bench_s3 tempo-synthetic-pkg-000001\n",
              "# conda version: 26.3.3\n",
              "+tempo-synthetic-pkg-000001-0.0.0-py313_0\n",
              "# update specs: ['tempo-synthetic-pkg-000001']\n"]
    with history_path.open("w") as fh:
        for _ in range(blocks_needed):
            fh.writelines(chunks)
    _FIXTURE_CACHE[lines] = prefix
    return _FIXTURE_CACHE[lines]


def _bench_history_update(prefix):
    from conda.history import History

    h = History(str(prefix))
    h.update()


def register_memray(lines: int) -> None:
    prefix = _setup(lines)
    _bench_history_update(prefix)


def main() -> int:
    import pyperf

    def _forward(cmd, args):
        cmd.extend(("-N", str(args.lines)))

    runner = pyperf.Runner(add_cmdline_args=_forward)
    runner.argparser.add_argument(
        "-N", "--lines", type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "10000")),
        help="number of history lines (default: 10000)",
    )
    args = runner.parse_args()
    lines = args.lines
    prefix = _setup(lines)

    runner.metadata["s3_lines"] = str(lines)
    runner.bench_func(
        f"s3_history_update_n{lines}",
        _bench_history_update, prefix,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
