#!/usr/bin/env python3
"""Shared fixtures for Phase 2 microbenchmarks.

Phase 2 scaffold; additional builders appear in later commits as new
suspects get benchmarks.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "bench"))

from seed_big_prefix import seed  # noqa: E402


PLACEHOLDER = "/opt/anaconda1anaconda2anaconda3"


def synthetic_prefix(n: int, *, tmpdir: Path) -> Path:
    """Build a synthetic prefix with N conda-meta records under ``tmpdir``.

    Minimal: just ``conda-meta/`` + ``*.json`` + ``history``. No ``bin/``,
    ``lib/``, no actual Python. Enough for targets that only read
    ``conda-meta/`` (``PrefixData``, ``History``, ``PrefixGraph``,
    ``SolverInputState``).
    """
    prefix = tmpdir / f"synth-{n}"
    prefix.mkdir(parents=True, exist_ok=True)
    (prefix / "conda-meta").mkdir(exist_ok=True)
    seed(prefix, n)
    return prefix


def synthetic_prefix_replace_actions(
    m: int,
    *,
    tmpdir: Path,
    file_size: int = 4096,
):
    """Build ``m`` ``PrefixReplaceLinkAction`` instances over real files.

    Layout created under ``tmpdir``::

        synth-pkg-<m>/
            info/index.json           minimal repodata-like stub
            bin/file-000000            file with one PLACEHOLDER occurrence
            bin/file-000001
            ...
        synth-target-<m>/              empty target prefix root
        synth-tmp-<m>/                 transaction temp_dir

    Each file is ``file_size`` bytes with the placeholder embedded near
    the start (so ``update_prefix`` always finds it). The resulting
    actions are ready to pass into
    ``UnlinkLinkTransaction._verify_individual_level``.

    Returns::

        (actions, transaction_context, target_prefix, pkg_dir, temp_dir)
    """
    from types import SimpleNamespace
    from conda.core.path_actions import PrefixReplaceLinkAction
    from conda.models.enums import FileMode, LinkType, PathEnum
    from conda.models.records import PathDataV1

    pkg_dir = tmpdir / f"synth-pkg-{m}"
    target_prefix = tmpdir / f"synth-target-{m}"
    temp_dir = tmpdir / f"synth-tmp-{m}"
    bin_dir = pkg_dir / "bin"
    for p in (pkg_dir, target_prefix, temp_dir, bin_dir, pkg_dir / "info"):
        p.mkdir(parents=True, exist_ok=True)

    (pkg_dir / "info" / "index.json").write_text(
        '{"name": "synth", "version": "0.0.0", "subdir": "noarch"}'
    )

    placeholder_bytes = PLACEHOLDER.encode()
    pad_before = 16
    pad_after = file_size - pad_before - len(placeholder_bytes)
    if pad_after < 0:
        raise ValueError(
            f"file_size={file_size} too small for PLACEHOLDER "
            f"({len(placeholder_bytes)}B) + {pad_before}B prefix",
        )
    content = b"A" * pad_before + placeholder_bytes + b"B" * pad_after
    assert len(content) == file_size

    source_short_paths = []
    for i in range(m):
        rel = f"bin/file-{i:06d}"
        full = pkg_dir / rel
        if not full.exists() or full.stat().st_size != file_size:
            full.write_bytes(content)
        source_short_paths.append(rel)

    repodata_record = SimpleNamespace(
        name="synth",
        subdir="noarch",
    )
    package_info = SimpleNamespace(
        repodata_record=repodata_record,
        extracted_package_dir=str(pkg_dir),
        package_metadata=None,
    )

    transaction_context = {
        "temp_dir": str(temp_dir),
        "target_site_packages_short_path": None,
    }

    actions = []
    for rel in source_short_paths:
        source_path_data = PathDataV1(
            _path=rel,
            path_type=PathEnum.hardlink,
            prefix_placeholder=PLACEHOLDER,
            file_mode=FileMode.text,
            sha256=None,
            size_in_bytes=file_size,
        )
        actions.append(
            PrefixReplaceLinkAction(
                transaction_context=transaction_context,
                package_info=package_info,
                extracted_package_dir=str(pkg_dir),
                source_short_path=rel,
                target_prefix=str(target_prefix),
                target_short_path=rel,
                link_type=LinkType.copy,
                prefix_placeholder=PLACEHOLDER,
                file_mode=FileMode.text,
                source_path_data=source_path_data,
            )
        )
    return actions, transaction_context, str(target_prefix), str(pkg_dir), str(temp_dir)


def reset_actions_and_tempdir(actions, temp_dir: str) -> None:
    """Reset each action's verified state and empty ``temp_dir``.

    Call this between repeated ``_verify_individual_level`` invocations
    in a benchmark.
    """
    import shutil

    for axn in actions:
        axn._verified = False
        axn.intermediate_path = None

    try:
        shutil.rmtree(temp_dir)
    except FileNotFoundError:
        pass
    os.makedirs(temp_dir, exist_ok=True)
