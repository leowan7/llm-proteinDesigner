# RFdiffusion + ProteinMPNN + ColabFold AF2 — RunPod Serverless Image
#
# Contains all model weights baked in so cold-start pulls from cache, not
# network. Designed for A100 40GB+ GPUs.
#
# Build:
#   docker build --platform linux/amd64 -t kendrew/rfdiffusion:v1 docker/rfdiffusion/
#
# Expected image size: ~25-30 GB (model weights dominate).

FROM nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# ---------- System dependencies ----------
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wget git python3.10 python3-pip python3.10-venv \
        build-essential cmake aria2 && \
    rm -rf /var/lib/apt/lists/*

# Ensure python3 points to 3.10
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.10 1

# ---------- PyTorch (CUDA 11.8) ----------
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cu118

# ---------- RFdiffusion ----------
RUN git clone --depth 1 https://github.com/RosettaCommons/RFdiffusion.git /opt/rfdiffusion
WORKDIR /opt/rfdiffusion
RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir hydra-core omegaconf

# RFdiffusion model weights (baked in — avoids runtime download)
RUN mkdir -p /opt/rfdiffusion/models && \
    wget -q http://files.ipd.uw.edu/pub/RFdiffusion/6f5902ac237024bdd0c176cb93063dc4/Base_ckpt.pt \
        -O /opt/rfdiffusion/models/Base_ckpt.pt && \
    wget -q http://files.ipd.uw.edu/pub/RFdiffusion/e29311f6f1bf1af907f9ef9f44b8328b/Complex_base_ckpt.pt \
        -O /opt/rfdiffusion/models/Complex_base_ckpt.pt && \
    wget -q http://files.ipd.uw.edu/pub/RFdiffusion/5532d2e1f3a4738decd58b19d633b3c3/ActiveSite_ckpt.pt \
        -O /opt/rfdiffusion/models/ActiveSite_ckpt.pt && \
    wget -q http://files.ipd.uw.edu/pub/RFdiffusion/f572d396fae9206628714fb2ce00f72e/Complex_beta_ckpt.pt \
        -O /opt/rfdiffusion/models/Complex_beta_ckpt.pt

# ---------- ProteinMPNN ----------
RUN git clone --depth 1 https://github.com/dauparas/ProteinMPNN.git /opt/ProteinMPNN

# ProteinMPNN ships weights in the repo; verify the default model exists.
RUN test -f /opt/ProteinMPNN/vanilla_model_weights/v_48_020.pt || \
    (echo "ProteinMPNN weights missing — check repo structure" && exit 1)

# ---------- ColabFold (AF2 multimer validation) ----------
# Install ColabFold with AlphaFold2 backend (no full database — structure
# prediction only, using paired MSA from ColabFold server or pre-computed).
RUN pip install --no-cache-dir "colabfold[alphafold]"

# Pre-download AF2 multimer weights during build so they are baked in.
RUN python -c "\
from colabfold.download import download_alphafold_params; \
download_alphafold_params('alphafold2_multimer_v3')" || \
    echo "WARN: AF2 weight pre-download failed — will download at runtime"

# ---------- Runtime dependencies ----------
RUN pip install --no-cache-dir runpod requests biopython pandas

# ---------- Handler ----------
COPY rp_handler.py /opt/rp_handler.py

WORKDIR /opt
CMD ["python", "/opt/rp_handler.py"]
