# Kendrew BoltzGen Pipeline — v3
# BoltzGen requires CUDA 12.4+ (cuequivariance_ops_cu12) and Python 3.12.
# Weights (~6 GB) are baked into the image to avoid cold-start downloads.
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/boltzgen_cache

# ---------- System deps ----------
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget git python3-pip python3-dev build-essential \
    software-properties-common && \
    rm -rf /var/lib/apt/lists/*

# ---------- Python 3.12 ----------
# nvidia/cuda base ships Python 3.10; BoltzGen requires 3.12.
RUN add-apt-repository -y ppa:deadsnakes/ppa && \
    apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv python3.12-dev && \
    rm -rf /var/lib/apt/lists/* && \
    ln -sf /usr/bin/python3.12 /usr/bin/python3 && \
    ln -sf /usr/bin/python3.12 /usr/bin/python && \
    python3.12 -m ensurepip --upgrade && \
    python3.12 -m pip install --no-cache-dir --upgrade pip setuptools wheel

# ---------- PyTorch (CUDA 12.4) ----------
RUN python3 -m pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cu124

# ---------- BoltzGen ----------
RUN python3 -m pip install --no-cache-dir boltzgen==0.1.0

# ---------- Pipeline utilities ----------
RUN python3 -m pip install --no-cache-dir \
    gemmi \
    requests \
    pyyaml

# ---------- Bake model weights (~6 GB) ----------
# Pre-download HuggingFace weights so pods start instantly.
# Cannot use BoltzGenModel() here — it requires CUDA, unavailable during build.
# Use huggingface_hub snapshot_download to fetch weights without GPU init.
RUN mkdir -p $HF_HOME && \
    python3 -c "import os; from huggingface_hub import snapshot_download; \
c = os.environ['HF_HOME']; \
snapshot_download(repo_id='boltzgen/boltzgen1_diverse', cache_dir=c); \
snapshot_download(repo_id='boltzgen/boltzgen1_adherence', cache_dir=c)" && \
    du -sh $HF_HOME && \
    echo "Weight cache populated"

# ---------- Pipeline script ----------
COPY run_pipeline.py /opt/run_pipeline.py

WORKDIR /opt
CMD ["python3", "/opt/run_pipeline.py"]
