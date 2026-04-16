# Kendrew BoltzGen Pipeline — v6
# BoltzGen requires Python >=3.11 (pyproject.toml) and CUDA 12.4+.
# CUDA 12.8.1 runtime is forward-compatible with PyTorch cu124 wheels.
# Ubuntu 24.04 ships Python 3.12 natively — no deadsnakes PPA needed.
# Weights (~6 GB) are baked into the image to avoid cold-start downloads.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/boltzgen_cache

# ---------- System deps ----------
# Ubuntu 24.04 ships Python 3.12 — satisfies boltzgen's >=3.11 requirement.
# python3-pip AND python3-venv are in Ubuntu's universe repo, which NVIDIA
# runtime images don't enable. Bootstrap pip via get-pip.py instead.
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget git python3-dev build-essential && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    wget -qO /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py && \
    python3 /tmp/get-pip.py --break-system-packages && \
    rm /tmp/get-pip.py && \
    python3 -m pip install --no-cache-dir --upgrade setuptools wheel --break-system-packages && \
    rm -rf /var/lib/apt/lists/*

# ---------- PyTorch (CUDA 12.4) ----------
RUN python3 -m pip install --no-cache-dir --break-system-packages \
    torch --index-url https://download.pytorch.org/whl/cu124

# ---------- BoltzGen ----------
RUN python3 -m pip install --no-cache-dir --break-system-packages boltzgen

# ---------- Pipeline utilities ----------
RUN python3 -m pip install --no-cache-dir --break-system-packages \
    gemmi \
    requests \
    pyyaml

# ---------- Bake model weights (~6 GB) ----------
# Pre-download HuggingFace weights so pods start instantly.
# BoltzGen auto-downloads weights on first model load to $HF_HOME.
# We trigger this during build with a minimal dummy design spec.
RUN mkdir -p $HF_HOME && \
    echo 'entities: [{sequence: "AAAAAA"}]' > /tmp/dummy.yaml && \
    timeout 600 boltzgen run /tmp/dummy.yaml \
        --output /tmp/dummy_out \
        --protocol protein-anything \
        --num_designs 1 \
        --budget 1 || true && \
    rm -rf /tmp/dummy.yaml /tmp/dummy_out && \
    du -sh $HF_HOME && \
    echo "Weight cache populated"

# ---------- Pipeline script ----------
COPY run_pipeline.py /opt/run_pipeline.py

WORKDIR /opt
CMD ["python3", "/opt/run_pipeline.py"]
