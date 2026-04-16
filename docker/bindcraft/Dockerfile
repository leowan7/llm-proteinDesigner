# Kendrew BindCraft Pipeline — v7 (based on official FreeBindCraft Dockerfile)
# Uses FreeBindCraft fork which replaces PyRosetta with:
#   - OpenMM for relaxation (GPU-accelerated, 2-4x faster)
#   - sc-rs (Rust) for shape complementarity
#   - FreeSASA + Biopython for SASA and interface analysis
# Fully MIT licensed — no RosettaCommons license required.
#
# Based on: https://github.com/cytokineking/FreeBindCraft/blob/master/Dockerfile
# Adapted for RunPod pod deployment with Kendrew pipeline integration.
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

SHELL ["/bin/bash", "-c"]

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    PYTHONUNBUFFERED=1 \
    BINDCRAFT_DIR=/app

# ---------- System deps ----------
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl git rsync libgfortran5 wget \
    build-essential pkg-config procps unzip && \
    rm -rf /var/lib/apt/lists/*

# ---------- OpenCL (for OpenMM GPU acceleration) ----------
RUN apt-get update && apt-get install -y --no-install-recommends \
    ocl-icd-libopencl1 clinfo && \
    rm -rf /var/lib/apt/lists/*
RUN mkdir -p /etc/OpenCL/vendors && \
    echo "libnvidia-opencl.so.1" > /etc/OpenCL/vendors/nvidia.icd

# ---------- Install Miniforge (conda) ----------
# FreeBindCraft's install script requires conda pre-installed.
# Miniforge comes pre-configured with conda-forge channel.
ENV CONDA_DIR=/miniforge3
RUN wget -q https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh -O /tmp/miniforge.sh && \
    bash /tmp/miniforge.sh -b -p ${CONDA_DIR} && \
    rm -f /tmp/miniforge.sh
ENV PATH=${CONDA_DIR}/bin:${PATH}
RUN conda config --set channel_priority strict && \
    conda config --set always_yes yes && \
    conda update -n base -c conda-forge conda && \
    conda clean -afy

# ---------- Clone FreeBindCraft ----------
WORKDIR /app
RUN git clone --depth 1 https://github.com/cytokineking/FreeBindCraft.git /app/src && \
    shopt -s dotglob && mv /app/src/* /app/ && rm -rf /app/src

# Ensure helper binaries are executable
RUN chmod +x /app/functions/dssp || true && \
    chmod +x /app/functions/sc || true

# ---------- FreeBindCraft install (no PyRosetta) ----------
# Creates conda env "BindCraft" with Python 3.10, JAX 0.6.0,
# ColabDesign, AF2 weights, OpenMM, FreeSASA, sc-rs, etc.
# Uses bash -lc (login shell) so conda shell functions are available.
# NOTE: This layer is slow (20-30 min) due to conda env + weight downloads.
RUN bash -lc 'source ${CONDA_DIR}/etc/profile.d/conda.sh && \
    bash /app/install_bindcraft.sh --pkg_manager conda --cuda 12.1 --no-pyrosetta'

# ---------- Verify critical dependencies installed ----------
RUN ${CONDA_DIR}/envs/BindCraft/bin/python -c "\
import jax; print('JAX', jax.__version__); \
import openmm; print('OpenMM', openmm.__version__); \
from Bio.PDB import PDBParser; print('Biopython OK')"

# ---------- Activate conda env for all subsequent commands ----------
ENV PATH=${CONDA_DIR}/envs/BindCraft/bin:${CONDA_DIR}/bin:${PATH} \
    LD_LIBRARY_PATH=${CONDA_DIR}/envs/BindCraft/lib:${LD_LIBRARY_PATH} \
    CONDA_DEFAULT_ENV="BindCraft" \
    BINDCRAFT_HOME=/app

# Prefer CUDA for OpenMM on NVIDIA GPUs (faster than OpenCL)
ENV OPENMM_PLATFORM_ORDER=CUDA,OpenCL \
    OPENMM_DEFAULT_PLATFORM=CUDA

# ---------- Pipeline utilities ----------
RUN ${CONDA_DIR}/envs/BindCraft/bin/pip install --no-cache-dir requests

# ---------- Pipeline script ----------
COPY run_pipeline.py /opt/run_pipeline.py

CMD ["/miniforge3/envs/BindCraft/bin/python", "/opt/run_pipeline.py"]
