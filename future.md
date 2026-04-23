      Future conda startup: Python 3.15 lazy imports and speculative research

# Future conda startup: Python 3.15 lazy imports and speculative research

| | |
|---|---|
| **Initiative** | [conda-tempo](https://github.com/jezdez/conda-tempo) — measuring and reducing conda's tempo |
| **Author** | Jannis Leidel ([@jezdez](https://github.com/jezdez)) |
| **Date** | April 3, 2026 (split from the Track A doc on April 23, 2026; migrated to conda-tempo repo same day) |
| **Status** | Research — not actionable until Python 3.15 feedstock packaging lands |
| **See also** | [Track A — startup latency (shippable today)](startup.md) · [Track B — transaction latency](transaction.md) |

This document collects the forward-looking parts of the original conda startup
research: the CPython optimization landscape, the Python 3.15 / PEP 810
lazy-imports prototype, and the speculative-but-unprototyped opportunities
(Rust bootstrapper, daemon, AOT compilation, plugin-group refactor). The
immediate, Python-3.10+ changes live in the [Track A (startup.md)](startup.md);
the transaction-pipeline work lives in the [Track B (transaction.md)](transaction.md).

## Contents

- [Summary](#summary)
- [1. Research: CPython startup optimization landscape](#1-research-cpython-startup-optimization-landscape)
  - [1.1 Techniques evaluated](#11-techniques-evaluated)
  - [1.2 PEP 810: explicit lazy imports](#12-pep-810-explicit-lazy-imports)
  - [1.3 Python distribution build comparison](#13-python-distribution-build-comparison)
- [2. Methodology: custom CPython builds](#2-methodology-custom-cpython-builds)
- [3. Results](#3-results)
  - [3.1 CPython interpreter startup (bare Python)](#31-cpython-interpreter-startup-bare-python)
  - [3.2 Module count reduction on 3.15 prototype](#32-module-count-reduction-on-315-prototype)
  - [3.3 Measured impact: Track A + Track C (with Python 3.15)](#33-measured-impact-track-a--track-c-with-python-315)
- [4. Python 3.15 features relevant to this work](#4-python-315-features-relevant-to-this-work)
- [5. Track C implementation roadmap (PEP 810 lazy imports)](#5-track-c-implementation-roadmap-pep-810-lazy-imports)
- [6. Speculative opportunities](#6-speculative-opportunities)
- [Changelog](#changelog)
- [Appendix A: CPython build configuration](#appendix-a-cpython-build-configuration)
- [Appendix B: Python distribution startup benchmarks](#appendix-b-python-distribution-startup-benchmarks)

---

## Summary

Python 3.15 introduces the `lazy import` keyword via PEP 810, which defers
module loading until first attribute access. Measured on a 3.15 prototype
combined with Track A's skip-plugin-hooks change:

| Command | Stock 3.13 | Python 3.15 + lazy | Saved | Speedup |
|---|---|---|---|---|
| `conda activate` | 371 ms | 110 ms | −261 ms | 3.4× |
| `conda --version` | 384 ms | 62 ms | −322 ms | 6.2× |
| `conda env list` | 391 ms | 247 ms [^1] | −144 ms | 1.6× |
| `conda config --show` | 389 ms | 243 ms [^1] | −146 ms | 1.6× |

Track A alone already reaches ~85 ms on activate and ~23 ms on `--version`
on Python 3.13, so on activate-like paths Track C adds no wall-time over
Track A. On subshell commands (`env list`, `list`, `config`, help variants)
Track C closes the remaining gap from the 240–250 ms floor down into the
190–210 ms range once all subcommands have their heaviest imports marked
`lazy`.

Track C cannot ship until conda-forge packages Python 3.15. Feedstock work
(B1 → C1 after the renumber) is the gating item.

Two distribution-level improvements are worth pursuing in the feedstock
independent of Track C:

- Enable PGO on the Anaconda Windows build (10–20% general speedup).
- Evaluate BOLT for the conda-forge Linux x86_64 recipe (5–8% on top of
  PGO+LTO).

Everything past that — Rust bootstrapper, daemon, AOT compilation,
plugin-group refactor — is speculative and collected under
[section 6](#6-speculative-opportunities).

---

## 1. Research: CPython startup optimization landscape

We surveyed 20+ years of CPython optimization work, including PEPs, the CPython
issue tracker, python-dev discussions, and recent conference talks.

### 1.1 Techniques evaluated

| Technique | Status in 3.15 | Impact | Notes |
|---|---|---|---|
| Frozen modules | Default since 3.11 | ~5 ms | stdlib bootstrap modules embedded in binary |
| PGO (Profile-Guided Optimization) | Mature | ~5–10% | Standard `make profile-opt` uses test suite |
| LTO (Link-Time Optimization) | Mature | ~3–5% | `--with-lto=full` in feedstock |
| Computed gotos | Mature | ~5% dispatch | `--with-computed-gotos` |
| Tail-call interpreter | 3.14+ (macOS ARM) | ~10% interp | `--with-tail-call-interp` |
| PEP 690 (global lazy imports) | Rejected | — | Too implicit, debugging concerns |
| PEP 810 (explicit lazy imports) | Accepted, in 3.15 | Up to 7× | `lazy import X` keyword |
| Deferred stdlib imports | 3.12+ | ~10–20 ms | `ast`, `warnings`, `traceback` deferred |
| BOLT (post-link optimizer) | Linux x86 only | ~5–8% | Not available on macOS ARM |
| Experimental JIT | 3.14+ | Negligible for startup | Benefits long-running code |

### 1.2 PEP 810: explicit lazy imports

PEP 810 (accepted for Python 3.15) adds an explicit `lazy` keyword for imports:

```python
lazy import requests          # module not loaded until first attribute access
lazy from tqdm import tqdm    # name bound but module not imported yet
```

Unlike PEP 690 (rejected), this is opt-in per-import, giving library authors
fine-grained control. The module is loaded on first attribute access, making it
transparent to calling code.

### 1.3 Python distribution build comparison

A natural question: could switching to a different Python distribution
(conda-forge, python-build-standalone, Homebrew) yield startup savings? We
compared the four Python distributions commonly used on macOS ARM64 with three
external research sources.

#### Build configurations

| Feature | Anaconda defaults | conda-forge | python-build-standalone (PBS) | Homebrew |
|---|---|---|---|---|
| Python version | 3.13.12 | 3.13.12 | 3.13.12 | 3.14.2 |
| Compiler | Clang 20.1.8 | Clang 19.1.7 | Clang 22.1.1 | Apple Clang 17 |
| Optimization level | `-O2`[^3] | `-O2`[^3] | `-O3` | varies |
| PGO | Yes | Yes | Yes | Yes |
| LTO | `--with-lto=full` | `--with-lto=full` | `--with-lto` (ThinLTO) | Yes |
| Computed gotos | Yes | Yes | Yes | Yes |
| Tail-call interp | macOS only | macOS only | macOS only | — |
| Experimental JIT | `yes-off` | `no` | `yes-off` | — |
| BOLT (post-link) | No | No | Linux x86_64 only | No |
| mimalloc | No | No | Yes (3.13+) | No |
| Frozen modules | 14 (stock) | 14 (stock) | 14 (stock) | 14 (stock) |
| Binary size | 6.1 MB (static) | 5.8 MB (static) | 50 KB + 17 MB dylib | varies |

On mainstream platforms (Linux x86_64, macOS ARM64), the Anaconda and
conda-forge builds use effectively identical optimization profiles. The
meaningful gaps are:

- **Windows**: Anaconda disables PGO entirely ("AP doesn't support PGO atm?"
  per the build script). conda-forge enables it. This is a 10–20% general
  performance difference on Windows.
- **linux-ppc64le**: Anaconda skips PGO, LTO, and `-O3` entirely. conda-forge
  builds fully optimized.
- **BOLT**: Only PBS applies BOLT, and only on Linux x86_64. Neither conda
  feedstock uses it. BOLT reorders basic blocks and functions based on profile
  data and can yield 5–8% additional speedup on top of PGO+LTO.

#### Startup benchmarks (macOS ARM64, Python 3.13.12)

All measurements: `hyperfine -N --warmup 10 --runs 100`, `/tmp` working
directory, absolute binary paths. Full data in
[Appendix B](#appendix-b-python-distribution-startup-benchmarks).

| Scenario | PBS | Anaconda | conda-forge | Homebrew 3.14 |
|---|---|---|---|---|
| `python -c pass` | 23.8 ms | 24.2 ms | 20.5 ms | 42.2 ms |
| `python -S -c pass` | 18.0 ms | 14.0 ms | 14.4 ms | — |
| `import json` | 26.0 ms | 30.9 ms | 31.0 ms | — |
| 7 stdlib imports[^4] | 48.5 ms | 44.0 ms | 43.9 ms | — |

> [!IMPORTANT]
> On macOS ARM64, PBS, Anaconda, and conda-forge are within measurement noise
> of each other for startup. The standard deviations (3–14 ms) exceed the
> systematic differences between builds. Switching Python distribution is
> **not** a viable path to reducing conda startup time.

#### External research: jjhelmus/cpython-benchmarks

Jonathan Helmus ([jjhelmus/cpython-benchmarks](https://github.com/jjhelmus/cpython-benchmarks))
benchmarks multiple Python distributions using [pyperformance](https://github.com/python/pyperformance)
and custom library-focused benchmarks. Key findings:

- PBS is the fastest overall, conda-forge a close second (within a few percent
  on interpreter benchmarks).
- The largest performance differences are in **bundled C libraries**, not the
  interpreter. python.org macOS builds show a 14× regression in LZMA
  compression (different liblzma version) and up to 69% SQLite differences.
- On Linux, conda-forge and PBS have roughly 2× faster LZMA decompression
  compared to distro packages (Debian, Ubuntu, Fedora), because both bundle
  optimized liblzma.
- The methodology uses `pyperf` with `--rigorous` mode and statistical
  significance testing.

These findings confirm that for conda's startup problem, the Python interpreter
itself is not the bottleneck. All mainstream distributions compile with
PGO+LTO+computed gotos, and the remaining differences are in the single-digit
percentages.

#### External research: python-build-standalone optimizations

[astral-sh/python-build-standalone](https://github.com/astral-sh/python-build-standalone)
builds are the most aggressively optimized CPython distributions available.
Their distinguishing features (not used by either conda feedstock):

1. **BOLT optimization** (Linux x86_64 only) — post-link binary rewriting
   that reorders basic blocks and functions based on runtime profiles. Uses
   `-reorder-blocks=ext-tsp`, `-reorder-functions=cdsort`, `-split-functions`,
   `-icf=safe`, `-inline-all`, and `-frame-opt=hot`. Estimated 5–8% total
   speedup on top of PGO+LTO.
2. **Parallel PGO profiling** — runs the PGO workload with `-j ${NUM_CPUS}`
   and uses `LLVM_PROFILE_FILE=code-%128m.profclangr` (128-file pool) to
   prevent profile data loss from PID collisions.
3. **Frame pointers** (`-fno-omit-frame-pointer`, `-mno-omit-leaf-frame-pointer`)
   — enables `perf`-based profiling and flame graphs at ~1–2% performance cost.
4. **mimalloc** — enabled explicitly on 3.13+ via `--with-mimalloc`.
5. **Microarchitecture builds** — x86_64_v2 (SSE4), v3 (AVX2), v4 (AVX-512)
   variants that leverage newer instruction sets.

Of these, **BOLT is the most interesting for conda feedstocks**, particularly
on Linux x86_64 (the most common CI and server platform). Conda-forge could
adopt BOLT by adding the appropriate patches and LLVM BOLT tooling to the
build. The PBS repository documents the required flags and workarounds
(ICF safety, skip-funcs for crash-prone functions, noexecstack hardening).

> [!NOTE]
> BOLT is not available on macOS ARM64 (LLVM limitation). The practical benefit
> for conda startup would be 5–8% of interpreter time — roughly 1–3 ms off bare
> Python startup, or 15–30 ms off a full conda invocation. Meaningful, but far
> less than the 57–286 ms savings from Track A's import-level changes.

#### Implications for this work

The Python distribution comparison reinforces the central finding of the
startup research: conda's startup overhead is dominated by **what conda
imports**, not by **how fast Python runs imports**. The interpreter startup
cost (14–24 ms) is a small fraction of conda's total startup (370+ ms). Even
if we could halve interpreter startup through BOLT or `-O3`, the impact on
conda would be <15 ms — well below the 57–286 ms savings from Track A.

That said, two distribution-level improvements are worth pursuing for the conda
feedstocks, independent of this startup work:

1. **Enable PGO on Anaconda Windows builds.** This is a 10–20% general
   performance improvement that's free to adopt. The conda-forge recipe already
   does it.
2. **Evaluate BOLT for the conda-forge Linux x86_64 recipe.** The
   python-build-standalone project has a tested BOLT configuration with
   documented workarounds. conda-forge could adopt this for a 5–8% improvement
   on Linux servers, benefiting all conda-forge users, not just conda.

<div align="right"><a href="#contents">↑ Contents</a></div>

---

## 2. Methodology: custom CPython builds

We built four variants of CPython 3.15.0a7 from source on macOS ARM64:

| Variant | Configuration |
|---|---|
| no-pgo | LTO + O3 only, no PGO |
| standard-pgo | LTO + O3 + standard PGO (`-m test --pgo`) |
| startup-pgo | LTO + O3 + custom startup-focused PGO profile |
| extfrozen-pgo | Above + extended frozen modules (argparse, json, etc.) |

The startup-focused PGO profile (`Tools/pgo_startup_profile.py`) exercises
the code paths most relevant to CLI tool startup: import machinery,
`importlib.metadata` scanning, argparse, JSON encode/decode, pathlib operations,
configparser, re compilation, and collections usage. This ensures the PGO
optimizer prioritizes the hot paths that matter for startup latency rather than
the test suite's broader coverage.

The extended frozen modules embed 16 additional stdlib modules into the CPython
binary (argparse, configparser, json, logging, pathlib, re, typing, etc.),
eliminating filesystem I/O for imports that every conda invocation needs.

Benchmarking tool: [hyperfine](https://github.com/sharkdp/hyperfine) with
`--shell=none` (`-N`) for sub-millisecond accuracy, 3 warmup runs, 30–50
measured runs. Module counts were obtained via `len(sys.modules)` at each
instrumentation point.

<div align="right"><a href="#contents">↑ Contents</a></div>

---

## 3. Results

### 3.1 CPython interpreter startup (bare Python)

| Variant | `python -c pass` | `import json,argparse,...` | vs. stock 3.13 |
|---|---|---|---|
| Stock conda-forge 3.13.12 | 21.6 ms | 59.2 ms | baseline |
| 3.15 no-PGO (LTO+O3) | 21.0 ms | 43.1 ms | −27% import |
| 3.15 standard-PGO | 20.7 ms | 40.7 ms | −31% import |
| 3.15 startup-PGO | 20.3 ms | 39.6 ms | −33% import |
| 3.15 startup-PGO+frozen | 20.6 ms | 39.3 ms | −34% import |

Python 3.15 is ~33% faster at importing the typical conda startup module set,
primarily due to stdlib deferred imports, improved import machinery, and our
startup-focused PGO profile.

### 3.2 Module count reduction on 3.15 prototype

| Checkpoint | Stock conda | Lazy prototype | Reduction |
|---|---|---|---|
| After parser build | 836 modules | 189 modules | −77% |
| After `create --help` | 836 modules | 662 modules | −21% |
| After `--version` | 836 modules | ~65 modules | −92% |

### 3.3 Measured impact: Track A + Track C (with Python 3.15)

We benchmarked the full Python 3.15 + lazy imports prototype with
`hyperfine --shell=none --runs 20`:

| Command | Stock 3.13 (ms) | 3.15 lazy (ms) | 3.15 lazy + A6 (ms) | Method |
|---|---|---|---|---|
| `conda activate` | 367 | 236 | 110 | both measured |
| `conda --version` | 384 | 62 | — | measured |
| `conda --help` | 389 | 257 | — | measured |
| `conda env list` | 394 | 247 [^1] | — | measured |
| `conda config --show` | 389 | 243 [^1] | — | measured |
| `conda create --help` | 384 | 244 [^1] | — | measured |
| `conda install --help` | 383 | 242 [^1] | — | measured |

Summary across both tracks:

| Scenario | Stock 3.13 | Track A | Saved | Track A + C | Saved |
|---|---|---|---|---|---|
| `conda activate` (A1+A6) | 371 ms | 85 ms | −286 ms (4.4×) | 110 ms | −261 ms (3.4×) |
| `conda --version` (A7) | 384 ms | 23 ms | −361 ms (16.7×) | 62 ms | −322 ms (6.2×) |
| `conda env list` | 391 ms | 382 ms | −9 ms (A1 only [^2]) | 247 ms [^1] | −144 ms (1.6×) |
| `conda config --show` | 389 ms | ~332 ms | −57 ms (est.) | 243 ms [^1] | −146 ms (1.6×) |

| Command | Stock | | Track A | | Saved | A + C | | Saved |
|:--|--:|:--|--:|:--|--:|--:|:--|--:|
| activate | 371 | `████████████████████` | 85 | `█████` | −286 ms | 110 | `██████` | −261 ms |
| --version | 384 | `████████████████████` | 23 | `█` | −361 ms | 62 | `███` | −322 ms |
| env list | 391 | `█████████████████████` | 382 | `████████████████████` | −9 ms | 247 | `█████████████` | −144 ms |
| config | 389 | `█████████████████████` | ~332 | `██████████████████` | −57 ms | 243 | `█████████████` | −146 ms |

> [!NOTE]
> Track A activate (85 ms) is actually faster than Track A + Track C
> (110 ms) because the 3.15 build still has `ruamel.yaml` and other context
> imports that the runtime A1 shim blocks more aggressively than the `lazy`
> keyword. The `lazy` keyword defers imports but still resolves them on first
> attribute access.

The additional gains from 3.15 come from three areas:

1. ~33% faster import machinery. Python 3.15 imports the same modules faster
   due to internal optimizations and stdlib deferred imports.
2. `lazy import` for context dependencies. `ruamel.yaml` (31 modules),
   `frozendict` (5 modules), and several stdlib modules are deferred until
   actually accessed, cutting the context import phase from ~109 ms to ~35 ms.
3. `lazy import` for plugin internals. When plugins do load, their internal
   imports (`rich`, `pydantic`, `tqdm`) are deferred, so only the code paths
   actually exercised pay the import cost.

<div align="right"><a href="#contents">↑ Contents</a></div>

---

## 4. Python 3.15 features relevant to this work

### 4.1 PEP 810: explicit lazy imports

The `lazy` keyword allows deferring imports of heavy modules (requests, pluggy,
tqdm, ruamel.yaml) without changing calling code. The deferred import triggers
transparently on first attribute access.

PEP 810 also provides `__lazy_modules__` as a per-module opt-in mechanism that
works without changing import syntax, useful for third-party libraries that
want to offer lazy loading to consumers.

### 4.2 Stdlib deferred imports

Python 3.12+ defers several stdlib imports (`ast`, `warnings`, `traceback`,
`typing`). Python 3.15 extends this further, giving ~10–20 ms of free startup
improvement.

### 4.3 Tail-call interpreter (macOS ARM64)

The `--with-tail-call-interp` flag (3.14+, macOS ARM64) uses tail calls for
opcode dispatch, improving interpreter throughput by ~10%. This is already
enabled in our prototype builds.

<div align="right"><a href="#contents">↑ Contents</a></div>

---

## 5. Track C implementation roadmap (PEP 810 lazy imports)

These changes use the `lazy import` keyword from PEP 810 and require Python
3.15+ at runtime (the syntax is a `SyntaxError` on older versions).

### C1. Python 3.15 feedstock packaging

> **Feedstock** · target: 3.15 beta 1 (~May 2026) · unlocks PEP 810

- Update the conda-forge `python` feedstock to build 3.15
- Include the startup-focused PGO profile as a build option
- Include the extended frozen modules for CLI-critical stdlib modules
- Ensure compatibility with conda-libmamba-solver and conda-rattler-solver
- Test across all platforms (Linux x86_64, Linux aarch64, macOS ARM64, Windows)

### C2. Apply `lazy import` across hot startup path

> **~170 lines** · 21 files · 6.2× for `--version` · 3.3× for activate

Apply `lazy import` / `lazy from` to heavy imports in:

- `context.py` — defer `platform`, `struct`, `warnings`, `pathlib`, `frozendict`
- `configuration.py` — defer `ruamel.yaml`, `frozendict`, `collections`, `re`
- `conda_argparse.py` — defer `context`, all `configure_parser_*` imports
- `exception_handler.py` — defer `os`, `logging`, `deprecations`
- `plugins/manager.py` — defer `pluggy`, `importlib.metadata`, all plugin modules
- `notices/core.py`, `notices/fetch.py` — defer `requests`, `models.channel`
- `resolve.py` — defer `tqdm`, `frozendict`

> [!NOTE]
> This requires a Python 3.15 minimum for conda, or conditional syntax via
> `__lazy_modules__` as a bridge for older versions.

### C3. Deep import graph cleanup

> **Ongoing** · incremental after C2 · further gains

- Move `requests` to a truly lazy/optional dependency for the notices system
- Defer `tqdm` loading until progress bars are actually displayed
- Apply lazy imports to deeper internal modules (`models/channel.py`,
  `core/solve.py`, `gateways/connection/`)

### Bridge to Track A's A22

PEP 810's `lazy import` operates at the bytecode level (`IMPORT_NAME` with a
lazy flag) and doesn't involve `__getattr__` at all, so once Track C lands it
would let us drop the tracer detection and subprocess test that Track A's A22
([#15893](https://github.com/conda/conda/pull/15893)) added to work around
coverage.py's C-tracer-triggered glibc corruption during interpreter shutdown.

<div align="right"><a href="#contents">↑ Contents</a></div>

---

## 6. Speculative opportunities

> [!NOTE]
> The following ideas go beyond the measured, ready-to-ship changes in
> Track A and Track C. They are not yet prototyped or benchmarked, but
> represent realistic next steps based on the profiling and architectural
> understanding gained during this research.

### S1. Rust bootstrapper pattern (conda-express / cx)

The [conda-express](https://github.com/jezdez/conda-express) project (cx) is a
lightweight Rust binary (7–11 MB) that acts as a front-end to conda. It handles
bootstrap, process hand-off, and shell integration in Rust, then `execvp()`s
into the real conda Python process for actual package management.

This pattern eliminates Python startup overhead entirely for a subset of
user-visible interactions:

| Interaction | Who handles it | Python startup cost? |
|---|---|---|
| `cx bootstrap` | Rust (rattler) | None — no Python involved |
| `cx shell myenv` | Rust → conda-spawn | None — Rust dispatches directly |
| `cx --version` | Rust (clap) | None — answers from compiled binary |
| `cx install numpy` | Rust → Python conda | Yes — full conda startup |

For commands that still require Python (install, create, solve), the Track A
and Track C optimizations remain essential. But for the outer shell —
bootstrapping, environment activation, version checks, help — cx responds in
<5 ms because no Python interpreter is launched.

How cx complements this work:

- With Track A, cx handles the "instant" commands (version, shell, help) in
  Rust, while Track A makes the Python-delegated commands (install, create,
  list) 2–4× faster than today.
- With Track C, Python 3.15 lazy imports further reduce the cost of the
  Python-delegated path, meaning even `cx install` benefits.
- cx is not a conda rewrite. It reuses the real conda binary for all package
  management logic. The Rust layer handles only dispatch, bootstrap, and shell
  integration.

The cx pattern is particularly interesting for `conda activate`. Today, that is
a shell function that invokes Python to generate shell variable assignments.
Even with Track A (85 ms), this is still 7× slower than cx's Rust-based
`cx shell` (subshell activation via conda-spawn, ~5 ms).

If cx were adopted as the recommended entry point, users would experience <5 ms
for bootstrap, help, shell, and version, while the Python-side improvements
from Track A and Track C would make `cx install` and `cx create` 2–4× faster
than their current equivalents.

### S2. Rewrite pluggy hot path in Rust (via PyO3)

The plugin system (pluggy) accounts for 239 ms and 429 modules during
`context.plugin_manager` initialization. While Track A's
[A6](startup.md#a6-skip-plugin-hooks-for-shell-activate-path)
sidesteps this for activate, every other command still pays the full cost.

One option: replace pluggy's core dispatch loop with a Rust extension (via
PyO3/maturin) that:

- Parses entry points and loads plugin metadata from a cached index (avoiding
  the `importlib.metadata.distributions()` scan on every invocation)
- Defers actual Python module imports until a hook is *called*, not *registered*
- Caches the plugin registry in a memory-mapped file between invocations

This would not eliminate the 429-module cost when plugins are actually needed
(e.g., `conda install` needs the solver), but it would make the discovery phase
near-instantaneous. Estimated savings: 50–100 ms off every subshell command.

Complexity is high. It requires a custom pluggy backend or fork, and would need
upstream buy-in or a conda-specific plugin loader.

### S3. Ahead-of-time compilation with Cython or mypyc

Compiling conda's hot startup modules (`context.py`, `configuration.py`,
`conda_argparse.py`, `activate.py`) with Cython or mypyc could reduce
interpretation overhead. These modules are import-heavy but also contain
non-trivial class instantiation (e.g., `Context.__init__()`, `Parameter`
descriptors) that would benefit from native code.

Estimated impact: 10–20% speedup on the import + init phases (~15–30 ms).
Modest compared to the structural wins from Track A/C, but additive.
Complexity is medium — requires a build step and binary wheel distribution.
Type annotation coverage in conda is already reasonable, which helps mypyc.

### S4. Persistent daemon / socket activation

Instead of paying Python startup cost on every invocation, a background conda
daemon could maintain a warm Python process and accept commands over a Unix
socket. The CLI would become a thin client that connects to the daemon, sends
the command, and streams output.

This is the pattern used by Gradle (Gradle Daemon) and nix-daemon. It would
make even heavy commands feel instant, since Python init, module loading, and
plugin discovery happen once at daemon start.

Estimated impact: near-zero perceived startup for all commands (1–5 ms for
socket connect + command send). The daemon pays the full 370 ms once at first
command, then amortizes it over all subsequent invocations.

Complexity is very high. Requires lifecycle management (auto-start,
auto-shutdown, staleness detection), security considerations (socket
permissions), and compatibility with all platforms. Significantly changes the
operational model.

### S5. Frozen importlib metadata cache

`importlib.metadata.distributions()` scans all installed packages' `dist-info`
directories on every invocation to discover entry points. This is part of the
plugin discovery cost in `context.plugin_manager`.

A lightweight alternative: cache the entry-point scan results in a JSON or
msgpack file inside the conda prefix, invalidated by a hash of
`conda-meta/*.json` timestamps. On subsequent runs, skip the filesystem scan
and load the cached plugin registry directly.

Estimated impact: 20–40 ms off plugin discovery. Complementary to S2 but much
simpler — no Rust, no pluggy fork. Complexity is low to medium, mainly in
cache invalidation logic and a fallback to full scan when the cache is stale.

### S6. Profile-guided module ordering in site-packages

CPython's filesystem import machinery resolves module paths by scanning
`sys.path` entries in order. For conda environments with many packages in
`site-packages`, the path resolution overhead is non-trivial.

A speculative idea: reorder `site-packages` directories or use a pre-built
module-to-path index (similar to `zipimport`'s directory cache) so that
frequently imported modules are found on the first `sys.path` entry.

Estimated impact: 5–15 ms. Minor compared to other opportunities, but
zero-risk and automatable as a post-install step. Could be implemented as a
conda plugin that runs after `conda install`.

### S7. Lazy plugin loading with entry-point groups

Instead of loading all plugins at `plugin_manager` init, register plugin hooks
by entry-point *group* and only load plugins in the group that the current
command actually needs:

- `conda.plugins.solvers` — only for install/create/update
- `conda.plugins.virtual_packages` — only for solve operations
- `conda.plugins.subcommands` — only when an unknown subcommand is invoked
- `conda.plugins.pre_commands` — only when pre-command hooks exist

This is a more surgical version of Track A's A6 that works for all commands,
not just activate. It requires refactoring the plugin hook specifications but
does not need any new Python features.

Estimated impact: 100–200 ms saved for commands that don't need the solver
(env list, config, list, info). Would bring these commands into the 150–250 ms
range on stock Python 3.13. Complexity is medium — requires changes to
`conda.plugins.hookspec` and the plugin manager, plus coordination with
external plugin authors.

<div align="right"><a href="#contents">↑ Contents</a></div>

---

## Changelog

| Date | Change |
|---|---|
| 2026-04-23 | **Migrated to [conda-tempo](https://github.com/jezdez/conda-tempo) repo.** Source-of-truth moved from gist `24c0e8cf4b8e740b1c50c64ff03ba46d` to `jezdez/conda-tempo/future.md`. Cross-links to Track A and Track B are now relative repo paths. |
| 2026-04-23 | Split off from the [Track A (startup.md)](startup.md) as part of the three-track reorganization: Track A (startup, shipping) stays in the original gist, Track B (transaction pipeline) gets its own gist, Track C (this gist) collects PEP 810 lazy imports and speculative research. Former Track B headings renumbered to Track C throughout. PR IDs B1–B3 from the old single-gist roadmap renumbered to C1–C3. Cross-references to Track A's A6, A7, A22 sections now point at the Track A gist by absolute URL. No measurement changes. |
| 2026-04-03 | Original research: CPython optimization landscape, PEP 810 evaluation, Python distribution build comparison, custom 3.15 builds (no-pgo / standard-pgo / startup-pgo / startup-pgo + frozen), full Track A+B conda prototype, speculative opportunities S1–S7. Published as part of the combined "Reducing conda Startup Latency" gist. |

---

<details>
<summary>Appendix A: CPython build configuration</summary>

## Appendix A: CPython build configuration

All builds used CPython 3.15.0a7 on macOS ARM64 (Apple M-series) with:

```
--with-lto=full
--with-computed-gotos
--with-tail-call-interp
--enable-optimizations  (PGO variants)
-O3
```

The startup-focused PGO profile replaces the standard `-m test --pgo` with a
custom script that exercises import machinery, importlib.metadata, argparse,
JSON, pathlib, os/sys, codecs, configparser, collections, and re — the actual
hot paths of a CLI tool startup.

</details>

<details>
<summary>Appendix B: Python distribution startup benchmarks</summary>

## Appendix B: Python distribution startup benchmarks

Benchmark environment: macOS 15 (Darwin 25.3.0), Apple M-series ARM64.
Tool: `hyperfine -N --warmup 10 --runs 100`, working directory `/tmp`.

### Bare startup (`python -c pass`)

| Distribution | Mean | σ | Min | Max |
|---|---|---|---|---|
| conda-forge 3.13.12 (Clang 19.1.7) | 20.5 ms | 2.9 ms | 15.3 ms | 26.0 ms |
| PBS 3.13.12 (Clang 22.1.1) | 23.8 ms | 4.1 ms | 16.7 ms | 33.6 ms |
| Anaconda 3.13.12 (Clang 20.1.8) | 24.2 ms | 6.4 ms | 15.0 ms | 39.7 ms |
| Homebrew 3.14.2 (Apple Clang 17) | 42.2 ms | 14.2 ms | 26.8 ms | 119.5 ms |

### No-site startup (`python -S -c pass`)

| Distribution | Mean | σ | Min | Max |
|---|---|---|---|---|
| Anaconda 3.13.12 | 14.0 ms | 1.1 ms | 12.4 ms | 17.4 ms |
| conda-forge 3.13.12 | 14.4 ms | 1.8 ms | 12.1 ms | 21.3 ms |
| PBS 3.13.12 | 18.0 ms | 2.6 ms | 15.1 ms | 27.5 ms |

### Import json (`python -c 'import json'`)

| Distribution | Mean | σ | Min | Max |
|---|---|---|---|---|
| PBS 3.13.12 | 26.0 ms | 2.7 ms | 22.1 ms | 33.7 ms |
| Anaconda 3.13.12 | 30.9 ms | 10.2 ms | 21.5 ms | 80.7 ms |
| conda-forge 3.13.12 | 31.0 ms | 9.7 ms | 21.8 ms | 82.4 ms |

### Heavy stdlib imports (`import json, os, pathlib, re, logging, hashlib, typing`)

| Distribution | Mean | σ | Min | Max |
|---|---|---|---|---|
| conda-forge 3.13.12 | 43.9 ms | 3.6 ms | 35.9 ms | 54.1 ms |
| Anaconda 3.13.12 | 44.0 ms | 11.1 ms | 35.0 ms | 92.4 ms |
| PBS 3.13.12 | 48.5 ms | 12.2 ms | 33.3 ms | 82.9 ms |

### Build configuration comparison

| Config var | PBS | Anaconda | conda-forge |
|---|---|---|---|
| `CC` | cc (Clang 22.1.1) | clang (20.1.8) | clang (19.1.7) |
| `OPT` | `-DNDEBUG -g -O3 -Wall` | `-DNDEBUG -O2 -Wall` | `-DNDEBUG -O2 -Wall` |
| `CONFIG_ARGS` (LTO) | `--with-lto` | `--with-lto=full` | `--with-lto=full` |
| PGO | Yes | Yes | Yes |
| Frozen modules loaded | 14 | 14 | 14 |
| Modules at startup | 34 | 37 | 35 |
| `sys.path` entries | 5 | 5 | 5 |

</details>

---

[^1]: The 3.15 test environment did not have heavy plugins installed (`conda_anaconda_tos` + `rich` + `pydantic`, `conda_libmamba_solver` + `libmambapy`). These would add to the measured times unless their imports are also made lazy.
[^2]: A1 alone shows minimal end-to-end savings for subshell commands. While A1 eliminates 120 modules (57 ms) from the context init phase, `requests` is re-loaded shortly after via `context.plugin_manager` → plugin entry points → `notices/fetch.py` → `import requests`. The full benefit requires combining A1 with A2/A3 (deferred plugin discovery), which prevents the plugin-triggered reload for commands that don't need the solver.
[^3]: Both feedstock recipes apply `sed 's/-O2/-O3/g'` to the Makefile during build, but sysconfig records the original `-O2` from `configure`. The actual compiled code may use `-O3`. The distinction is moot in practice — both builds run PGO, which generates profile-guided optimization passes that dominate over the `-O2`/`-O3` difference.
[^4]: The seven stdlib imports tested: `json`, `os`, `pathlib`, `re`, `logging`, `hashlib`, `typing` — representative of conda's startup import set.
