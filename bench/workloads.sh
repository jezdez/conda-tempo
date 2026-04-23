#!/usr/bin/env bash
# Track B Phase 1 measurement workloads.
#
# Runs hyperfine on three fixed conda workloads and writes JSON + metadata
# to ../data/phase1/<workload>/. See bench/README.md for prereqs.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$REPO_ROOT/data/phase1"
mkdir -p "$OUT"

HYPERFINE="${HYPERFINE:-hyperfine}"
command -v "$HYPERFINE" >/dev/null || {
    echo "error: hyperfine not found on PATH (override with HYPERFINE=...)" >&2
    exit 1
}
command -v conda >/dev/null || {
    echo "error: conda not found on PATH" >&2
    exit 1
}

CONDA_VERSION=$(conda --version | awk '{print $2}')
DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)

run_w1() {
    mkdir -p "$OUT/w1"
    echo "W1: fresh install, small (~15 pkgs)"
    "$HYPERFINE" \
        --warmup 1 --runs 5 \
        --prepare 'conda env remove -y -n bench_w1 >/dev/null 2>&1 || true' \
        --cleanup 'conda env remove -y -n bench_w1 >/dev/null 2>&1 || true' \
        --export-json "$OUT/w1/hyperfine.json" \
        --export-markdown "$OUT/w1/hyperfine.md" \
        'conda create -n bench_w1 -c conda-forge -y python=3.13 requests'
    printf '{"conda_version":"%s","date":"%s"}\n' "$CONDA_VERSION" "$DATE" > "$OUT/w1/run.json"
}

run_w2() {
    mkdir -p "$OUT/w2"
    echo "W2: fresh install, data-science (~150 pkgs, noarch: python heavy)"
    "$HYPERFINE" \
        --warmup 1 --runs 5 \
        --prepare 'conda env remove -y -n bench_w2 >/dev/null 2>&1 || true' \
        --cleanup 'conda env remove -y -n bench_w2 >/dev/null 2>&1 || true' \
        --export-json "$OUT/w2/hyperfine.json" \
        --export-markdown "$OUT/w2/hyperfine.md" \
        'conda create -n bench_w2 -c conda-forge -y python=3.13 pandas scikit-learn matplotlib jupyter'
    printf '{"conda_version":"%s","date":"%s"}\n' "$CONDA_VERSION" "$DATE" > "$OUT/w2/run.json"
}

run_w3() {
    mkdir -p "$OUT/w3"
    echo "W3: synthetic-prefix install, --dry-run --no-deps against bench_big (5k synthetic records)"
    if ! conda env list | awk '{print $1}' | grep -qx bench_big; then
        echo "error: prefix 'bench_big' missing. Seed it first:" >&2
        echo "       python bench/seed_big_prefix.py --name bench_big --records 5000" >&2
        exit 2
    fi
    # --no-deps + single noarch package keeps the solve bounded. The heavy
    # lifting is in the post-solve diff/graph code traversing the 5k
    # synthetic records in conda-meta (S1, S2). Larger seeds confirm the
    # O(N^2) scaling (see Phase-0 finding in track-b-transaction.md
    # changelog) but are too slow for a 5-run hyperfine.
    "$HYPERFINE" \
        --warmup 1 --runs 5 \
        --export-json "$OUT/w3/hyperfine.json" \
        --export-markdown "$OUT/w3/hyperfine.md" \
        'conda install -n bench_big -c conda-forge -y --dry-run --no-deps tzdata'
    printf '{"conda_version":"%s","date":"%s"}\n' "$CONDA_VERSION" "$DATE" > "$OUT/w3/run.json"
}

run_w4() {
    mkdir -p "$OUT/w4"
    # W4 = W2's data-science install but with a cold package cache, so
    # every run pays fetch + extract + verify + link instead of reusing
    # cached .conda archives. Primary cost centres we want to surface:
    # network I/O (proxy / SSL), .conda extraction (zstd + tarfile),
    # and per-file link+verify (S6/S7/S8 territory).
    #
    # The prepare hook wipes (1) the target env and (2) the package
    # cache directory. Without step 2 the benchmark just measures the
    # warm-cache case (= W2).
    #
    # Uses 3 runs instead of 5 because each run downloads ~200 MB
    # of .conda archives from conda-forge CDN; network-bound variance
    # dominates the small-samples noise anyway.
    local pkgs_dir
    pkgs_dir=$(conda config --show pkgs_dirs --json 2>/dev/null | python3 -c 'import sys, json; print(json.load(sys.stdin)["pkgs_dirs"][0])')
    echo "W4: cold-cache data-science install, wiping ${pkgs_dir} between runs"
    "$HYPERFINE" \
        --warmup 0 --runs 3 \
        --prepare "conda env remove -y -n bench_w4 >/dev/null 2>&1 || true; rm -rf \"${pkgs_dir}\"; mkdir -p \"${pkgs_dir}\"" \
        --cleanup 'conda env remove -y -n bench_w4 >/dev/null 2>&1 || true' \
        --export-json "$OUT/w4/hyperfine.json" \
        --export-markdown "$OUT/w4/hyperfine.md" \
        'conda create -n bench_w4 -c conda-forge -y python=3.13 pandas scikit-learn matplotlib jupyter'
    printf '{"conda_version":"%s","date":"%s","pkgs_dir":"%s"}\n' "$CONDA_VERSION" "$DATE" "$pkgs_dir" > "$OUT/w4/run.json"
}

case "${1:-all}" in
    w1) run_w1 ;;
    w2) run_w2 ;;
    w3) run_w3 ;;
    w4) run_w4 ;;
    all) run_w1; run_w2; run_w3 ;;
    *)
        echo "Usage: $0 [w1|w2|w3|w4|all]" >&2
        exit 1
        ;;
esac
