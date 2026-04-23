#!/usr/bin/env python3
"""S5 microbenchmark: ``_verify_prefix_level`` clobber check.

Background (from Track B suspects):

    S5 | _verify_prefix_level clobber check reloads records
        link.py:698-705

The check builds:

  * ``unlink_paths``: set of ``target_short_path`` for all actions
    that are unlinking an existing file.
  * For every ``CreatePrefixRecordAction`` being linked: walk
    ``all_link_path_actions`` and compare each target against
    ``unlink_paths`` to detect clobbering.

The cost scales with total number of link paths across packages — a
fresh scientific-Python env is ~29 000 link paths (the posix.link
calls in S7). The loop reads from a dict per path and does an
``os.path.lexists`` when a potential collision is detected.

This bench isolates the ``_verify_prefix_level`` wall time at
parameterised P (number of packages) × F (link paths per package).

``register_pyperf(runner, n)`` — n packages, 150 link paths each.
``register_memray(n)``        — single invocation.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


_FIXTURE_CACHE: dict = {}


def _setup(p: int, files_per_pkg: int = 150, collisions: int = 0):
    """Build a synthetic (PrefixActionGroup, target_prefix) pair.

    P packages, each with K ``target_short_path`` entries. Optionally
    inject C target paths that collide between packages (two
    different packages declaring the same path) to exercise the
    clobber-reporting branch.
    """
    key = (p, files_per_pkg, collisions)
    if key in _FIXTURE_CACHE:
        return _FIXTURE_CACHE[key]

    from dataclasses import dataclass
    from conda.core.link import ActionGroup, PrefixActionGroup
    from conda.core.path_actions import (
        CreatePrefixRecordAction,
        LinkPathAction,
        UnlinkPathAction,
    )
    from conda.models.enums import LinkType, PathEnum
    from conda.models.records import PathDataV1

    tmp_root = Path(os.environ.get("CONDA_BENCH_TMPDIR", tempfile.gettempdir()))
    tmp_root.mkdir(parents=True, exist_ok=True)
    target_prefix = tmp_root / f"s5-target-{p}"
    target_prefix.mkdir(parents=True, exist_ok=True)

    # Minimal transaction_context + package_info stubs. PathAction inits
    # just stash the references; the verify loop only reads
    # target_short_path / all_link_path_actions / link_type.
    transaction_context = {"temp_dir": str(target_prefix / ".tmp")}

    def _make_repodata_record(pkg_idx: int):
        return SimpleNamespace(
            name=f"synth-pkg-{pkg_idx:06d}",
            subdir="noarch",
        )

    def _make_package_info(pkg_idx: int):
        return SimpleNamespace(
            repodata_record=_make_repodata_record(pkg_idx),
            extracted_package_dir=str(target_prefix / f"fakepkg-{pkg_idx:06d}"),
            package_metadata=None,
        )

    def _make_link_path(pkg_idx: int, file_idx: int, collide_with: int | None = None):
        if collide_with is not None:
            rel = f"site-packages/shared_pkg/file_{collide_with:06d}.py"
        else:
            rel = f"site-packages/synth_{pkg_idx:06d}/file_{file_idx:06d}.py"
        source_path_data = PathDataV1(
            _path=rel,
            path_type=PathEnum.hardlink,
            sha256=None,
            size_in_bytes=0,
        )
        return LinkPathAction(
            transaction_context=transaction_context,
            package_info=_make_package_info(pkg_idx),
            extracted_package_dir=str(target_prefix / f"fakepkg-{pkg_idx:06d}"),
            source_short_path=rel,
            target_prefix=str(target_prefix),
            target_short_path=rel,
            link_type=LinkType.hardlink,
            source_path_data=source_path_data,
        )

    link_action_groups = []
    record_action_groups = []
    collisions_remaining = collisions
    for pi in range(p):
        link_paths = []
        for fi in range(files_per_pkg):
            if collisions_remaining > 0 and fi == 0 and pi > 0:
                # Create a collision: this path is also declared by
                # package 0 (pi=0's first file). The verify_prefix_level
                # should report it.
                la = _make_link_path(pi, fi, collide_with=0)
                collisions_remaining -= 1
            else:
                la = _make_link_path(pi, fi)
            link_paths.append(la)

        # CreatePrefixRecordAction wraps the link paths with
        # ``all_link_path_actions``; verify_prefix_level reads that attr.
        record_action = CreatePrefixRecordAction(
            transaction_context=transaction_context,
            package_info=_make_package_info(pi),
            target_prefix=str(target_prefix),
            target_short_path=f"conda-meta/synth-pkg-{pi:06d}.json",
            requested_link_type=LinkType.hardlink,
            requested_spec=f"synth-pkg-{pi:06d}",
            all_link_path_actions=link_paths,
        )

        link_action_groups.append(
            ActionGroup(
                type="link",
                pkg_data=_make_repodata_record(pi),
                actions=link_paths,
                target_prefix=str(target_prefix),
            )
        )
        record_action_groups.append(
            ActionGroup(
                type="record",
                pkg_data=_make_repodata_record(pi),
                actions=[record_action],
                target_prefix=str(target_prefix),
            )
        )

    prefix_action_group = PrefixActionGroup(
        remove_menu_action_groups=(),
        unlink_action_groups=(),
        unregister_action_groups=(),
        link_action_groups=link_action_groups,
        register_action_groups=(),
        compile_action_groups=(),
        make_menu_action_groups=(),
        entry_point_action_groups=(),
        prefix_record_groups=record_action_groups,
    )

    _FIXTURE_CACHE[key] = (str(target_prefix), prefix_action_group)
    return _FIXTURE_CACHE[key]


def _bench_verify_prefix_level(target_prefix: str, prefix_action_group) -> None:
    from conda.core.link import UnlinkLinkTransaction

    UnlinkLinkTransaction._verify_prefix_level(
        (target_prefix, prefix_action_group),
    )


def register_memray(p: int) -> None:
    target_prefix, prefix_action_group = _setup(p)
    _bench_verify_prefix_level(target_prefix, prefix_action_group)


def main() -> int:
    import pyperf

    def _forward(cmd, args):
        cmd.extend(("-N", str(args.packages)))

    runner = pyperf.Runner(add_cmdline_args=_forward)
    runner.argparser.add_argument(
        "-N", "--packages", type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "100")),
        help="number of synthetic packages (default: 100, 150 link paths each)",
    )
    args = runner.parse_args()
    p = args.packages
    target_prefix, prefix_action_group = _setup(p)

    runner.metadata["s5_p"] = str(p)
    runner.bench_func(
        f"s5_verify_prefix_level_p{p}",
        _bench_verify_prefix_level, target_prefix, prefix_action_group,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
