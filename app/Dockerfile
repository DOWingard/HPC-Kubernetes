# The portal shells out to the Slurm CLI as the requesting tenant, so it builds
# on the cluster's base image to share its Slurm version, munge and UID map.
ARG SLURM_IMAGE=hpclab/slurm:0.1.0
FROM ${SLURM_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3-venv \
 && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt /opt/portal/requirements.txt
RUN python3 -m venv /opt/portal/venv \
 && /opt/portal/venv/bin/pip install --no-cache-dir -r /opt/portal/requirements.txt

COPY app/hpc_portal /opt/portal/hpc_portal
WORKDIR /opt/portal
ENV PATH=/opt/portal/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000 9341
CMD ["uvicorn", "--factory", "hpc_portal.api:main", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
