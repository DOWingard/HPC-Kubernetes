# A "bare-metal" compute node: full systemd as PID 1 managing munge and slurmd
# as ordinary services, so the node is administered exactly like a physical
# host (systemctl, journalctl, unit drop-ins, cgroup delegation).
ARG SLURM_IMAGE=hpclab/slurm:0.1.0
FROM ${SLURM_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends systemd systemd-sysv dbus \
 && rm -rf /var/lib/apt/lists/* \
 # Units that make no sense without real hardware, consoles or a kernel to manage.
 && systemctl mask \
      getty.target console-getty.service systemd-udevd.service systemd-udev-trigger.service \
      systemd-modules-load.service sys-kernel-debug.mount sys-kernel-tracing.mount \
      systemd-remount-fs.service systemd-firstboot.service systemd-logind.service \
      slurmctld.service slurmdbd.service \
 && rm -f /etc/machine-id && touch /etc/machine-id

COPY infra/baremetal/node-init /opt/hpc/bin/node-init
COPY infra/baremetal/systemd/hpc-node-init.service /etc/systemd/system/
COPY infra/baremetal/systemd/munge.service.d/ /etc/systemd/system/munge.service.d/
COPY infra/baremetal/systemd/slurmd.service.d/ /etc/systemd/system/slurmd.service.d/
COPY infra/baremetal/slurmd.default /etc/default/slurmd

RUN chmod 0755 /opt/hpc/bin/node-init \
 && systemctl enable hpc-node-init.service munge.service slurmd.service

STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
