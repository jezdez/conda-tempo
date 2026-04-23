#!/usr/bin/env python3
"""S12 microbenchmark: ``cps.extract.extract_stream`` per-member path-safety.

Background (from Track B suspects):

    S12 | conda_package_streaming.extract.extract_stream per-member
          os.path.realpath + os.path.commonpath
          conda_package_streaming/extract.py:33-46

For every tar member the extract pass runs::

    def is_within_dest_dir(name):
        abs_target = os.path.realpath(os.path.join(dest_dir, name))
        prefix = os.path.commonpath((dest_dir, abs_target))
        return prefix == dest_dir

``dest_dir`` is invariant for the whole extract, yet each member pays
``realpath`` (a syscall, per-component) and ``commonpath`` (pure
Python). For a scientific-Python env (W2-ish with ~29 k tar members)
that's 58 k extra path syscalls plus tens of thousands of Python-level
string splits.

This bench isolates the safety-check cost per member and compares the
current implementation against a memoized one. We don't actually
extract — we just feed a list of member names into the check in a
hot loop.

``register_pyperf(runner, n)`` — n = number of members to scan.
``register_memray(n)``        — one run.
"""
from __future__ import annotations

import os
import os.path
import sys
import tempfile
from pathlib import Path

_FIXTURE_CACHE: dict = {}


def _setup(m: int):
    """Return (dest_dir_realpath, members) — ``m`` synthetic tar member names.

    Members are a mix of shallow paths ("site-packages/x.py") and deeper
    ones ("lib/python3.13/site-packages/x/y/z/__init__.py") to match a
    realistic scientific-Python archive.
    """
    if m in _FIXTURE_CACHE:
        return _FIXTURE_CACHE[m]
    tmp_root = Path(
        os.environ.get("CONDA_BENCH_TMPDIR", tempfile.gettempdir())
    )
    tmp_root.mkdir(parents=True, exist_ok=True)
    dest = tmp_root / f"s12-dest-{m}"
    dest.mkdir(parents=True, exist_ok=True)
    dest_str = os.path.realpath(str(dest))

    members: list[str] = []
    for i in range(m):
        depth = (i % 4) + 1
        parts = [f"lvl{j}" for j in range(depth)] + [f"file-{i:06d}.py"]
        members.append("/".join(parts))
    _FIXTURE_CACHE[m] = (dest_str, members)
    return _FIXTURE_CACHE[m]


def _is_within_current(dest_dir: str, member: str) -> bool:
    """The shipping ``extract_stream`` implementation."""
    abs_target = os.path.realpath(os.path.join(dest_dir, member))
    prefix = os.path.commonpath((dest_dir, abs_target))
    return prefix == dest_dir


def _scan_current(dest_dir: str, members: list[str]) -> int:
    """The shipping per-member check applied across ``members``."""
    ok = 0
    for member in members:
        if _is_within_current(dest_dir, member):
            ok += 1
    return ok


def _scan_proposed(dest_dir: str, members: list[str]) -> int:
    """Proposed B12: precompute ``dest_dir + '/'`` as the expected prefix.

    Safe equivalence: if ``os.path.realpath(os.path.join(dest_dir,
    member))`` starts with ``dest_dir + os.sep``, it's within dest_dir.
    We still ``realpath`` the joined target to resolve any symlinks in
    ``member`` itself, but skip the ``commonpath`` step and the
    redundant ``realpath(dest_dir)`` (which is constant per extract).
    """
    dest_dir_sep = dest_dir if dest_dir.endswith(os.sep) else dest_dir + os.sep
    ok = 0
    for member in members:
        abs_target = os.path.realpath(os.path.join(dest_dir, member))
        if abs_target == dest_dir or abs_target.startswith(dest_dir_sep):
            ok += 1
    return ok


def register_memray(m: int) -> None:
    dest, members = _setup(m)
    _scan_current(dest, members)


def main() -> int:
    import pyperf

    def _forward(cmd, args):
        cmd.extend(("-N", str(args.count)))

    runner = pyperf.Runner(add_cmdline_args=_forward)
    runner.argparser.add_argument(
        "-N", "--count", type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "10000")),
        help="number of tar members to scan (default: 10000)",
    )
    args = runner.parse_args()
    m = args.count
    dest, members = _setup(m)

    # sanity: same number of matches
    a = _scan_current(dest, members)
    b = _scan_proposed(dest, members)
    assert a == b == m, (a, b, m)

    runner.metadata["s12_m"] = str(m)
    runner.bench_func(
        f"s12_is_within_current_m{m}",
        _scan_current, dest, members,
    )
    runner.bench_func(
        f"s12_is_within_proposed_m{m}",
        _scan_proposed, dest, members,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
