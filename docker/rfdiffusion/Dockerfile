# Kendrew RFdiffusion Pipeline — v9 (Strict UID Enforcement)
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
# (PyTorch and DGL wheels are notorious for carrying UID 995210)
RUN python3 -m pip install --no-cache-dir \
    torch==2.4.1 torchvision==0.19.1 \
    --index-url https://download.pytorch.org/whl/cu118 && \
    python3 -m pip install --no-cache-dir torchdata==0.9.0 && \
    python3 -m pip install --no-cache-dir dgl -f https://data.dgl.ai/wheels/torch-2.4/cu118/repo.html && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- RFdiffusion + SE3Transformer ----------
RUN git clone --depth 1 https://github.com/RosettaCommons/RFdiffusion.git /opt/rfdiffusion && \
    cd /opt/rfdiffusion/env/SE3Transformer && \
    python3 -m pip install --no-cache-dir -r requirements.txt && \
    python3 setup.py install && \
    cd /opt/rfdiffusion && \
    python3 -m pip install --no-cache-dir -e . && \
    python3 -m pip install --no-cache-dir hydra-core omegaconf pyrsistent e3nn opt_einsum && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- RFdiffusion weights ----------
RUN mkdir -p $MODELS_DIR && \
    wget -q http://files.ipd.uw.edu/pub/RFdiffusion/6f5902ac237024bdd0c176cb93063dc4/Base_ckpt.pt \
         -O $MODELS_DIR/Base_ckpt.pt && \
    wget -q http://files.ipd.uw.edu/pub/RFdiffusion/e29311f6f1bf1af907f9ef9f44b8328b/Complex_base_ckpt.pt \
         -O $MODELS_DIR/Complex_base_ckpt.pt && \
    wget -q http://files.ipd.uw.edu/pub/RFdiffusion/5532d2e1f3a4738decd58b19d633b3c3/ActiveSite_ckpt.pt \
         -O $MODELS_DIR/ActiveSite_ckpt.pt && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- ProteinMPNN + weights ----------
RUN git clone --depth 1 https://github.com/dauparas/ProteinMPNN.git /opt/ProteinMPNN && \
    mkdir -p /opt/ProteinMPNN/vanilla_model_weights && \
    wget -q https://github.com/dauparas/ProteinMPNN/raw/main/vanilla_model_weights/v_48_020.pt \
         -O /opt/ProteinMPNN/vanilla_model_weights/v_48_020.pt && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- ColabFold + AF2 weights ----------
RUN python3 -m pip install --no-cache-dir "colabfold[alphafold]==1.5.5" && \
    python3 -c "from colabfold.download import download_alphafold_params; download_alphafold_params('alphafold2_multimer_v3')" && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Python utilities ----------
RUN python3 -m pip install --no-cache-dir requests biopython pandas && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Pipeline script ----------
COPY run_pipeline.py /opt/run_pipeline.py

WORKDIR /opt
CMD ["python3", "/opt/run_pipeline.py"]
