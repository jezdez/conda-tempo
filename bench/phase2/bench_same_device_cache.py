#!/usr/bin/env python3
"""Compare package-cache selection for conda/conda#16347 on macOS APFS.

Run with a Python environment containing conda's development dependencies,
pytest, and pytest-mock. Both fixed revisions must exist in --conda-repo.
Conda Python modules are loaded from Git objects without changing the checkout.

Each sample runs in a fresh process. Timing includes cache selection, link-type
detection, and creation of 512 files of 64 KiB each. Source files are freshly
written and OS-cached, without fsync. Setup, imports, verification, and cleanup
are excluded. Base/head order alternates across repetitions.

Example:
    python bench/phase2/bench_same_device_cache.py \
        --conda-repo ../conda --output results.json --repeats 5
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import importlib.util
import json
import os
import platform
import plistlib
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = "7d3e81335f3016229f8f5dc7e82f69217d97f6d3"
HEAD = "6137fcd5c5dde3015bdd4d6b5f65cbf0dcb5a3c9"
FILES = 512
FILE_SIZE = 64 * 1024
PACKAGE = "demo-1.0-0"


def captured(args, *, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True)
    if result.returncode:
        raise RuntimeError(
            f"{Path(args[0]).name} failed with exit status {result.returncode}: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout


class GitSourceLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Import one revision's conda Python modules directly from Git objects."""

    def __init__(self, repo: Path, revision: str):
        self.repo = repo
        self.revision = revision
        files = (
            captured(
                ["git", "ls-tree", "-r", "--name-only", revision, "--", "conda"],
                cwd=repo,
            )
            .decode()
            .splitlines()
        )
        self.modules = {}
        for path in files:
            if path.endswith("/__init__.py"):
                self.modules[path[:-12].replace("/", ".")] = (path, True)
            elif path.endswith(".py"):
                self.modules[path[:-3].replace("/", ".")] = (path, False)

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.modules:
            source_path, package = self.modules[fullname]
            return importlib.util.spec_from_file_location(
                fullname,
                self.repo / source_path,
                loader=self,
                submodule_search_locations=(
                    [str(self.repo / Path(source_path).parent)] if package else None
                ),
            )
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        source_path, _ = self.modules[module.__name__]
        source = captured(
            ["git", "show", f"{self.revision}:{source_path}"], cwd=self.repo
        )
        exec(compile(source, str(self.repo / source_path), "exec"), module.__dict__)


def worker(repo: str, config_path: str, label: str, sample: str):
    config = json.loads(Path(config_path).read_text())
    revision = BASE if label == "base" else HEAD
    loader = GitSourceLoader(Path(repo), revision)
    sys.meta_path.insert(0, loader)

    import pytest
    from pytest_mock import MockerFixture

    from conda.base.context import context
    from conda.core.link import determine_link_type
    from conda.core.package_cache_data import PackageCacheData
    from conda.gateways.disk import test as disk_test
    from conda.gateways.disk.create import create_link
    from conda.models.records import PackageRecord

    remote = Path(config["remote_cache"])
    local = Path(config["local_cache"])
    target = Path(config["targets"]) / f"{label}-{sample}"
    target.mkdir()
    record = PackageRecord(**config["metadata"])
    mocker = MockerFixture(
        pytest.Config.fromdictargs({}, ["--noconftest", "-c", os.devnull])
    )
    try:
        for name, value in {
            "pkgs_dirs": (str(remote), str(local)),
            "always_copy": False,
            "always_softlink": False,
            "allow_softlinks": False,
        }.items():
            mocker.patch.object(
                type(context),
                name,
                new_callable=mocker.PropertyMock,
                return_value=value,
            )
        PackageCacheData.clear()
        loader_spy = mocker.spy(loader, "exec_module")

        start = time.perf_counter()
        selected = (
            PackageCacheData.get_entry_to_link(record)
            if label == "base"
            else PackageCacheData.get_entry_to_link(record, str(target))
        )
        source = Path(selected.extracted_package_dir)
        link_type = determine_link_type(str(source), str(target))
        for i in range(FILES):
            create_link(
                str(source / f"file-{i:04}.bin"),
                str(target / f"file-{i:04}.bin"),
                link_type,
            )
        elapsed_ms = (time.perf_counter() - start) * 1000

        imports_during_timing = loader_spy.call_count
        assert imports_during_timing == 0
        assert source.parent == (remote if label == "base" else local)
        assert link_type.name == "hardlink"
        same_inode = 0
        for i in range(FILES):
            src = source / f"file-{i:04}.bin"
            dst = target / f"file-{i:04}.bin"
            assert dst.stat().st_size == FILE_SIZE
            assert (
                hashlib.sha256(dst.read_bytes()).hexdigest() == config["payload_sha256"]
            )
            same_inode += (src.stat().st_dev, src.stat().st_ino) == (
                dst.stat().st_dev,
                dst.stat().st_ino,
            )
        assert same_inode == (0 if label == "base" else FILES)

        extra = {}
        if label == "head" and sample == "0":
            disk_test.paths_on_same_device.cache_clear()
            stat = mocker.spy(disk_test, "stat")
            for _ in range(10):
                assert PackageCacheData.get_entry_to_link(
                    record, str(target)
                ).extracted_package_dir == str(local / PACKAGE)
            extra["device_stat_calls_for_ten_selections"] = stat.call_count
            assert stat.call_count == 4
            assert PackageCacheData.get_entry_to_link(
                record
            ).extracted_package_dir == str(remote / PACKAGE)
            extra["without_target_keeps_first_cache"] = True
            mocker.patch.object(
                type(context),
                "pkgs_dirs",
                new_callable=mocker.PropertyMock,
                return_value=(str(remote),),
            )
            assert PackageCacheData.get_entry_to_link(
                record, str(target)
            ).extracted_package_dir == str(remote / PACKAGE)
            extra["no_same_device_keeps_first_cache"] = True
        print(
            json.dumps(
                {
                    "revision": revision,
                    "case": label,
                    "sample": int(sample),
                    "elapsed_ms": elapsed_ms,
                    "selected_cache": "remote" if label == "base" else "local",
                    "requested_link_type": link_type.name,
                    "effective_link_type": "copy fallback"
                    if label == "base"
                    else "hardlink",
                    "conda_modules_loaded_during_timing": imports_during_timing,
                    "files_verified": FILES,
                    "hardlinked_files": same_inode,
                    **extra,
                }
            )
        )
    finally:
        mocker.stopall()
        shutil.rmtree(target)


def benchmark(repo: Path, repeats: int):
    with tempfile.TemporaryDirectory(prefix="conda-pr16347-") as tmp:
        scratch = Path(tmp)
        mount = scratch / "remote-volume"
        mount.mkdir()
        image = scratch / "remote.dmg"
        captured(
            [
                "hdiutil",
                "create",
                "-size",
                "256m",
                "-fs",
                "APFS",
                "-volname",
                "PR16347Verification",
                str(image),
            ]
        )
        attached = False
        try:
            captured(
                [
                    "hdiutil",
                    "attach",
                    str(image),
                    "-nobrowse",
                    "-owners",
                    "on",
                    "-mountpoint",
                    str(mount),
                    "-plist",
                ]
            )
            attached = True
            remote = mount / "pkgs"
            local = scratch / "local-cache"
            targets = scratch / "targets"
            targets.mkdir()
            assert remote.parent.stat().st_dev != local.parent.stat().st_dev
            filesystem = {}
            for name, location in (("remote", mount), ("target", targets)):
                device = (
                    captured(["df", "-P", str(location)])
                    .decode()
                    .splitlines()[-1]
                    .split()[0]
                )
                info = plistlib.loads(captured(["diskutil", "info", "-plist", device]))
                filesystem[name] = info.get("FilesystemType") or info.get(
                    "FilesystemName"
                )
            if any(str(value).lower() != "apfs" for value in filesystem.values()):
                raise RuntimeError("Both benchmark filesystems must be APFS")
            metadata = dict(
                name="demo",
                version="1.0",
                build="0",
                build_number=0,
                channel="https://example.invalid/channel",
                subdir="noarch",
                fn=f"{PACKAGE}.conda",
                url=f"https://example.invalid/channel/noarch/{PACKAGE}.conda",
                md5="a" * 32,
                size=100,
            )
            payload = random.Random(16347).randbytes(FILE_SIZE)
            for cache in (remote, local):
                info_dir = cache / PACKAGE / "info"
                info_dir.mkdir(parents=True)
                (cache / "urls.txt").touch()
                for filename in ("index.json", "repodata_record.json"):
                    (info_dir / filename).write_text(json.dumps(metadata))
                for i in range(FILES):
                    (info_dir.parent / f"file-{i:04}.bin").write_bytes(payload)
            config_path = scratch / "config.json"
            config_path.write_text(
                json.dumps(
                    dict(
                        remote_cache=str(remote),
                        local_cache=str(local),
                        targets=str(targets),
                        metadata=metadata,
                        payload_sha256=hashlib.sha256(payload).hexdigest(),
                    )
                )
            )
            results = []
            for sample in range(repeats):
                for label in ("base", "head") if sample % 2 == 0 else ("head", "base"):
                    output = captured(
                        [
                            sys.executable,
                            str(Path(__file__).resolve()),
                            "_worker",
                            str(repo),
                            str(config_path),
                            label,
                            str(sample),
                        ],
                        cwd=repo,
                    )
                    results.append(json.loads(output))
            medians = {
                label: statistics.median(
                    result["elapsed_ms"]
                    for result in results
                    if result["case"] == label
                )
                for label in ("base", "head")
            }
            report = dict(
                base=BASE,
                head=HEAD,
                files=FILES,
                bytes_per_file=FILE_SIZE,
                repeats=repeats,
                timing_scope=(
                    "cache selection, link-type detection, and linking 512 files "
                    "into an existing empty target directory"
                ),
                source_files="freshly written and OS-cached, no fsync",
                filesystem=filesystem,
                distinct_source_devices=True,
                macos=captured(["sw_vers", "-productVersion"]).decode().strip(),
                architecture=captured(["uname", "-m"]).decode().strip(),
                python=platform.python_version(),
                median_ms=medians,
                speedup=medians["base"] / medians["head"],
                samples=results,
            )
        finally:
            if attached:
                result = subprocess.run(
                    ["hdiutil", "detach", str(mount)], capture_output=True
                )
                if result.returncode:
                    captured(["hdiutil", "detach", "-force", str(mount)])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conda-repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This benchmark requires macOS and APFS")
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    repo = args.conda_repo.resolve()
    for revision in (BASE, HEAD):
        captured(["git", "cat-file", "-e", f"{revision}^{{commit}}"], cwd=repo)
    report = benchmark(repo, args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "_worker":
        worker(*sys.argv[2:])
    else:
        main()
