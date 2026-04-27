#!/usr/bin/env bash
# macOS full benchmark driver — mirrors docker/run_linux.sh for Linux.
#
# Runs every Phase-1 and Phase-2 benchmark against the activated conda
# devenv and writes to data/phase1/ and data/phase2/. Intended to be
# invoked after sourcing dev/start:
#
#     source <path-to>/conda/dev/start -p 3.13 -i miniforge
#     bench/run_all.sh all
#
# Modes:
#     phase1          hyperfine W1/W2/W3 (5 runs + 1 warmup each)
#     phase1-profile  cProfile + memray + time_recorder per workload
#     phase2-pyperf   pyperf full/fast sweep for S2/S6/S7/S9/S11
#     phase2-memray   one memray pass per suspect at representative N
#     all             all of the above, in order (~80 min)
#
# Scalene is intentionally not run here — the conda-forge scalene
# build for Python 3.13 on macOS 26 fails to load (arm64e ABI
# mismatch). See bench/README.md for the rationale and the Linux
# Docker runner that covers it.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_ROOT}"

command -v conda >/dev/null || { echo "error: conda not on PATH" >&2; exit 1; }
command -v hyperfine >/dev/null || { echo "error: hyperfine not on PATH" >&2; exit 1; }

MODE="${1:-all}"

log() { printf '\n=== %s: %s ===\n' "$1" "$(date -u +%H:%M:%S)"; }

cleanup_envs() {
    for e in bench_w1 bench_w2 bench_big; do
        conda env remove -y -n "$e" >/dev/null 2>&1 || true
    done
}

run_phase1_hyperfine() {
    log "Phase 1 hyperfine W1/W2/W3"
    cleanup_envs
    bench/workloads.sh w1
    bench/workloads.sh w2
    python bench/seed_big_prefix.py --name bench_big --records 5000
    bench/workloads.sh w3
}

run_phase1_profile() {
    log "Phase 1 profile (cProfile + memray + time_recorder)"

    # W1
    conda env remove -y -n bench_w1 >/dev/null 2>&1 || true
    python bench/run_cprofile.py w1 -- create -n bench_w1 -c conda-forge -y python=3.13 requests
    conda env remove -y -n bench_w1 >/dev/null 2>&1 || true
    python bench/run_memray.py w1 -- create -n bench_w1 -c conda-forge -y python=3.13 requests
    conda env remove -y -n bench_w1 >/dev/null 2>&1 || true
    python bench/parse_time_recorder.py w1 -- create -n bench_w1 -c conda-forge -y python=3.13 requests
    conda env remove -y -n bench_w1 >/dev/null 2>&1 || true

    # W2
    python bench/run_cprofile.py w2 -- create -n bench_w2 -c conda-forge -y python=3.13 pandas scikit-learn matplotlib jupyter
    conda env remove -y -n bench_w2 >/dev/null 2>&1 || true
    python bench/run_memray.py w2 -- create -n bench_w2 -c conda-forge -y python=3.13 pandas scikit-learn matplotlib jupyter
    conda env remove -y -n bench_w2 >/dev/null 2>&1 || true
    python bench/parse_time_recorder.py w2 -- create -n bench_w2 -c conda-forge -y python=3.13 pandas scikit-learn matplotlib jupyter
    conda env remove -y -n bench_w2 >/dev/null 2>&1 || true

    # W3 — re-seed bench_big (it was destroyed by the W2 loop if the user
    # did a clean run, or by clean_envs at top; either way make sure).
    python bench/seed_big_prefix.py --name bench_big --records 5000
    python bench/run_cprofile.py w3 -- install -n bench_big -c conda-forge -y --dry-run --no-deps tzdata
    python bench/run_memray.py w3 -- install -n bench_big -c conda-forge -y --dry-run --no-deps tzdata
    python bench/parse_time_recorder.py w3 -- install -n bench_big -c conda-forge -y --dry-run --no-deps tzdata
}

run_phase2_pyperf() {
    log "Phase 2 pyperf sweeps"
    # S6: 0.7 ms/action → full mode cheap (1–2 min)
    python bench/phase2/run_pyperf.py s6_verify_individual --sizes 50 200 1000 --mode full
    # S7: full mode at M=5000 is ~14 min (four variants per size)
    python bench/phase2/run_pyperf.py s7_link_parallel --sizes 200 1000 5000 --mode full
    # S9: per_package is slow on devenv python (~40 min fast mode)
    python bench/phase2/run_pyperf.py s9_pyc_batching --sizes 10 30 60 --mode fast
    # S11: cheap
    python bench/phase2/run_pyperf.py s11_libmamba_installed --sizes 1000 5000 10000 --mode full
    # S2: N=1000 is 47 s/call — fast mode required (~30 min)
    python bench/phase2/run_pyperf.py s2_prefix_graph --sizes 100 500 1000 --mode fast
}

run_phase2_memray() {
    log "Phase 2 memray (representative N per suspect)"
    python bench/phase2/run_memray.py s6_verify_individual -n 1000
    python bench/phase2/run_memray.py s7_link_parallel -n 1000
    python bench/phase2/run_memray.py s9_pyc_batching -n 30
    python bench/phase2/run_memray.py s11_libmamba_installed -n 5000
    python bench/phase2/run_memray.py s2_prefix_graph -n 500
}

case "${MODE}" in
    phase1|phase1-hyperfine) run_phase1_hyperfine ;;
    phase1-profile)           run_phase1_profile ;;
    phase2-pyperf)            run_phase2_pyperf ;;
    phase2-memray)            run_phase2_memray ;;
    all)
        run_phase1_hyperfine
        run_phase1_profile
        run_phase2_pyperf
        run_phase2_memray
        ;;
    *)
        echo "Usage: $0 [phase1|phase1-profile|phase2-pyperf|phase2-memray|all]" >&2
        exit 1
        ;;
esac

cleanup_envs
log "DONE ${MODE}"
echo "Results in data/phase1/ and data/phase2/"
