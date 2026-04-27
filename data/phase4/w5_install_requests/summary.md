# W5: `conda install <real-pkg>` against bench_big

End-to-end companion to the S19 microbench. Exercises the same
`diff_for_unlink_link_precs` hot path but through the full conda CLI
(solver + diff + dry-run plan), against the realistic bench_big
fixture (exponential fan-out, version-constrained deps).

Command: `conda install -n bench_big -c conda-forge -y --dry-run requests`

`requests` has ~15 transitive deps on conda-forge, none of which exist
in the bench_big synthetic prefix. The solver plans to install all of
them, then `diff_for_unlink_link_precs` runs with
`previous_records = PrefixGraph(prefix_records)` on the full
bench_big (the heavy call) plus the final-precs diff logic. This is
the realistic path users hit when installing anything into a long-lived
research environment.

## A/B: pure Python B2 stack vs Trial 3 (rattler in PrefixGraph)

| N (prefix size) | Pure Python (B2) | Trial 3 | Δ | speedup |
|---:|---:|---:|---:|---:|
| 1 000 | 1.75 s ± 0.03 | 1.90 s ± 0.01 | +0.15 s | 0.92× |
| 5 000 | 4.71 s ± 0.01 | 2.95 s ± 0.01 | −1.76 s | **1.6×** |
| 10 000 | 12.60 s ± 0.07 | 4.11 s ± 0.08 | −8.49 s | **3.1×** |
| 50 000 | **628.8 s (10.5 min)** | **21.3 s** | −607.5 s | **29.5×** |

## Interpretation

At N=1 000 the per-call rattler conversion overhead narrowly dominates
the PrefixGraph savings (~0.15 s regression). Crossover happens
between 1 000 and 5 000, above which Trial 3 wins.

At N=50 000 `conda install requests` takes 10.5 minutes on the pure
Python stack vs 21 seconds with Trial 3. This is the workload users
hit when they run `conda install <anything>` on a long-lived research
prefix, and the 10-minute cost is exactly the pain-point that motivates
the whole track.

## Why this shows up when W3 didn't

W3 uses `conda install --dry-run --no-deps tzdata` which avoids dep
resolution, so the solver exits essentially immediately and
`diff_for_unlink_link_precs` gets called against a final_precs of
length 1 (just tzdata). The PrefixGraph call in the diff is trivial.

W5 forces real dep resolution by removing `--no-deps`. The solver
has to walk conda-forge's metadata, build a plan with ~15 new
packages, then call `diff_for_unlink_link_precs` with the full
bench_big prefix + 15 new records. That full-prefix PrefixGraph call
is the bottleneck, and it's the exact thing S19 isolated and Trial 3
accelerates.
