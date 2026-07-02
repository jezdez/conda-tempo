# Windows Phase 2 Data

These `pyperf_*.json` files were produced with full pyperf defaults on
2026-07-01 via:

```powershell
pixi run windows-phase2
```

The run completed S6, S7, S8, S9, S10, and S11 on the dedicated
win-64/x86_64 host described in `data/machine_windows.json`. S8 is the
runtime outlier: `pyperf_n5.json` took 2 h 41 min because it benchmarks
serial extraction plus five thread-pool sizes with 60 values each.
