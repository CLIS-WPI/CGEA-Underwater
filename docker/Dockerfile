# CGEA Underwater Experimental Platform
# GPU-enabled: Ubuntu 22.04 + Python 3.12 + PyTorch CUDA + Sionna 2.1 + aubellhop
#
# NOTE: Spec requested Python 3.11; aubellhop requires Python >=3.12.
# We use Python 3.12 so Sionna 2.1.x and aubellhop both install.
# Sionna RT may install transitively with `sionna` but MUST NOT be used for
# underwater acoustic propagation — Bellhop/AUBellhop owns that role.

FROM nvidia/cuda:12.6.3-cudnn-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CUDA_HOME=/usr/local/cuda \
    PATH=/usr/local/cuda/bin:$PATH \
    LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH \
    CGEA_ROOT=/workspace/cgea-underwater \
    PYTHONPATH=/workspace/cgea-underwater/src

RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common \
        ca-certificates \
        curl \
        wget \
        git \
        build-essential \
        gfortran \
        libgfortran5 \
        libopenblas-dev \
        liblapack-dev \
        pkg-config \
        && add-apt-repository -y ppa:deadsnakes/ppa \
        && apt-get update && apt-get install -y --no-install-recommends \
        python3.12 \
        python3.12-dev \
        python3.12-venv \
        && curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12 \
        && ln -sf /usr/bin/python3.12 /usr/local/bin/python \
        && ln -sf /usr/bin/python3.12 /usr/local/bin/python3 \
        && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/cgea-underwater

# PyTorch 2.9+ required by Sionna 2.1
RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install \
        torch==2.9.1 \
        torchvision \
        torchaudio \
        --index-url https://download.pytorch.org/whl/cu126

COPY docker/requirements.txt /tmp/requirements.txt
RUN python -m pip install -r /tmp/requirements.txt

COPY pyproject.toml README.md /workspace/cgea-underwater/
COPY src /workspace/cgea-underwater/src
COPY configs /workspace/cgea-underwater/configs
COPY tests /workspace/cgea-underwater/tests
COPY scripts /workspace/cgea-underwater/scripts

RUN python -m pip install -e .

COPY docker/verify_env.py /usr/local/bin/verify_cgea_env.py
RUN chmod +x /usr/local/bin/verify_cgea_env.py

CMD ["bash"]
