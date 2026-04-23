# Linux arm64 benchmark runner for conda-tempo.
#
# Goal: reproduce the Phase-1 + Phase-2 harness on Linux to validate
# the macOS → Linux projections (especially S7's "~3× on ext4" claim)
# and unlock Scalene (which has a broken arm64e build on conda-forge
# for macOS 26, but works fine on Linux).
#
# Now includes cph + cps at pinned SHAs so the Linux S8 benchmark
# measures the workspace versions (matching bench/setup_workspace.sh
# on macOS).
#
# Usage:
#   docker build --platform linux/arm64 -t conda-tempo-linux docker/
#   docker run --rm -it -v $(pwd):/repo:ro -v conda-tempo-work:/work \
#       conda-tempo-linux bash

FROM --platform=linux/arm64 condaforge/miniforge3:latest

ARG CONDA_SHA=7c1ebba7c
ARG CPH_SHA=5da82cc
ARG CPS_SHA=e47a70b
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        build-essential \
        hyperfine \
        python3-dbg \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --filter=blob:none https://github.com/conda/conda.git /opt/conda-src \
    && cd /opt/conda-src && git checkout ${CONDA_SHA}
RUN git clone --filter=blob:none https://github.com/conda/conda-package-handling.git /opt/cph-src \
    && cd /opt/cph-src && git checkout ${CPH_SHA}
RUN git clone --filter=blob:none https://github.com/conda/conda-package-streaming.git /opt/cps-src \
    && cd /opt/cps-src && git checkout ${CPS_SHA}

WORKDIR /opt/conda-src
RUN bash -c 'source ./dev/start -p 3.13 -i miniforge -u \
    && conda install --prefix "${CONDA_PREFIX}" -c conda-forge -y \
        memray pyperf scalene \
    && conda remove --prefix "${CONDA_PREFIX}" -y --force \
        conda-package-handling conda-package-streaming \
    && pip install -e /opt/cps-src --no-deps --quiet \
    && pip install -e /opt/cph-src --no-deps --quiet \
    && conda clean -afy'

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV CONDA_SRC=/opt/conda-src
ENV TEMPO_REPO=/repo
ENV TEMPO_WORK=/work

WORKDIR /work
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]
