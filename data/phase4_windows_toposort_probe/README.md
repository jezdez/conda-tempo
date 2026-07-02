# Windows W3 topological-sort probe

Date: 2026-07-02

Purpose: isolate the remaining Windows W3 realistic-prefix cost after aligning
the conda checkout to B2 and conda-libmamba-solver to B11.

The generated `w3_strategy/realistic.json` run used:

- `conda/conda:jezdez/track-b-b2-prefix-graph-by-name` at `d0aecb759`
- `conda-libmamba-solver:track-b-b11-cache-installed` at `c7977ae`
- `conda-package-handling:track-b-b13-reuse-zipfile` at `c3b0afe`
- `conda-package-streaming:track-b-b20-safety-fast-path` at `ec20ed7`
- the local `PrefixGraph._toposort_raise_on_cycles` prototype captured in
  `prefix_graph_toposort_prototype.patch`

The combined W3 strategy command was:

```powershell
pixi run python bench/run_windows.py w3-strategy `
  --phase phase4_windows_toposort_probe `
  --w3-records 5000 10000 50000 `
  --timeout 900
```

The prototype is a measurement artifact, not a production-ready conda patch.
Before promoting it to a PR, the cycle-handling path needs tests that confirm
`PrefixGraph._topo_sort_handle_cycles()` sees the same remaining graph shape
after acyclic nodes are removed.
