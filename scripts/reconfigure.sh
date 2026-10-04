#!/usr/bin/env bash
# Rolls a slurm.conf / cgroup.conf change out to the running cluster the way an
# admin would on real hardware: update the config source, then `scontrol
# reconfigure`, which also pushes the new files to configless nodes.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/scripts/lib/common.sh"

run_step reconfigure kubectl apply -k "$ROOT/k8s/apps/slurm"
want=$(sha256sum <"$ROOT/k8s/apps/slurm/config/slurm.conf" | cut -d' ' -f1)
# The kubelet syncs ConfigMap volumes periodically (up to ~1 minute).
for ((i = 1; i <= 40; i++)); do
  have=$(kubectl -n hpc exec slurmctld-0 -c slurmctld -- sha256sum /etc/slurm/slurm.conf | cut -d' ' -f1)
  [[ $have == "$want" ]] && break
  ((i == 40)) && die reconfigure "slurmctld still sees the old slurm.conf after $((i * 3))s"
  ((i % 5 == 0)) && log info reconfigure "waiting for ConfigMap to propagate ($((i * 3))s)"
  sleep 3
done
run_step reconfigure kubectl -n hpc exec slurmctld-0 -c slurmctld -- scontrol reconfigure
log info reconfigure "slurmctld reconfigured; configless nodes received the new files"
