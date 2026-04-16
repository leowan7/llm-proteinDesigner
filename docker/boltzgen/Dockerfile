# Kendrew BoltzGen Pipeline — v5
# BoltzGen requires Python >=3.11 (pyproject.toml) and CUDA 12.4+.
# Ubuntu 24.04 ships Python 3.12 natively — no deadsnakes PPA needed.
# Weights (~6 GB) are baked into the image to avoid cold-start downloads.
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/boltzgen_cache

# ---------- System deps ----------
# Ubuntu 24.04 ships Python 3.12 — satisfies boltzgen's >=3.11 requirement.
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget git python3-pip python3-dev python3-venv build-essential && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel --break-system-packages && \
    rm -rf /var/lib/apt/lists/*

# ---------- PyTorch (CUDA 12.4) ----------
RUN python3 -m pip install --no-cache-dir --break-system-packages \
    torch --index-url https://download.pytorch.org/whl/cu124

# ---------- BoltzGen ----------
# Must use latest (not 0.1.0) — the `boltzgen download` CLI was added post-0.1.0.
RUN python3 -m pip install --no-cache-dir --break-system-packages boltzgen

# ---------- Pipeline utilities ----------
RUN python3 -m pip install --no-cache-dir --break-system-packages \
    gemmi \
    requests \
    pyyaml

# ---------- Bake model weights (~6 GB) ----------
# Pre-download HuggingFace weights so pods start instantly.
# Uses BoltzGen's built-in CLI to download from boltzgen/boltzgen-1 (public).
# Avoids `download all` which includes gated inference-data repo.
RUN mkdir -p $HF_HOME && \
    boltzgen download design-diverse --cache $HF_HOME && \
    boltzgen download inverse-fold --cache $HF_HOME && \
    boltzgen download folding --cache $HF_HOME && \
    du -sh $HF_HOME && \
    echo "Weight cache populated"

# ---------- Pipeline script ----------
COPY run_pipeline.py /opt/run_pipeline.py

WORKDIR /opt
CMD ["python3", "/opt/run_pipeline.py"]
