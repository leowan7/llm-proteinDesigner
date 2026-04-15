# Kendrew RFdiffusion Pipeline — v10 (fix silent build failures)
FROM runpod/base:0.6.2-cuda11.8.0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/opt/rfdiffusion" \
    MODELS_DIR=/opt/rfdiffusion/models \
    COLABFOLD_CACHE_DIR=/opt/colabfold_weights \
    COLABFOLD_CACHE=/opt/colabfold_weights

# ---------- System deps ----------
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget git cmake python3-pip python3-dev build-essential && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    rm -rf /var/lib/apt/lists/*

# ---------- PyTorch + DGL ----------
# PyTorch 2.2.2+cu118 uses cuDNN 8, matching JAX 0.4.23 requirements.
# Both frameworks share libcudnn.so.8 — no version conflict.
RUN python3 -m pip install --no-cache-dir \
    torch==2.2.2 torchvision==0.17.2 \
    --index-url https://download.pytorch.org/whl/cu118 && \
    python3 -m pip install --no-cache-dir torchdata==0.7.1 && \
    python3 -m pip install --no-cache-dir dgl -f https://data.dgl.ai/wheels/torch-2.2/cu118/repo.html
RUN find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- RFdiffusion + SE3Transformer ----------
RUN git clone --depth 1 https://github.com/RosettaCommons/RFdiffusion.git /opt/rfdiffusion && \
    cd /opt/rfdiffusion/env/SE3Transformer && \
    python3 -m pip install --no-cache-dir -r requirements.txt && \
    python3 setup.py install && \
    cd /opt/rfdiffusion && \
    python3 -m pip install --no-cache-dir -e . && \
    python3 -m pip install --no-cache-dir hydra-core omegaconf pyrsistent e3nn opt_einsum
RUN find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- RFdiffusion weights ----------
RUN mkdir -p $MODELS_DIR && \
    wget -q http://files.ipd.uw.edu/pub/RFdiffusion/6f5902ac237024bdd0c176cb93063dc4/Base_ckpt.pt \
         -O $MODELS_DIR/Base_ckpt.pt && \
    wget -q http://files.ipd.uw.edu/pub/RFdiffusion/e29311f6f1bf1af907f9ef9f44b8328b/Complex_base_ckpt.pt \
         -O $MODELS_DIR/Complex_base_ckpt.pt && \
    wget -q http://files.ipd.uw.edu/pub/RFdiffusion/5532d2e1f3a4738decd58b19d633b3c3/ActiveSite_ckpt.pt \
         -O $MODELS_DIR/ActiveSite_ckpt.pt
RUN find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- ProteinMPNN + weights ----------
RUN git clone --depth 1 https://github.com/dauparas/ProteinMPNN.git /opt/ProteinMPNN && \
    mkdir -p /opt/ProteinMPNN/vanilla_model_weights && \
    wget -q https://github.com/dauparas/ProteinMPNN/raw/main/vanilla_model_weights/v_48_020.pt \
         -O /opt/ProteinMPNN/vanilla_model_weights/v_48_020.pt
RUN find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- ColabFold + AF2 weights ----------
# Pin nvidia-cudnn-cu11<9.0 so JAX 0.4.23 finds libcudnn.so.8 (not 9.0).
# Without this, JAX falls back to CPU and AF2 takes ~3 min vs ~4 sec per design.
RUN python3 -m pip install --no-cache-dir \
    "nvidia-cudnn-cu11<9.0" \
    "colabfold[alphafold]==1.5.5" \
    "jax[cuda11_pip]==0.4.23" \
    -f https://storage.googleapis.com/jax-releases/jax_cuda_releases.html && \
    python3 -c "from colabfold.download import download_alphafold_params; download_alphafold_params('alphafold2_multimer_v3')"
RUN find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Python utilities ----------
RUN python3 -m pip install --no-cache-dir requests biopython pandas

# ---------- Pipeline script ----------
COPY run_pipeline.py /opt/run_pipeline.py

WORKDIR /opt
CMD ["python3", "/opt/run_pipeline.py"]
