# Kendrew PXDesign Pipeline — v4
# PXDesign requires CUDA 12.1+ and Python >=3.10 (setup.py: python_requires='>=3.10').
# Uses devel image because CUTLASS and DeepSpeed compile custom CUDA kernels.
# Ubuntu 22.04 ships with Python 3.10 — no deadsnakes PPA needed.
#
# Official PXDesign Dockerfile uses private protenix:v0.0.3 base (inaccessible).
# We replicate their exact pip install steps on a public CUDA image.
FROM nvidia/cuda:12.1.0-cudnn8-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PXDESIGN_DIR=/opt/pxdesign \
    CUTLASS_PATH=/opt/cutlass

# ---------- System deps ----------
# curl is required by download_tool_weights.sh
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl git cmake python3-pip python3-dev build-essential \
    libffi-dev && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel && \
    rm -rf /var/lib/apt/lists/*

# ---------- PyTorch 2.3.1 (CUDA 12.1) ----------
# Pinned to 2.3.1 per official PXDesign requirements.txt
RUN python3 -m pip install --no-cache-dir \
    torch==2.3.1 --index-url https://download.pytorch.org/whl/cu121

# ---------- CUTLASS v3.5.1 (header-only, for DeepSpeed Evo attention) ----------
RUN git clone --depth 1 --branch v3.5.1 \
    https://github.com/NVIDIA/cutlass.git $CUTLASS_PATH

# ---------- Clone PXDesign ----------
RUN git clone --depth 1 https://github.com/bytedance/PXDesign.git $PXDESIGN_DIR

# ---------- Install PXDesign deps (replicating official Dockerfile pattern) ----------
# Official Dockerfile does NOT use install.sh — it pip installs directly.
# Order follows their Dockerfile: Protenix -> ColabDesign -> JAX -> PXDesignBench -> PXDesign

# Protenix (PXDesign fork)
RUN pip --no-cache-dir install git+https://github.com/bytedance/Protenix.git@v0.5.0+pxd

# ColabDesign + PXDesignBench deps
RUN pip install --no-cache-dir git+https://github.com/sokrypton/ColabDesign.git --no-deps && \
    pip install --no-cache-dir \
    posix_ipc einops transformers==4.51.3 optax==0.2.5 dm-haiku==0.0.13

# JAX with CUDA support
RUN pip install --no-cache-dir \
    "jax[cuda]==0.4.29" -f https://storage.googleapis.com/jax-releases/jax_cuda_releases.html

# Remaining deps from requirements.txt
RUN pip install --no-cache-dir \
    numpy==1.26.3 natsort dm-tree \
    biopython==1.83 modelcif==0.7 biotite==1.0.1 \
    scipy scikit-learn scikit-learn-extra \
    "deepspeed>=0.15.1" protobuf==3.20.2 \
    matplotlib==3.9.2 rdkit-pypi \
    pyyaml ml_collections tqdm pandas

# PXDesignBench
RUN pip install --no-cache-dir git+https://github.com/bytedance/PXDesignBench.git@v0.1.2 --no-deps

# PXDesign itself (editable install — setup.py has no install_requires)
RUN cd $PXDESIGN_DIR && pip install --no-cache-dir -e .

# ---------- Download tool weights (AF2, ProteinMPNN, CCD cache) ----------
RUN cd $PXDESIGN_DIR && \
    bash download_tool_weights.sh && \
    echo "=== Weight validation ===" && \
    ls -lh tool_weights/af2/ && \
    ls -lh tool_weights/mpnn/ && \
    ls -lh tool_weights/ccd/ && \
    echo "Weight download complete"

# ---------- Pipeline utilities ----------
RUN python3 -m pip install --no-cache-dir \
    gemmi \
    requests

# ---------- JIT kernel warmup (best-effort, no GPU during build) ----------
# Attempt to trigger model imports so Python bytecode is cached.
# Actual CUDA kernel compilation will happen on first RunPod job.
RUN cd $PXDESIGN_DIR && \
    python3 -c "import pxdesign; print('PXDesign importable')" && \
    python3 -c "import protenix; print('Protenix importable')" && \
    python3 -c "import torch; print('PyTorch', torch.__version__)" || \
    echo "Import check failed — will validate at runtime"

# ---------- Pipeline script ----------
COPY run_pipeline.py /opt/run_pipeline.py

WORKDIR /opt
CMD ["python3", "/opt/run_pipeline.py"]
