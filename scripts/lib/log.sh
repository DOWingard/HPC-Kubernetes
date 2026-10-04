# Structured JSON logging for shell scripts. Source this file; do not execute it.
# Usage: log <level> <context> <message> [error]

_json_escape() {
  local s=${1//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\t'/\\t}
  printf '%s' "$s"
}

log() {
  local level=$1 context=$2 message=$3 error=${4:-}
  local line
  line=$(printf '{"timestamp":"%s","level":"%s","context":"%s","message":"%s"' \
    "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)" "$level" "$(_json_escape "$context")" "$(_json_escape "$message")")
  if [[ -n $error ]]; then
    line+=$(printf ',"error":"%s"' "$(_json_escape "$error")")
  fi
  printf '%s}\n' "$line" >&2
}

die() {
  log error "$1" "$2" "${3:-}"
  exit 1
}

# Run a command, log its duration, and fail with context if it exits non-zero.
run_step() {
  local context=$1; shift
  local start end rc
  start=$(date +%s.%N)
  log debug "$context" "exec: $*"
  if "$@"; then
    end=$(date +%s.%N)
    log info "$context" "ok ($(awk -v a="$start" -v b="$end" 'BEGIN{printf "%.1f", b-a}')s): $1"
  else
    rc=$?
    die "$context" "command failed (exit $rc): $*" "exit_code=$rc"
  fi
}
