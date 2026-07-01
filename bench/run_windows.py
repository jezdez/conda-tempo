#!/usr/bin/env python3
"""Windows runner for Track B transaction-latency benchmarks.

This mirrors the shell-based macOS/Linux harness without depending on Bash,
POSIX paths, or hyperfine prepare/cleanup hooks. It writes hyperfine-shaped JSON
plus a compact Markdown summary so the report workflow can consume the results.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PHASE = "phase1_windows"
DEFAULT_PROFILE_PHASE = "phase1_windows"
DEFAULT_PHASE2 = "phase2_windows"
DEFAULT_W3_50K_TIMEOUT = 3600
PYTHON = sys.executable


WORKLOADS = {
    "w1": {
        "description": "fresh install, small",
        "args": ["create", "-n", "bench_w1", "-c", "conda-forge", "-y", "python=3.13", "requests"],
        "env": "bench_w1",
        "runs": 5,
        "warmups": 1,
    },
    "w2": {
        "description": "fresh data-science install",
        "args": [
            "create",
            "-n",
            "bench_w2",
            "-c",
            "conda-forge",
            "-y",
            "python=3.13",
            "pandas",
            "scikit-learn",
            "matplotlib",
            "jupyter",
        ],
        "env": "bench_w2",
        "runs": 5,
        "warmups": 1,
    },
    "w3": {
        "description": "synthetic-prefix dry-run install",
        "args": ["install", "-n", "bench_big", "-c", "conda-forge", "-y", "--dry-run", "--no-deps", "tzdata"],
        "env": None,
        "runs": 5,
        "warmups": 1,
    },
    "w4": {
        "description": "cold-cache data-science install",
        "args": [
            "create",
            "-n",
            "bench_w4",
            "-c",
            "conda-forge",
            "-y",
            "python=3.13",
            "pandas",
            "scikit-learn",
            "matplotlib",
            "jupyter",
        ],
        "env": "bench_w4",
        "runs": 3,
        "warmups": 0,
        "cold_cache": True,
    },
}


def conda_cmd(*args: str) -> list[str]:
    return [PYTHON, "-m", "conda", *args]


def run(cmd: list[str], *, timeout: int | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd), flush=True)
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise SystemExit(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr}"
        )
    return proc


def remove_env(name: str) -> None:
    run(conda_cmd("env", "remove", "-y", "-n", name), check=False)


def conda_version() -> str:
    proc = run(conda_cmd("--version"))
    return proc.stdout.strip().split()[-1]


def package_cache_dirs() -> list[Path]:
    proc = run(conda_cmd("config", "--show", "pkgs_dirs", "--json"))
    return [Path(pkgs_dir) for pkgs_dir in json.loads(proc.stdout)["pkgs_dirs"]]


def package_cache_dir() -> Path:
    pkgs_dir = package_cache_dirs()[0]
    if pkgs_dir.anchor == str(pkgs_dir) or len(pkgs_dir.parts) < 3:
        raise SystemExit(f"refusing to wipe unsafe package cache path: {pkgs_dir}")
    return pkgs_dir


def wipe_package_cache() -> Path:
    pkgs_dir = package_cache_dir()
    shutil.rmtree(pkgs_dir, ignore_errors=True)
    pkgs_dir.mkdir(parents=True, exist_ok=True)
    return pkgs_dir


def cached_conda_packages(*, min_size_bytes: int = 1_000_000) -> list[Path]:
    packages = []
    for pkgs_dir in package_cache_dirs():
        if not pkgs_dir.is_dir():
            continue
        for package in pkgs_dir.glob("*.conda"):
            try:
                size = package.stat().st_size
            except OSError:
                continue
            if size >= min_size_bytes:
                packages.append(package)
    return packages


def ensure_extract_cache(min_packages: int) -> None:
    os.environ["CONDA_BENCH_PKGS_DIRS"] = os.pathsep.join(
        str(pkgs_dir) for pkgs_dir in package_cache_dirs()
    )
    packages = cached_conda_packages()
    if len(packages) >= min_packages:
        return

    env_name = "bench_s8_cache_seed"
    remove_env(env_name)
    run(
        conda_cmd(
            "create",
            "-n",
            env_name,
            "-c",
            "conda-forge",
            "-y",
            "python=3.13",
            "pandas",
            "scikit-learn",
            "matplotlib",
            "jupyter",
        )
    )
    remove_env(env_name)
    packages = cached_conda_packages()
    if len(packages) < min_packages:
        raise SystemExit(
            f"S8 needs at least {min_packages} cached .conda packages; "
            f"found {len(packages)} after seeding"
        )
    os.environ["CONDA_BENCH_PKGS_DIRS"] = os.pathsep.join(
        str(pkgs_dir) for pkgs_dir in package_cache_dirs()
    )


def seed_big(records: int) -> None:
    run([PYTHON, "bench/seed_big_prefix.py", "--name", "bench_big", "--records", str(records)])


def summarize_times(times: list[float]) -> dict[str, float | list[float]]:
    mean = sum(times) / len(times)
    variance = sum((t - mean) ** 2 for t in times) / len(times)
    return {
        "mean": mean,
        "stddev": math.sqrt(variance),
        "median": sorted(times)[len(times) // 2],
        "min": min(times),
        "max": max(times),
        "times": times,
    }


def write_result(phase: str, workload: str, command: list[str], times: list[float], extra: dict[str, object]) -> None:
    out_dir = REPO_ROOT / "data" / phase / workload
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = summarize_times(times)
    payload = {
        "results": [
            {
                "command": " ".join(command),
                **stats,
            }
        ]
    }
    (out_dir / "hyperfine.json").write_text(json.dumps(payload, indent=2))
    (out_dir / "hyperfine.md").write_text(
        "| Command | Mean [s] | Min [s] | Max [s] | Relative |\n"
        "|---|---:|---:|---:|---:|\n"
        f"| {' '.join(command)} | {stats['mean']:.3f} | {stats['min']:.3f} | {stats['max']:.3f} | 1.00 |\n"
    )
    run_meta = {
        "conda_version": conda_version(),
        "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "phase": phase,
        **extra,
    }
    (out_dir / "run.json").write_text(json.dumps(run_meta, indent=2, sort_keys=True))


def run_workload(name: str, *, phase: str, records: int = 5000, timeout: int | None = None) -> None:
    spec = WORKLOADS[name]
    if name == "w3":
        seed_big(records)
    env_name = spec["env"]
    command = conda_cmd(*spec["args"])
    times = []
    failures = []
    for i in range(spec["warmups"] + spec["runs"]):
        is_warmup = i < spec["warmups"]
        if env_name:
            remove_env(env_name)
        pkgs_dir = None
        if spec.get("cold_cache"):
            pkgs_dir = wipe_package_cache()
        started = time.perf_counter()
        try:
            proc = run(command, timeout=timeout, check=False)
            elapsed = time.perf_counter() - started
        except subprocess.TimeoutExpired:
            elapsed = float(timeout or 0)
            proc = None
        if env_name:
            remove_env(env_name)
        if proc is None or proc.returncode != 0:
            failures.append(
                {
                    "iteration": i + 1,
                    "warmup": is_warmup,
                    "returncode": None if proc is None else proc.returncode,
                    "stderr": "timeout" if proc is None else proc.stderr[-2000:],
                }
            )
        elif not is_warmup:
            times.append(elapsed)
        print(f"{name} iteration {i + 1}: {elapsed:.3f}s", flush=True)
    if not times:
        raise SystemExit(f"{name}: no successful timed runs; failures={failures!r}")
    write_result(
        phase,
        name if records == 5000 else f"{name}_{records // 1000}k",
        command,
        times,
        {
            "records": records if name == "w3" else None,
            "failures": failures,
            "pkgs_dir": str(pkgs_dir) if spec.get("cold_cache") else None,
            "runner": "bench/run_windows.py",
        },
    )


def run_profiles(*, phase: str) -> None:
    profile_workloads = {
        "w1": WORKLOADS["w1"]["args"],
        "w2": WORKLOADS["w2"]["args"],
        "w4": WORKLOADS["w4"]["args"],
    }
    for name, args in profile_workloads.items():
        env_name = WORKLOADS[name]["env"]
        if env_name:
            remove_env(env_name)
        if name == "w4":
            wipe_package_cache()
        run([PYTHON, "bench/run_cprofile.py", "--phase", phase, name, "--", *args])
        if env_name:
            remove_env(env_name)
        if name == "w4":
            wipe_package_cache()
        run([PYTHON, "bench/parse_time_recorder.py", "--phase", phase, name, "--", *args])
        if env_name:
            remove_env(env_name)


def run_phase2(*, phase: str, mode: str) -> None:
    specs = [
        ("s6_verify_individual", ["50", "200", "1000"]),
        ("s7_link_parallel", ["200", "1000", "5000"]),
        ("s8_extract_pool", ["5"]),
        ("s9_pyc_batching", ["10", "30", "60"]),
        ("s10_prefix_record_writes", ["15", "150", "1000", "5000"]),
        ("s11_libmamba_installed", ["1000", "5000", "10000"]),
    ]
    for suspect, sizes in specs:
        if suspect == "s8_extract_pool":
            ensure_extract_cache(max(int(size) for size in sizes))
        run(
            [
                PYTHON,
                "bench/phase2/run_pyperf.py",
                suspect,
                "--sizes",
                *sizes,
                "--mode",
                mode,
                "--phase",
                phase,
            ]
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "mode",
        choices=[
            "w1",
            "w2",
            "w3",
            "w3-50k",
            "w4",
            "phase1",
            "phase1-profile",
            "phase2",
            "phase4",
            "all",
        ],
    )
    ap.add_argument("--phase", default=None, help="output dir under data/")
    ap.add_argument("--phase2-mode", choices=["fast", "full"], default="full")
    ap.add_argument("--timeout", type=int, default=None)
    ns = ap.parse_args()

    phase = ns.phase or (DEFAULT_PHASE if ns.mode != "phase4" else "phase4_windows")

    if ns.mode in {"w1", "w2", "w3", "w4"}:
        run_workload(ns.mode, phase=phase, timeout=ns.timeout)
    elif ns.mode == "w3-50k":
        run_workload("w3", phase=phase, records=50000, timeout=ns.timeout or DEFAULT_W3_50K_TIMEOUT)
    elif ns.mode == "phase1":
        for workload in ("w1", "w2", "w3"):
            run_workload(workload, phase=phase)
    elif ns.mode == "phase1-profile":
        run_profiles(phase=ns.phase or DEFAULT_PROFILE_PHASE)
    elif ns.mode == "phase2":
        run_phase2(phase=ns.phase or DEFAULT_PHASE2, mode=ns.phase2_mode)
    elif ns.mode == "phase4":
        for workload in ("w1", "w2", "w3", "w4"):
            run_workload(workload, phase=phase)
        run_workload("w3", phase=phase, records=50000, timeout=ns.timeout or DEFAULT_W3_50K_TIMEOUT)
    elif ns.mode == "all":
        for workload in ("w1", "w2", "w3"):
            run_workload(workload, phase=phase)
        run_profiles(phase=phase)
        run_phase2(phase=DEFAULT_PHASE2, mode=ns.phase2_mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
