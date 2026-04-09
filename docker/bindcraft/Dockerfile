# Kendrew BindCraft Pipeline — v1 (Strict UID Enforcement)
FROM runpod/base:0.6.2-cuda11.8.0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    BINDCRAFT_DIR=/opt/BindCraft \
    COLABFOLD_CACHE_DIR=/opt/colabfold_weights \
    COLABFOLD_CACHE=/opt/colabfold_weights

# ---------- System deps ----------
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget git cmake python3-pip python3-dev build-essential \
    libxml2-dev libxslt1-dev zlib1g-dev libffi-dev libhdf5-dev && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    rm -rf /var/lib/apt/lists/* && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- PyTorch 2.2.2+cu118 ----------
# PyTorch 2.2.2+cu118 uses cuDNN 8, matching JAX 0.4.23 requirements.
# Both frameworks share libcudnn.so.8 — no version conflict.
RUN python3 -m pip install --no-cache-dir \
    torch==2.2.2 torchvision==0.17.2 \
    --index-url https://download.pytorch.org/whl/cu118 && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- JAX + ColabFold (for AF2 inside BindCraft) ----------
# Pin nvidia-cudnn-cu11<9.0 so JAX 0.4.23 finds libcudnn.so.8 (not 9.0).
RUN python3 -m pip install --no-cache-dir \
    "nvidia-cudnn-cu11<9.0" \
    "colabfold[alphafold]==1.5.5" \
    "jax[cuda11_pip]==0.4.23" \
    -f https://storage.googleapis.com/jax-releases/jax_cuda_releases.html && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Clone BindCraft ----------
RUN git clone --depth 1 https://github.com/martinpacesa/BindCraft.git $BINDCRAFT_DIR && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- BindCraft install (MIT, no PyRosetta) ----------
# The install script installs ColabDesign, OpenMM, FreeSASA, Biopython,
# and other BindCraft-specific dependencies. This layer takes 10+ minutes.
RUN cd $BINDCRAFT_DIR && \
    bash install_bindcraft.sh --cuda '11.8' --no-pyrosetta && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- AF2 weights (pre-download for faster cold starts) ----------
RUN python3 -c "from colabfold.download import download_alphafold_params; download_alphafold_params('alphafold2_multimer_v3')" && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Python utilities ----------
RUN python3 -m pip install --no-cache-dir requests biopython pandas && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Pipeline script (last for layer caching) ----------
COPY run_pipeline.py /opt/run_pipeline.py

WORKDIR /opt
CMD ["python3", "/opt/run_pipeline.py"]
