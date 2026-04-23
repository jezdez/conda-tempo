#!/usr/bin/env bash
# Thin forwarder: invoke the requested pixi task inside the container.
# The container's entrypoint has already activated the pixi env, so
# ``pixi run <task>`` resolves to already-activated executables.
#
# Usage: run_linux.sh [phase1|phase1-profile|phase2|all|scalene|shell]
set -euo pipefail

MODE="${1:-all}"

case "${MODE}" in
    phase1)               exec pixi run phase1 ;;
    phase1-profile)       exec pixi run phase1-profile ;;
    phase2|phase2-pyperf) exec pixi run phase2-pyperf ;;
    phase2-memray)        exec pixi run phase2-memray ;;
    scalene)
        # Scalene doesn't have a single pixi task yet (macOS can't run
        # it due to the arm64e binary issue); invoke the harness
        # scripts directly against the four workloads we care about.
        set -x
        python bench/run_scalene.py w1 -- create -n bench_w1 -c conda-forge -y python=3.13 requests || true
        conda env remove -y -n bench_w1 >/dev/null 2>&1 || true
        python bench/run_scalene.py w2 -- create -n bench_w2 -c conda-forge -y python=3.13 pandas scikit-learn matplotlib jupyter || true
        conda env remove -y -n bench_w2 >/dev/null 2>&1 || true
        python bench/seed_big_prefix.py --name bench_big --records 5000
        python bench/run_scalene.py w3 -- install -n bench_big -c conda-forge -y --dry-run --no-deps tzdata || true
        python bench/phase2/run_scalene.py s11_libmamba_installed -n 5000 || true
        python bench/phase2/run_scalene.py s6_verify_individual -n 200 || true
        ;;
    all)
        "$0" phase1
        "$0" phase1-profile
        "$0" phase2
        "$0" scalene
        ;;
    shell) exec bash -l ;;
    *)
        echo "Usage: $0 [phase1|phase1-profile|phase2|phase2-memray|scalene|all|shell]" >&2
        exit 1
        ;;
esac
