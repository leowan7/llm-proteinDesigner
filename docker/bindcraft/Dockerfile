# Kendrew BindCraft Pipeline — v4 (FreeBindCraft, no PyRosetta)
# Uses FreeBindCraft fork which replaces PyRosetta with:
#   - OpenMM for relaxation (GPU-accelerated, 2-4x faster)
#   - sc-rs (Rust) for shape complementarity
#   - FreeSASA + Biopython for SASA and interface analysis
# Fully MIT licensed — no RosettaCommons license required.
FROM runpod/base:0.6.2-cuda11.8.0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    BINDCRAFT_DIR=/opt/BindCraft

# ---------- System deps ----------
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget git cmake python3-pip python3-dev build-essential \
    libxml2-dev libxslt1-dev zlib1g-dev libffi-dev libhdf5-dev \
    cargo && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    rm -rf /var/lib/apt/lists/*
RUN find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Install Miniconda ----------
# FreeBindCraft's install script requires conda to be pre-installed.
# It discovers the path via `conda info --base`.
ENV CONDA_DIR=/opt/conda
RUN wget -q https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh && \
    bash /tmp/miniconda.sh -b -p $CONDA_DIR && \
    rm /tmp/miniconda.sh && \
    $CONDA_DIR/bin/conda init bash
ENV PATH="$CONDA_DIR/bin:$PATH"

# ---------- Clone FreeBindCraft ----------
RUN git clone --depth 1 https://github.com/cytokineking/FreeBindCraft.git $BINDCRAFT_DIR
RUN find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- FreeBindCraft install (conda env, no PyRosetta) ----------
# install_bindcraft.sh creates a conda env named "BindCraft" containing:
#   - PyTorch, JAX (CUDA 11.8), ColabDesign, ColabFold, AF2 weights
#   - OpenMM, FreeSASA, sc-rs, Biopython, pandas, matplotlib, etc.
# --no-pyrosetta skips PyRosetta install entirely.
# NOTE: This layer is slow (20-30 min) due to conda env + weight downloads.
RUN cd $BINDCRAFT_DIR && \
    bash install_bindcraft.sh --cuda '11.8' --no-pyrosetta
RUN find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Activate conda env for all subsequent commands ----------
# CONDA_DIR is known because we installed miniconda ourselves above.
ENV PATH="$CONDA_DIR/envs/BindCraft/bin:$PATH" \
    CONDA_DEFAULT_ENV="BindCraft"

# ---------- Pipeline utilities (install into conda env) ----------
# Use the explicit conda env pip to guarantee it lands in the right env.
RUN $CONDA_DIR/envs/BindCraft/bin/pip install --no-cache-dir requests

# ---------- Pipeline script (last for layer caching) ----------
COPY run_pipeline.py /opt/run_pipeline.py

WORKDIR /opt
CMD ["python", "/opt/run_pipeline.py"]
