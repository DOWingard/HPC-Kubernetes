#!/usr/bin/env bash
# Brings the whole lab up idempotently: Docker network, k3d cluster, images,
# secrets, monitoring + Argo CD, Slurm control plane, portal, and finally the
# bare-metal node. Safe to re-run after a partial failure.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/scripts/lib/common.sh"

STARTED=$(date +%s)
IMAGES_CHANGED=false
require_tools
load_env
mkdir -p "$HPC_SHARED_DIR" "$ROOT/.data"

ensure_network() {
  if docker network inspect "$DOCKER_NET" >/dev/null 2>&1; then
    local subnet
    subnet=$(docker network inspect -f '{{(index .IPAM.Config 0).Subnet}}' "$DOCKER_NET")
    [[ $subnet == "$DOCKER_SUBNET" ]] \
      || die network "existing network $DOCKER_NET has subnet $subnet, expected $DOCKER_SUBNET; remove it with 'make down'"
    log info network "docker network $DOCKER_NET present ($subnet)"
  else
    run_step network docker network create --driver bridge \
      --subnet "$DOCKER_SUBNET" --ip-range "$DOCKER_IP_RANGE" --gateway 172.28.0.1 "$DOCKER_NET"
  fi
}

ensure_cluster() {
  if k3d cluster get "$CLUSTER" >/dev/null 2>&1; then
    log info k3d "cluster $CLUSTER already exists"
    if ! docker inspect -f '{{.State.Running}}' "$K3D_SERVER" 2>/dev/null | grep -q true; then
      run_step k3d k3d cluster start "$CLUSTER" --wait
    fi
  else
    local cfg="$ROOT/.data/k3d-cluster.yaml"
    K3S_IMAGE=$K3S_IMAGE envsubst '${K3S_IMAGE} ${HPC_SHARED_DIR}' <"$ROOT/infra/k3d/cluster.yaml" >"$cfg"
    run_step k3d k3d cluster create --config "$cfg"
  fi
  k3d kubeconfig get "$CLUSTER" >"$KUBECONFIG"
  chmod 600 "$KUBECONFIG"
  run_step k3d kubectl wait --for=condition=Ready node --all --timeout=120s
}

import_images() {
  local img
  for img in "${IMAGES[@]}"; do
    docker image inspect "$img" >/dev/null 2>&1 || die images "image $img not built; run 'make images'"
  done
  local stamp="$ROOT/.data/images.ids" ids
  ids=$(docker image inspect -f '{{.Id}}' "${IMAGES[@]}")
  if [[ -f $stamp && $(cat "$stamp") == "$ids" ]]; then
    log info images "images unchanged since last import"
    IMAGES_CHANGED=false
    return
  fi
  run_step images k3d image import --cluster "$CLUSTER" "${IMAGES[@]}"
  echo "$ids" >"$stamp"
  IMAGES_CHANGED=true
}

restart_if_images_changed() {
  # Tags are fixed (0.1.0), so a rebuilt image only reaches running pods via a restart.
  [[ $IMAGES_CHANGED == true && -n $PREEXISTING_WORKLOADS ]] || return 0
  local workloads=$PREEXISTING_WORKLOADS
  log info images "images changed; restarting hpc workloads: $(echo $workloads)"
  # shellcheck disable=SC2086
  kubectl -n hpc rollout restart $workloads >/dev/null || die images "rollout restart failed"
  if docker inspect bm-node-01 >/dev/null 2>&1; then
    docker rm -f bm-node-01 >/dev/null && log info images "removed stale bm-node-01 container"
  fi
}

apply_secret() {
  # Rendered client-side and applied, so re-runs update rather than fail.
  local ns=$1 name=$2; shift 2
  kubectl create secret generic "$name" -n "$ns" "$@" --dry-run=client -o yaml | kubectl apply -f - >/dev/null \
    || die secrets "failed to apply secret $ns/$name"
  log info secrets "secret $ns/$name applied"
}

create_secrets() {
  local ns
  for ns in hpc monitoring argocd; do
    kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  done
  local keyfile
  keyfile=$(mktemp)
  trap 'rm -f "$keyfile"' RETURN
  base64 -d <<<"$MUNGE_KEY_B64" >"$keyfile" || die secrets "MUNGE_KEY_B64 is not valid base64"
  apply_secret hpc munge-key --from-file=munge.key="$keyfile"
  apply_secret hpc slurm-db --from-literal=root-password="$MYSQL_ROOT_PASSWORD" \
    --from-literal=slurmdbd-password="$SLURMDBD_DB_PASSWORD"
  apply_secret hpc portal-tokens --from-literal=tokens="$PORTAL_API_TOKENS"
  apply_secret monitoring grafana-admin --from-literal=admin-user=admin \
    --from-literal=admin-password="$GRAFANA_ADMIN_PASSWORD"
}

install_platform() {
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update >/dev/null
  helm repo add argo https://argoproj.github.io/argo-helm --force-update >/dev/null
  run_step helm helm repo update prometheus-community argo
  run_step helm:kps helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
    --version "$KPS_CHART_VERSION" -n monitoring \
    -f "$ROOT/k8s/platform/kube-prometheus-stack-values.yaml" --wait --timeout 10m
  run_step helm:argocd helm upgrade --install argocd argo/argo-cd \
    --version "$ARGOCD_CHART_VERSION" -n argocd \
    -f "$ROOT/k8s/platform/argocd-values.yaml" --wait --timeout 10m
}

deploy_workloads() {
  if [[ -n ${GITOPS_REPO_URL:-} ]]; then
    log info deploy "GitOps mode: Argo CD will sync k8s/apps from $GITOPS_REPO_URL@${GITOPS_REVISION:-main}"
    export GITOPS_REVISION=${GITOPS_REVISION:-main}
    local app
    for app in "$ROOT"/k8s/argocd/applications/*.yaml; do
      envsubst '${GITOPS_REPO_URL} ${GITOPS_REVISION}' <"$app" | kubectl apply -f - >/dev/null \
        || die deploy "failed to apply Argo CD application $(basename "$app")"
      log info deploy "applied $(basename "$app")"
    done
    for app in slurm portal; do
      wait_for "argocd app hpclab-$app synced" 600 \
        bash -c "kubectl -n argocd get application hpclab-$app -o jsonpath='{.status.sync.status}' | grep -q Synced"
    done
  else
    log warn deploy "GITOPS_REPO_URL is empty; applying k8s/apps directly with kubectl (Argo CD stays idle)"
    # Jobs are immutable; deleting first lets the idempotent bootstrap re-run.
    kubectl -n hpc delete job slurm-accounting-bootstrap --ignore-not-found >/dev/null
    local app
    for app in slurm portal observability; do
      run_step "deploy:$app" kubectl apply -k "$ROOT/k8s/apps/$app"
    done
  fi
}

wait_for() {
  local what=$1 timeout=$2; shift 2
  local start=$SECONDS
  until "$@" >/dev/null 2>&1; do
    ((SECONDS - start < timeout)) || die wait "timed out after ${timeout}s waiting for: $what"
    if (((SECONDS - start) % 30 == 0)); then log info wait "still waiting for: $what ($((SECONDS - start))s)"; fi
    sleep 3
  done
  log info wait "ready: $what ($((SECONDS - start))s)"
}

wait_for_control_plane() {
  wait_for "statefulset/mariadb" 300 kubectl -n hpc rollout status sts/mariadb --timeout=5s
  wait_for "statefulset/slurmdbd" 300 kubectl -n hpc rollout status sts/slurmdbd --timeout=5s
  wait_for "statefulset/slurmctld" 300 kubectl -n hpc rollout status sts/slurmctld --timeout=5s
  wait_for "accounting bootstrap job" 300 kubectl -n hpc wait --for=condition=complete job/slurm-accounting-bootstrap --timeout=5s
  wait_for "deployment/portal" 300 kubectl -n hpc rollout status deploy/portal --timeout=5s
  wait_for "deployment/slurm-exporter" 300 kubectl -n hpc rollout status deploy/slurm-exporter --timeout=5s
}

start_baremetal() {
  local ip
  ip=$(k3d_server_ip)
  [[ -n $ip ]] || die baremetal "could not determine $K3D_SERVER address on $DOCKER_NET"
  log info baremetal "controller reachable for bare-metal node at $ip:6817 (hostPort)"
  SLURMCTLD_ADDR=$ip run_step baremetal "${COMPOSE[@]}" up -d
  wait_for "bm-node-01 registered with slurmctld" 180 bash -c \
    "kubectl -n hpc exec slurmctld-0 -c slurmctld -- sinfo -h -n bm-node-01 -o %T | grep -Eq '^(idle|mixed|allocated)'"
}

ensure_network
ensure_cluster
import_images
create_secrets
install_platform
PREEXISTING_WORKLOADS=$(kubectl -n hpc get statefulset,deployment -o name 2>/dev/null | grep -v mariadb || true)
deploy_workloads
restart_if_images_changed
wait_for_control_plane
start_baremetal

log info up "lab is up in $(($(date +%s) - STARTED))s"
cat >&2 <<EOF

  Job portal API : http://localhost:30080/docs
  Grafana        : http://localhost:30300   (admin / GRAFANA_ADMIN_PASSWORD from .env)
  Prometheus     : http://localhost:30090
  Argo CD        : http://localhost:30808   (admin / see 'make argocd-password')
  kubectl        : export KUBECONFIG=$KUBECONFIG
  Slurm CLI      : make slurm CMD="sinfo"

EOF
