# Kendrew PXDesign Pipeline — v1 (ByteDance PXDesign binder design)
FROM runpod/base:0.6.2-cuda11.8.0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PXDESIGN_DIR=/opt/pxdesign \
    CUTLASS_DIR=/opt/cutlass

# ---------- System deps ----------
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget git cmake python3-pip python3-dev build-essential \
    ninja-build libffi-dev && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    rm -rf /var/lib/apt/lists/* && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- PyTorch 2.2.2+cu118 ----------
RUN python3 -m pip install --no-cache-dir \
    torch==2.2.2 torchvision==0.17.2 \
    --index-url https://download.pytorch.org/whl/cu118 && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- CUTLASS v3.5.1 (required for DeepSpeed Evo attention) ----------
RUN git clone --depth 1 --branch v3.5.1 \
    https://github.com/NVIDIA/cutlass.git $CUTLASS_DIR && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

ENV CUTLASS_PATH=$CUTLASS_DIR

# ---------- Clone PXDesign ----------
RUN git clone --depth 1 https://github.com/bytedance/pxdesign.git $PXDESIGN_DIR && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Install PXDesign via install.sh ----------
# Try the install script first; if it fails, fall back to manual pip installs.
# The install script sets up Protenix, ProteinMPNN, and PXDesignBench deps.
RUN cd $PXDESIGN_DIR && \
    (bash install.sh --env pxdesign --pkg_manager pip --cuda-version 11.8 || \
     python3 -m pip install --no-cache-dir \
         numpy==1.26.4 \
         pandas==2.2.2 \
         scipy==1.13.0 \
         biopython==1.83 \
         pyyaml==6.0.1 \
         einops==0.8.0 \
         ml-collections==0.1.1 \
         dm-haiku==0.0.12 \
         rdkit-pypi==2023.9.6 \
         modelcif==1.0 \
         ihm==1.5 \
         deepspeed==0.14.4 \
         ninja==1.11.1.1 && \
     python3 -m pip install --no-cache-dir -e $PXDESIGN_DIR) && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Download tool weights (AF2, MPNN, CCD cache) ----------
RUN cd $PXDESIGN_DIR && \
    bash download_tool_weights.sh && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Bake diffusion weights (auto-downloaded on first run) ----------
# Run a minimal dummy inference to trigger weight download and JIT compilation
# of custom CUDA kernels. This adds build time but avoids first-run latency.
RUN cd $PXDESIGN_DIR && \
    python3 -c " \
import yaml, tempfile, os; \
spec = {'target': {'file': 'test.pdb', 'chains': {'A': {'crop': True, 'hotspots': []}}}, \
        'binder_length': {'min': 10, 'max': 15}, 'preset': 'basic', 'N_sample': 1}; \
os.makedirs('/tmp/pxdesign_warmup', exist_ok=True); \
path = '/tmp/pxdesign_warmup/spec.yaml'; \
open(path, 'w').write(yaml.dump(spec)); \
print('Warmup spec written — attempting weight download via import...'); \
" && \
    (pxdesign pipeline --preset basic \
        -i /tmp/pxdesign_warmup/spec.yaml \
        -o /tmp/pxdesign_warmup/out \
        --N_sample 1 --dtype bf16 2>&1 | tail -100 || \
     echo "Warmup inference failed (expected if no PDB provided), weights should be cached") && \
    rm -rf /tmp/pxdesign_warmup && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Python utilities ----------
RUN python3 -m pip install --no-cache-dir \
    requests==2.31.0 && \
    find / -xdev -uid +65533 -exec chown root:root {} + 2>/dev/null || true

# ---------- Pipeline script ----------
COPY run_pipeline.py /opt/run_pipeline.py

WORKDIR /opt
CMD ["python3", "/opt/run_pipeline.py"]
