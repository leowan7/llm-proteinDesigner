# Kendrew RFantibody Pipeline — v2
# RFantibody uses uv for environment management and registers CLI entry points:
#   rfdiffusion, proteinmpnn, rf2, qvls, qvextract, qvscorefile
# CUDA 11.8 is correct — confirmed by official RFantibody Dockerfile.
# Devel image required for SE3-Transformer and DeepSpeed CUDA kernel compilation.
FROM nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    RFANTIBODY_DIR=/opt/rfantibody \
    WEIGHTS_DIR=/opt/rfantibody/weights

# ---------- System deps ----------
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget git cmake python3-pip python3-dev build-essential \
    curl libhdf5-dev && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    rm -rf /var/lib/apt/lists/*

# ---------- uv package manager ----------
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    ln -sf /root/.local/bin/uv /usr/local/bin/uv

# ---------- Clone RFantibody ----------
RUN git clone --depth 1 https://github.com/RosettaCommons/RFantibody.git $RFANTIBODY_DIR

# ---------- Install via uv sync (registers CLI entry points) ----------
# uv sync reads pyproject.toml, installs all deps (PyTorch, JAX, SE3-Transformer,
# DGL, etc.) and creates .venv with CLI tools: rfdiffusion, proteinmpnn, rf2,
# qvls, qvextract, qvscorefile, qvfrompdbs, qvrename, qvsplit
RUN cd $RFANTIBODY_DIR && \
    uv sync && \
    echo "=== Verifying CLI entry points ===" && \
    .venv/bin/rfdiffusion --help > /dev/null 2>&1 && echo "rfdiffusion OK" && \
    .venv/bin/proteinmpnn --help > /dev/null 2>&1 && echo "proteinmpnn OK" && \
    .venv/bin/rf2 --help > /dev/null 2>&1 && echo "rf2 OK" && \
    echo "All CLI tools verified"

# Make venv binaries available globally
ENV PATH="$RFANTIBODY_DIR/.venv/bin:$PATH"

# ---------- Download model weights (~10 GB) ----------
RUN cd $RFANTIBODY_DIR && \
    bash include/download_weights.sh && \
    echo "=== Weight validation ===" && \
    test -f weights/RF2_ab.pt && echo "RF2_ab.pt OK" && \
    ls -lh weights/ && \
    echo "Weight download complete"

# ---------- Validate bundled framework PDBs ----------
RUN test -f $RFANTIBODY_DIR/scripts/examples/example_inputs/h-NbBCII10.pdb && \
    echo "VHH framework OK" && \
    test -f $RFANTIBODY_DIR/scripts/examples/example_inputs/hu-4D5-8_Fv.pdb && \
    echo "scFv framework OK"

# ---------- Pipeline utilities ----------
# biopython is required by pipeline_normalize.py for Bug-9 PDB sanitization.
RUN pip install --no-cache-dir requests biopython

# ---------- Pipeline script ----------
COPY run_pipeline.py /opt/run_pipeline.py

WORKDIR /opt
CMD ["python3", "/opt/run_pipeline.py"]
