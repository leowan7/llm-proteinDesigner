# Kendrew BoltzGen Pipeline — v1 (Strict UID Enforcement)
FROM runpod/base:0.6.2-cuda11.8.0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/hf_cache

# ---------- System deps ----------
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget git python3-pip python3-dev build-essential \
    software-properties-common && \
    rm -rf /var/lib/apt/lists/* && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Python 3.12 ----------
# runpod/base ships Python 3.10; BoltzGen requires 3.12.
RUN add-apt-repository -y ppa:deadsnakes/ppa && \
    apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv python3.12-dev && \
    rm -rf /var/lib/apt/lists/* && \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 && \
    update-alternatives --set python3 /usr/bin/python3.12 && \
    ln -sf /usr/bin/python3.12 /usr/bin/python && \
    python3.12 -m ensurepip --upgrade && \
    python3.12 -m pip install --no-cache-dir --upgrade pip setuptools wheel && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- PyTorch ----------
# PyTorch 2.2.2+cu118 for CUDA 11.8 compatibility with runpod/base.
RUN python3.12 -m pip install --no-cache-dir \
    torch==2.2.2 \
    --index-url https://download.pytorch.org/whl/cu118 && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- BoltzGen ----------
RUN python3.12 -m pip install --no-cache-dir boltzgen==0.1.0 && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Python utilities ----------
RUN python3.12 -m pip install --no-cache-dir \
    requests==2.31.0 \
    pyyaml==6.0.1 \
    pandas==2.2.1 && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Bake model weights (~6 GB) ----------
# Run a minimal import to trigger HuggingFace weight download during build.
# This avoids a 5-10 min cold start on every pod launch.
RUN mkdir -p $HF_HOME && \
    python3.12 -c "from boltzgen import BoltzGenModel; BoltzGenModel()" && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Pipeline script ----------
COPY run_pipeline.py /opt/run_pipeline.py

WORKDIR /opt
CMD ["python3.12", "/opt/run_pipeline.py"]
