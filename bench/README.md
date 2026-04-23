# bench/

Measurement harness for the [Track B transaction-latency research](../track-b-transaction.md).

> **Primary entry point is pixi** (see [`../pixi.toml`](../pixi.toml)).
> `pixi run phase1`, `pixi run phase2-s11`, `pixi run all`. The shell
> scripts in this directory (`workloads.sh`, `run_all.sh`,
> `setup_workspace.sh`) still work for ad-hoc invocations but are no
> longer the documented path. Details below.

## Quickstart with pixi

```
# One-time: install pixi (brew install pixi), then:
pixi install                    # resolve + install everything
pixi run verify-workspace       # confirm conda/cph/cps load from ../
pixi run phase1                 # hyperfine W1/W2/W3 (~5 min)
pixi run phase2-s11             # one suspect (~3 min)
pixi run all                    # everything in order (~90 min)
```

Linux (via Docker / OrbStack — same arm64 CPU, different kernel + FS):

```
pixi run linux-build            # build the Docker image (~1 min)
pixi run linux-all              # phase1 + profile + phase2 + scalene
pixi run linux-fetch            # copy data out to data/phase{1,2}_linux/
```

Cross-platform list of every task: `pixi task list`.

## What's here

| File | Purpose |
|---|---|
| `workloads.sh` | hyperfine driver for W1 (small install), W2 (data-science install), W3 (synthetic-prefix install, `--dry-run --no-deps tzdata` against a 5k-record `bench_big`). Writes JSON to `../data/phase1/<workload>/`. |
| `run_all.sh` | Unified macOS orchestrator (Phase 1 hyperfine + profile + Phase 2 pyperf + memray). Symmetric with `../docker/run_linux.sh`. Called from the pixi `all` task. |
| `run_cprofile.py` | cProfile wrapper around a single workload invocation. Writes `.prof` binaries and a top-20 text digest. |
| `run_memray.py` | memray wrapper around a single workload invocation. Writes aggregated `.bin`, summary table, peak-memory JSON, and HTML flamegraph. |
| `run_scalene.py` | Scalene wrapper around a single workload invocation. Writes JSON profile. Linux-only (conda-forge's macOS arm64e build is broken). |
| `seed_big_prefix.py` | Builds a synthetic prefix with N fake `conda-meta/*.json` records, plus a matching `history` file. Used to populate `bench_big` for W3 and future W4-full. |
| `parse_time_recorder.py` | Extracts per-phase timings from conda's `time_recorder` decorator output. |
| `setup_workspace.sh` | *Deprecated* — installs cph + cps editable via `pip -e` when the devenv is the old `conda/dev/start` setup. Superseded by `pixi.toml`'s pypi path-deps. Kept as a fallback for when pixi isn't available. |
| `tools/conda` | Shim that routes `conda` → `python -m conda.cli.main` to bypass the pip-install guard. Put on PATH by pixi's `[activation.env]`. See the file for details. |

## Prereqs

### With pixi (recommended)

- [pixi](https://pixi.sh) installed (`brew install pixi` or
  `curl -fsSL https://pixi.sh/install.sh | sh`).
- Sibling workspace checkouts under the parent of this repo:
  ```
  ~/Code/git/
      conda-tempo/              ← this repo
      conda/                    ← https://github.com/conda/conda
      conda-package-handling/   ← https://github.com/conda/conda-package-handling
      conda-package-streaming/  ← https://github.com/conda/conda-package-streaming
  ```
- hyperfine on PATH (`brew install hyperfine`). Everything else
  (conda, memray, pyperf, scalene) comes from conda-forge via pixi.

### Without pixi (legacy, uses `conda/dev/start`)

- `conda` on PATH (any supported version; the tempo plan targets `main`)
- `hyperfine` installed (`brew install hyperfine` or equivalent)
- `memray` installed into the conda env used to run the benchmarks
  (`conda install -c conda-forge memray` — see the debug-symbol caveat
  below if running on macOS with a stock conda-forge CPython).
- A separate conda prefix for `bench_big` (W3), created with
  `python bench/seed_big_prefix.py --name bench_big --records 5000`
  (Phase 1 uses 5k; the seed scales to 50k for future microbenchmarks
  but 50k is too slow for a 5-run hyperfine — see the Phase-0 scaling
  finding in `../track-b-transaction.md`.)
- First run of W1 and W2 needs network to populate the package cache.
  Subsequent runs reuse the cache.

### memray and the "No symbol information" warning

`run_memray.py` uses `--native` to unwind C-extension stacks. On macOS
with a stock conda-forge Python, memray will print:

> ⚠ No symbol information was found for the Python interpreter ⚠

This is accurate: conda-forge ships CPython with the exported symbol
table intact but with DWARF debug sections stripped. The practical
consequence in reports:

- Python-level stacks inside conda resolve fully (e.g. `main at
  conda/cli/main.py`) because Python frames come from pyc bytecode,
  not DWARF.
- C-level stacks show the function name but lose file:line
  (e.g. `_PyObject_Malloc at <unknown>` instead of
  `_PyObject_Malloc at Objects/obmalloc.c:1234`).
- The allocator breakdown (pymalloc vs arena vs libc malloc) still
  works because it keys on symbol name, not file:line.

For Track B Phase 1/2 purposes this is adequate — we mostly ask "which
conda function allocates" and the Python-level attribution is lossless.
If Phase 3 ever needs C-level file:line inside CPython, the path is
Linux with `python3-dbg` (Debian) or a custom CPython built with
`CFLAGS="-g"` in a separate devenv; neither is wired up today.

### Scalene on macOS

Scalene adds a per-line Python / native / system time decomposition
that neither cProfile nor memray provides. It's integrated via
[`run_scalene.py`](run_scalene.py) (Phase 1) and
[`phase2/run_scalene.py`](phase2/run_scalene.py).

**Current limitation:** the conda-forge scalene build for Python 3.13
on macOS 26 fails to load with:

    ImportError: dlopen(...) cpu type/subtype in slice (arm64e.old)
    does not match fat header (arm64e)

This is a conda-forge rebuild-needed issue (Xcode 16 SDK), not a
scalene bug. As of writing there is no working combination of
conda-forge scalene + macOS 26 + Python 3.13 arm64. Options:

- Run Scalene inside the Linux Docker container at `../docker/`
  (recommended — the container's conda-forge Linux scalene works
  fine, and Scalene's decomposition is actually more useful on Linux
  where DWARF symbols are intact).
- Install Scalene via pip (`pip install scalene` in the devenv).
  This works but introduces a non-conda dependency and conflicts with
  the project's conda-first tooling rule — not recommended.
- Wait for a rebuilt conda-forge arm64 scalene.

The Phase-1 and Phase-2 Scalene data at
[`../data/phase1_linux/<w>/scalene.json`](../data/phase1_linux/) and
[`../data/phase2_linux/<suspect>/scalene_n<N>.json`](../data/phase2_linux/)
come from the Linux container.

### Linux confirmation runs

The `docker/` directory at the repo root ships a Dockerfile and
driver script that reproduces the full harness inside a Linux arm64
container (OrbStack, Docker Desktop, or any arm64 Linux host works):

```
docker build --platform linux/arm64 -t conda-tempo-linux docker/
docker run --rm -v $(pwd):/repo:ro -v conda-tempo-work:/work \
    conda-tempo-linux bash /repo/docker/run_linux.sh all
docker run --rm -v conda-tempo-work:/work:ro -v $(pwd)/data:/out \
    conda-tempo-linux bash -c "cp -r /work/data/. /out/phase1_linux/"
```

The container:

- Installs conda main at a pinned SHA, builds the devenv with
  `dev/start -p 3.13 -i miniforge -u`.
- Installs memray, pyperf, scalene from conda-forge Linux (no arm64e
  issue).
- Benchmarks run on container-internal ext4; the repo is bind-mounted
  read-only for source code only — I/O-heavy benchmarks don't touch
  the host filesystem.

See [`../track-b-transaction.md`](../track-b-transaction.md#linux-confirmation-run-docker--orbstack-arm64)
for the macOS-vs-Linux comparison table produced by a full run.

## Run everything

```
bench/workloads.sh all
```

Or individually:

```
bench/workloads.sh w1        # small fresh install (~15 pkgs)
bench/workloads.sh w2        # data-science fresh install (~150 pkgs)
bench/workloads.sh w3        # synthetic-prefix install (--dry-run --no-deps tzdata against bench_big)
```

Output goes to `../data/phase1/<workload>/`:

- `hyperfine.json` — raw timing data (5 runs after 1 warmup)
- `cprofile.prof` + `cprofile.top20.txt` — from `run_cprofile.py` (invoke separately)
- `memray.bin` + `memray.summary.txt` + `memray.meta.json` + `memray.flamegraph.html` — from `run_memray.py` (invoke separately)
- `time_recorder.json` — per-phase timings (invoke separately)

## Machine metadata

Before committing data, write `../data/machine.json` with:

```json
{
  "host": "e.g. M3 Max 14-core, 36 GB",
  "os": "macOS 15.2 (Darwin 25.3.0)",
  "fs": "APFS on NVMe",
  "conda_version": "e.g. 26.1.1",
  "python_version": "3.13.12",
  "date": "YYYY-MM-DD",
  "notes": "anything worth flagging (thermal, battery, load)"
}
```

One file per machine is enough; operators can overwrite on subsequent runs.

## Scope rules

The harness itself must stay thin. No inference, no regression detection, no
CI integration yet. Its job is: run the workload, save the numbers, exit. The
interpretation happens in `../track-b-transaction.md`.
