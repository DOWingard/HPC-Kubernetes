#!/usr/bin/env bash
# Installs pinned k3d, kubectl and helm binaries into ./.tools/bin so the lab
# never depends on (or mutates) system-wide tool versions.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/scripts/lib/log.sh"

K3D_VERSION=v5.9.0
KUBECTL_VERSION=v1.37.1
HELM_VERSION=v4.3.0

BIN="$ROOT/.tools/bin"
mkdir -p "$BIN"

case "$(uname -m)" in
  x86_64) ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) die tools "unsupported architecture: $(uname -m)" ;;
esac
OS=$(uname -s | tr '[:upper:]' '[:lower:]')

fetch() {
  local url=$1 dest=$2
  log info tools "downloading $url"
  curl -fsSL --retry 3 --retry-delay 2 -o "$dest" "$url" \
    || die tools "download failed: $url" "curl exit $?"
}

installed_version() { [[ -x $BIN/$1 ]] && "$BIN/$1" "${@:2}" 2>/dev/null || true; }

if [[ $(installed_version k3d version) != *"$K3D_VERSION"* ]]; then
  fetch "https://github.com/k3d-io/k3d/releases/download/${K3D_VERSION}/k3d-${OS}-${ARCH}" "$BIN/k3d"
  chmod +x "$BIN/k3d"
fi

if [[ $(installed_version kubectl version --client) != *"$KUBECTL_VERSION"* ]]; then
  fetch "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/${OS}/${ARCH}/kubectl" "$BIN/kubectl"
  chmod +x "$BIN/kubectl"
fi

if [[ $(installed_version helm version --short) != *"$HELM_VERSION"* ]]; then
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  fetch "https://get.helm.sh/helm-${HELM_VERSION}-${OS}-${ARCH}.tar.gz" "$tmp/helm.tgz"
  tar -xzf "$tmp/helm.tgz" -C "$tmp"
  mv "$tmp/${OS}-${ARCH}/helm" "$BIN/helm"
fi

log info tools "k3d=$("$BIN/k3d" version | head -1) kubectl=$KUBECTL_VERSION helm=$("$BIN/helm" version --short)"
