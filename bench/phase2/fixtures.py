#!/usr/bin/env python3
"""Shared fixtures for Phase 2 microbenchmarks.

Builders:

  * ``synthetic_prefix(n, *, tmpdir)`` — synthetic conda-meta/*.json
    prefix with N records. Used by S1/S2/S3/S11.
  * ``synthetic_prefix_replace_actions(m, *, tmpdir)`` — M real
    ``PrefixReplaceLinkAction`` instances over real files. Used by
    S4/S6.
  * ``synthetic_hardlink_actions(m, *, tmpdir)`` — M real
    ``LinkPathAction(link_type=HARDLINK)`` instances. Used by S7.
  * ``synthetic_py_packages(p, *, tmpdir, files_per_pkg=10)`` — P
    directories each containing K ``.py`` files, ready to feed into
    ``compile_multiple_pyc``. Used by S9.
  * ``synthetic_prefix_records(n, *, deps_per_record=5)`` — N in-memory
    ``PrefixRecord`` instances with pseudo-random deps. Used by S2.

All builders are idempotent and cheap to rerun.
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
    """Build a synthetic prefix with N conda-meta records under ``tmpdir``."""
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
    """Build ``m`` ``PrefixReplaceLinkAction`` instances over real files."""
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

    repodata_record = SimpleNamespace(name="synth", subdir="noarch")
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
    """Reset verified state + empty temp_dir between bench iterations."""
    import shutil

    for axn in actions:
        axn._verified = False
        axn.intermediate_path = None

    try:
        shutil.rmtree(temp_dir)
    except FileNotFoundError:
        pass
    os.makedirs(temp_dir, exist_ok=True)


def clear_target_prefix(target_prefix: str, *, subdirs: tuple[str, ...] = ()) -> None:
    """Wipe a target prefix so a fresh ``execute()`` pass can run."""
    import shutil

    try:
        shutil.rmtree(target_prefix)
    except FileNotFoundError:
        pass
    os.makedirs(target_prefix, exist_ok=True)
    for sub in subdirs:
        os.makedirs(os.path.join(target_prefix, sub), exist_ok=True)


def synthetic_hardlink_actions(
    m: int,
    *,
    tmpdir: Path,
    file_size: int = 4096,
):
    """Build ``m`` ``LinkPathAction(link_type=HARDLINK)`` instances."""
    from types import SimpleNamespace
    from conda.core.path_actions import LinkPathAction
    from conda.models.enums import LinkType, PathEnum
    from conda.models.records import PathDataV1

    pkg_dir = tmpdir / f"synth-link-pkg-{m}"
    target_prefix = tmpdir / f"synth-link-target-{m}"
    bin_dir = pkg_dir / "bin"
    for p in (pkg_dir, target_prefix, bin_dir, pkg_dir / "info"):
        p.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "info" / "index.json").write_text(
        '{"name": "synth", "version": "0.0.0", "subdir": "noarch"}'
    )

    content = b"A" * file_size
    source_short_paths = []
    for i in range(m):
        rel = f"bin/file-{i:06d}"
        full = pkg_dir / rel
        if not full.exists() or full.stat().st_size != file_size:
            full.write_bytes(content)
        source_short_paths.append(rel)

    repodata_record = SimpleNamespace(name="synth", subdir="noarch")
    package_info = SimpleNamespace(
        repodata_record=repodata_record,
        extracted_package_dir=str(pkg_dir),
        package_metadata=None,
    )
    transaction_context = {
        "temp_dir": str(tmpdir / f"synth-link-tmp-{m}"),
        "target_site_packages_short_path": None,
    }

    actions = []
    for rel in source_short_paths:
        source_path_data = PathDataV1(
            _path=rel,
            path_type=PathEnum.hardlink,
            sha256=None,
            size_in_bytes=file_size,
        )
        actions.append(
            LinkPathAction(
                transaction_context=transaction_context,
                package_info=package_info,
                extracted_package_dir=str(pkg_dir),
                source_short_path=rel,
                target_prefix=str(target_prefix),
                target_short_path=rel,
                link_type=LinkType.hardlink,
                source_path_data=source_path_data,
            )
        )
    return actions, transaction_context, str(target_prefix), str(pkg_dir)


def synthetic_py_packages(
    p: int,
    *,
    tmpdir: Path,
    files_per_pkg: int = 10,
    lines_per_file: int = 20,
):
    """Build ``p`` synthetic "packages" each with K .py files."""
    prefix = tmpdir / f"synth-pyc-{p}-{files_per_pkg}"
    prefix.mkdir(parents=True, exist_ok=True)

    def _py_content(i: int) -> str:
        lines = [f"# synthetic py file {i}", "import sys", ""]
        for j in range(lines_per_file):
            lines.append(f"def fn_{i}_{j}(x, y):")
            lines.append(f"    \"\"\"fn {i}.{j}\"\"\"")
            lines.append(f"    return x + y + {j}")
            lines.append("")
        return "\n".join(lines) + "\n"

    packages = []
    for pi in range(p):
        pkg_dir = prefix / f"pkg-{pi:06d}" / "site-packages" / f"pkgmod_{pi:06d}"
        pkg_dir.mkdir(parents=True, exist_ok=True)

        py_paths = []
        pyc_paths = []
        for ki in range(files_per_pkg):
            py = pkg_dir / (f"__init__.py" if ki == 0 else f"file_{ki-1}.py")
            if not py.exists():
                py.write_text(_py_content(pi * 1000 + ki))
            py_paths.append(str(py))
            pycache = pkg_dir / "__pycache__"
            pycache.mkdir(exist_ok=True)
            tag = f"cpython-{sys.version_info.major}{sys.version_info.minor}"
            pyc_paths.append(str(pycache / f"{py.stem}.{tag}.pyc"))
        packages.append((py_paths, pyc_paths))
    return packages, str(prefix)


def clear_pyc_cache(packages) -> None:
    """Remove all compiled .pyc files from a synthetic_py_packages fixture."""
    for _py_paths, pyc_paths in packages:
        for p in pyc_paths:
            try:
                os.remove(p)
            except FileNotFoundError:
                pass


def synthetic_prefix_records(n: int, *, deps_per_record: int = 5):
    """Build ``n`` in-memory ``PrefixRecord`` instances with simple
    bare-name deps. Kept for backwards compatibility with existing
    benches (S1/S2/S11) that were written against this shape. New
    benches should prefer :func:`synthetic_realistic_prefix_records`
    which produces version/build-constrained deps closer to real
    conda-forge distributions.

    The dependency graph is guaranteed acyclic: record ``i`` depends
    only on records with index ``< i``. Real conda prefixes are DAGs
    too (package deps can't cycle by construction). Without this
    guarantee the resulting ``PrefixGraph._toposort_handle_cycles``
    path dominates at O(N^2) and swamps whatever Track B suspect
    (e.g. S2 ``PrefixGraph.__init__``) is actually under test.

    RNG seeded with 42 so the fixture is deterministic across
    worker subprocesses.
    """
    import random

    from conda.models.records import PrefixRecord

    rng = random.Random(42)
    names = [f"pkg-{i:06d}" for i in range(n)]

    records = []
    for i, name in enumerate(names):
        # Pick deps only from records with a strictly smaller index
        # so the graph is acyclic.
        candidate_pool = names[:i]
        k = min(deps_per_record, len(candidate_pool))
        deps = rng.sample(candidate_pool, k) if k else []
        rec = PrefixRecord(
            name=name,
            version="0.0.0",
            build="py313_0",
            build_number=0,
            channel="synthetic",
            subdir="noarch",
            platform=None,
            depends=tuple(deps),
            md5="0" * 32,
            sha256="0" * 64,
            size=0,
            timestamp=0,
            fn=f"{name}-0.0.0-py313_0.conda",
            url="",
            files=[],
        )
        records.append(rec)
    return records


def synthetic_realistic_prefix_records(
    n: int,
    *,
    mean_deps: float = 2.5,
    max_deps: int = 30,
    constrained_fraction: float = 0.4,
    seed: int = 42,
):
    """Build ``n`` in-memory ``PrefixRecord`` instances with realistic
    dep structure.

    Matches conda-forge's observed distribution more closely than
    :func:`synthetic_prefix_records`:

    * **Fan-out**: power-law distributed (Zipf-ish). Mean ~2.5
      deps/record, tail extends to ``max_deps``. Matches a
      2024-era conda-forge environment's distribution.
    * **Version constraints**: ``constrained_fraction`` of deps
      (default 40 %) carry a ``>=X.Y`` version constraint that the
      matcher has to parse and evaluate. Rest are bare names.
    * **Build strings**: records have varied build strings
      (``py313_0``, ``h0a0a0a0_0``, ``pyhd8ed1ab_0``, etc.), spread
      across the fixture so build-string match paths get exercised.
    * **Version strings**: records have non-trivial versions
      (``1.2.3``, ``2.0.0rc1``, ``3.13``) so matchers that parse
      version ranges do meaningful work.
    * **Noarch/subdir mix**: 30 % ``noarch``, 70 % a host subdir.

    The graph stays acyclic via the same index-ordering trick as
    :func:`synthetic_prefix_records`. RNG is seeded for determinism.

    This fixture is designed to exercise ``MatchSpec.match()`` and
    ``PrefixGraph.__init__()`` with per-match work that looks like
    what conda actually does in production, not the near-trivial
    cost of matching bare names.
    """
    import random

    from conda.models.records import PrefixRecord

    rng = random.Random(seed)

    # Real conda-forge has a long tail of package-name styles. Keep
    # names synthetic but varied enough to exercise the by-name
    # index non-trivially (conda-forge has 20k+ packages; we generate
    # up to N unique names).
    names = [f"pkg-{i:06d}" for i in range(n)]

    # Version and build pools, picked lexically varied so matchers
    # have to actually compare.
    version_pool = [
        "0.1.0",
        "0.5.2",
        "1.0.0",
        "1.2.3",
        "1.4.1",
        "2.0.0",
        "2.1.0rc1",
        "3.0.0",
        "3.13",
        "4.5.6",
    ]
    build_pool = [
        "py313_0",
        "py313_1",
        "py312_0",
        "h0a0a0a0_0",
        "h1b1b1b1_0",
        "pyhd8ed1ab_0",
        "hc9c84f9_0",
        "0",
    ]
    subdir_pool = [
        "noarch",
        "noarch",
        "noarch",  # bias towards noarch 30 %
        "linux-64",
        "linux-64",
        "osx-arm64",
        "win-64",
        "linux-aarch64",
    ]

    def _pick_fanout() -> int:
        """Power-law fan-out: many records with 0-3 deps, long tail
        to ``max_deps``. Exponential distribution approximates the
        observed conda-forge shape (many packages with few deps,
        a small number with 20+)."""
        val = int(rng.expovariate(1.0 / mean_deps))
        return min(val, max_deps)

    def _pick_version() -> str:
        return rng.choice(version_pool)

    def _pick_build() -> str:
        return rng.choice(build_pool)

    def _pick_subdir() -> str:
        return rng.choice(subdir_pool)

    def _pick_dep_string(target_name: str, target_version: str) -> str:
        """Return a dep spec targeting ``target_name``. Varies:
        bare name / name+version constraint / name+version+build."""
        roll = rng.random()
        if roll < (1.0 - constrained_fraction):
            return target_name
        # Constrained: name >= X
        major = target_version.split(".", 1)[0]
        if roll < (1.0 - constrained_fraction) + constrained_fraction * 0.75:
            try:
                m = int(major)
                return f"{target_name} >={major}.0,<{m + 1}.0"
            except ValueError:
                return f"{target_name} >={target_version}"
        # Name with pinned version string
        return f"{target_name} {target_version}"

    records = []
    for i, name in enumerate(names):
        version = _pick_version()
        build = _pick_build()
        subdir = _pick_subdir()
        platform = None if subdir == "noarch" else subdir.split("-")[0]

        # Pick deps from records with strictly smaller index (DAG).
        candidate_pool = list(range(i))
        k = min(_pick_fanout(), len(candidate_pool))
        dep_indices = rng.sample(candidate_pool, k) if k else []
        deps = tuple(
            _pick_dep_string(names[j], records[j].version) for j in dep_indices
        )

        rec = PrefixRecord(
            name=name,
            version=version,
            build=build,
            build_number=0,
            channel="synthetic",
            subdir=subdir,
            platform=platform,
            depends=deps,
            md5="0" * 32,
            sha256="0" * 64,
            size=0,
            timestamp=0,
            fn=f"{name}-{version}-{build}.conda",
            url="",
            files=[],
        )
        records.append(rec)
    return records


def conda_packages_from_cache(
    min_size_bytes: int = 1_000_000,
    max_count: int = 10,
):
    """Return up to ``max_count`` real ``.conda`` packages from the
    caller's active package cache, filtered to those above
    ``min_size_bytes``.

    Used by S8 (extract pool scaling). Returns absolute filesystem
    paths sorted descending by file size. Returns [] if the cache
    has no eligible .conda files.
    """
    import json
    import subprocess

    try:
        out = subprocess.check_output(
            ["conda", "config", "--show", "pkgs_dirs", "--json"],
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    pkgs_dirs = json.loads(out).get("pkgs_dirs") or []

    candidates = []
    for pdir in pkgs_dirs:
        p = Path(pdir)
        if not p.is_dir():
            continue
        for f in p.glob("*.conda"):
            try:
                size = f.stat().st_size
            except OSError:
                continue
            if size >= min_size_bytes:
                candidates.append((size, str(f)))
    candidates.sort(reverse=True)
    return [path for _size, path in candidates[:max_count]]
