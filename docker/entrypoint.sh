#!/usr/bin/env bash
# Container entrypoint: source the conda devenv, make the conda-tempo
# bench scripts discoverable, then exec the requested command.
set -euo pipefail

cd "${CONDA_SRC}"
# shellcheck disable=SC1091
source ./dev/start -p 3.13 -i miniforge >/dev/null 2>&1

if [ -d "${TEMPO_REPO}/bench" ]; then
    mkdir -p "${TEMPO_WORK}/bench"
    cp -r "${TEMPO_REPO}/bench/." "${TEMPO_WORK}/bench/"
    find "${TEMPO_WORK}/bench" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
fi

export PYTHONPATH="${TEMPO_WORK}/bench:${TEMPO_WORK}/bench/phase2:${PYTHONPATH:-}"
export CONDA_BENCH_TMPDIR="${TEMPO_WORK}/tmp"
mkdir -p "${CONDA_BENCH_TMPDIR}"

export TEMPO_DATA="${TEMPO_WORK}/data"
mkdir -p "${TEMPO_DATA}"

if [ $# -eq 0 ]; then
    exec bash -l
fi

exec "$@"
