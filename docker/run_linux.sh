#!/usr/bin/env bash
# Drives the Phase-1 + Phase-2 benchmark suite inside the Linux
# container. Called by docker run (see ../docker/Dockerfile).
#
# Usage: run_linux.sh [phase1|phase1-profile|phase2|scalene|all|shell]
set -euo pipefail

MODE="${1:-all}"

echo "=== Linux benchmark runner, mode=${MODE}, $(date -u +%H:%M:%S) ==="
uname -a; python --version; conda --version; hyperfine --version; echo

run_phase1_hyperfine() {
    echo "=== Phase 1: hyperfine W1/W2/W3 ==="
    mkdir -p "${TEMPO_DATA}/phase1/w1" "${TEMPO_DATA}/phase1/w2" "${TEMPO_DATA}/phase1/w3"
    cd "${TEMPO_WORK}"

    conda env remove -y -n bench_w1 >/dev/null 2>&1 || true
    hyperfine --warmup 1 --runs 5 \
        --prepare 'conda env remove -y -n bench_w1 >/dev/null 2>&1 || true' \
        --cleanup 'conda env remove -y -n bench_w1 >/dev/null 2>&1 || true' \
        --export-json "${TEMPO_DATA}/phase1/w1/hyperfine.json" \
        --export-markdown "${TEMPO_DATA}/phase1/w1/hyperfine.md" \
        'conda create -n bench_w1 -c conda-forge -y python=3.13 requests'

    conda env remove -y -n bench_w2 >/dev/null 2>&1 || true
    hyperfine --warmup 1 --runs 5 \
        --prepare 'conda env remove -y -n bench_w2 >/dev/null 2>&1 || true' \
        --cleanup 'conda env remove -y -n bench_w2 >/dev/null 2>&1 || true' \
        --export-json "${TEMPO_DATA}/phase1/w2/hyperfine.json" \
        --export-markdown "${TEMPO_DATA}/phase1/w2/hyperfine.md" \
        'conda create -n bench_w2 -c conda-forge -y python=3.13 pandas scikit-learn matplotlib jupyter'

    python "${TEMPO_WORK}/bench/seed_big_prefix.py" --name bench_big --records 5000
    hyperfine --warmup 1 --runs 5 \
        --export-json "${TEMPO_DATA}/phase1/w3/hyperfine.json" \
        --export-markdown "${TEMPO_DATA}/phase1/w3/hyperfine.md" \
        'conda install -n bench_big -c conda-forge -y --dry-run --no-deps tzdata'
}

run_phase1_profile() {
    echo "=== Phase 1: cProfile + memray + time_recorder ==="
    cd "${TEMPO_WORK}"
    mkdir -p "${TEMPO_DATA}/phase1"/{w1,w2,w3}

    conda env remove -y -n bench_w1 >/dev/null 2>&1 || true
    python bench/run_cprofile.py w1 -- create -n bench_w1 -c conda-forge -y python=3.13 requests || true
    conda env remove -y -n bench_w1 >/dev/null 2>&1 || true
    python bench/run_memray.py w1 -- create -n bench_w1 -c conda-forge -y python=3.13 requests || true
    conda env remove -y -n bench_w1 >/dev/null 2>&1 || true
    python bench/parse_time_recorder.py w1 -- create -n bench_w1 -c conda-forge -y python=3.13 requests || true
    conda env remove -y -n bench_w1 >/dev/null 2>&1 || true

    python bench/run_cprofile.py w2 -- create -n bench_w2 -c conda-forge -y python=3.13 pandas scikit-learn matplotlib jupyter || true
    conda env remove -y -n bench_w2 >/dev/null 2>&1 || true
    python bench/run_memray.py w2 -- create -n bench_w2 -c conda-forge -y python=3.13 pandas scikit-learn matplotlib jupyter || true
    conda env remove -y -n bench_w2 >/dev/null 2>&1 || true
    python bench/parse_time_recorder.py w2 -- create -n bench_w2 -c conda-forge -y python=3.13 pandas scikit-learn matplotlib jupyter || true
    conda env remove -y -n bench_w2 >/dev/null 2>&1 || true

    python bench/seed_big_prefix.py --name bench_big --records 5000
    python bench/run_cprofile.py w3 -- install -n bench_big -c conda-forge -y --dry-run --no-deps tzdata || true
    python bench/run_memray.py w3 -- install -n bench_big -c conda-forge -y --dry-run --no-deps tzdata || true
    python bench/parse_time_recorder.py w3 -- install -n bench_big -c conda-forge -y --dry-run --no-deps tzdata || true
}

run_phase2_subset() {
    echo "=== Phase 2: S6 + S7 + S9 + S11 ==="
    cd "${TEMPO_WORK}"
    python bench/phase2/run_pyperf.py s6_verify_individual --sizes 50 200 1000 --mode full
    python bench/phase2/run_memray.py s6_verify_individual -n 1000
    python bench/phase2/run_pyperf.py s7_link_parallel --sizes 200 1000 5000 --mode full
    python bench/phase2/run_memray.py s7_link_parallel -n 1000
    python bench/phase2/run_pyperf.py s9_pyc_batching --sizes 10 30 60 --mode fast
    python bench/phase2/run_memray.py s9_pyc_batching -n 30
    python bench/phase2/run_pyperf.py s11_libmamba_installed --sizes 1000 5000 --mode fast
    python bench/phase2/run_memray.py s11_libmamba_installed -n 5000
}

run_scalene() {
    echo "=== Scalene: Phase-1 W1/W2/W3 + Phase-2 S11/S6 ==="
    cd "${TEMPO_WORK}"
    python bench/run_scalene.py w1 -- create -n bench_w1 -c conda-forge -y python=3.13 requests || true
    conda env remove -y -n bench_w1 >/dev/null 2>&1 || true
    python bench/run_scalene.py w2 -- create -n bench_w2 -c conda-forge -y python=3.13 pandas scikit-learn matplotlib jupyter || true
    conda env remove -y -n bench_w2 >/dev/null 2>&1 || true
    python bench/run_scalene.py w3 -- install -n bench_big -c conda-forge -y --dry-run --no-deps tzdata || true
    python bench/phase2/run_scalene.py s11_libmamba_installed -n 5000 || true
    python bench/phase2/run_scalene.py s6_verify_individual -n 200 || true
}

case "${MODE}" in
    phase1|phase1-hyperfine) run_phase1_hyperfine ;;
    phase1-profile)          run_phase1_profile ;;
    phase2)                  run_phase2_subset ;;
    scalene)                 run_scalene ;;
    all)
        run_phase1_hyperfine
        run_phase1_profile
        run_phase2_subset
        run_scalene
        ;;
    shell) exec bash -l ;;
    *)
        echo "Usage: $0 [phase1|phase1-profile|phase2|scalene|all|shell]" >&2
        exit 1
        ;;
esac

echo "=== DONE ${MODE}: $(date -u +%H:%M:%S) ==="
echo "Results in: ${TEMPO_DATA}"
