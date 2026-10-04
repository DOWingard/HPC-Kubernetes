# Shared base for every Slurm component (controller, dbd, burst slurmd, portal,
# bare-metal node). Building all of them from one base guarantees identical
# UIDs/GIDs for service accounts and tenants, which POSIX permissions on the
# shared filesystem depend on.
FROM ubuntu:26.04

ARG DEBIAN_FRONTEND=noninteractive

# Fixed IDs are created before the packages so their postinst scripts reuse
# them instead of allocating dynamic system IDs.
RUN groupadd --system --gid 980 munge \
 && useradd --system --uid 980 --gid munge --home-dir /nonexistent --shell /usr/sbin/nologin munge \
 && groupadd --system --gid 981 slurm \
 && useradd --system --uid 981 --gid slurm --home-dir /var/lib/slurm --shell /usr/sbin/nologin slurm \
 && groupadd --gid 3000 hpcusers \
 && groupadd --gid 3001 physics \
 && groupadd --gid 3002 chemistry \
 && useradd --uid 2001 --gid hpcusers --groups physics --home-dir /shared/home/alice --no-create-home --shell /bin/bash alice \
 && useradd --uid 2002 --gid hpcusers --groups chemistry --home-dir /shared/home/bob --no-create-home --shell /bin/bash bob

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      slurmctld slurmd slurmdbd slurm-client munge \
      libpmix2t64 python3 ca-certificates gettext-base procps iproute2 \
 && rm -rf /var/lib/apt/lists/* \
 && rm -f /etc/munge/munge.key

COPY scripts/lib/log.sh /opt/hpc/lib/log.sh
COPY infra/images/slurm/bin/ /opt/hpc/bin/
RUN chmod 0755 /opt/hpc/bin/* \
 && ln -s k8s-power /opt/hpc/bin/k8s-resume \
 && ln -s k8s-power /opt/hpc/bin/k8s-suspend \
 && ln -s k8s-power /opt/hpc/bin/k8s-resume-fail \
 && install -d -o slurm -g slurm -m 0755 /var/spool/slurmctld /var/spool/slurmd /var/log/slurm /run/slurmdbd

ENV PATH=/opt/hpc/bin:$PATH
