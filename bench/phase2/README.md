# bench/phase2/

Phase 2 microbenchmarks for the [Track B transaction-latency research](../../track-b-transaction.md).

Phase 1 measured end-to-end wall time, per-phase timings, and CPU/memory
profiles for three realistic conda workloads. Phase 2 isolates each
individual suspect (S1, S2, S6, S11, …) in a small fixture and asks two
questions:

1. **Does the suspect reproduce in isolation?** A 10-line benchmark that
   builds the smallest fixture that exercises the code path. If the
   scaling isn't visible here, the original CPU cost came from
   somewhere else.
2. **What's the before/after number for a Phase-3 PoC?** Same fixture,
   same tool, run against the stock code and against a candidate fix.
   `pyperf compare_to` reports significance.

## Layout

| File | Purpose |
|---|---|
| `fixtures.py` | Shared fixture builder: `synthetic_prefix(n, *, tmpdir)` — creates an on-disk prefix with N synthetic `conda-meta/*.json` records. Wraps the Phase-1 seed script. |
| `bench_<suspect_id>.py` | One microbenchmark per suspect. Self-contained pyperf script; also exposes `register_memray(n)` for the memray harness. |
| `run_pyperf.py` | Sweep orchestrator: invokes `bench_<id>.py` once per prefix size, writes `data/phase2/<id>/pyperf_n<N>.json` per size. |
| `run_memray.py` | Memray harness: runs the suspect's `register_memray(n)` under `memray.Tracker`, writes `.bin` / `.summary.txt` / `.meta.json` / `.flamegraph.html` per N. |

## Why not pytest-benchmark?

Track A uses pytest-benchmark in the conda repo itself because it
integrates with CodSpeed CI. Phase 2 here is investigation — we need
subprocess-per-sample isolation (to avoid fixture caches from pytest's
same-interpreter model leaking between suspects), automatic loop
calibration, and `pyperf compare_to` for PoC before/after. When a
Phase-3 PoC lands in conda, it still ships a pytest-benchmark test for
CI regression prevention; the two tools answer different questions.

## Adding a new suspect benchmark

Copy an existing `bench_s*.py` and replace the hot path. The file must:

- Expose `main() -> int` that builds a `pyperf.Runner` and calls
  `runner.bench_func(...)`. Use an `add_cmdline_args` callback to
  forward `-N <records>` to worker subprocesses (custom args added to
  `runner.argparser` are parsed in the master but *not* automatically
  re-injected into workers — this is the non-obvious pyperf gotcha).
- Expose `register_memray(n: int) -> None` that runs the same hot path
  a representative number of times (100 is a good default).
- Accept `-N / --records` (not `-n`; pyperf already uses `-n` for
  `--loops`).
- Keep the fixture import off the hot path (`import` inside the setup
  helper, not at module top).

## Running

```
# Full pyperf sweep for one suspect, committed data:
python bench/phase2/run_pyperf.py s11_libmamba_installed \
    --sizes 1000 5000 10000

# Quick smoke during development:
python bench/phase2/run_pyperf.py s11_libmamba_installed \
    --sizes 1000 --mode fast

# memray at a single N:
python bench/phase2/run_memray.py s11_libmamba_installed -n 5000

# Ad-hoc pyperf commands against the committed data:
pyperf stats      data/phase2/s11_libmamba_installed/pyperf_n5000.json
pyperf hist       data/phase2/s11_libmamba_installed/pyperf_n5000.json
pyperf compare_to before.json after.json
```

## Stability

On macOS `pyperf system tune` is a no-op. Close VS Code, Slack, and
anything else that allocates before running the full mode. Expect
`~1–3 %` standard deviation at N=5000; wider bands at N=10000 because
each sample takes longer and fewer fit in pyperf's budget.

On Linux CI boxes run `sudo pyperf system tune` before and
`sudo pyperf system reset` after. That locks CPU frequency, disables
turbo, and pins the scaling governor, typically cutting stddev by
3–5×.
