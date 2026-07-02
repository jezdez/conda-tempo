#!/usr/bin/env python3
"""S10 microbenchmark: ``CreatePrefixRecordAction`` JSON writes.

S10 was the only original Track B suspect never benchmarked. The concern is
Windows-specific: one ``conda-meta/*.json`` write per linked package may be
expensive on NTFS under antivirus.

This fixture constructs real ``CreatePrefixRecordAction`` instances with empty
path data and executes them against a synthetic prefix. That isolates the
``PrefixData.insert()`` and JSON-on-disk path without mixing in link, verify, or
extract work covered by other suspects.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_FIXTURE_CACHE: dict[int, tuple[list[object], str]] = {}


def _setup(n: int):
    if n in _FIXTURE_CACHE:
        return _FIXTURE_CACHE[n]

    from conda.core.path_actions import CreatePrefixRecordAction
    from conda.models.channel import Channel
    from conda.models.enums import LinkType
    from conda.models.match_spec import MatchSpec
    from conda.models.package_info import PackageInfo
    from conda.models.records import PackageRecord, PathsData

    tmp_root = Path(os.environ.get("CONDA_BENCH_TMPDIR", tempfile.gettempdir()))
    tmp_root.mkdir(parents=True, exist_ok=True)
    workspace = tmp_root / f"s10-prefix-records-{n}"
    extracted_root = workspace / "pkgs"
    target_prefix = workspace / "prefix"
    transaction_context = {"temp_dir": str(workspace / "tmp")}
    channel = Channel("https://conda.anaconda.org/tempo-synthetic")

    actions = []
    for i in range(n):
        name = f"tempo-record-{i:06d}"
        version = "0.0.0"
        build = "py313_0"
        extracted_dir = extracted_root / f"{name}-{version}-{build}"
        extracted_dir.mkdir(parents=True, exist_ok=True)
        repodata_record = PackageRecord(
            name=name,
            version=version,
            build=build,
            build_number=0,
            channel=channel,
            subdir="noarch",
            md5="0" * 32,
            sha256="0" * 64,
            size=0,
            timestamp=0,
            fn=f"{name}-{version}-{build}.conda",
            url=f"https://conda.anaconda.org/tempo-synthetic/noarch/{name}-{version}-{build}.conda",
            depends=(),
        )
        package_info = PackageInfo(
            extracted_package_dir=str(extracted_dir),
            package_tarball_full_path=str(extracted_dir) + ".conda",
            channel=channel,
            repodata_record=repodata_record,
            url=repodata_record.url,
            package_metadata=None,
            paths_data=PathsData(paths_version=1, paths=()),
        )
        actions.extend(
            CreatePrefixRecordAction.create_actions(
                transaction_context,
                package_info,
                str(target_prefix),
                LinkType.hardlink,
                MatchSpec(name),
                (),
            )
        )

    _FIXTURE_CACHE[n] = (actions, str(target_prefix))
    return actions, str(target_prefix)


def _reset_prefix(target_prefix: str) -> None:
    from conda.core.prefix_data import PrefixData

    PrefixData._cache_.clear()
    shutil.rmtree(target_prefix, ignore_errors=True)
    meta = Path(target_prefix) / "conda-meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "history").write_text("==> 2026-01-01 00:00:00 <==\n")


def _bench_prefix_record_writes(actions, target_prefix: str) -> None:
    _reset_prefix(target_prefix)
    for action in actions:
        action.execute()


def register_memray(n: int) -> None:
    actions, target_prefix = _setup(n)
    _bench_prefix_record_writes(actions, target_prefix)


def main() -> int:
    import pyperf

    def _forward_records(cmd, args):
        cmd.extend(("-N", str(args.count)))

    runner = pyperf.Runner(add_cmdline_args=_forward_records)
    runner.argparser.add_argument(
        "-N",
        "--count",
        type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "150")),
        help="number of CreatePrefixRecordAction fixtures (default: 150)",
    )
    args = runner.parse_args()
    n = args.count

    runner.metadata["s10_n"] = str(n)
    actions, target_prefix = _setup(n)
    runner.bench_func(
        f"s10_prefix_record_writes_n{n}",
        _bench_prefix_record_writes,
        actions,
        target_prefix,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
