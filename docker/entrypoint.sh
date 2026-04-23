#!/usr/bin/env bash
# Container entrypoint: overlay the host's live bench/ harness on
# top of the baked-in copy at /opt/workspace/conda-tempo, ensure
# a writable benchmark working directory under /work, then exec
# whatever command was requested inside the pre-built pixi env.
#
# Layout:
#   /opt/workspace/conda/                    (pinned SHA, editable install)
#   /opt/workspace/conda-package-handling/   (pinned SHA, editable install)
#   /opt/workspace/conda-package-streaming/  (pinned SHA, editable install)
#   /opt/workspace/conda-tempo/              (baked; overlaid with /repo at runtime)
#       .pixi/envs/default/                  (pre-built)
#       pixi.toml
#       bench/                               (gets re-staged from /repo/bench/)
#   /repo                                    (host conda-tempo, bind-mounted, ro)
#   /work                                    (named volume for benchmark output)
#       tmp/                                 (CONDA_BENCH_TMPDIR for fixtures)
#       data/                                (where benchmarks write)
set -euo pipefail

# Stage the host's current bench harness over the baked-in copy so
# scripts can create __pycache__ etc., and so local changes to bench/
# don't require rebuilding the image.
if [ -d "${TEMPO_REPO}/bench" ]; then
    cp -r "${TEMPO_REPO}/bench/." "${TEMPO_TEMPO}/bench/"
    # Drop any __pycache__ from the overlay.
    find "${TEMPO_TEMPO}/bench" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
fi

# Benchmarks that generate fixtures must put them on container ext4,
# not on the bind-mounted /repo.
export CONDA_BENCH_TMPDIR="${TEMPO_WORK}/tmp"
mkdir -p "${CONDA_BENCH_TMPDIR}"

# Tell the bench harness where to write data. Scripts in bench/ resolve
# data/ relative to their own location (TEMPO_TEMPO/bench/.. = TEMPO_TEMPO).
# We symlink /opt/workspace/conda-tempo/data → /work/data so the named
# volume captures everything.
mkdir -p "${TEMPO_WORK}/data"
if [ -L "${TEMPO_TEMPO}/data" ]; then
    rm "${TEMPO_TEMPO}/data"
fi
rm -rf "${TEMPO_TEMPO}/data" 2>/dev/null || true
ln -s "${TEMPO_WORK}/data" "${TEMPO_TEMPO}/data"

cd "${TEMPO_TEMPO}"

# Activate the pre-built pixi env via shell-hook so the rest of the
# container sees pixi's PATH (including the bench/tools/conda shim),
# CONDA_PREFIX, etc. This sidesteps the double-``pixi run`` wrap
# problem when run_linux.sh internally invokes ``pixi run``.
eval "$(pixi shell-hook --manifest-path "${TEMPO_TEMPO}/pixi.toml")"

# If no command supplied, drop into an interactive shell.
if [ $# -eq 0 ]; then
    exec bash -l
fi

exec "$@"
