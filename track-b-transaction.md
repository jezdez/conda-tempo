      Reducing conda Transaction Latency: Track B

# Reducing conda Transaction Latency: Track B

| | |
|---|---|
| **Initiative** | [conda-tempo](https://github.com/jezdez/conda-tempo) — measuring and reducing conda's tempo |
| **Author** | Jannis Leidel ([@jezdez](https://github.com/jezdez)) |
| **Date** | April 24, 2026 |
| **Status** | Phase 4 complete; cph + cps audited; B4/B12/B13/B14 implemented; B20 supersedes B12 with +22.6 % on Linux / beats py-rattler; S5/S15/S16 measured; W4 workload added |
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
(double ZipFile parse), S14 (Python-level chunked checksum), and S15
(cph-vs-cps dispatch overhead) all live in these repos, not in conda
itself. Both are checked out at
``~/Code/git/conda-package-{handling,streaming}`` and installed
source-editable into the devenv via pixi.toml (path-editable
[pypi-dependencies]); [`docker/Dockerfile`](docker/Dockerfile) pins
each at a specific SHA. All Phase-2 numbers for S8+ benchmark the
workspace checkouts, not the conda-forge shipped versions.

#### cph deprecation note (2026-04-26)

The cps author has stated (personal communication) that "I would
prefer to build the necessary API into -streaming and update software
to drop -handling." S15 quantifies what that means for performance:
cph adds ~30 ms (0.8 %) on a 3.8 s cps-direct extract of 5 packages
— essentially free dispatch. The consolidation case is about API
surface area, not speed.

Implications for this Track-B cycle:

- **B13 has a pair of paired fixes** (cps side + cph consumer). If
  cph goes away the cph side becomes moot; the cps change (``zf=``
  kwarg) is still the headline fix.
- **B12 and B14 are cps-side** and survive any cph deprecation.
- **B8** targets conda-side ``EXTRACT_THREADS`` and is unaffected.

Deeper audit of cph: I reviewed every module under
``src/conda_package_handling/`` (`api.py`, `conda_fmt.py`,
`tarball.py`, `streaming.py`, `utils.py`, `validate.py`,
`interface.py`, `cli.py`). The install hot path is
``api.extract`` → ``CondaFormat_v2.extract`` → ``streaming._extract``
→ cps. Nothing outside that chain is exercised by conda transactions.
Other cph code (``create``, ``transmute``, ``validate_converted_files_match_streaming``,
``_sort_file_order``, ``get_pkg_details``, ``list_contents``) runs
only during package building / conversion / introspection, not
during installs.

#### Unpacking speedups: the full picture

Single-package extract cProfile (3 real scientific-Python .conda
archives, total 6268 tar members, 3.08 s wall):

| Call | tottime | calls | per call | % of wall |
|---|---:|---:|---:|---:|
| ``io.open`` (write output files) | 679 ms | 6 271 | 108 µs | 22 % |
| ``io.close`` | 291 ms | 6 282 | 46 µs | 9 % |
| ``zstd.read`` | 270 ms | 18 538 | 15 µs | 9 % |
| ``posix.lstat`` | 235 ms | **91 734** | 2.6 µs | 8 % |
| ``io.write`` | 229 ms | 15 043 | 15 µs | 7 % |
| **``posix.chmod``** | 207 ms | 6 268 | 33 µs | **7 %** |
| **``posix.utime``** | 141 ms | 6 268 | 23 µs | **5 %** |
| ``realpath`` | 90 ms | 6 284 | 14 µs | 3 % |
| tarfile __read | 67 ms | 47 067 | 1.4 µs | 2 % |

Observations:

- **91 734 `posix.lstat` calls for 6 268 files = 15 lstat per file**
  is stdlib tarfile's internal path-component / parent-dir / is-link
  checks. Reducing this means either pre-creating all parent dirs
  ourselves (possible but invasive) or replacing the stdlib tar
  reader entirely. Out of scope as a Track B fix.
- **`posix.chmod` is unavoidable** for conda-forge packages. Real
  packages use tar modes `0o664` for files and `0o775` for
  executables; after umask 022 the effective modes are `0o644` and
  `0o755`, which always differ from the default a freshly-opened
  file receives. No "skip chmod if already correct" shortcut works.
- **`posix.utime` is pure overhead** on conda packages — the tar
  mtime is canonicalised to a constant at build time, so preserving
  it on disk encodes no information. **Implemented as B14**:
  `TarfileNoSameOwner.utime` becomes a no-op, mirroring the existing
  `chown` no-op. Measured 3.4 % reduction on the S15 extract fixture.
- **zstd decompression is only 9 %** — multi-threaded decompression
  would not materially help. Single-frame conda-forge compression
  can't be parallelised at the frame level anyway.
- **`io.open`/`io.write`/`io.close` together are ~38 %** of extract
  wall time. These are per-file syscalls (open, write, close) that
  can't be reduced without rewriting the extractor to issue fewer,
  larger syscalls — probably with `sendfile` or `copy_file_range`
  from kernel buffer to file. A custom streaming tar extractor could
  do this, but not within the Python-level harness we maintain.

Speedup-options table (what we can still pursue):

| Idea | Max projected saving | Effort | Recommendation |
|---|---:|---|---|
| **B14** (skip `utime`) | **~5 %** | trivial | **implemented** on cps:jezdez/track-b-b14-extract-utime |
| Multi-threaded zstd decompression | 0 % | medium | drop — single-frame conda-forge archives can't parallelise |
| Pre-create parent dirs, skip per-file lstat | 5–8 % | medium | consider if B14 isn't enough; requires tarfile-internals patching |
| Use `os.open` + write loop, bypass BufferedWriter | 3–5 % | medium | marginal return |
| Skip `chmod` when mode matches default | 0 % for conda-forge | trivial | drop — conda-forge mode distribution doesn't benefit |
| **Adopt py-rattler (Rust backend) in cps** | **~12 % on Linux, 0 % on mac** | **medium** (new optional build dep) | **see S16 and dedicated section below** |
| Custom vendored C/Rust tar extractor in cps | 15-30 % ceiling | **big** | defer — effort vs. gain doesn't justify yet |

#### Unpacking: where the limits actually are (2026-04-26)

S16 (``bench_s16_rattler_extract.py``) compares cps's current
``extract(path, dest)`` against ``rattler.package_streaming.extract``
from [``py-rattler``](https://pypi.org/project/py-rattler/), the
Python wrapper over the Rust ``rattler_package_streaming`` crate
shipped by prefix.dev under the ``conda/`` org. Same 5 real
scientific-Python ``.conda`` archives, identical input:

| Platform | cps (Python + stdlib tarfile) | py-rattler (Rust) | Δ |
|---|---:|---:|---:|
| macOS APFS (M1 Pro; 8 471 files, 295 MB) | 3.71 s, 80 MB/s, 2 284 files/s | 3.67 s, 80 MB/s, 2 284 files/s | within noise |
| Linux ext4 (OrbStack, 17 375 files, 1 423 MB) | 2.88 s, 495 MB/s, 6 033 files/s | 2.56 s, 557 MB/s, 6 787 files/s | **+12.5 %** |

**Rust is not the bottleneck-eliminator you might expect here.** The
extract is syscall-bound: `open`/`write`/`close`/`chmod`/`utime`
dominate wall time. Rust calls the same kernel as Python, so it
doesn't get a free win. What Rust *does* remove is the Python-level
per-call overhead (generator machinery, `TarFile.extractall`'s
path-resolution walks, the 15 lstats-per-file that stdlib tarfile
performs internally). On a fast filesystem that's ~10-15 %; on APFS
the filesystem itself is the cap.

For comparison, the filesystem ceiling we hit on each platform:

- APFS: **~2 300 files/s** (both implementations) — matches the S7
  serial ``posix.link`` rate of 0.4 ms/call and S8's extract
  saturation at K=1 or K=2.
- ext4 (Linux container with virtiofs): **~6 800 files/s** (rattler)
  / 6 000 (cps). Likely ~10 k files/s on bare metal.

This reframes the "rewrite in Rust" case for cps. Adopting py-rattler:

**Pros**
- **~12 % faster extract on Linux**, where conda CI runs and the
  majority of users install packages. Real saving on W2-scale
  installs: ~1 s / 10 s.
- **Alignment with the conda ecosystem direction** — py-rattler is
  already part of the ``conda/`` org (prefix.dev's contribution),
  not a community-maintained side project. The consolidation story
  is consistent with the cps author's stated interest in folding
  cph into cps: a single Rust-backed extract path across the
  ecosystem.
- **Drops ~500 lines of hand-written cps Python** (``extract_stream``
  + ``TarfileNoSameOwner`` + ``tar_generator``) for a thinner wrapper
  around the Rust crate.
- Rust side already handles the quirks (tar filter, path safety,
  permissions, zstd streaming) that cps has accumulated over time.

**Cons**
- **Build-time Rust toolchain** required for wheels. py-rattler
  currently ships cpython-arm64/x86_64/aarch64 wheels on PyPI + conda-forge;
  this is fine for pip users but adds a conda-forge rebuild step
  any time rattler bumps.
- **Cold-start import cost** of the shared library (~5 ms on
  typical platforms, one-time).
- **Less flexibility for in-process streaming use cases** — cps's
  current ``stream_conda_component`` is a Python generator that
  callers can pause or interpose on (used by
  ``conda-package-handling`` for listing). py-rattler's
  ``extract(path, dest)`` is a one-shot call; an equivalent
  Python-interpolable streaming API would need to be added to
  rattler first.

**Practical adoption paths**

| Option | What it looks like | Effort | Risk |
|---|---|---|---|
| A. Optional fast path in cps | ``cps.extract.extract(path, dest)`` tries ``import rattler`` first; falls back to stdlib tarfile if not installed | small (~30 LOC) | low |
| B. Hard dep swap | cps requires py-rattler; all ``extract_stream`` call sites rewritten | medium-large | medium — breaks streaming API users |
| C. cps absorbs cph + gains rattler fast path | Consolidates cph into cps AND adds rattler as fast backend; cph deprecated | large | medium — ecosystem coordination |

Option A is the cleanest incremental step. It delivers the Linux
speedup for users who install py-rattler without forcing it on
those who can't. cps-level tests cover both code paths.

**What B14-style Python-side optimizations look like against this
ceiling:** B14 bought us 3.4 % on macOS by removing `utime`. A future
"B20 skip per-file lstat via parent-dir precreate" could buy another
5-8 %. Together those would close roughly half the Linux gap (12.5 %
→ ~5 % behind rattler). Good ROI as pure-Python wins; still not worth
abandoning a py-rattler adoption if cultural alignment is the primary
driver.

**Strategic takeaway**: Rust adoption should be justified on
ecosystem-alignment and code-maintenance grounds — ~10-15 % speed
is a pleasant kicker but not the headline. Pure-Python cleanup wins
(B12 / B13 / B14 / B20-candidates) together achieve a similar
magnitude with zero new build deps.

#### B20: hybrid safety check (2026-04-26, after a security pushback)

The initial B20 sketch was "drop the per-member `realpath`, use
`normpath` + `startswith`". That would have been a **security
regression**: the `realpath`-based check catches symlink-chain
traversal attacks (tar member A creates a symlink under `dest_dir`
pointing at `/etc`; member B's name looks fine under string
normalisation but its actual write follows member A's symlink out
of `dest_dir`). A string-only check can't see that.

The first retry was "use `filter="data"` and drop the manual
check" — audited by stdlib, handles symlinks correctly. Measured
across 186 real conda-forge archives (1 274 symlinks, 0
rejections) the safety is solid. But pyperf measurement showed
**+7 % on Linux ext4, –7 % on macOS APFS**: `data_filter` does
more per-member work than cps's pre-existing check, and that extra
work overwhelms the saved wrapper overhead on macOS.

The shipped B20 is a **hybrid**: track a per-stream "risky-member
seen?" flag. While the flag is false, use string-only
`normpath + startswith` (no syscalls). The first time any member
is a symlink / hardlink / has an absolute name / contains `..`,
flip the flag on and fall back to the full `realpath`-based check
for the remainder of the stream. Safety is identical to the
all-realpath baseline because:

- before any risky member is seen, the filesystem tree under
  `dest_dir` was created by us and contains no symlinks to
  traverse, so string normalisation is sufficient;
- after the flag is on, we pay the full cost.

Profile of the real conda-forge package cache (186 archives,
30 299 tar members, 1 274 symlinks):

- **81 % of all members extract via the fast path**.
- **142 / 186 packages never trigger the fallback**.
- A handful of base packages (ncurses, python, openssl, krb5,
  libxcb) symlink early and fall back after ≤ 20 % of members.

Measured wall time (5 real scientific-Python archives, same
fixture as S8 / S16):

| | cps main | cps + B20 (hybrid) | filter="data" | py-rattler |
|---|---:|---:|---:|---:|
| macOS APFS | 3.71 s | **3.63 s** (neutral) | 3.96 s (–7 %) | 3.67 s |
| Linux ext4 | 2.88 s | **2.23 s (+22.6 %)** | 2.68 s (+7 %) | 2.38 s |

**Linux ext4: B20 is +22.6 % over cps main, +6.3 % faster than
py-rattler.** Pure Python beats Rust because the bottleneck is
syscalls per member, and B20 does strictly fewer syscalls (the
rattler extract still has to check per-member safety in its own
Rust code; we skip the check entirely on the fast path).

macOS APFS is within noise because APFS caps file creation at
~2 300/s regardless of caller — no language or algorithmic change
to the safety check can beat the filesystem.

Implemented on
``conda/conda-package-streaming:jezdez/track-b-b20-safety-fast-path``
(supersedes the earlier B12 branch; the dest_dir-memoisation idea
from B12 is subsumed into the fast path here).

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
- **W4. Cold-cache data-science install:** same command as W2 (`pandas + scikit-learn + matplotlib + jupyter`) but with the package cache wiped between every hyperfine iteration. Exposes the fetch + extract path. Baseline macOS 43.9 s ± 1.5 s (home 1 Gbps, 3 runs). The delta vs W2 warm-cache (~17 s) is mostly network-bound CDN throughput + zstd extract.

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
| `diff_for_unlink_link_precs` with k link / k unlink against N=50 000 | **confirmed (S1)** | [`bench_s1_diff_sort.py`](bench/phase2/bench_s1_diff_sort.py) |
| `PrefixGraph.__init__` with N records | **confirmed (S2)** | [`bench_s2_prefix_graph.py`](bench/phase2/bench_s2_prefix_graph.py) |
| `History.update()` against a synthetic N-line history | **measured, small (S3)** | [`bench_s3_history_update.py`](bench/phase2/bench_s3_history_update.py) |
| `PrefixReplaceLinkAction.verify` on 1/10/50 MB binaries (SHA-256 cost) | **confirmed (S4)** | [`bench_s4_verify_big_files.py`](bench/phase2/bench_s4_verify_big_files.py) |
| `_verify_prefix_level` with N synthetic collisions against big prefix | **confirmed, small (S5)** | [`bench_s5_verify_prefix_level.py`](bench/phase2/bench_s5_verify_prefix_level.py) |
| `_verify_individual_level` on a package with M prefix-replace files | **confirmed (S6)** | [`bench_s6_verify_individual.py`](bench/phase2/bench_s6_verify_individual.py) |
| `execute_threads = 1` → parallel `posix.link` fan-out at M hardlinks | **confirmed (S7)** | [`bench_s7_link_parallel.py`](bench/phase2/bench_s7_link_parallel.py) |
| `do_extract_action` on a 200 MB conda-zstd package with 1/3/6/12 threads | **confirmed (S8)** | [`bench_s8_extract_pool.py`](bench/phase2/bench_s8_extract_pool.py) |
| subprocess pyc-compile: per-package vs batched (S9) | **benchmarked, but S9 misidentified — see correction** | [`bench_s9_pyc_batching.py`](bench/phase2/bench_s9_pyc_batching.py) |
| `SolverInputState.installed` — per-access cost at parameterized N | **confirmed (S11)** | [`bench_s11_libmamba_installed.py`](bench/phase2/bench_s11_libmamba_installed.py) |
| `cps.extract_stream` per-member path-safety (`realpath + commonpath`) | **confirmed, small (S12)** | [`bench_s12_extract_safety.py`](bench/phase2/bench_s12_extract_safety.py) |
| `cps.stream_conda_component` double `ZipFile` parse per .conda | **confirmed (S13)** | [`bench_s13_zipfile_single.py`](bench/phase2/bench_s13_zipfile_single.py) |
| `cph._checksum` vs stdlib `hashlib.file_digest` at 50 MB | **null result (S14)** | [`bench_s14_checksum_file_digest.py`](bench/phase2/bench_s14_checksum_file_digest.py) |

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

#### S1, S3, S4, S12, S13, S14 — completeness round

Six additional Phase-2 suspects measured in a follow-up pass. Numbers
and takeaways:

| Suspect | Fixture | Current | Proposed fix | Speedup |
|---|---|---:|---:|---:|
| **S1** (diff sort key) | N=50 000, k=2 000 | 12.46 s | 15.9 ms | **782×** |
| **S1** (diff sort key) | N=10 000, k=400 | 321 ms | 1.49 ms | **215×** |
| **S3** (History.update) | 50 000 lines | 29.8 ms | — | null (small absolute) |
| **S4** (verify big files) | 3 × 50 MB | 302 ms (~100 ms/file) | gate SHA-256 on ``extra_safety_checks`` | **27 %** (B4 implemented, confirmed) |
| **S5** (clobber check) | 500 pkgs × 150 paths (75k) | 259 ms | precompute path→rec map | small; ~80 ms at W2 scale |
| **S12** (extract_stream safety) | 30 000 members | 347 ms | skip ``commonpath``, use ``startswith`` | 20 % → 279 ms (B12 implemented) |
| **S13** (ZipFile double parse) | 10 archives | 999 µs | parse once, reuse | **2×** → 502 µs (B13 implemented) |
| **S14** (``_checksum`` vs ``file_digest``) | 50 MB SHA-256 | 27.2 ms | ``hashlib.file_digest`` | null (~4 % within noise) |

Takeaways:

- **S1 has the strongest confirmed scaling** — 782× at N=50 000 makes
  B1 the most important latent fix for users with large prefixes. A
  full ``conda update --all`` on a research env would save ~24 s just
  in the two sort tails.
- **S4 confirmed and implemented (B4).** SHA-256 on large binaries is
  ~25 % of verify-per-file wall time; gating on ``extra_safety_checks``
  gives a measured 27 % reduction across 1/10/50 MB fixtures.
- **S5 is confirmed but small.** At W2 scale (29k link paths)
  ``_verify_prefix_level`` takes ~80 ms — under 0.3 % of W2 wall
  time. B5 (precompute map) would shave further but not worth a PR
  on its own.
- **S12 / S13 are real but small wins.** Implemented as B12/B13
  cleanup PRs to cps (B13 has a companion cph change for the new
  ``zf=`` kwarg).
- **S3 and S14 are null.** 30 ms for a 50k-line history is negligible
  at W-workload scale; ``file_digest`` doesn't beat the chunked loop
  because SHA-256 itself dominates wall time, not the Python-level
  iteration overhead.

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

#### Implementation status (Phase-3 PoCs, 2026-04-26)

Eight B-branches implemented and measured locally (plus paired cps/cph
branches for B13, plus one cps-level no-op fix B14). Not yet pushed or
PR'd; see the per-repo branches:

| Fix | Branch | Measured speedup | W-series impact |
|---|---|---|---|
| **B1** | `conda/conda:jezdez/track-b-b1-diff-sort-index` | **782× at N=50 000** (12.46 s → 15.9 ms) | ~24 s saved on ``update --all`` against 50k prefix |
| **B2** | `conda/conda:jezdez/track-b-b2-prefix-graph-by-name` | **53× at N=1 000** (4.18 s → 78 ms) | latent; helps big `update --all` |
| **B4** | `conda/conda:jezdez/track-b-b4-sha256-gate` | **27 % per-file** at 1/10/50 MB files | ~25 % off verify phase for ML-heavy prefixes |
| **B6** | `conda/conda:jezdez/track-b-b6-verify-parallel` | 1.26× at K=2 (opt-in via `verify_threads`) | ≤ ~25 % off W1 verify phase when enabled |
| **B7** | dropped | n/a | regresses on Linux |
| **B8** | `conda/conda:jezdez/track-b-b8-extract-threads` | ~8 % on mac, flat Linux | ~0.3 s off fetch/extract |
| **B9a** | dropped | n/a | aggregation is already wired; misidentified hot path |
| **B9c** | `conda/conda:jezdez/track-b-b9c-codesign-batch` | **W1 mac 33 %, W2 mac 15 %** | ~4 s off W2 mac, ~3 s off W1 mac |
| **B11** | `conda/conda-libmamba-solver:jezdez/track-b-b11-cache-installed` | **6500× per-access** (2.56 ms → 392 ns) | W3 mac **36.4 s → 12.1 s** standalone; stacked with B1/B2 → **1.72 s** |
| **B12** | `conda/conda-package-streaming:jezdez/track-b-b12-extract-safety` | 20 % per-member (11.6 → 9.3 µs) | superseded by B20 |
| **B13** | `conda/conda-package-streaming:jezdez/track-b-b13-single-zipfile-parse` + `conda/conda-package-handling:jezdez/track-b-b13-reuse-zipfile` | 2× (999 → 502 µs for 10 archives) | ~9 ms off W2 |
| **B14** | `conda/conda-package-streaming:jezdez/track-b-b14-extract-utime` | 3.4 % per extract (5-pkg fixture) | scales with total file count across installs |
| **B20** | `conda/conda-package-streaming:jezdez/track-b-b20-safety-fast-path` | **+22.6 % on Linux, neutral on mac** | **beats py-rattler on Linux by 6.3 %**; subsumes B12 |

B1 + B2 + B6 + B8 + B9c are cherry-picked into
``conda/conda:jezdez/track-b-stack``. B4 and the cps/cph B12/B13
pair are not yet in the stack (separate branches, would need another
combined branch to measure together).

##### S9 correction

Phase 1 W2's 186 subprocess calls on macOS are **`codesign`** on
osx-arm64 binaries post-prefix-rewrite (`conda/core/portability.py:121`),
not `compileall`. `conda/core/link.py:996` already
uses `AggregateCompileMultiPycAction` to batch all pyc compiles into
a single subprocess across packages. The Phase-2 S9 microbenchmark
measured a synthetic "N subprocess calls vs 1 batched call" comparison,
which confirmed the value of batching in principle — but conda's
shipping code already does this. **B9a as originally scoped is a
non-fix.** The real macOS subprocess overhead is codesign, which is
individually called per rewritten binary; batching codesign would
be a different fix (call it B9c, deferred) requiring a buffer/flush
pattern around `update_prefix`'s `subprocess.run(["codesign", ...])`.
On Linux there is no codesign step, which is part of why W2 is 2.5×
faster on Linux end-to-end.

### Phase 3: spot PoCs

One PR per surviving suspect, same scope rules as Track A.

| ID | Fixes | Sketch |
|---|---|---|
| B1 | S1 | Precompute `{rec: i for i, rec in enumerate(previous_records)}`; same for `final_precs`. Replace the `.index(x)` key function with a dict lookup. Phase-2 data: 782× at N=50 000. ~10 LOC. |
| B2 | S2 | Build a `by_name: dict[str, list[PrefixRecord]]` index once; replace the O(N²) inner loop with `for rec in by_name.get(spec.name, ()):`. Phase-2 data: O(N²) at ~9.5 µs per comparison → O(N×K) after fix, projected ~8-order-of-magnitude speedup at N=50 000. Preserves semantics. ~15 LOC plus tests. |
| B3 | S3 | Append-only history updates. `History.update()` only needs the last `==>` block. Read the file from the end until the last header, parse only that block. Verify against `History.get_user_requests()`. Phase-2 data: 30 ms at N=50 000 lines — small absolute cost, consider deferring. |
| B4 | S4 | Gate ``sha256_in_prefix = compute_sum(...)`` on ``context.extra_safety_checks``. Implemented on ``conda/conda:jezdez/track-b-b4-sha256-gate``. Phase-2 data: **27 % per-file verify reduction** at 1/10/50 MB. The sole consumer of the recorded hash (``doctor.health_checks.altered_files``) already handles ``None`` gracefully. ~12 LOC. |
| B5 | S5 | Build a single `{short_path: prefix_rec}` map once before the clobber loop. Phase-2 data: ``_verify_prefix_level`` is 2.8 µs/path and only ~80 ms at W2 scale — not worth a PR on its own. |
| B6 | S6 | Push the verify fan-out down one level: replace the bare `for` at `link.py:632` with a `ThreadPoolExecutor(max_workers=context.verify_threads or 4).map(...)`. Phase-2 data: 0.73 ms/action O(M) → expected ~4× speedup on NVMe. Thread-safety confirmed (each action writes to its own uuid-named intermediate). ~10 LOC plus one test. |
| B7 | S7 | **Revised:** Linux confirmation showed `ThreadPoolExecutor` parallel link *regresses* by 2–3× on fast filesystems (kernel serialization of inode creation is already fast enough that Python scheduling overhead dominates). macOS-only win at 1.7×. Options: (a) drop B7 entirely; (b) gate the fan-out behind a slow-disk heuristic (``stat`` the prefix, benchmark a handful of hardlinks, only parallelize if > 0.1 ms each); (c) leave it as an opt-in when the user sets `execute_threads > 1` manually. Recommended: (c) — leave user override working, don't change default. ~0 LOC (just documentation). |
| B8 | S8 | Change `EXTRACT_THREADS = min(cpu, 3)` to `EXTRACT_THREADS = 2`. Phase-2 data: serial and K=2 are best on both macOS and Linux; K ≥ 3 regresses on Linux by 28–40 %. One-line constant change in `conda/core/package_cache_data.py:74`. News entry noting the behaviour change. |
| ~~B9a~~ | ~~S9 (original)~~ | **DROPPED.** Phase-1 S9 analysis misidentified the hot path — the 186 subprocess calls on macOS W2 are ``codesign``, not ``compileall``. ``AggregateCompileMultiPycAction`` already handles the compile aggregation (see ``link.py:996``). No fix needed. |
| B9b | S9 (extended) | Top-level "compile all packages in one subprocess at end of transaction" pass. Gated on: verifying no post-link script depends on a prior package's `.pyc` being present before later packages are linked. Bigger PR, >50 LOC. Also possibly a non-fix given the existing aggregation. |
| **B9c** | **codesign (osx-arm64 binary rewrite)** | Queue osx-arm64 codesign calls during ``update_prefix()`` and flush as a single ``codesign -s - -f *paths`` at the end of ``_verify_individual_level``. Implemented on ``conda/conda:jezdez/track-b-b9c-codesign-batch``. Phase-4 data: W2 mac 26.67 s → 22.55 s (15 % reduction), W1 mac 10.37 s → 6.90 s (33 % reduction from a handful of base-package binary signatures). ~45 LOC in ``conda/core/portability.py`` + ``link.py``. |
| B11 | S11 | Cache the sorted result of `SolverInputState.installed` once per solve. Phase-2 data: 2.35 ms per access at N=5 000 → ~50 ns with cache. Fix lives in `conda/conda-libmamba-solver`, not `conda`. |
| **B14** | extract utime no-op (cps-level) | Make ``TarfileNoSameOwner.utime`` a no-op mirroring the existing ``chown`` no-op. conda packages have canonicalised tar mtimes at build time (``anonymize_tarinfo``); preserving them on disk encodes no user-meaningful information. Implemented on ``conda/conda-package-streaming:jezdez/track-b-b14-extract-utime``. Phase-2 S15 fixture: 3.78 s → 3.65 s (3.4 % reduction on 5-package extract). Per-file saving ~23 µs; compounds across large installs. |
| B12 | S12 | Precompute ``dest_dir + os.sep`` once per call to ``extract_stream`` and replace the per-member ``commonpath`` check with a ``startswith`` check. Implemented on ``conda/conda-package-streaming:jezdez/track-b-b12-extract-safety``. Phase-2 data: 20 % per-member reduction (11.6 µs → 9.3 µs). Small absolute impact; ships as a cps cleanup PR. |
| B13 | S13 | Accept a pre-opened ``zipfile.ZipFile`` via a new ``zf=`` kwarg on ``stream_conda_component``. Implemented on ``conda/conda-package-streaming:jezdez/track-b-b13-single-zipfile-parse``; companion cph consumer on ``conda/conda-package-handling:jezdez/track-b-b13-reuse-zipfile`` threads one ZipFile through both ``pkg`` and ``info`` components. Phase-2 data: 2× (999 µs → 502 µs for 10 archives). |

Dependencies: B7 gates on B6. Everything else is independent.

### Phase 4: end-to-end confirmation

Re-run W1/W2/W3 with hyperfine on the merged stack. Publish a
stacked-estimate table analogous to the [Track A version](track-a-startup.md#35a-stacked-estimate-conda-run-with-full-track-a).

#### Stacked run (2026-04-26)

Combined branches:

- `conda/conda:jezdez/track-b-stack` — cherry-picks B1 + B2 + B6 + B8 + B9c
- `conda/conda-libmamba-solver:jezdez/track-b-b11-cache-installed`

Default config (``verify_threads = 1``, so B6 is dormant). Both
editable-installed into the pixi env; Linux container rebuilt from
the same workspace checkouts via a bind-mount + ``pip install -e``.
B9c affects osx-arm64 only (codesign branch), so Linux numbers are
the same as the stack without B9c.

| Workload | Baseline (mac) | Stack (mac) | Change | Baseline (Linux) | Stack (Linux) | Change |
|---|---:|---:|---:|---:|---:|---:|
| W1 | 10.37 s | **6.90 s** | **−33 % (1.50×)** | 3.32 s | 3.05 s | −8 % |
| W2 | 26.67 s | **22.55 s** | **−15 %** | 10.66 s | 10.55 s | −1 % |
| W3 | 36.44 s | **1.72 s** | **−95 % (21.3×)** | 19.41 s | **1.21 s** | **−94 % (16.0×)** |

Observations:

- **W3 is the dominant beneficiary** on both platforms. B11 alone was
  3×; stacking B1 + B2 + B11 compounds because once ``.installed`` is
  cached, subsequent ``diff_for_unlink_link_precs`` and
  ``PrefixGraph.__init__`` calls also run at their post-fix costs.
- **W1 picks up an unexpected 33 % on macOS** from B9c — the python
  base package itself has osx-arm64 binaries (`bin/python3.13`, a
  few shared libs) that were paying per-file codesign in the shipping
  code. Batching those three-ish signatures into a single
  ``codesign`` call buys back ~3 s of fork/exec cost on macOS.
- **W2 on macOS drops 15 %**, the remaining 11 s that's not codesign
  is dominated by ``posix.link`` (S7 — can't fix without regressing
  Linux) and pyc compile (already aggregated; no fix available).
- **Linux stack wins relative to baseline are modest (1–8 %)** because
  the bottlenecks B2/B6/B9c target aren't hit on Linux fresh installs:
  no codesign, B6 opt-in, large-prefix paths not exercised by W1/W2.
  W3 still wins hugely because B11 is platform-agnostic.

Remaining headroom (after this stack):

- W1 mac: 6.9 s is mostly solver + verify; no single remaining fix
  with a big projected win.
- W2 mac: 22.5 s still dominated by ``posix.link`` (9 s), pyc
  subprocess (also ~1 s since already aggregated), and verify-phase
  prefix rewrites. B7 could save ~4 s on mac (2-3× regression on
  Linux so not shippable without platform gate).
- W3: essentially on the noise floor for the solver itself. The
  remaining second is progress-bar teardown + pyperf overhead.

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
| 2026-04-26 | **B20: hybrid safety check beats Rust on Linux** (with a careful security audit). First sketch was "swap realpath for normpath" — rejected as a security regression (loses symlink-chain traversal protection). Second sketch was "use stdlib ``filter='data'`` and drop the manual check" — safer but platform-asymmetric (+7 % Linux, −7 % mac). Final shipped hybrid: start in fast-path mode using string-only ``normpath + startswith``; flip to full realpath the first time any member is a symlink / hardlink / has absolute name / contains ``..``. Safety is identical to the pre-B20 all-realpath check (no risky member → no symlinks on disk → string normalisation is sufficient; risky member → fallback kicks in before writing anything). Compatibility survey: 186 conda-forge archives, 30 299 members, 1 274 symlinks, 0 failures; 81 % of members take the fast path, 142 / 186 archives never trigger the fallback. Measured end-to-end on the S16 fixture: **Linux ext4 2.88 s → 2.23 s (+22.6 %) — faster than py-rattler's 2.38 s by 6.3 %**. macOS APFS is within noise (APFS caps file-creation rate at ~2300/s regardless of language). Implemented on ``conda/conda-package-streaming:jezdez/track-b-b20-safety-fast-path``, subsumes the earlier B12 branch. |
| 2026-04-26 | **Rust-in-cps exploration (S16) + unpacking ceiling analysis.** Benchmarked ``rattler.package_streaming.extract`` (py-rattler, the Rust-backed extract shipped under the ``conda/`` org by prefix.dev) against cps's current stdlib-tarfile path on the same 5 real scientific-Python ``.conda`` archives. Results: **macOS APFS within noise** (both ~80 MB/s, ~2300 files/s); **Linux ext4 ~12 % faster in rattler's favour** (557 vs 495 MB/s, 6787 vs 6033 files/s). Headline finding: extract is *syscall-bound*, not CPU-bound — Rust's advantage over Python + stdlib tarfile is only the per-call Python overhead and stdlib tarfile's 15-lstat-per-file path-resolution cost. The filesystem ceiling is what we hit on both platforms. Added an "Unpacking: where the limits actually are" section to the doc with three practical adoption paths for py-rattler in cps (optional fast path / hard-dep swap / cph absorb + rattler backend) and a strategic takeaway that Rust adoption here is about ecosystem alignment and maintenance-surface reduction, not speed. [Superseded by B20 — pure Python can beat Rust here, which the next changelog entry confirms.] |
| 2026-04-26 | **Deep cph audit + S15 + B14.** Per-module review of ``src/conda_package_handling/`` confirms the install hot path is ``api.extract`` → ``CondaFormat_v2.extract`` → ``streaming._extract`` → cps and nothing else in cph is touched during transactions (create/transmute/validate paths run only at build time). New S15 microbenchmark: cph dispatch adds 0.8 % (30 ms on 3.78 s) over calling cps directly — essentially free — so the cps author's direction of folding cph into cps is about API surface, not performance. Full cProfile of extract wall time added to the doc: 22 % in ``io.open``, 12 % in ``chmod``+``utime``, 8 % in ``lstat`` from stdlib tarfile internals, 9 % in zstd decompression. New **B14 implemented** (``TarfileNoSameOwner.utime`` → no-op, mirroring existing ``chown`` no-op; conda packages have canonicalised mtimes at build time): 3.4 % per-extract reduction on the 5-package fixture. Speedup-options table added: ``chmod`` can't easily skip because conda-forge uses 0o664/0o775 modes that always differ from umask-default; multi-threaded zstd is 0 % because conda-forge compresses single-frame; bigger wins (20-40 %) would require a custom vendored tar extractor which is out of Track B scope. |
| 2026-04-26 | **Follow-through: B4, B12, B13 implemented; W4 workload added; S5 measured.** New branches: ``conda/conda:jezdez/track-b-b4-sha256-gate`` (27 % per-file verify reduction at 1/10/50 MB; gates the SHA-256 hash on ``context.extra_safety_checks``). ``conda/conda-package-streaming:jezdez/track-b-b12-extract-safety`` (per-member safety check drops ``commonpath`` in favour of ``startswith``, 20 % faster). ``conda/conda-package-streaming:jezdez/track-b-b13-single-zipfile-parse`` + companion ``conda/conda-package-handling:jezdez/track-b-b13-reuse-zipfile`` (thread one ``ZipFile`` through both components of a ``.conda``; 2× faster per archive, via a new ``zf=`` kwarg). New **W4 workload** (cold-cache data-science install, wipes pkgs/ between iterations): 43.9 s ± 1.5 s on macOS — ~17 s attributable to cold-cache fetch + extract over warm W2. New **S5 benchmark**: ``_verify_prefix_level`` scales at 2.8 µs/path — confirmed but small (~80 ms at W2 scale), not worth a standalone PR. |
| 2026-04-26 | **Phase 4 + Phase-2 completeness + B9c.** Combined ``conda/conda:jezdez/track-b-stack`` (cherry-picks B1 + B2 + B6 + B8 + B9c) and ``conda/conda-libmamba-solver:jezdez/track-b-b11-cache-installed`` measured end-to-end: **W3 36.4 s → 1.72 s (21.3×) on mac / 19.4 s → 1.21 s (16.0×) on Linux**; W1 mac gains 33 % from B9c alone (base-package binary codesign batching). B9c added: queues osx-arm64 codesign calls in ``portability.update_prefix`` and flushes a single batched ``codesign -s - -f *paths`` at the end of ``_verify_individual_level``. Six additional Phase-2 benchmarks landed for S1, S3, S4, S12, S13, S14. Standout results: S1 at N=50 000 confirms **782×** speedup projection (12.46 s → 16 ms), S4 confirms SHA-256 on large binaries is ~25 % of per-file verify cost; S14 is a null result (``hashlib.file_digest`` is within noise of the chunked loop). |
| 2026-04-26 | **Phase 3: five B-branches implemented and measured locally**, no PRs yet. **B8** (``EXTRACT_THREADS = 2``, one-liner) in ``conda/conda:jezdez/track-b-b8-extract-threads``. **B1** (``diff_for_unlink_link_precs`` dict-lookup sort key, ~15 LOC) in ``conda/conda:jezdez/track-b-b1-diff-sort-index``. **B2** (``PrefixGraph.__init__`` by-name index, 19 LOC, **53× at N=1000**) in ``conda/conda:jezdez/track-b-b2-prefix-graph-by-name``. **B6** (opt-in parallel ``_verify_individual_level`` via ``context.verify_threads``, 40 LOC, 1.26× at K=2) in ``conda/conda:jezdez/track-b-b6-verify-parallel``. **B11** (cache ``SolverInputState.installed`` on instance, 22 LOC, **6500× per-access / W3 36s → 12s**) in ``conda/conda-libmamba-solver:jezdez/track-b-b11-cache-installed``. **B9a dropped**: the Phase-1 W2 186-subprocess overhead is ``codesign`` on osx-arm64 binaries (``conda/core/portability.py:121``), not ``compileall`` — conda already aggregates pyc compile at ``link.py:996``. Re-scoping to a "batch codesign" fix (B9c) deferred. **B7 also dropped**: Linux regresses by 2–3×. Uncovered a methodology fix: the S2 fixture was generating cyclic dependency graphs and the ``_toposort_handle_cycles`` path was swallowing the benchmark signal; DAG-enforcing fixture committed separately. |
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
