# Linux arm64 benchmark runner for conda-tempo.
#
# Goal: reproduce the Phase-1 + Phase-2 harness on Linux to validate
# the macOS → Linux projections (especially S7's "~3× on ext4" claim)
# and unlock Scalene (which has a broken arm64e build on conda-forge
# for macOS 26, but works fine on Linux).
#
# Usage:
#   docker build --platform linux/arm64 -t conda-tempo-linux docker/
#   docker run --rm -it -v $(pwd):/repo:ro -v conda-tempo-work:/work \
#       conda-tempo-linux bash
#   # inside:
#   bash /repo/docker/run_linux.sh all
#
# The repo is mounted read-only so the container can't accidentally
# modify the host working tree. Results land in a named volume that
# the host can extract afterwards.

FROM --platform=linux/arm64 condaforge/miniforge3:latest

ARG CONDA_SHA=7c1ebba7c
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
    && cd /opt/conda-src \
    && git checkout ${CONDA_SHA}

WORKDIR /opt/conda-src
RUN bash -c 'source ./dev/start -p 3.13 -i miniforge -u \
    && conda install --prefix "${CONDA_PREFIX}" -c conda-forge -y \
        memray pyperf scalene \
    && conda clean -afy'

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV CONDA_SRC=/opt/conda-src
ENV TEMPO_REPO=/repo
ENV TEMPO_WORK=/work

WORKDIR /work
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]
