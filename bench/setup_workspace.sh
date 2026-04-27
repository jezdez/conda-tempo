#!/usr/bin/env bash
# Install conda-package-handling (cph) and conda-package-streaming (cps)
# source-editable in the currently activated conda devenv, so the
# benchmark harness measures local workspace checkouts instead of the
# conda-forge shipped versions.
#
# Usage:
#     source <path-to>/conda/dev/start -p 3.13 -i miniforge
#     bench/setup_workspace.sh
#
# Idempotent: rerunning upgrades the editable installs in-place.
# Re-run after every dev/start -u (which rebuilds the env and can
# clobber user pip installs).
#
# Expects conda-package-handling and conda-package-streaming to be
# checked out as siblings of this repository (e.g. all under the same
# parent directory). Override the parent path via $TEMPO_WORKSPACE if
# that isn't the case:
#     TEMPO_WORKSPACE=/path/to/parent bench/setup_workspace.sh
set -euo pipefail

if [ -z "${CONDA_PREFIX:-}" ]; then
    echo "error: source dev/start first so CONDA_PREFIX is set" >&2
    exit 1
fi

WORKSPACE="${TEMPO_WORKSPACE:-$(cd "$(dirname "$0")/../.." && pwd)}"
for repo in conda-package-handling conda-package-streaming; do
    src="${WORKSPACE}/${repo}"
    if [ ! -d "${src}" ]; then
        echo "error: ${src} does not exist. Clone it first:" >&2
        echo "       git clone git@github.com:conda/${repo}.git ${src}" >&2
        exit 2
    fi
done

cph_sha=$(git -C "${WORKSPACE}/conda-package-handling" rev-parse --short HEAD)
cps_sha=$(git -C "${WORKSPACE}/conda-package-streaming" rev-parse --short HEAD)

echo "Installing cph (${cph_sha}) and cps (${cps_sha}) editable into ${CONDA_PREFIX}..."

# Drop any conda-installed copies first so the editable install wins.
conda remove --prefix "${CONDA_PREFIX}" -y --force conda-package-handling conda-package-streaming >/dev/null 2>&1 || true

# cps has no C extensions; straight editable install.
pip install -e "${WORKSPACE}/conda-package-streaming" --quiet --no-deps

# cph ships archive_utils_cy.pyx (Cython). pyproject.toml handles the build
# for a normal install; for editable we rely on the tree's prebuilt
# .so (if present) or pip will build it.
pip install -e "${WORKSPACE}/conda-package-handling" --quiet --no-deps

python - <<'PY'
import conda_package_handling, conda_package_streaming, inspect
print(f"cph: {conda_package_handling.__version__} at {inspect.getfile(conda_package_handling)}")
print(f"cps: at {inspect.getfile(conda_package_streaming)}")
PY
