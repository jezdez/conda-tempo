# bench/

Measurement harness for the [Track B transaction-latency research](../track-b-transaction.md).

## What's here

| File | Purpose |
|---|---|
| `workloads.sh` | hyperfine driver for W1 (small install), W2 (data-science install), W3-dryrun (large-prefix solve+diff). Writes JSON to `../data/phase1/<workload>/`. |
| `profile.py` | cProfile wrapper around a single workload invocation. Writes `.prof` binaries and a top-20 text digest. |
| `seed_big_prefix.py` | Builds a synthetic prefix with N fake `conda-meta/*.json` records, plus a matching `history` file. Used to populate `bench_big` for W3 and future W4-full. |
| `parse_time_recorder.py` | Extracts per-phase timings from conda's `time_recorder` decorator output. |

## Prereqs

- `conda` on PATH (any supported version; the tempo plan targets `main`)
- `hyperfine` installed (`brew install hyperfine` or equivalent)
- A separate conda prefix for `bench_big` (W3), created with
  `python bench/seed_big_prefix.py --name bench_big --records 50000`
- First run of W1 and W2 needs network to populate the package cache.
  Subsequent runs reuse the cache.

## Run everything

```
bench/workloads.sh all
```

Or individually:

```
bench/workloads.sh w1        # small fresh install (~15 pkgs)
bench/workloads.sh w2        # data-science fresh install (~150 pkgs)
bench/workloads.sh w3        # large-prefix solve + diff (dry-run against bench_big)
```

Output goes to `../data/phase1/<workload>/`:

- `hyperfine.json` — raw timing data (5 runs after 1 warmup)
- `cprofile.prof` + `cprofile.top20.txt` — from `profile.py` (invoke separately)
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
