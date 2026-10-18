# Existing DGX Spark-compatible NVIDIA image, pinned to its local digest.
ARG BASE_IMAGE=nvcr.io/nvidia/pytorch:25.11-py3@sha256:417cbf33f87b5378849df37983552cd1f8bc8b62fe1ceabe004de816a55dff21
FROM ${BASE_IMAGE}

ARG APP_UID=1000
ARG APP_GID=1000
ENV VIRTUAL_ENV=/opt/quant-venv \
    PATH=/opt/quant-venv/bin:${PATH} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/quant-matplotlib \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    QUANT_MAIL_MODE=simulate

# Preserve NVIDIA's PyTorch/CUDA/NumPy stack in an overlay environment.
COPY requirements-container.txt /opt/quant-tools/requirements-container.txt
RUN python -m venv --system-site-packages "$VIRTUAL_ENV" \
    && "$VIRTUAL_ENV/bin/python" -m pip install --no-cache-dir --no-deps --no-index -r /opt/quant-tools/requirements-container.txt \
    && mkdir -p /workspace/quant_lab /home/quant \
    && chown ${APP_UID}:${APP_GID} /workspace/quant_lab /home/quant

WORKDIR /workspace/quant_lab
COPY --chown=${APP_UID}:${APP_GID} *.py requirements*.txt watchlist.json ./
COPY --chown=${APP_UID}:${APP_GID} tests/ ./tests/
COPY --chown=${APP_UID}:${APP_GID} scripts/ ./scripts/
COPY --chown=${APP_UID}:${APP_GID} config/ ./config/
COPY --chown=${APP_UID}:${APP_GID} docs/ ./docs/
COPY scripts/container_entrypoint.sh /opt/quant-tools/container_entrypoint.sh
RUN chmod 755 /opt/quant-tools/container_entrypoint.sh
ENV HOME=/home/quant
USER ${APP_UID}:${APP_GID}
# NVIDIA's driver/device setup runs before the project entrypoint.
ENTRYPOINT ["/opt/nvidia/nvidia_entrypoint.sh", "/opt/quant-tools/container_entrypoint.sh"]
CMD ["python", "run.py"]
