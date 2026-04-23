      Reducing conda Transaction Latency: Track B

# Reducing conda Transaction Latency: Track B

| | |
|---|---|
| **Initiative** | [conda-tempo](https://github.com/jezdez/conda-tempo) — measuring and reducing conda's tempo |
| **Author** | Jannis Leidel ([@jezdez](https://github.com/jezdez)) |
| **Date** | April 23, 2026 |
| **Status** | Planning — measurement harness pending |
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

---

## Investigation phases

Each phase gates on the previous one. PoCs only get built for suspects
that survive Phase 2.

### Phase 1: measurement harness

Scaffold committed in [`bench/`](bench/); baseline measurements pending. Three fixed workloads:

- **W1. Fresh install, small:** `conda create -n bench_w1 -y python=3.13 requests` (~15 pkgs). Baseline per-transaction overhead.
- **W2. Fresh install, data-science:** `conda create -n bench_w2 -y python=3.13 pandas scikit-learn matplotlib jupyter` (~150 pkgs, `noarch: python` heavy → `.pyc` compile dominates).
- **W3. Large-prefix dry-run:** `conda update -n bench_big -y --all --dry-run`, where `bench_big` is seeded to 50k synthetic `PrefixRecord` JSON files via [`bench/seed_big_prefix.py`](bench/seed_big_prefix.py). Exercises S1 and S2 (solve-side suspects); the verify/execute suspects (S3–S8) need a real transaction and are deferred to a future W4.

Driver: [`bench/workloads.sh`](bench/workloads.sh) wraps hyperfine
(`--warmup 1 --runs 5 --export-json`). cProfile via
[`bench/profile.py`](bench/profile.py); conda-internal per-phase timings via
[`bench/parse_time_recorder.py`](bench/parse_time_recorder.py) against the
existing `time_recorder("fetch_extract_execute")` and
`time_recorder("unlink_link_execute")` markers. See [`bench/README.md`](bench/README.md)
for prereqs.

Deliverable: per-phase wall-time table + cProfile top-20 per workload, committed
to `data/phase1/<workload>/`.

### Phase 2: micro-benchmarks

Pending. `pytest-benchmark` with `--benchmark-autosave` against a
synthetic fixture that builds a 50k-record `PrefixData`.

| Target | Expected to confirm |
|---|---|
| `diff_for_unlink_link_precs` with 2k link / 2k unlink against 50k | S1 |
| `PrefixGraph.__init__` with 50k records | S2 |
| `History.update()` against a synthetic 100k-line history | S3 |
| `PrefixReplaceLinkAction.verify` on a 50 MB binary | S4 |
| `_verify_prefix_level` with 100 synthetic collisions against 50k | S5 |
| `_verify_individual_level` on a package with 2000 prefix-replace files | S6 |
| `do_extract_action` on a 200 MB conda-zstd package with 1/3/6/12 threads | S8 |

Deliverable: keep/drop verdict per suspect, with an estimated wall-time
saving on W2 and W3.

### Phase 3: spot PoCs

One PR per surviving suspect, same scope rules as Track A.

| ID | Fixes | Sketch |
|---|---|---|
| B1 | S1 | Precompute `{rec: i for i, rec in enumerate(previous_records)}`; same for `final_precs`. Replace the `.index(x)` key function with a dict lookup. ~10 LOC. |
| B2 | S2 | Index `PrefixGraph` candidates by package name. `MatchSpec(dep).name` is always set; build `by_name: dict[str, list[PrefixRecord]]` once and iterate only `by_name[spec.name]` instead of all N. Preserves semantics. |
| B3 | S3 | Append-only history updates. `History.update()` only needs the last `==>` block. Read the file from the end until the last header, parse only that block. Verify against `History.get_user_requests()`. |
| B4 | S4 (conditional) | Only compute `sha256_in_prefix` when `context.extra_safety_checks`. Requires a grep for readers of `sha256_in_prefix` first. |
| B5 | S5 | Build a single `{short_path: prefix_rec}` map once before the clobber loop. |
| B6 | S6 (conditional) | Parallelize `_verify_individual_level` inside a prefix using the existing `verify_executor`. Needs a thread-safety check on `PrefixReplaceLinkAction.intermediate_path`. |
| B7 | S7 (conditional) | Change the default of `verify_threads` and `execute_threads` from 1 to `min(cpu, 4)` when neither is set. Behavior change; news entry required. Measure first. |

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
| 2026-04-23 | **Phase 1 harness scaffold committed** in [`bench/`](bench/): `workloads.sh` for W1/W2/W3-dryrun, `profile.py` for cProfile, `seed_big_prefix.py` for the W3 synthetic 50k-record prefix, `parse_time_recorder.py` for conda's internal per-phase timings. Data layout under `data/phase1/<workload>/`. No measurements yet. |
| 2026-04-23 | **Migrated to [conda-tempo](https://github.com/jezdez/conda-tempo) repo.** Source-of-truth moved from gist `1fd8467189ff7bd928fdea5a3ec4c73f` to `jezdez/conda-tempo/track-b-transaction.md`. Cross-links to Track A and Track C are now relative repo paths. |
| 2026-04-23 | Track B scaffold created. Phase 0 of the transaction-perf plan: split the former single-gist report into three (Track A trimmed, Track B new, Track C new for PEP 810 and speculative research). Suspects S1–S10 identified from a read-through of `link.py`, `path_actions.py`, `package_cache_data.py`, `solve.py`, `prefix_data.py`, `prefix_graph.py`, `history.py`, and `gateways/disk/`. No measurements yet. |
