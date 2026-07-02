# Windows W3 topological-sort PR confirmation

Date: 2026-07-02

Purpose: confirm the production conda PR branch for the Windows W3
topological-sort follow-up after the draft PR was opened.

The generated `w3_strategy/realistic.json` run used:

- `conda/conda:jezdez/track-b-prefix-graph-toposort` at `ea7a30aa2`
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
