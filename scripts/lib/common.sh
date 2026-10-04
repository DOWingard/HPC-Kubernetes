# Shared settings for the lab scripts. Source after setting $ROOT.
source "$ROOT/scripts/lib/log.sh"
source "$ROOT/scripts/lib/env.sh"
source "$ROOT/k8s/platform/versions.env"

export PATH="$ROOT/.tools/bin:$PATH"
export KUBECONFIG="$ROOT/.data/kubeconfig"

CLUSTER=hpclab
DOCKER_NET=hpc-net
DOCKER_SUBNET=172.28.0.0/24
# Docker assigns dynamic addresses only from the lower half, keeping the
# bare-metal node's fixed 172.28.0.200 collision-free.
DOCKER_IP_RANGE=172.28.0.0/25
K3D_SERVER=k3d-${CLUSTER}-server-0
export HPC_SHARED_DIR="$ROOT/.data/shared"
IMAGE_TAG=0.1.0
IMAGES=(hpclab/slurm:$IMAGE_TAG hpclab/portal:$IMAGE_TAG)
COMPOSE=(docker compose -f "$ROOT/infra/baremetal/docker-compose.yml")

require_tools() {
  local t
  for t in docker k3d kubectl helm envsubst; do
    command -v "$t" >/dev/null || die preflight "required tool '$t' not found; run 'make tools'"
  done
  docker info >/dev/null 2>&1 || die preflight "docker daemon is not reachable"
}

k3d_server_ip() {
  docker inspect -f "{{(index .NetworkSettings.Networks \"$DOCKER_NET\").IPAddress}}" "$K3D_SERVER" 2>/dev/null
}
