# Lazy-index measurements from 2026-09-07

These measurements support [conda-build #6125](https://github.com/conda/conda-build/issues/6125)
and the [Track B lazy-index follow-up](../../../../track-b-transaction.md#b31-preserve-lazy-indexes-during-conda-build-solves).

- `results.json` contains three profiled and three unprofiled runs per revision
  for each cold-index size.
- `control.json` repeats the comparison with 100,000 records already realized
  before the measured calls.
- `summary.json` and `control-summary.json` contain medians and RSS ranges.
- `input-manifests.json` records each generated repodata file's package count,
  byte size, and SHA-256 digest.

The [harness and reproduction instructions](../../../../bench/phase2/lazy_index/README.md)
describe the setup and measurement limits. Every large channel is accompanied
by an output channel containing one additional package. The raw Memray capture
files are not included because they can contain local runtime filenames.
