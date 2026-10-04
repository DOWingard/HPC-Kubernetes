#!/usr/bin/env bash
# Guided demo: submit jobs through the REST API as two tenants and watch Slurm
# run them on the bare-metal node and on Kubernetes burst pods it creates on demand.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/scripts/lib/common.sh"
load_env

API=${PORTAL_URL:-http://localhost:30080}
token_for() { tr ',' '\n' <<<"$PORTAL_API_TOKENS" | sed -n "s/^$1://p"; }
ALICE=$(token_for alice)
BOB=$(token_for bob)
[[ -n $ALICE && -n $BOB ]] || die demo "PORTAL_API_TOKENS must contain tokens for alice and bob"

say() { printf '\n\033[1;36m# %s\033[0m\n' "$*"; }
run() { printf '\033[2m$ %s\033[0m\n' "$*"; eval "$@"; }
pause() { sleep "${DEMO_PAUSE:-2}"; }

submit() {
  local token=$1 body=$2 resp
  resp=$(curl -fsS -X POST "$API/submit-job" -H "Authorization: Bearer $token" \
    -H 'Content-Type: application/json' -d "$body") || die demo "submit failed: $body"
  echo "$resp" | jq -c . >&2
  jq -r .job_id <<<"$resp"
}

curl -fsS "$API/readyz" >/dev/null || die demo "portal not ready at $API; run 'make up' first"

say "Cluster capacity: bare-metal partition is online, burst partition is powered down (idle~)"
run "curl -s $API/cluster -H \"Authorization: Bearer \$ALICE\" | jq -c '.partitions'"
run "kubectl -n hpc exec slurmctld-0 -c slurmctld -- sinfo"
pause

# Each job burns its full CPU allocation so the Grafana panels have something to show.
BURN='for i in $(seq ${SLURM_CPUS_ON_NODE}); do timeout 75 sh -c "while :; do :; done" & done; wait; echo "cgroup: $(cat /proc/self/cgroup)"'

say "alice (physics) submits a 4-CPU job to the bare-metal partition"
J1=$(submit "$ALICE" "$(jq -nc --arg c "$BURN" '{name:"md-sim",partition:"local-node",cpus:4,mem_mb:512,command:$c}')")
say "bob (chemistry) submits two 2-CPU jobs to the k8s-burst partition"
J2=$(submit "$BOB" "$(jq -nc --arg c "$BURN" '{name:"dft-a",partition:"k8s-burst",cpus:2,command:$c}')")
J3=$(submit "$BOB" "$(jq -nc --arg c "$BURN" '{name:"dft-b",partition:"k8s-burst",cpus:2,command:$c}')")
pause

say "Slurm runs ResumeProgram: one Kubernetes pod per burst node it needs"
for _ in $(seq 1 12); do
  pods=$(kubectl -n hpc get pods -l app.kubernetes.io/name=slurmd-burst --no-headers 2>/dev/null | grep -c Running || true)
  ((pods >= 2)) && break
  sleep 3
done
run "kubectl -n hpc get pods -l app.kubernetes.io/name=slurmd-burst -o wide"
run "kubectl -n hpc logs slurmctld-0 -c slurmctld --tail=200 | grep k8s-power | tail -4 | jq -r .message"
sleep 10
run "kubectl -n hpc exec slurmctld-0 -c slurmctld -- squeue -o '%.5i %.6u %.10P %.8j %.9T %.4C %R'"

say "Tenant isolation: bob cannot see alice's job, and POSIX permissions keep him out of her home"
run "curl -s -o /dev/null -w '%{http_code}\n' $API/job-status/$J1 -H \"Authorization: Bearer \$BOB\""
run "docker exec bm-node-01 runuser -u bob -- ls /shared/home/alice 2>&1 || true"

say "Live metrics: open Grafana -> 'HPC Lab - Slurm & Kubernetes CPU allocation' (http://localhost:30300)"
say "Waiting for the jobs to finish (~75s)"
for j in "$J1" "$J2" "$J3"; do
  tok=$ALICE; [[ $j == "$J1" ]] || tok=$BOB
  for _ in $(seq 1 60); do
    state=$(curl -fsS "$API/job-status/$j" -H "Authorization: Bearer $tok" | jq -r .state)
    [[ $state =~ ^(COMPLETED|FAILED|CANCELLED|TIMEOUT|OUT_OF_MEMORY)$ ]] && break
    sleep 3
  done
done
run "curl -s $API/job-status/$J2 -H \"Authorization: Bearer \$BOB\" | jq -c '{job_id,state,nodes,cpus,exit_code,source}'"
run "docker exec bm-node-01 cat /shared/home/bob/slurm-$J2.out"

say "After SuspendTime (90s idle) Slurm runs SuspendProgram and the burst pods are deleted"
say "Watch it with: watch kubectl -n hpc get pods  (or make logs)"
