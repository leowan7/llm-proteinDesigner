# Kendrew RFantibody Pipeline — v1 (Antibody CDR Loop Design)
FROM runpod/base:0.6.2-cuda11.8.0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/opt/rfantibody" \
    RFANTIBODY_DIR=/opt/rfantibody \
    WEIGHTS_DIR=/opt/rfantibody/weights

# ---------- System deps ----------
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget git cmake python3-pip python3-dev build-essential \
    libhdf5-dev && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    rm -rf /var/lib/apt/lists/* && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- uv package manager ----------
RUN python3 -m pip install --no-cache-dir uv==0.6.6 && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- PyTorch 2.2.2+cu118 ----------
RUN python3 -m pip install --no-cache-dir \
    torch==2.2.2 torchvision==0.17.2 \
    --index-url https://download.pytorch.org/whl/cu118 && \
    python3 -m pip install --no-cache-dir torchdata==0.7.1 && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- RFantibody repo + SE3-Transformers ----------
RUN git clone --depth 1 https://github.com/RosettaCommons/RFantibody.git $RFANTIBODY_DIR && \
    cd $RFANTIBODY_DIR && \
    python3 -m pip install --no-cache-dir -e . && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- SE3-Transformers (shared dep with RFdiffusion) ----------
# RFantibody depends on SE3-Transformers for equivariant attention layers.
# Install from the RFdiffusion repo which bundles a compatible version.
RUN git clone --depth 1 https://github.com/RosettaCommons/RFdiffusion.git /tmp/rfdiffusion && \
    cd /tmp/rfdiffusion/env/SE3Transformer && \
    python3 -m pip install --no-cache-dir -r requirements.txt && \
    python3 setup.py install && \
    rm -rf /tmp/rfdiffusion && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Additional Python deps ----------
RUN python3 -m pip install --no-cache-dir \
    hydra-core omegaconf pyrsistent e3nn opt_einsum \
    requests biopython pandas && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- JAX 0.4.23 + cuDNN 8 (for RF2 validation) ----------
RUN python3 -m pip install --no-cache-dir \
    "nvidia-cudnn-cu11<9.0" \
    "jax[cuda11_pip]==0.4.23" \
    -f https://storage.googleapis.com/jax-releases/jax_cuda_releases.html && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- DGL (graph neural network framework) ----------
RUN python3 -m pip install --no-cache-dir \
    dgl -f https://data.dgl.ai/wheels/torch-2.2/cu118/repo.html && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Model weights ----------
# Downloads RFdiffusion antibody weights, AbMPNN weights, RF2 antibody weights
RUN cd $RFANTIBODY_DIR && \
    bash include/download_weights.sh && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- RF2 weight path fix ----------
# Ensure RF2 config points to the correct downloaded weight location.
RUN if [ -f "$RFANTIBODY_DIR/src/rfantibody/rf2/config/base.yml" ]; then \
        sed -i "s|model_weights:.*|model_weights: $WEIGHTS_DIR/RF2_ab.pt|g" \
            $RFANTIBODY_DIR/src/rfantibody/rf2/config/base.yml; \
    fi && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Pipeline script ----------
COPY run_pipeline.py /opt/run_pipeline.py

WORKDIR /opt
CMD ["python3", "/opt/run_pipeline.py"]
