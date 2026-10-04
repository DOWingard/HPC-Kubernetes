# Loads ./.env and fails fast if any required secret is missing or empty.
# Source after log.sh; expects $ROOT to be set.

load_env() {
  local env_file="$ROOT/.env"
  [[ -f $env_file ]] || die env "missing $env_file; run 'make env' first"
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
  local missing=()
  for var in MUNGE_KEY_B64 MYSQL_ROOT_PASSWORD SLURMDBD_DB_PASSWORD GRAFANA_ADMIN_PASSWORD PORTAL_API_TOKENS; do
    [[ -n ${!var:-} ]] || missing+=("$var")
  done
  ((${#missing[@]} == 0)) || die env "required env vars missing or empty: ${missing[*]}"
  log debug env "environment loaded from $env_file"
}
