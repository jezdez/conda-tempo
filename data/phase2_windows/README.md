# Windows Phase 2 Smoke Data

These `pyperf_*.json` files were produced with `--mode fast` while
validating the native Windows harness on 2026-07-01. They confirm that
the Windows runner, fixture setup, S8 package-cache handoff, and S10
benchmark execute on win-64.

Do not treat this directory as full-budget Phase 2 data until
`pixi run windows-phase2` has been rerun without `--phase2-mode fast`.
