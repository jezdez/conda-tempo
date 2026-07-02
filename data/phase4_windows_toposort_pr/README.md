# Windows W3 topological-sort PR confirmation

Date: 2026-07-02

Purpose: confirm the production conda PR implementation for the Windows W3
topological-sort follow-up in the branch combination where the bottleneck is
visible.

The generated `w3_strategy/realistic.json` run used:

- `conda/conda:jezdez/track-b-prefix-graph-toposort` at `ea7a30aa2`
  (pre-restack PR branch containing B2 plus the B21 production commit)
- `conda-libmamba-solver:track-b-b11-cache-installed` at `c7977ae`
- `conda-package-handling:track-b-b13-reuse-zipfile` at `c3b0afe`
- `conda-package-streaming:track-b-b20-safety-fast-path` at `ec20ed7`

The combined W3 strategy command was:

```powershell
pixi run python bench/run_windows.py w3-strategy `
  --phase phase4_windows_toposort_pr `
  --w3-records 5000 10000 50000 `
  --timeout 900
```

This branch implements the pure-Python child-adjacency topological-sort
strategy from the prototype, with the additional production requirement that
the working graph is still mutated as nodes are yielded so cycle recovery sees
the same remaining graph shape.

After this confirmation run, PR #16331 was restacked onto `origin/main` as a
standalone B21 PR (`cf8894ae3`) because the implementation does not depend on
B2. The timing artifact remains a combined B2+B11+B21 measurement, since the
Windows W3 end-to-end bottleneck is exposed only after B2 removes the larger
graph-construction cost.
