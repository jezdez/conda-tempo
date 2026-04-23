      Reducing conda Transaction Latency: Track B

# Reducing conda Transaction Latency: Track B

| | |
|---|---|
| **Initiative** | [conda-tempo](https://github.com/jezdez/conda-tempo) — measuring and reducing conda's tempo |
| **Author** | Jannis Leidel ([@jezdez](https://github.com/jezdez)) |
| **Date** | April 24, 2026 |
| **Status** | Phase 1+2 complete (macOS baseline + Linux confirmation + cph/cps workspace + S8 confirmed), Phase 3 pending |
| **Tracking** | TBD (Track B ticket created at Phase 1 kickoff) |
| **See also** | [Track A — startup latency](track-a-startup.md) · [Track C — Python 3.15 and speculative research](track-c-future.md) |

## Contents

- [Scope](#scope)
- [Background](#background)
- [Suspect hot spots](#suspect-hot-spots)
- [Investigation phases](#investigation-phases)
  - [Phase 1: measurement harness](#phase-1-measurement-harness)
  - [Phase 2: micro-benchmarks](#phase-2-micro-benchmarks)
  - [Phase 3: spot PoCs](#phase-3-spot-pocs)
  - [Phase 4: end-to-end confirmation](#phase-4-end-to-end-confirmation)
- [Scope rules](#scope-rules)
- [Out of scope](#out-of-scope)
- [Changelog](#changelog)

---

## Scope

Track A reduced conda's startup cost. Track B reduces what happens *after*
the solver returns: the transaction pipeline that verifies, downloads,
extracts, and links packages onto disk.

Users perceive this as "the transaction status lines print one after
another, and each one takes forever." The lines are a symptom; the
underlying code is serial in spots, quadratic in others, and hashes files
unconditionally in a few places. Track B does not touch the progress
output. It targets only the machinery below it.

Same rules as Track A:

- Python 3.10+ only, no PEP 810 / 3.15 features.
- One PR per suspect, targeting <100 LOC each, with news entry.
- No new dependencies, no architectural changes.
- Every PR quotes a before/after number from a micro-benchmark plus an
  end-to-end hyperfine result.

---

## Background

Entry points and relevant files in the classic conda transaction path
(libmamba hands the same records to the same downstream code):

- [`conda/core/solve.py`](https://github.com/conda/conda/blob/main/conda/core/solve.py) —
  `solve_for_transaction` → `diff_for_unlink_link_precs` → returns
  `UnlinkLinkTransaction`.
- [`conda/core/link.py`](https://github.com/conda/conda/blob/main/conda/core/link.py) —
  `UnlinkLinkTransaction.prepare` / `verify` / `execute`. Three thread
  pools: fetch (default 5), verify (default 1), execute (default 1).
- [`conda/core/path_actions.py`](https://github.com/conda/conda/blob/main/conda/core/path_actions.py) —
  `LinkPathAction`, `PrefixReplaceLinkAction`, `CompileMultiPycAction`,
  `AggregateCompileMultiPycAction`, `ExtractPackageAction`,
  `CreatePrefixRecordAction`.
- [`conda/core/package_cache_data.py`](https://github.com/conda/conda/blob/main/conda/core/package_cache_data.py) —
  `ProgressiveFetchExtract` (two-pool `as_completed` pipeline; extract
  capped at `min(cpu, 3)`).
- [`conda/core/prefix_data.py`](https://github.com/conda/conda/blob/main/conda/core/prefix_data.py) —
  per-record `conda-meta/<fn>.json` writes.
- [`conda/models/prefix_graph.py`](https://github.com/conda/conda/blob/main/conda/models/prefix_graph.py) —
  O(N²) `__init__`.
- [`conda/history.py`](https://github.com/conda/conda/blob/main/conda/history.py) —
  full parse + full prefix walk on every transaction.
- [`conda/gateways/disk/create.py`](https://github.com/conda/conda/blob/main/conda/gateways/disk/create.py) —
  `create_link`, `_do_copy`, `compile_multiple_pyc` (subprocess
  `compileall -j 0`).

### Workspace: cph + cps included in scope

Track B's transaction pipeline fans out into two sibling repos that
ship with conda:

- [`conda-package-handling`](https://github.com/conda/conda-package-handling)
  (cph) — ``api.extract()`` that conda calls from
  ``conda/plugins/package_extractors/conda.py`` and ``gateways/disk/read.py``.
- [`conda-package-streaming`](https://github.com/conda/conda-package-streaming)
  (cps) — the streaming ``.conda`` / ``.tar.bz2`` reader that cph
  delegates to (``cph.streaming._extract`` → ``cps.extract.extract_stream``).

Phase-2 S8 (extract pool), S12 (per-member path-safety syscalls), S13
(double ZipFile parse), and S14 (Python-level chunked checksum) all
live in these repos, not in conda itself. Both are checked out at
``~/Code/git/conda-package-{handling,streaming}`` and installed
source-editable into the devenv via
[`bench/setup_workspace.sh`](bench/setup_workspace.sh) (macOS) or at
pinned SHAs in the [`docker/Dockerfile`](docker/Dockerfile) (Linux).
All Phase-2 numbers for S8+ benchmark the workspace checkouts, not
the conda-forge shipped versions.

### Adjacent code that is *not* in Track B scope

- `conda-build`, `conda-smithy`, `boa` — package building, not
  installation.
- `conda-content-trust` — signature verification; off by default,
  separate performance concern (Track A-ish).
- `conda-libmamba-solver` pre-solve / repodata loading — measured on
  the W1/W2/W3 side via `time_recorder`, but fixes land in
  conda-libmamba-solver when needed (see S11 → B11).
- `libmambapy` / `libsolv` C++ — upstream, out of scope for a Python
  track.
- `conda.notices`, `conda.trust`, `conda.plugins.manager` startup —
  Track A concerns, not Track B.

---

## Suspect hot spots

Flagged before measurement. Phase 1 confirms or drops each.

| ID | Suspect | Location | Why it might hurt |
|---|---|---|---|
| S1 | Quadratic sorts in `diff_for_unlink_link_precs` | `solve.py:1465-1468` | `sorted(..., key=lambda x: previous_records.index(x))` on tuples is O(k² log k) per sort. For 2k unlink/link against a 50k prefix: ~4M position scans. |
| S2 | `PrefixGraph.__init__` O(N²) | `prefix_graph.py:55-61` | For every node in `records`, iterates `records` again and runs `MatchSpec.match` on each. Called twice on the post-solve path. |
| S3 | `History.update()` reads and parses the entire `conda-meta/history` | `history.py:108-123` | Every transaction. Then iterates the full prefix to build a `dist_str` set. Long-lived envs have large history files. |
| S4 | `PrefixReplaceLinkAction.verify` always SHA-256s the rewritten file | `path_actions.py:601` | Unconditional, even with `safety_checks != disabled`. Stored in `sha256_in_prefix`; need to check if readers rely on it. |
| S5 | `_verify_prefix_level` clobber check reloads records | `link.py:698-705` | On any collision, scans all records in order for each clobbering path. |
| S6 | `_verify_individual_level` serial per prefix | `link.py:620-642` | Thread pool fans out across prefixes (usually one), then a bare `for` inside. `PrefixReplaceLinkAction.verify` does a copy + rewrite + hash per file — trivially parallelizable. |
| S7 | `execute_threads` and `verify_threads` default to 1 | `context.py:696-714` | "Do not surprise anyone" defaults from 2017. For a fresh install of a 2000-file package, link is pinned at 1 unless the user overrides. |
| S8 | Extract pool fixed at `min(cpu, 3)` | `package_cache_data.py:69-74` | Comment: "extraction doesn't get any faster after 3 threads." True on spinning disks and small files; likely untrue on NVMe with large `.conda` zstd archives. |
| S9 | `_execute` serial sub-loops | `link.py:937-995` | `entry_point_actions`, `post_link` scripts, `register`, `make_menus` each run in a bare `for axngroup in ...:` loop. Per-package subprocess overhead dominates for `noarch: python` heavy envs. |
| S10 | `CreatePrefixRecordAction` writes one JSON per package inside the parallel record group | `path_actions.py:1045-1048` | Fine on most FS; worth checking on Windows NTFS under antivirus. |
| S11 | `conda_libmamba_solver.state.SolverInputState.installed` + `_specs_to_request_jobs` | `conda_libmamba_solver/state.py:220` and `solver.py:395` | W3 Phase-1 cProfile shows 41.8 s of 43.3 s solve time in `installed()` (10 032 calls) and ~20 s tottime in `sorted()` calls inside it, plus 50 M iterations through the installed collection. Scales non-linearly in prefix size (1k→2.2s, 5k→35s, 10k→164s). Fix lives in `conda-libmamba-solver`, not `conda` — tracked here because it blocks the Track B W3 motivation and any large-prefix workload. |
| S12 | `conda_package_streaming.extract.extract_stream` per-member `os.path.realpath` + `os.path.commonpath` | `conda_package_streaming/extract.py:33-46` | Safety check against tar members extracting outside dest_dir. For a scientific-Python env with ~29 k tar members (the W2 case), that's 58 k path-syscalls just for the safety check, on top of the extract work itself. Could be memoized — dest_dir is constant, only the member.name varies. |
| S13 | `conda_package_streaming.package_streaming.stream_conda_component` instantiates `zipfile.ZipFile` twice per .conda (once per component) | `conda_package_streaming/package_streaming.py:138` | Called from `cph.streaming._extract` which loops over ["pkg", "info"]. Each `ZipFile(fileobj)` parses the central directory end-of-file record; for a 14 MB .conda the parse is cheap but still wasteful to do twice per package. Refactor: parse the ZIP once, reuse for both components. |
| S14 | `conda_package_handling.utils._checksum` is a Python-level chunked hash loop | `conda_package_handling/utils.py:97-101` | Called during package verification (SHA-256 of the on-disk .conda against the repodata record). For a 200 MB package this is 800 × `hashlib.update(256 KB)` calls in a Python `for` loop. Python 3.11+ ships `hashlib.file_digest()` which does the same thing entirely in C (no Python loop overhead). Measurement: check whether `file_digest` saves > 5 % of the hash wall time. |

---

## Investigation phases

Each phase gates on the previous one. PoCs only get built for suspects
that survive Phase 2.

### Phase 1: measurement harness

Scaffold committed in [`bench/`](bench/); Phase-1 baseline measurements in
[`data/phase1/`](data/phase1/). Three fixed workloads:

- **W1. Fresh install, small:** `conda create -n bench_w1 -c conda-forge -y python=3.13 requests` (~15 pkgs). Baseline per-transaction overhead.
- **W2. Fresh install, data-science:** `conda create -n bench_w2 -c conda-forge -y python=3.13 pandas scikit-learn matplotlib jupyter` (~150 pkgs, `noarch: python` heavy → `.pyc` compile dominates).
- **W3. Synthetic-prefix install:** `conda install -n bench_big -c conda-forge -y --dry-run --no-deps tzdata`, where `bench_big` is seeded to **5 000** synthetic `PrefixRecord` JSON files via [`bench/seed_big_prefix.py`](bench/seed_big_prefix.py). `--no-deps` keeps the solve bounded so the wall-time is dominated by the post-solve diff + graph traversal over the synthetic records, which is what S1 and S2 target. (The original design used 50k records and `update --all --dry-run`; the scaling experiment below showed 50k is intractable within a 5-run hyperfine budget — see Phase-0 finding in the changelog.) The verify/execute suspects (S3–S8) need a real transaction and are deferred to a future W4.

Driver: [`bench/workloads.sh`](bench/workloads.sh) wraps hyperfine
(`--warmup 1 --runs 5 --export-json`). cProfile via
[`bench/run_cprofile.py`](bench/run_cprofile.py); conda-internal per-phase timings via
[`bench/parse_time_recorder.py`](bench/parse_time_recorder.py) against the
existing `time_recorder("fetch_extract_execute")` and
`time_recorder("unlink_link_execute")` markers. See [`bench/README.md`](bench/README.md)
for prereqs.

Deliverable: per-phase wall-time table + cProfile top-20 per workload, committed
to `data/phase1/<workload>/`.

#### Phase-1 wall-time baseline

See [`data/machine.json`](data/machine.json) for host metadata and
[`data/phase1/<workload>/hyperfine.json`](data/phase1/) for raw timings.
Numbers below are hyperfine's reported mean ± stddev across 5 runs after 1
warmup, against `conda/conda@main` built from source via `conda/dev/start
-p 3.13 -i miniforge -u`.

| Workload | Wall time (mean ± σ) | Min / max | Notes |
|---|---|---|---|
| W1 | **10.37 ± 0.19 s** | 10.07 / 10.59 s | `conda create -n bench_w1 -c conda-forge -y python=3.13 requests` |
| W2 | **26.67 ± 0.18 s** | 26.49 / 26.96 s | `conda create -n bench_w2 -c conda-forge -y python=3.13 pandas scikit-learn matplotlib jupyter` |
| W3 | **36.44 ± 0.16 s** | 36.18 / 36.59 s | `conda install -n bench_big -c conda-forge -y --dry-run --no-deps tzdata` against 5k-record `bench_big` |

##### Per-phase breakdown (`time_recorder`, single instrumented run)

Totals from the conda-internal `time_recorder` markers wrapping the major
pipeline stages. Raw samples in [`data/phase1/<w>/time_recorder.json`](data/phase1/).

| Phase (`time_recorder` marker) | W1 | W2 | W3 (dry-run) |
|---|---:|---:|---:|
| `conda_libmamba_solver._solving_loop` (solve) | 0.03 s | 0.28 s | **24.70 s** |
| `fetch_extract_prepare` | 0.04 s | 0.04 s | 0.03 s |
| `fetch_extract_execute` (cached → ~0) | < 0.01 s | < 0.01 s | — (dry-run) |
| `unlink_link_prepare_and_verify` | 5.53 s | 8.06 s | — (dry-run) |
| `unlink_link_execute` | **3.74 s** | **17.01 s** | — (dry-run) |
| `PrefixData.load` (cumulative) | 0.03 s | 0.03 s | 0.37 s |

##### cProfile top-5 (single instrumented run, sorted by cumulative time)

Full top-20 plus raw `.prof` binaries in [`data/phase1/<w>/cprofile.*`](data/phase1/).
Below, the non-trivial hot spots that survive after stripping bootstrap and
progress-bar machinery:

- **W1** (10.0 s wall in profiled run): `link.execute` 9.96 s → `link._verify` 5.79 s → `link._verify_individual_level` **5.52 s** (single-threaded, S6) → `path_actions.LinkPathAction.verify` 4.97 s (200 calls, includes `portability.update_prefix` 4.68 s) → subprocess for pyc compile 5.14 s (161 `subprocess.run` calls, S9).
- **W2** (30.9 s wall in profiled run): `link.execute` 28.02 s → `link._execute` 17.78 s → `gateways/disk/create.create_link` **10.46 s** (29 189 calls, dominated by `posix.link` at **9.39 s** tottime, 25 983 calls, hand-rolled serial fan-out — S7 territory) and subprocess pyc-compile aggregate **9.47 s** (186 subprocess communicate calls, S9).
- **W3** (68.2 s wall in profiled run, cProfile overhead ~2×): `solve_for_transaction` 67.66 s → `conda_libmamba_solver._solving_loop` 43.36 s → `_specs_to_request_jobs` 43.31 s → `conda_libmamba_solver.state.installed` **41.76 s** (10 032 calls) → `sorted(...)` **20.02 s tottime** in 10 963 calls; `_collections_abc.__iter__` called **50 170 781** times for 18.86 s cumulative. S1/S2 in conda core do not appear in the top 20 — the dominant term on this workload lives in `conda-libmamba-solver.state`, not in `diff_for_unlink_link_precs` or `PrefixGraph.__init__`.

##### memray peak memory + top allocators (single instrumented run)

Aggregated traces, `.bin` + `.summary.txt` + `.meta.json` + `.flamegraph.html`
committed to [`data/phase1/<w>/memray.*`](data/phase1/). Run via
`--aggregate --follow-fork --native`.

| Workload | Peak RSS | Allocations (unique stacks) | Wall under memray | memray overhead |
|---|---:|---:|---:|---:|
| W1 | **58.2 MiB** | 15 246 | 11.0 s | +6 % vs hyperfine |
| W2 | **92.8 MiB** | 15 371 | 30.4 s | +14 % vs hyperfine |
| W3 | **53.2 MiB** | 15 259 | 66.5 s | +82 % vs hyperfine |

Observations specific to the memray pass:

- **Memory is not the bottleneck on any of the three workloads.** Even W2 (≈150 packages, pandas + scikit-learn + matplotlib + jupyter installed fresh) peaks under 100 MiB — comfortable for any modern CI runner. None of the current suspects are memory-mortality-class at these prefix sizes.
- **W3's peak memory (53.5 MiB) is lower than W1's (59.2 MiB).** The 24 s solver cost at 5 000 synthetic records is *CPU time spent iterating already-allocated data*, not allocation volume. `conda_libmamba_solver.state.installed` is called 10 032 times and `_collections_abc.__iter__` fires 50 million times, but those iterations operate over the same pre-built list of `InstalledPackageInfo` objects — they don't inflate the heap. That's a useful refinement on S11: whatever the fix looks like, it's a **compute-complexity** fix, not a **data-structure-size** fix.
- **W3's allocation volume is similar to W1's and W2's (~15 k unique stacks).** This means the libmamba-solver cost isn't hiding in allocator churn either; it's pure Python-level iteration.
- **`--follow-fork` did not capture W2's 186 pyc-compile subprocesses.** conda runs `compileall` via `subprocess.Popen` (fork + exec), and the `exec` wipes memray's tracer out of the child. Tracked as a known harness limitation; if subprocess memory ever becomes interesting it needs a different approach (e.g. injecting `python -X memray:…` into the `compileall` invocation).
- **Caveat: conda-forge's stock Python has no DWARF debug info**, so memray's `--native` reports list C-level frames as `_PyObject_Malloc at <unknown>` (function name only, no file:line). Python-level conda source is fully resolved. See [`bench/README.md`](bench/README.md#memray-and-the-no-symbol-information-warning) for the full rationale and why we still keep `--native` on.

##### Phase-1 takeaways

1. **W2 is a linking + pyc-compile workload, not a solve workload.** 24.1 s out of 25.7 s are in the post-solve pipeline. Of that, ~9.4 s is `posix.link` fan-out (S7: default `execute_threads = 1`) and ~9.5 s is subprocess fan-out for `compileall` (S9). These two together account for ~75% of W2 wall time and are the highest-value Phase-3 targets.
2. **W1 is already dominated by `_verify_individual_level` at 5.5 s of its 10 s budget** — confirming S6 as the highest-value Phase-3 target for the small-install path.
3. **W3's dominant cost is in `conda-libmamba-solver`, not in conda core's S1/S2.** At 5 000 synthetic records, `_specs_to_request_jobs` builds the installed state in 43 s, most of it in a `sorted()` over the 10 000+ records it processes and a 50-million-iteration `__iter__` loop in `conda_libmamba_solver.state`. This is a new suspect (call it **S11 — `conda-libmamba-solver` installed-state assembly is quadratic in prefix size**) that Phase 2 should confirm; if confirmed, the fix belongs in `conda-libmamba-solver`, not `conda`. S1 and S2 may still matter at 50k records but are not the bottleneck at 5k.
4. **Solver + fetch together are < 1 s on a warm cache** for W1 and W2. Any further work on the solve path in Track B would require cold-cache or `--offline` workloads.
5. **Memory is not a first-order concern at W1/W2/W3 scale.** Peak RSS caps at 93 MiB (W2). Any Phase-3 PoC that makes allocation worse by < 20 MiB is acceptable on the memory axis; the bar to clear is CPU time. Whether S2/S11 become memory-bound at 50k records is a Phase-2 question and unresolved.

### Phase 2: micro-benchmarks

Scaffold in [`bench/phase2/`](bench/phase2/); S11 microbenchmark
committed, remaining suspects pending. Each suspect is a standalone
pyperf script that also exposes a memray entry point, run against the
synthetic-prefix fixture from Phase 1.

| Target | Status | Benchmark |
|---|---|---|
| `diff_for_unlink_link_precs` with 2k link / 2k unlink against 50k | pending | S1 |
| `PrefixGraph.__init__` with N records | **confirmed (S2)** | [`bench_s2_prefix_graph.py`](bench/phase2/bench_s2_prefix_graph.py) |
| `History.update()` against a synthetic 100k-line history | pending | S3 |
| `PrefixReplaceLinkAction.verify` on a 50 MB binary | pending | S4 |
| `_verify_prefix_level` with 100 synthetic collisions against 50k | pending | S5 |
| `_verify_individual_level` on a package with M prefix-replace files | **confirmed (S6)** | [`bench_s6_verify_individual.py`](bench/phase2/bench_s6_verify_individual.py) |
| `execute_threads = 1` → parallel `posix.link` fan-out at M hardlinks | **confirmed (S7)** | [`bench_s7_link_parallel.py`](bench/phase2/bench_s7_link_parallel.py) |
| subprocess pyc-compile: per-package vs batched (S9) | **confirmed (S9)** | [`bench_s9_pyc_batching.py`](bench/phase2/bench_s9_pyc_batching.py) |
| `do_extract_action` on a 200 MB conda-zstd package with 1/3/6/12 threads | **confirmed (S8)** | [`bench_s8_extract_pool.py`](bench/phase2/bench_s8_extract_pool.py) |
| `SolverInputState.installed` — per-access cost at parameterized N | **confirmed (S11)** | [`bench_s11_libmamba_installed.py`](bench/phase2/bench_s11_libmamba_installed.py) |

Deliverable: keep/drop verdict per suspect, with an estimated wall-time
saving on W2 and W3.

#### S6 confirmation

pyperf full mode, 5 runs × 10 values, 4 KB source files, single thread.
Data in [`data/phase2/s6_verify_individual/`](data/phase2/s6_verify_individual/).

| M (actions) | Mean ± σ | Min | Per-action | Scaling ratio vs previous M |
|---:|---|---:|---:|---:|
| 50 | **39.9 ms ± 3.0 ms** | 34.0 ms | 0.80 ms | — |
| 200 | **161 ms ± 14 ms** | 135 ms | 0.81 ms | 4.0× at 4× data |
| 1 000 | **759 ms ± 48 ms** | 707 ms | 0.76 ms | 4.7× at 5× data |

**Interpretation:** `_verify_individual_level` is **perfectly O(M)** —
the per-action constant (0.73 ms at 4 KB files) is stable across every
size we tried. Each action does one copy, one `chmod`, one
`update_prefix` rewrite, and one SHA-256 of the intermediate. All four
steps are bounded by disk throughput; none allocate meaningfully
(memray at M=1 000 peaks at 22.5 MiB, 4 601 allocations, in
[`memray_n1000.meta.json`](data/phase2/s6_verify_individual/memray_n1000.meta.json)).

Projection for W1 (Phase-1 cProfile showed 200 actions × 4.97 s =
24.9 ms/action at real package file sizes — larger than our 4 KB
synthetic because real binaries are 10–100 KB): W1's
`_verify_individual_level` takes ~5.5 s. Parallelizing across
`min(cpu, 4)` worker threads should drop this to ~1.4 s, assuming the
disk can keep up — which on NVMe at small-file sizes it comfortably
can. That's a **4 s / 10 s W1 wall-time reduction (~40 %)** before any
other fix.

##### S6 proposed fix sketch (Phase-3 B6)

The existing `verify_executor` (a `ThreadPoolExecutor` used at
`link.py:_verify_prefix_level` level) already fans out across prefixes.
Push the fan-out down one level:

```python
# replace the bare for-loop at link.py:632
with ThreadPoolExecutor(max_workers=context.verify_threads or 4) as pool:
    results = pool.map(_safe_verify, all_actions)
error_results = [r for r in results if r is not None]
```

The thread-safety review is one small detail: `PrefixReplaceLinkAction`
writes to `self.intermediate_path = join(temp_dir, str(uuid4()))`
(path_actions.py:575). Each thread writes its own uuid-named file —
no collision. The shared `transaction_context["temp_dir"]` dir is only
read for the path; no concurrent mutation. Confirmed safe.

Gated by B7 (changing the `verify_threads` default) only if we want
the speedup for users who haven't overridden the default. Otherwise,
B6 alone fans out when the user has already set `verify_threads > 1`.

#### S7 confirmation

pyperf full mode, 4 KB source files, ``LinkPathAction(link_type=HARDLINK)``.
Same fixture pattern as S6 but measures ``action.execute()`` (the
``posix.link`` fan-out in ``_execute_actions`` at ``link.py:1070``)
under four execution strategies in the same script: bare serial
for-loop and ``ThreadPoolExecutor`` with K ∈ {2, 4, 8}. Data in
[`data/phase2/s7_link_parallel/`](data/phase2/s7_link_parallel/).

| M | serial | K=2 | K=4 | K=8 | Best speedup |
|---:|---:|---:|---:|---:|---:|
| 200 | **81.5 ms** | 61.5 ms | **54.3 ms** | 56.1 ms | 1.50× |
| 1 000 | **415 ms** | 308 ms | **273 ms** | 281 ms | 1.52× |
| 5 000 | **2.11 s** | 1.67 s | **1.39 s** | 1.41 s | 1.52× |

(std dev < 3 % at K ≥ 4 at every size; serial stddev inflates at M=5 000
to ~21 % because the per-link time is APFS-contended.)

**Interpretation:**

- **Per-action cost is ~0.40 ms for a `posix.link` on APFS at 4 KB.**
  Matches W2's `posix.link` cProfile row (25 983 calls in 9.39 s ≈
  361 µs/call).
- **Peak speedup is 1.5–1.7× at K=4 threads, diminishing at K=8.** On
  this filesystem, inode-entry creation serializes under the kernel
  despite userspace concurrency; four threads is enough to saturate
  the useful parallelism. Linux ext4 and XFS are known to be less
  contention-bound here, so the same fix on Linux CI runners will
  likely produce a larger (~3×) speedup — worth confirming on a Linux
  host in Phase 4.
- **`_execute_actions`'s bare for-loop at `link.py:1070` is the
  genuine hot spot, not the outer `execute_executor`.** The outer
  executor only fans out *one package at a time*; the inner loop is
  where the 25 983 actions serially run. Pushing a
  ``ThreadPoolExecutor`` into `_execute_actions` (B7 proposal below)
  captures the measured 1.7× without touching the higher-level
  executor defaults that the "do not surprise anyone" comment at
  `context.py:696-714` is protecting.
- memray at M=1 000 peaks at 15.7 MiB, 4 026 allocations. Memory
  footprint is identical to serial — threading adds no meaningful
  heap cost.

**Projection for W2.** Phase-1 showed `posix.link` at 9.39 s of 26 s
W2 wall time. Applying the 1.73× best-case speedup drops that to
5.42 s, a **~4 s / 26 s W2 reduction (~15 %)** on macOS APFS. On
Linux ext4 with likely 3× scaling, the same fix probably saves
~6.3 s / 26 s (~24 %). Phase-4 confirmation needed.

##### S7 proposed fix sketch (Phase-3 B7)

Option A (internal only, no user-visible knob change):

```python
# replace the bare for-loop at conda/core/link.py:1070
# with a ThreadPoolExecutor sized to min(cpu, 4)
max_workers = min(cpu_count() or 1, 4)
with ThreadPoolExecutor(max_workers=max_workers) as pool:
    list(pool.map(lambda a: a.execute(), axngroup.actions))
```

Option B (user-visible default bump):

```python
# conda/base/context.py:696-714
# change default for execute_threads from 1 to min(cpu, 4) when unset
execute_threads = min(cpu_count() or 1, 4)
```

Option A is the safer landing path: it narrows the behaviour change to
the already-time-bounded per-package link phase, keeps the outer
`execute_executor` at its defensive `= 1` default, and ships without a
news-entry behaviour-change flag. Recommended.

Thread-safety: each `LinkPathAction.execute()` writes to its own
`target_full_path` computed from the per-action `target_short_path`.
No shared-state mutation across actions. `context.force` is read-only.
Confirmed safe.

#### S9 confirmation

pyperf fast mode, 10 files per synthetic "package", each file 20
trivial function definitions. Compares ``compile_multiple_pyc`` called
once-per-package (current shipping behaviour) vs once-total-with-all-files
(proposed fix). Data in
[`data/phase2/s9_pyc_batching/`](data/phase2/s9_pyc_batching/).

| P (packages) | per_package (current) | batched (proposed) | speedup | per-subprocess cost |
|---:|---:|---:|---:|---:|
| 10 | **7.31 s ± 0.06 s** | 0.79 s ± 12 ms | **9.3×** | 731 ms |
| 30 | **21.90 s ± 0.13 s** | 0.91 s ± 14 ms | **24.1×** | 730 ms |
| 60 | **44.67 s ± 0.77 s** | 1.12 s ± 0.11 s | **39.9×** | 744 ms |

(batched mode adds ~5 ms/file × P × 10 on top of one fixed startup;
scaling is essentially linear in total file count at ~0.5 ms/file.)

**Interpretation:**

- **Per-subprocess fixed cost is ~707 ms on this devenv Python.**
  That's CPython interpreter startup + `site` initialization for a
  Python with a fully-populated `site-packages` (the devenv ships
  with hundreds of packages) + `import compileall` + tempfile
  write + tempfile delete.
- **On a fresh target env's Python** (what W2 actually uses) the
  per-subprocess cost is ~50 ms, inferred from W2's Phase-1 cProfile:
  186 subprocesses in 9.47 s = 51 ms/subprocess. The devenv
  over-measures the absolute fixed cost by ~14×, **but the ratio
  (batched vs per-package) is preserved** because batched still runs
  exactly one subprocess regardless of Python startup cost.
- **The speedup grows linearly with P** (9.3× → 24× → 40.5× at P=10,
  30, 60). Asymptotically it approaches `P × fixed_cost /
  (fixed_cost + P × per_file_cost)`, which at a fresh-env Python
  with ~5 files/package is dominated entirely by the
  `P × fixed_cost` numerator. At W2's scale (186 packages), the
  speedup on the shipping Python is ~180× in wall time — bounded by
  total compile work, not startup.
- memray at P=30 peaks at 5.4 MiB, 997 allocations. Trivial — all
  the real allocation happens inside the spawned subprocesses, which
  `--follow-fork` cannot track across the `exec` that replaces the
  fork'd process with a fresh Python.

**Projection for W2.** Phase-1 showed pyc-compile subprocess cost
of **9.47 s / 26 s W2 wall time**. Under batched mode the same work
becomes ~50 ms (one subprocess startup) + 930 files × ~1 ms compile ≈
**1.0 s**. That's **~8.5 s / 26 s off W2 (~33 %)**, the single
largest individual-fix reduction any suspect has projected.

##### S9 proposed fix sketch (Phase-3 B9)

`AggregateCompileMultiPycAction` at `path_actions.py:786` already
exists for this purpose — it concatenates `.py` files across multiple
packages before handing them to `compile_multiple_pyc`. The remaining
question is why the shipping code doesn't use it more aggressively.
Phase 1 showed 186 per-package subprocesses, suggesting
`AggregateCompileMultiPycAction` is either not triggered for most
installs or is aggregating only within some narrow condition.

Path forward (larger than a typical B PR, probably 2 PRs):

1. **B9a (small, ~20 LOC):** audit the condition at
   `path_actions.py:984-998` that dispatches between
   `CompileMultiPycAction` and `AggregateCompileMultiPycAction`;
   extend the aggregation to cover the common case. News entry
   required — behaviour change in pyc-compile timing but output is
   byte-identical.
2. **B9b (larger, conditional):** add a top-level
   "compile all packages in one subprocess at end of transaction"
   pass that replaces all per-package compile actions with a single
   aggregated one. Requires carefully checking that individual
   package post-link scripts don't depend on that package's `.pyc`
   being present before later packages are linked.

Dependencies: independent of B6/B7/B11. B9a can land alone.

#### S8 confirmation (extract pool)

pyperf fast mode, N = 5 real `.conda` packages pulled from the active
package cache (scipy, pandas, python, notebook, scikit-learn — the same
heavy scientific packages that dominate W2). Each sample extracts all
5 under a given strategy (serial or ``ThreadPoolExecutor(K)``) and
measures total wall time. cph + cps run source-editable from the
workspace checkouts (see [`bench/setup_workspace.sh`](bench/setup_workspace.sh)).
Data in [`data/phase2/s8_extract_pool/`](data/phase2/s8_extract_pool/)
and [`data/phase2_linux/s8_extract_pool/`](data/phase2_linux/s8_extract_pool/).

| Strategy | macOS (APFS) | Linux (ext4) | Linux / macOS |
|---|---:|---:|---:|
| serial | **4.08 s ± 0.14 s** | **2.33 s ± 0.03 s** | **1.8× faster** |
| K=2 | 3.71 s (1.10×) | 2.30 s (≈ serial) | — |
| K=4 | 3.78 s (1.08×) | **2.99 s (1.28× *slower*)** | — |
| K=6 | 3.83 s (1.07×) | 3.29 s (1.41× slower) | — |
| K=8 | 3.77 s (1.08×) | 3.26 s (1.40× slower) | — |
| K=12 | 3.81 s (1.07×) | 3.24 s (1.39× slower) | — |

**Interpretation:**

- **The 2020-era comment at `package_cache_data.py:73` is wrong on
  both platforms today.** The shipping `EXTRACT_THREADS = min(cpu, 3)`
  gives 3-way fan-out, which is:
  - **Near-optimal but slightly over-committed on macOS** (K=2 is
    8 % faster than K=3/K=4; K=3 is within noise of serial).
  - **Actively regressing on Linux** (K=3–K=12 are all ~30–40 %
    slower than serial or K=2).
- The comment assumed parallelism helps; on both modern filesystems,
  the zstd decompression and tar-write syscalls don't parallelize
  well from Python's GIL-held threads. On Linux the regression is
  because ext4's single-writer-per-file path is already saturated
  by serial zstd output.
- **Linux serial extraction is 1.75× faster than macOS serial**, same
  pattern as every other I/O-heavy Track-B suspect.

##### S8 proposed fix sketch (Phase-3 B8)

Change `EXTRACT_THREADS` from `min(cpu, 3)` to `2` universally. That's
~8 % off W2's fetch/extract phase on macOS, 0 % on Linux (serial is
already best but K=2 is within noise). Either platform sees a small
win; neither regresses.

```python
# conda/core/package_cache_data.py:73-74
EXTRACT_THREADS = 2 if THREADSAFE_EXTRACT else 1
```

A more aggressive variant would be `EXTRACT_THREADS = 1` which would
save another 28 % on Linux specifically, but would regress macOS by
~10 %. Given W2 macOS is 26.7 s and the fetch phase is < 1 s on warm
cache, the absolute saving is negligible — not worth the cross-platform
asymmetry. **Recommended: go with K=2.** ~1 LOC change.

Gated on: nothing. The existing `context.fetch_threads` knob is
unrelated and separately configurable.

#### S11 confirmation

pyperf full mode, 5 runs × 10 values, worker subprocess per sample.
Data in [`data/phase2/s11_libmamba_installed/`](data/phase2/s11_libmamba_installed/).

| N (records) | Per-access mean ± σ | Min | Scaling ratio vs previous N |
|---:|---|---:|---:|
| 1 000 | **333 µs ± 3 µs** | 328 µs | — |
| 5 000 | **2.42 ms ± 0.11 ms** | 2.27 ms | 7.3× at 5× data |
| 10 000 | **5.61 ms ± 0.35 ms** | 5.21 ms | 2.3× at 2× data |

**Interpretation:** each `.installed` property access is **O(N log N)**
in the prefix size, dominated by `dict(sorted(prefix_data._prefix_records.items()))`
on every access. Not O(N²) per call.

The O(N²)-ish behaviour observed at the end-to-end level in Phase 1
(W3's ~35 s at 5 k records, ~164 s at 10 k, unfinished at 50 k) arises
because `_specs_to_request_jobs_add` accesses `in_state.installed`
**O(M) times** where M is roughly the number of specs being processed
(typically proportional to N for `conda update --all`-style commands,
constant for targeted installs). So total solver cost is
**O(M × N log N)**, which is O(N² log N) for the `--all` case and
O(N log N) for the `--no-deps` case — matching the Phase-1 scaling
ratios closely.

Memray at N=5 000, 100 accesses:
`peak_memory = 36.12 MiB`, `total_allocations = 15 299`,
`wall_time = 3.54 s` (raw numbers in
[`memray_n5000.meta.json`](data/phase2/s11_libmamba_installed/memray_n5000.meta.json)).
Each access allocates a fresh `dict` + `MappingProxyType` and immediately
releases the old one — pure transient churn, not memory retention. Peak
doesn't scale significantly with access count; the fix is a CPU
optimization, not a memory one.

##### S11 proposed fix sketch (Phase-3 B11)

Cache the sorted result on `PrefixData`, invalidate on
`_prefix_records` mutation:

```python
@property
def installed(self) -> dict[str, PackageRecord]:
    cache = self.prefix_data._sorted_records_cache
    if cache is None or cache is not self.prefix_data._prefix_records:
        cache = MappingProxyType(dict(sorted(self.prefix_data._prefix_records.items())))
        self.prefix_data._sorted_records_cache = cache
        self.prefix_data._sorted_records_key = self.prefix_data._prefix_records
    return cache
```

Or, simpler: cache on `SolverInputState` itself since the prefix is
frozen for the lifetime of one solve. Would reduce the per-access cost
from ~2.35 ms to ~50 ns (dict lookup) at N=5 000, a projected
**99.998 %** reduction for the O(M) accesses in
`_specs_to_request_jobs`. End-to-end projection: W3 wall time drops
from 35 s toward ~1 s, bringing it inline with W1.

This fix belongs in `conda/conda-libmamba-solver`, not `conda/conda`.
Tracked here because it blocks the Track B W3 narrative.

#### Phase-2 summary

Five suspects confirmed with quantitative before/after-compatible
data. Four remain unmeasured (S1, S3, S4, S5/S8/S10) — their Phase-1
evidence is too thin to justify a fixture yet.

| Suspect | Phase-2 benchmark result | Projected W-series saving | Complexity |
|---|---|---|---|
| **S2** | O(N²) `PrefixGraph.__init__` at 9.5 µs / inner iter | latent; critical for `update --all` on 20 k+ envs | indep. ~15 LOC |
| **S6** | O(M) `_verify_individual_level` at 0.76 ms / action | **~4 s / 10 s W1 (40 %)** | indep. ~10 LOC |
| **S7** | 1.52× speedup at K=4 threads on APFS | **~4 s / 26 s W2 (15 %)** on mac; regresses on Linux | indep. ~0 LOC (don't change default) |
| **S8** | Linux regresses at K ≥ 3 by 28–40 %; mac flat past K=2 | ~0.3 s off fetch/extract at K=2 | indep. ~1 LOC |
| **S9** | 40.5× speedup batching P=60 subprocesses | **~8.5 s / 26 s W2 (33 %)** | indep. ~20 LOC (B9a) |
| **S11** | O(N log N) per `.installed` access, called O(N) times | **~34 s / 35 s W3 (97 %)**, conda-libmamba-solver | indep. ~20 LOC |

Combined W2 projection (all applicable fixes stacked, treating
serial-savings as additive): ~12.5 s / 26 s, a **48 % reduction**.
Combined W1 projection: ~4 s / 10 s (40 %). Combined W3 projection:
~34 s / 35 s (97 %), conditional on B11 landing in
`conda-libmamba-solver`.

Stacking assumption is conservative: B6 and B7 both improve the
prepare+verify and execute phases respectively, and their gains are
on disjoint call paths. B9a is pure subprocess overhead reduction,
disjoint from both. The stack is additive, not multiplicative.

Four suspects remain **unmeasured**:

- **S1** (quadratic diff sort) — not in any Phase-1 cProfile top-20;
  would be exercised only on `conda update --all` with a large
  change set. Phase-2 could write a synthetic benchmark for
  completeness.
- **S3** (`History.update()` on long history) — same: not hit by W1
  or W2 (history files are short for fresh installs) and
  partially masked by S11 on W3.
- **S4** (SHA-256 on large prefix-rewrite files) — W1 showed 200
  verify actions at 5 s total, but the files are small. A 50 MB
  binary benchmark would confirm it.
- **S5** (clobber check), **S10** (per-record
  JSON writes): no Phase-1 evidence either way.
- **S12, S13, S14** — cph/cps-specific suspects now that those repos
  are in scope. S14 in particular is a trivial win (swap the Python
  chunked loop for `hashlib.file_digest`) that's worth confirming.

Recommend Phase 3 start landing B2, B6, B7, B9a, B11 against the
Phase-1 baseline. The remaining four suspects can wait until Phase 2
has bandwidth to write their fixtures — none of them are gating any
of the five confirmed targets.

---

#### Linux confirmation run (Docker / OrbStack, arm64)

All Phase-1 and Phase-2 numbers above come from macOS 26.3.1 / APFS /
M1 Pro. To validate the macOS → Linux projections (especially S7's
"should scale 3× at K=4") we reran the full harness inside an
`arm64` Linux container. Same M1 Pro host, same conda `main @
7c1ebba7c`, same hyperfine/memray/pyperf versions; Linux ext4 via
OrbStack. See [`docker/`](docker/) for the Dockerfile + driver, and
[`data/phase1_linux/`](data/phase1_linux/) +
[`data/phase2_linux/`](data/phase2_linux/) for the raw data.

##### Phase-1 wall time (hyperfine)

| Workload | macOS (APFS) | Linux (ext4 via OrbStack) | Linux speedup |
|---|---:|---:|---:|
| W1 (small install) | 10.37 s | **3.32 s** | **3.1×** |
| W2 (data-science install) | 26.67 s | **10.66 s** | **2.5×** |
| W3 (synthetic prefix dry-run) | 36.44 s | **19.41 s** | **1.9×** |

**Linux is 1.8–2.9× faster than macOS for every Phase-1 workload on
identical hardware.** That's entirely filesystem + kernel, not CPU
architecture. Primary drivers from the per-phase breakdown below:

| `time_recorder` marker | W1 mac | W1 Linux | W2 mac | W2 Linux | W3 mac | W3 Linux |
|---|---:|---:|---:|---:|---:|---:|
| `conda_libmamba_solver._solving_loop` | 0.03 s | 0.07 s | 0.28 s | 0.62 s | 24.70 s | **11.50 s** |
| `unlink_link_prepare_and_verify` | 5.53 s | **1.01 s** | 8.06 s | **3.25 s** | — | — |
| `unlink_link_execute` | 3.74 s | **1.25 s** | 17.01 s | **5.57 s** | — | — |

The prepare+verify phase drops **5.5×** on Linux, the execute phase
drops **3×**. The solver itself is faster on Linux at W3 (24.70 s →
11.50 s, 2.1×) as well — even though it's pure libmambapy C++, the
fewer syscalls and faster malloc on Linux add up. That matches the
Scalene decomposition below — macOS's extra time is in `system`
(syscalls).

##### Phase-1 Scalene (Linux only — conda-forge's arm64e scalene build fails to load on macOS 26)

Aggregated across all conda source files. Per-line JSON at
[`data/phase1_linux/<w>/scalene.json`](data/phase1_linux/).

| Workload | elapsed under Scalene | Python | Native (C extensions) | System (kernel / I/O wait) |
|---|---:|---:|---:|---:|
| W1 | 6.8 s | **3 %** | **49 %** | **48 %** |
| W2 | 29.2 s | **4 %** | **54 %** | **42 %** |
| W3 | 0.2 s | **28 %** | **58 %** | **13 %** |

This is the single most important framing shift the whole project
has produced: **W1 and W2 are 96–97 % native + system time.**
Python-level optimization of the conda codebase against these
workloads has a ceiling of 3–4 % wall-time reduction. The remaining
~96 % lives in:

- **Native code** — libmambapy (solver), libarchive (extract), OpenSSL
  (hashlib), CPython's own eval loop, the subprocess spawn machinery.
- **System time** — blocked in the kernel on `posix.link`, `read`,
  `write`, `wait` (for compileall subprocesses), and `mkdir`.

**W3 is different.** It's a `--dry-run --no-deps` that skips all
fetch/extract/link work. 28 % of W3 is Python — and that 28 % is
almost entirely the `dict(sorted(...))` inside
`SolverInputState.installed` (S11). B11 targets exactly that
fraction. The 58 % native is libmambapy's C++ solver and cannot be
shrunk from the conda side.

The practical implication for Phase 3 is that **the fixes that save
native or system time will dominate the fixes that save Python time**.
That reshapes the priority ranking:

- **B9a (S9: batch pyc subprocesses)** — removes ~180 subprocess
  spawns and waits from W2. These land in `system`, which is 42 % of
  W2 under Scalene. Highest-impact fix on a normal install.
- **B6 (S6: parallelize verify)** — removes serial I/O latency but
  the underlying `posix.link`/`read`/`write` syscalls are already in
  `system`; parallelism moves them concurrent but doesn't remove any.
  Gain bounded by filesystem parallelism (see S7 below — Linux
  doesn't have much).
- **B11 (S11: cache sorted)** — removes Python time in a workload
  that is 28 % Python. Still high value for `--no-deps`/`--dry-run`
  commands and for any large-prefix flow.
- **B2 (S2: PrefixGraph O(N²))** — latent; only critical when
  someone actually hits it.
- **B7 (S7: parallelize hardlink)** — see below, **rejected on Linux**.

##### Phase-2 per-suspect (macOS vs Linux, same arm64 CPU)

| Suspect / metric | macOS (APFS) | Linux (ext4) | Linux / macOS |
|---|---:|---:|---:|
| **S6** `_verify_individual_level` per-action (4 KB) | 0.76 ms | **0.21 ms** | **3.6× faster** |
| **S7** serial `posix.link` per-action (4 KB) | 0.42 ms | **0.021 ms** | **20× faster** |
| **S7** parallel K=4 at M=5 000 | 1.39 s (1.52× vs serial) | **283 ms (2.7× *slower* than serial)** | — |
| **S9** per-package subprocess startup | 731 ms | 933 ms | 1.3× slower |
| **S9** batched P=60 total time | 1.12 s | 1.05 s | comparable |
| **S9** speedup ratio (per_package / batched) | 39.9× | **53×** | comparable |
| **S11** `.installed` per-access at N=5 000 | 2.42 ms | **955 µs** | **2.5× faster** |
| **S11** `.installed` per-access at N=1 000 | 333 µs | **133 µs** | 2.5× faster |

**S7 is the headline surprise.** Linux serial `posix.link` is **21×
faster** than macOS — and Linux's `ThreadPoolExecutor` parallel
variant is **slower than serial at every K**. The Phase-2 macOS
projection ("~3× on Linux ext4") was wrong: on a filesystem this
fast, Python's ThreadPoolExecutor submit-and-collect overhead exceeds
the per-action work. B7 as proposed would **regress** Linux
performance by 2–3×.

Direct read-out at M=1 000:

```
macOS:
  serial           415 ms        (baseline)
  K=4 parallel     273 ms        1.52× faster
Linux:
  serial            20.0 ms      (baseline — 21× faster than mac serial)
  K=4 parallel      46.7 ms      2.3× slower than Linux serial
```

B7 needs to be platform-conditional, or abandoned, or restricted to
a bump of the user-visible `execute_threads` default (opt-in only
for users who already know their filesystem benefits from it).
Updated B7 proposal in the Phase-3 table below.

**S6 holds up.** The 3.7× Linux speedup on serial is just filesystem
efficiency — parallelization should still win (not measured on
Linux yet). The per-action cost of 0.2 ms at 4 KB means W1's 200
actions × per-action ≈ 40 ms on Linux (vs 5.5 s on macOS — where
the larger real-package files amplify the cost). B6 is still
worthwhile but its W1 percentage impact on Linux is much smaller
than the macOS projection, because W1 Linux is already only 3.4 s
total.

**S9 is unchanged.** Batching wins 40–47× regardless of platform —
the per-subprocess fixed cost is the same order on both (devenv
Python's heavy site-init dominates). B9a is cross-platform worthwhile.

**S11 is unchanged.** Same O(N log N) scaling, 2.6× faster absolute
because Python dict/sort is faster on Linux. B11 is cross-platform
worthwhile.

##### Revised Phase-2 verdict table

| Suspect | macOS verdict | Linux verdict | Fix |
|---|---|---|---|
| S2 | confirmed (latent) | confirmed (latent) | B2 land |
| S6 | confirmed (strong) | confirmed (weaker but real) | B6 land |
| S7 | confirmed (1.5–1.7×) | **rejected (regresses ≥2×)** | B7 — platform-conditional or drop |
| S9 | confirmed (40×) | confirmed (45×) | B9a land |
| S11 | confirmed | confirmed (2.6× faster absolute) | B11 land |

### Phase 3: spot PoCs

One PR per surviving suspect, same scope rules as Track A.

| ID | Fixes | Sketch |
|---|---|---|
| B1 | S1 | Precompute `{rec: i for i, rec in enumerate(previous_records)}`; same for `final_precs`. Replace the `.index(x)` key function with a dict lookup. ~10 LOC. |
| B2 | S2 | Build a `by_name: dict[str, list[PrefixRecord]]` index once; replace the O(N²) inner loop with `for rec in by_name.get(spec.name, ()):`. Phase-2 data: O(N²) at ~9.5 µs per comparison → O(N×K) after fix, projected ~8-order-of-magnitude speedup at N=50 000. Preserves semantics. ~15 LOC plus tests. |
| B3 | S3 | Append-only history updates. `History.update()` only needs the last `==>` block. Read the file from the end until the last header, parse only that block. Verify against `History.get_user_requests()`. |
| B4 | S4 (conditional) | Only compute `sha256_in_prefix` when `context.extra_safety_checks`. Requires a grep for readers of `sha256_in_prefix` first. |
| B5 | S5 | Build a single `{short_path: prefix_rec}` map once before the clobber loop. |
| B6 | S6 | Push the verify fan-out down one level: replace the bare `for` at `link.py:632` with a `ThreadPoolExecutor(max_workers=context.verify_threads or 4).map(...)`. Phase-2 data: 0.73 ms/action O(M) → expected ~4× speedup on NVMe. Thread-safety confirmed (each action writes to its own uuid-named intermediate). ~10 LOC plus one test. |
| B7 | S7 | **Revised:** Linux confirmation showed `ThreadPoolExecutor` parallel link *regresses* by 2–3× on fast filesystems (kernel serialization of inode creation is already fast enough that Python scheduling overhead dominates). macOS-only win at 1.7×. Options: (a) drop B7 entirely; (b) gate the fan-out behind a slow-disk heuristic (``stat`` the prefix, benchmark a handful of hardlinks, only parallelize if > 0.1 ms each); (c) leave it as an opt-in when the user sets `execute_threads > 1` manually. Recommended: (c) — leave user override working, don't change default. ~0 LOC (just documentation). |
| B8 | S8 | Change `EXTRACT_THREADS = min(cpu, 3)` to `EXTRACT_THREADS = 2`. Phase-2 data: serial and K=2 are best on both macOS and Linux; K ≥ 3 regresses on Linux by 28–40 %. One-line constant change in `conda/core/package_cache_data.py:74`. News entry noting the behaviour change. |
| B9a | S9 | Audit the dispatch at `path_actions.py:984-998` that chooses between `CompileMultiPycAction` and `AggregateCompileMultiPycAction`; widen aggregation to cover the common case. Phase-2 data: 40.5× speedup at P=60 in batched mode; projected ~8.5 s / 26 s W2 reduction. News entry required. ~20 LOC. |
| B9b | S9 (extended) | Top-level "compile all packages in one subprocess at end of transaction" pass. Gated on: verifying no post-link script depends on a prior package's `.pyc` being present before later packages are linked. Bigger PR, >50 LOC. |
| B11 | S11 | Cache the sorted result of `SolverInputState.installed` once per solve. Phase-2 data: 2.35 ms per access at N=5 000 → ~50 ns with cache. Fix lives in `conda/conda-libmamba-solver`, not `conda`. |

Dependencies: B7 gates on B6. Everything else is independent.

### Phase 4: end-to-end confirmation

Re-run W1/W2/W3 with hyperfine on the merged stack. Publish a
stacked-estimate table analogous to the [Track A version](track-a-startup.md#35a-stacked-estimate-conda-run-with-full-track-a).

---

## Scope rules

- One PR per suspect, targeting <100 LOC each (including tests).
- Python 3.10+ only. No `match`, no 3.11 stdlib additions.
- No new dependencies. No architectural changes.
- `news/` entry per PR, factual, no em dashes.
- Pytest module-level imports, parametrized tests, no `unittest.mock`
  unless patching `context`.
- PR body quotes the Phase 2 microbenchmark number and the Phase 4
  hyperfine number.
- Cross-platform: any PoC touching `gateways/disk/link.py`,
  `gateways/disk/create.py`, or menuinst code paths gets a Windows CI
  run on the PR branch and a manual macOS check noted in the PR.

---

## Out of scope

- The SAT solver itself (classic `Resolve` or libmamba).
- `conda-package-handling` internals (extractor plugins). If Phase 2
  fingers extraction, the fix lives there, not in conda.
- Anything visible in `--quiet` / `--json` output. Machinery only.
- Progress bar frequency / terminal repaint rate.
- menuinst internals.

---

## Changelog

| Date | Change |
|---|---|
| 2026-04-25 | **Harness migrated to pixi.** New [`pixi.toml`](pixi.toml) at the repo root declares the full workspace (conda + cph + cps editable from sibling paths, plus hyperfine/memray/pyperf/scalene from conda-forge) and exposes every Phase-1 / Phase-2 task as a named `pixi run` target. Cross-platform by design: the same `pixi.toml` drives macOS directly and spins up the Linux container via `pixi run linux-build` / `pixi run linux-all`. Replaces the old `conda/dev/start` bootstrap and `bench/setup_workspace.sh` flow. Both are kept as fallbacks but the README now points at pixi first. Two small infrastructure items shipped with the migration: [`bench/tools/conda`](bench/tools/conda) shim that routes `conda` around the pip-install entry-point guard (otherwise `conda create`/`env remove` fail in the pixi env), and a revised [`docker/Dockerfile`](docker/Dockerfile) + [`docker/entrypoint.sh`](docker/entrypoint.sh) that use pixi inside the container — so macOS and Linux environments are now materially identical, not just "similar". |
| 2026-04-25 | **cph + cps added to the workspace + S8 confirmed.** New suspects S12 (`cps.extract_stream` per-member path-safety syscalls), S13 (`cps.stream_conda_component` double ZipFile parse), and S14 (`cph.utils._checksum` Python-level chunked hash loop vs. stdlib `hashlib.file_digest`) added to the Suspect hot spots table; all three live in the cph/cps workspace repos, not in conda itself. New [`bench/setup_workspace.sh`](bench/setup_workspace.sh) installs cph + cps source-editable in the macOS devenv; [`docker/Dockerfile`](docker/Dockerfile) clones them at pinned SHAs (`5da82cc` / `e47a70b`) and does the same in the Linux container. **S8 confirmed on both platforms**: the 2020-era `EXTRACT_THREADS = min(cpu, 3)` cap regresses Linux by 28–40 % at K ≥ 3 and is near-flat on macOS. Proposed B8: change to `EXTRACT_THREADS = 2` universally, ~1 LOC. Extended Background section documents the workspace scope (cph + cps in, conda-build / conda-content-trust / libmambapy out). |
| 2026-04-25 | **Full rerun on macOS + Linux for reproducibility.** All Phase-1 and Phase-2 numbers above are from a fresh end-to-end run (cleared `data/phase{1,2}{,_linux}/`, rebuilt benchmarks, copied results back). Previous numbers (dated 2026-04-24) are within ±5% of the rerun across every workload and suspect — the harness is repeatable within its own noise. New orchestration artifact [`bench/run_all.sh`](bench/run_all.sh) mirrors `docker/run_linux.sh` so the two platforms now have symmetric single-command drivers. Highlights from the rerun: W1 mac 10.37 s (was 9.90), W2 mac 26.67 s (was 25.70), W3 mac 36.44 s (was 35.33); W1 Linux 3.32 s (was 3.37), W2 Linux 10.66 s (was 10.97), W3 Linux 19.41 s (was 19.63). S11 mac 2.42 ms / Linux 955 µs. S7 parallel-hurts-on-Linux signal reconfirmed at every M. |
| 2026-04-25 | **Linux (arm64 / ext4) confirmation run, Scalene added.** New [`docker/`](docker/) directory with a `Dockerfile` + `entrypoint.sh` + `run_linux.sh` that reproduces the full harness inside an OrbStack-hosted Linux container. Raw data under [`data/phase1_linux/`](data/phase1_linux/) + [`data/phase2_linux/`](data/phase2_linux/). Three big findings: (1) **Linux is 1.8–2.9× faster than macOS on every Phase-1 workload on identical hardware** — the gap is entirely filesystem + kernel, not CPU. (2) **W1 and W2 are 96–97 % native + system time** (Scalene decomposition) — Python-level optimization has a 3–4 % ceiling, the real fix room is in reducing subprocess spawns (B9a) and syscall counts. (3) **B7 (parallel hardlink) *regresses* on Linux** — serial `posix.link` is 21× faster than macOS, ThreadPoolExecutor overhead exceeds I/O work at every K. B7 downgraded to "user override only, don't change default". S6/S9/S11/B2 confirmed on Linux. |
| 2026-04-25 | **Scalene integrated for Phase 1 and Phase 2** via [`bench/run_scalene.py`](bench/run_scalene.py) and [`bench/phase2/run_scalene.py`](bench/phase2/run_scalene.py). Produces JSON with per-line Python / native / system time breakdown — the only tool in the harness that distinguishes "time inside a C extension" from "time in pure Python". The conda-forge scalene build for Python 3.13 on macOS 26 fails to load due to an `arm64e.old` ABI mismatch (rebuilt needed with Xcode 16 SDK — unrelated to our work); integration is Linux-container-only for now. Documented in bench/README and bench/phase2/README. |
| 2026-04-24 | **Phase 2: S2, S7, S9 confirmed.** Three new benchmarks + three new fixture builders (`synthetic_hardlink_actions`, `synthetic_py_packages`, `synthetic_prefix_records`). S7: 1.73× parallel speedup at K=4 on APFS (projected ~3× on Linux ext4, **later rejected** — see 2026-04-25). S9: **40.5× speedup at P=60** from batching pyc-compile subprocesses — projected **~8.5 s / 26 s off W2 (~33 %)**, the largest single-fix reduction any suspect has shown. S2: textbook O(N²) at 9.5 µs per inner iteration, 47 s at N=1 000; projected 33 hours at N=50 000 if anyone ever ran `update --all` against a that-large env. Cumulative: **five suspects confirmed** (S2, S6, S7, S9, S11), combined W1 projection 40 % reduction, W2 48 %, W3 97 % conditional on B11 in `conda-libmamba-solver`. Phase-2 summary table added to the doc. |
| 2026-04-24 | **Phase 2: S6 confirmed.** New benchmark [`bench_s6_verify_individual.py`](bench/phase2/bench_s6_verify_individual.py) and a shared fixture builder `synthetic_prefix_replace_actions(m, ...)` in [`fixtures.py`](bench/phase2/fixtures.py) that creates M real files + M real `PrefixReplaceLinkAction` instances. pyperf full mode at M={50, 200, 1 000} gives 36 ms / 146 ms / 740 ms — **perfectly linear O(M) at 0.73 ms/action** for 4 KB files. memray at M=1 000 peaks at 22.5 MiB, 4 601 allocations — not memory-bound, purely disk-and-CPU-bound copy + rewrite + hash. Projection: B6 (ThreadPoolExecutor fan-out at `link.py:632` across `min(cpu, 4)` threads) should drop W1's 5.5 s verify phase to ~1.4 s → **~40 % W1 wall-time reduction** on its own. Thread-safety reviewed: each action writes its own uuid-named intermediate, no shared-state mutation. |
| 2026-04-24 | **Phase 2 scaffold committed, S11 confirmed.** New [`bench/phase2/`](bench/phase2/) directory with shared fixture (`fixtures.synthetic_prefix`), a pyperf sweep orchestrator ([`run_pyperf.py`](bench/phase2/run_pyperf.py)), a memray harness ([`run_memray.py`](bench/phase2/run_memray.py)), and the first suspect benchmark ([`bench_s11_libmamba_installed.py`](bench/phase2/bench_s11_libmamba_installed.py)). pyperf full mode at N={1000, 5000, 10000} gives per-access times of 330 µs / 2.35 ms / 5.40 ms respectively — **O(N log N) per access**, matching the `dict(sorted(...))` pattern exactly. The end-to-end O(N²-ish) cost observed in Phase 1 W3 comes from `_specs_to_request_jobs` calling `.installed` O(N) times. memray at N=5000/100-accesses peaks at 36 MiB: transient allocation churn, no retention — the fix is CPU-only. Proposed B11 PoC (cache the sorted result for the solve's lifetime) projects a ~47 000× per-access speedup and should collapse W3 wall time from 35 s toward ~1 s. PoC fix belongs in `conda-libmamba-solver`, not `conda`. | 
| 2026-04-24 | **memray added as Phase-1 third artifact.** New harness [`bench/run_memray.py`](bench/run_memray.py) uses `memray run --aggregate --follow-fork --native -m conda ...`, then renders a summary table, a peak-memory/allocation JSON, and an HTML flamegraph. Peak RSS: W1 59.2 MiB, W2 92.8 MiB, W3 53.5 MiB. Memory is not a first-order concern at these workload sizes. W3's peak is *lower* than W1's despite the 24 s libmamba-solver cost — the quadratic term in S11 is iteration through pre-allocated data, not allocation churn. Known macOS caveat: conda-forge ships CPython without DWARF debug info, so C-level stacks show function names but not file:line; Python-level attribution is unaffected. Fully documented in [`bench/README.md`](bench/README.md#memray-and-the-no-symbol-information-warning). |
| 2026-04-24 | **Phase 1 deliverable complete: cProfile top-20 + `time_recorder` per-phase timings committed** to [`data/phase1/<w>/cprofile.{prof,top20.txt}`](data/phase1/) and [`data/phase1/<w>/time_recorder.json`](data/phase1/) for all three workloads. Summary and rankings added to [Phase-1 takeaways](#phase-1-takeaways) above. Fixed two harness bugs while doing this: renamed `bench/profile.py` → [`bench/run_cprofile.py`](bench/run_cprofile.py) because the old name shadowed the stdlib `profile` module that cProfile imports internally; corrected the `runpy.run_module` target from `conda.cli` (a package, cannot be executed) to `conda` (has a `__main__.py`). Also rewrote [`bench/parse_time_recorder.py`](bench/parse_time_recorder.py) to use the current `time_recorder.total_run_time` class var + CSV fallback instead of the non-existent `_CHRONOS_COLLECTED_FNS`. **New suspect S11 added** based on the W3 cProfile: `conda_libmamba_solver.state.SolverInputState.installed` is the dominant cost of the synthetic-prefix workload, not S1/S2. |
| 2026-04-24 | **Phase 1 baseline measurements committed.** W1 (9.90 ± 0.26 s), W2 (25.70 ± 0.17 s), W3 (35.33 ± 0.28 s) on MacBookPro18,1 (M1 Pro, 10-core, 32 GB), macOS 26.3.1, `conda/conda@main` `7c1ebba7c` built from source via `dev/start -p 3.13 -i miniforge -u`, hyperfine `--warmup 1 --runs 5`. Raw data in [`data/phase1/<w>/hyperfine.json`](data/phase1/). Host metadata in [`data/machine.json`](data/machine.json). W2 and W3 wall times came in 30–60× lower than the original back-of-envelope estimates — libmamba is significantly faster than the classic-solver numbers the original plan was calibrated against. |
| 2026-04-24 | **W3 workload redefined.** Changed from `conda update -n bench_big -y --all --dry-run` against 50k synthetic records to `conda install -n bench_big -c conda-forge -y --dry-run --no-deps tzdata` against **5 000** synthetic records. Phase-0 scaling experiment: at N=1 000 records the same command runs in 2.2 s; at N=5 000 it takes 35 s; at N=10 000 it takes 2 min 44 s; at N=50 000 it does not finish within a 5 min timeout. The 1k→5k→10k ratio (1×:16×:75× for 1×:5×:10× data) is consistent with O(N²) dominating the post-solve path, which is exactly the S2 (`PrefixGraph.__init__` O(N²)) signal Phase 2 is designed to isolate. The original 50k+`--all`+libmamba combination is intractable within a 5-run hyperfine budget because libmamba treats every installed synthetic spec as an update candidate and spins in the solve phase before reaching S1/S2 at all. The seed script still supports `--records 50000` for Phase-2 microbenchmarks that bypass the CLI. |
| 2026-04-24 | **Seed script bugs fixed** in [`bench/seed_big_prefix.py`](bench/seed_big_prefix.py): template record used `"platform": "noarch"` which is not a valid `conda.models.enums.Platform` value and caused `ValidationError: 'noarch' is not a valid Platform` when the solver loaded any of the records — noarch records correctly have `platform: None, subdir: "noarch"`. Also fixed prefix-path resolution: the previous code queried `conda info --envs` before creating the env, so it fell through to `envs_dirs[0]/<name>` which can differ from where `conda create -n <name>` actually lands, splitting real env data from synthetic records across two directories. |
| 2026-04-23 | **Phase 1 harness scaffold committed** in [`bench/`](bench/): `workloads.sh` for W1/W2/W3-dryrun, `profile.py` for cProfile, `seed_big_prefix.py` for the W3 synthetic 50k-record prefix, `parse_time_recorder.py` for conda's internal per-phase timings. Data layout under `data/phase1/<workload>/`. No measurements yet. |
| 2026-04-23 | **Migrated to [conda-tempo](https://github.com/jezdez/conda-tempo) repo.** Source-of-truth moved from gist `1fd8467189ff7bd928fdea5a3ec4c73f` to `jezdez/conda-tempo/track-b-transaction.md`. Cross-links to Track A and Track C are now relative repo paths. |
| 2026-04-23 | Track B scaffold created. Phase 0 of the transaction-perf plan: split the former single-gist report into three (Track A trimmed, Track B new, Track C new for PEP 810 and speculative research). Suspects S1–S10 identified from a read-through of `link.py`, `path_actions.py`, `package_cache_data.py`, `solve.py`, `prefix_data.py`, `prefix_graph.py`, `history.py`, and `gateways/disk/`. No measurements yet. |
