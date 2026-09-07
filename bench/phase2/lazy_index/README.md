# Lazy-index solver preparation

This harness accompanies [conda-build #6125](https://github.com/conda/conda-build/issues/6125)
and [conda-libmamba-solver #1044](https://github.com/conda/conda-libmamba-solver/pull/1044).
Recorded measurements are in
[`data/phase2/lazy_index/2026-09-07`](../../../data/phase2/lazy_index/2026-09-07/).

It loads each conda-libmamba-solver revision from an existing checkout's Git
objects without changing that checkout. It generates deterministic local
repodata in a separate process, then measures the exact detection and channel
discovery methods with a real conda `Index`. Assertions check equal output
channels, removal of the platform attribute, and whether records were realized.

The original runs used Python 3.14.7, conda 26.7.1, conda-build 26.7.1,
libmambapy 2.9.0, and Memray 1.20.0 on macOS arm64. The RSS measurement uses
macOS `ru_maxrss` byte units. These scripts are not a portable RSS benchmark
for other operating systems.

Use a disposable output directory and a Python environment with those packages:

```bash
export CONDA_LIBMAMBA_SOLVER_REPO=/path/to/conda-libmamba-solver
export LAZY_INDEX_OUTPUT_DIR="$(mktemp -d)"

git -C "$CONDA_LIBMAMBA_SOLVER_REPO" fetch origin main pull/1044/head

python bench/phase2/lazy_index/run_suite.py \
  --counts 10000 100000 1000000 --trials 3 --profile-trials 3 \
  --modes both --result results.json

python bench/phase2/lazy_index/run_suite.py \
  --counts 100000 --trials 3 --profile-trials 3 \
  --modes both --preload --result control.json
```

The two immutable revisions are recorded in `benchmark.py`. Both Git objects
must remain available in the checkout when rerunning the historical comparison.
The million-record fixture contains about 405 MB of JSON and the original
method calls require several GiB of process memory. The output directory also
holds Memray captures and diagnostic logs.

Memray captures system allocator requests during the method calls with normal
Python allocator behavior. Native stack capture and Python allocator tracing
are disabled. The controller checks `metadata.peak_memory` against the sum of
high-watermark allocation records. RSS and elapsed time are measured in separate
unprofiled processes. Each revision runs three times, alternating order.

The cold-index comparison includes unnecessary repodata parsing, record
construction, and roughly 23 MB of first-use encoding-detection model allocation
in the local file transport. These are synthetic local-channel measurements,
not conda-forge or complete conda-build measurements. The preloaded control
shows what remains after the index has already been realized upstream.

The published scripts replace the original machine-specific input and output
paths with environment variables. The measured operations are unchanged.
Individual measurements contain only numeric results, source revisions,
generated capture names, and allocation function names. Raw captures and logs
remain local because they can contain runtime filenames.
