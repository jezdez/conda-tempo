# Linux arm64 benchmark runner for conda-tempo (pixi-based).
#
# Lays out the workspace under /opt/workspace/ so pixi's path
# deps (``path = "../conda"`` etc.) resolve as siblings, matching
# the macOS layout under ``~/Code/git/``:
#
#     /opt/workspace/
#         conda/                        pinned at CONDA_SHA
#         conda-package-handling/       pinned at CPH_SHA
#         conda-package-streaming/      pinned at CPS_SHA
#         conda-tempo/                  copied from the host at build
#             pixi.toml                 ../conda, ../cph, ../cps
#             bench/                    harness
#
# At runtime the entrypoint stages bench/ from the host /repo mount
# over the pre-built /opt/workspace/conda-tempo/bench/, so the
# container always runs the current bench scripts. Data lands in
# /work (a docker volume), never on the bind-mounted host /repo.
#
# Usage:
#   pixi run linux-build
#   pixi run linux-all
#   pixi run linux-fetch      # copies data out to data/phase{1,2}_linux/

FROM --platform=linux/arm64 debian:trixie-slim

ARG PIXI_VERSION=0.67.2
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

RUN curl -fsSL "https://github.com/prefix-dev/pixi/releases/download/v${PIXI_VERSION}/pixi-aarch64-unknown-linux-musl.tar.gz" \
        | tar -xzC /usr/local/bin \
    && pixi --version

WORKDIR /opt/workspace

# Clone the three Python packages pixi will install editable.
RUN git clone --filter=blob:none https://github.com/conda/conda.git conda \
    && git -C conda checkout ${CONDA_SHA}
RUN git clone --filter=blob:none https://github.com/conda/conda-package-handling.git conda-package-handling \
    && git -C conda-package-handling checkout ${CPH_SHA}
RUN git clone --filter=blob:none https://github.com/conda/conda-package-streaming.git conda-package-streaming \
    && git -C conda-package-streaming checkout ${CPS_SHA}

# Stage conda-tempo. We copy the whole repo so pixi.toml + bench/ +
# docker/ + pixi.lock (if committed) all land. The entrypoint later
# overlays /repo's current state on top of this at runtime.
COPY . conda-tempo/

# Pre-warm the pixi environment so first run doesn't pay the install cost.
WORKDIR /opt/workspace/conda-tempo
RUN pixi install

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV TEMPO_REPO=/repo
ENV TEMPO_WORK=/work
ENV TEMPO_TEMPO=/opt/workspace/conda-tempo

WORKDIR /work
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]
