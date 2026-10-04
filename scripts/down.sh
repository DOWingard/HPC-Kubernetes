#!/usr/bin/env bash
# Tears the lab down. With --purge also deletes .data (shared filesystem and
# kubeconfig); files there are owned by container UIDs, so removal runs in a container.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/scripts/lib/common.sh"

PURGE=false
[[ ${1:-} == --purge ]] && PURGE=true

if docker inspect bm-node-01 >/dev/null 2>&1; then
  # Compose validates required variables even on `down`; dummies are fine here.
  MUNGE_KEY_B64=x SLURMCTLD_ADDR=0.0.0.0 run_step baremetal "${COMPOSE[@]}" down --remove-orphans
fi
if k3d cluster get "$CLUSTER" >/dev/null 2>&1; then
  run_step k3d k3d cluster delete "$CLUSTER"
fi
if docker network inspect "$DOCKER_NET" >/dev/null 2>&1; then
  docker network rm "$DOCKER_NET" >/dev/null || log warn network "could not remove $DOCKER_NET (still in use?)"
fi
if $PURGE && [[ -d $ROOT/.data ]]; then
  run_step purge docker run --rm -v "$ROOT/.data:/data" hpclab/slurm:0.1.0 sh -c 'rm -rf /data/* /data/.[!.]*'
  rmdir "$ROOT/.data" 2>/dev/null || true
fi
log info down "lab removed (purge=$PURGE)"
