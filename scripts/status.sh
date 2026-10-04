#!/usr/bin/env bash
# One-screen view of the lab across all three layers.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/scripts/lib/common.sh"

k3d cluster get "$CLUSTER" >/dev/null 2>&1 || die status "cluster $CLUSTER does not exist; run 'make up'"
ctl() { kubectl -n hpc exec slurmctld-0 -c slurmctld -- "$@"; }

echo "== Slurm nodes"
ctl sinfo -N -o '%12N %11P %10T %4c %14o %E'
echo
echo "== Slurm queue"
ctl squeue -o '%6i %8u %11P %12j %11T %6C %10M %R'
echo
echo "== Kubernetes pods (hpc)"
kubectl -n hpc get pods -o wide --no-headers | awk '{printf "%-36s %-6s %-18s %s\n", $1, $2, $3, $6}'
echo
echo "== Bare-metal node (systemd)"
if docker inspect bm-node-01 >/dev/null 2>&1; then
  docker exec bm-node-01 systemctl --no-pager --plain list-units hpc-node-init.service munge.service slurmd.service \
    | sed -n '1,4p'
else
  echo "bm-node-01 is not running"
fi
