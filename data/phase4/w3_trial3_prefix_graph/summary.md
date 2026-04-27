# Trial 3: PrefixGraph-only rattler fast path, end-to-end measurement

Experiment branch: `conda/conda:jezdez/experiment-prefix-graph-rattler`
(stacked on B2).

## Microbench (S18 fixture, synthetic DAG, `PrefixGraph(records).graph`)

| N | post-B2 pure Python | Trial 3 (rattler inside PrefixGraph) | speedup |
|---:|---:|---:|---:|
| 100 | 2.71 ms | 1.47 ms | **1.8×** |
| 1 000 | 68.5 ms | 16.4 ms | **4.2×** |
| 5 000 | 1.28 s | 98.6 ms | **13×** |

## End-to-end W3 (5 hyperfine runs, alternating A/B, same session)

| Workload | Baseline (stack) | Trial 3 | Δ |
|---|---:|---:|---:|
| W3 @ 5 000, iter 1 | 2.18 s ± 0.12 | 2.35 s ± 0.04 | +0.17 s |
| W3 @ 5 000, iter 2 | 2.12 s ± 0.05 | 2.32 s ± 0.02 | +0.20 s |
| W3 @ 50 000, iter 1 | 14.94 s ± 0.56 | 15.55 s ± 0.45 | +0.62 s |
| W3 @ 50 000, iter 2 | 12.68 s ± 1.59 | 14.59 s ± 2.64 | +1.90 s (noisy) |
| W3 @ 50 000, iter 3 | 11.54 s ± 0.32 | 12.48 s ± 0.14 | +0.94 s |

## CPU user time (more stable signal than wall time)

| Workload | Baseline | Trial 3 | Δ |
|---|---:|---:|---:|
| W3 @ 5 000 | ~1.40 s | ~1.59 s | +0.19 s |
| W3 @ 50 000 | ~8.55 s | ~9.53 s | +0.98 s |

## Why the disconnect

PrefixGraph's microbench win (13× at N=5 000) does not translate to W3
because W3's `conda install --dry-run --no-deps tzdata` path hits
PrefixGraph only twice per invocation and processes only a fraction
of the prefix records per call. cProfile attributes ~1 s of the W3@50k
run to PrefixGraph-related work total, which is the upper bound on
what any PrefixGraph optimisation can save on this workload.

Trial 3 pays per-record conversion cost (~260 ms per 50k records),
plus rattler-side toposort and match work. On workloads where
PrefixGraph is actually seconds of wall time (large realistic prefixes
with dense deps, e.g. `conda update --all` or `conda remove` against
a research env), the speedup would likely translate. W3 doesn't
exercise that case, so the measurement setup we have here can't
confirm the end-to-end win from this seam.
