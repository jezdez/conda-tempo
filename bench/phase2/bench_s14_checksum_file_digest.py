#!/usr/bin/env python3
"""S14 microbenchmark: ``cph.utils._checksum`` vs stdlib ``hashlib.file_digest``.

Background (from Track B suspects):

    S14 | cph.utils._checksum is a Python-level chunked hash loop
          conda_package_handling/utils.py:97-101

    def _checksum(fd, algorithm, buffersize=65536):
        hash_impl = hashlib.new(algorithm)
        for block in iter(lambda: fd.read(buffersize), b""):
            hash_impl.update(block)
        return hash_impl.hexdigest()

Python 3.11+ ships ``hashlib.file_digest()`` which does the same thing
entirely in C — no Python-level fread/update loop. For a large file
(conda caches keep ~200 MB archives routinely) the difference is
whether we pay 3 000+ Python bytecode round-trips for every hash.

This benchmark hashes a 50 MB file with both implementations to quantify
the win. Two synthetic variants so the benchmark doesn't depend on
having any particular size of real package on disk.

``register_pyperf(runner, n)`` — n in megabytes. Registers two benches:
    s14_checksum_current_NMB    — the cph loop
    s14_checksum_file_digest_NMB — the hashlib.file_digest replacement

``register_memray(n)`` — a single SHA-256 via file_digest.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

_FIXTURE_CACHE: dict = {}


def _setup(mb: int) -> str:
    if mb in _FIXTURE_CACHE:
        return _FIXTURE_CACHE[mb]
    tmp_root = Path(
        os.environ.get("CONDA_BENCH_TMPDIR", tempfile.gettempdir())
    )
    tmp_root.mkdir(parents=True, exist_ok=True)
    path = tmp_root / f"s14-blob-{mb}mb.bin"
    target = mb * 1024 * 1024
    if not path.exists() or path.stat().st_size != target:
        # Pseudo-random content so hash is meaningful, written in 1 MB chunks.
        with path.open("wb") as fh:
            chunk = os.urandom(1024 * 1024)
            for _ in range(mb):
                fh.write(chunk)
    _FIXTURE_CACHE[mb] = str(path)
    return _FIXTURE_CACHE[mb]


def _checksum_cph(path: str) -> str:
    """The current cph implementation (conda_package_handling/utils.py)."""
    from conda_package_handling.utils import _checksum

    with open(path, "rb") as fd:
        return _checksum(fd, "sha256")


def _checksum_file_digest(path: str) -> str:
    """Proposed replacement using stdlib ``hashlib.file_digest``."""
    with open(path, "rb") as fd:
        return hashlib.file_digest(fd, "sha256").hexdigest()


def register_memray(mb: int) -> None:
    path = _setup(mb)
    _checksum_file_digest(path)


def main() -> int:
    import pyperf

    def _forward(cmd, args):
        cmd.extend(("-N", str(args.megabytes)))

    runner = pyperf.Runner(add_cmdline_args=_forward)
    runner.argparser.add_argument(
        "-N", "--megabytes", type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "50")),
        help="blob size in MB (default: 50)",
    )
    args = runner.parse_args()
    mb = args.megabytes
    path = _setup(mb)

    # Sanity: both impls produce the same hash on the same file.
    a = _checksum_cph(path)
    b = _checksum_file_digest(path)
    assert a == b, f"hash mismatch: {a!r} vs {b!r}"

    runner.metadata["s14_mb"] = str(mb)
    runner.bench_func(
        f"s14_checksum_current_{mb}mb",
        _checksum_cph,
        path,
    )
    runner.bench_func(
        f"s14_checksum_file_digest_{mb}mb",
        _checksum_file_digest,
        path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
