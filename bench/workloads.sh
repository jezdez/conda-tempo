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
        'conda create -n bench_w1 -y python=3.13 requests'
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
        'conda create -n bench_w2 -y python=3.13 pandas scikit-learn matplotlib jupyter'
    printf '{"conda_version":"%s","date":"%s"}\n' "$CONDA_VERSION" "$DATE" > "$OUT/w2/run.json"
}

run_w3() {
    mkdir -p "$OUT/w3"
    echo "W3: large-prefix update, dry-run against bench_big"
    if ! conda env list | awk '{print $1}' | grep -qx bench_big; then
        echo "error: prefix 'bench_big' missing. Seed it first:" >&2
        echo "       python bench/seed_big_prefix.py --name bench_big --records 50000" >&2
        exit 2
    fi
    "$HYPERFINE" \
        --warmup 1 --runs 3 \
        --export-json "$OUT/w3/hyperfine.json" \
        --export-markdown "$OUT/w3/hyperfine.md" \
        'conda update -n bench_big -y --all --dry-run'
    printf '{"conda_version":"%s","date":"%s"}\n' "$CONDA_VERSION" "$DATE" > "$OUT/w3/run.json"
}

case "${1:-all}" in
    w1) run_w1 ;;
    w2) run_w2 ;;
    w3) run_w3 ;;
    all) run_w1; run_w2; run_w3 ;;
    *)
        echo "Usage: $0 [w1|w2|w3|all]" >&2
        exit 1
        ;;
esac
