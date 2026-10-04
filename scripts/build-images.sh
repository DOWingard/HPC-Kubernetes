#!/usr/bin/env bash
# Builds the three lab images. The portal and bare-metal images layer on the
# Slurm base so every component shares one Slurm build and UID/GID map.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/scripts/lib/log.sh"
TAG=0.1.0

build() {
  local image=$1 dockerfile=$2
  run_step "build:$image" docker build --quiet -t "$image" -f "$ROOT/$dockerfile" \
    --build-arg "SLURM_IMAGE=hpclab/slurm:$TAG" "$ROOT"
}

build "hpclab/slurm:$TAG" infra/images/slurm/Dockerfile
build "hpclab/baremetal:$TAG" infra/baremetal/Dockerfile
build "hpclab/portal:$TAG" app/Dockerfile
