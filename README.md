# conda-tempo

Measuring and reducing conda's tempo.

Three tracks of performance research on conda, each in its own document:

| Track | Document | What | Status |
|---|---|---|---|
| A | [startup.md](startup.md) | Startup latency on Python 3.10+: imports, plugin discovery, context init. Ships now. | 16 of 25 PRs merged |
| B | [transaction.md](transaction.md) | Transaction pipeline: solve → fetch → verify → link → history. Post-solver machinery, cross-platform. | Planning, measurement harness pending |
| C | [future.md](future.md) | Python 3.15 PEP 810 lazy imports, CPython build research, speculative opportunities (Rust bootstrapper, daemon, AOT, plugin-group refactor). | Research — not actionable until 3.15 feedstock lands |

## What "tempo" means

conda does a lot of work before, during, and after a user-visible command runs.
Each of the three documents takes one slice of that work, measures it, identifies
a short list of suspects, and fixes what the measurements justify. The pattern is
the same across tracks:

1. Measure a fixed workload with a fixed harness (hyperfine, cProfile,
   `time_recorder`, CodSpeed, pytest-benchmark).
2. Identify the top suspects. Write microbenchmarks that isolate each one.
3. Only build proof-of-concept fixes for suspects the microbenchmarks confirm.
4. One PR per fix, <100 LOC where possible, news entry, before/after numbers.
5. Re-measure the full stack at the end and publish a stacked estimate.

The [scope rules](startup.md#scope-rules) and [out-of-scope list](startup.md#out-of-scope)
in Track A apply to all three tracks.

## Related work

- [conda-express](https://github.com/jezdez/conda-express) — Rust front-end that
  handles bootstrap and subshell activation in ~5 ms. Complements Tempo by
  eliminating Python startup for the commands that don't need it.
- [conda-presto](https://github.com/jezdez/conda-presto) — TODO, link when public.
- [conda/conda#15867](https://github.com/conda/conda/issues/15867) — Track A
  tracking ticket with the per-PR checklist.

## History

This work started as a single gist ("Reducing conda startup latency") on
2026-04-03, was split into three tracks on 2026-04-23, and migrated to this repo
the same day. The three original gists (linked in each document's changelog) are
frozen and carry a notice pointing here.
