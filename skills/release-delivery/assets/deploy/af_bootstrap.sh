#!/usr/bin/env bash
# Prepare a Debian or Ubuntu host for the deployment standard (ADR 0009; contracts v1 §11).
#
#   sudo ./af_bootstrap.sh install [--trigger pull|push] [--deploy-root /srv/deploy]
#                                  [--no-proxy] [--replace-caddyfile]
#   sudo ./af_bootstrap.sh check   [--trigger pull|push] [--deploy-root /srv/deploy] [--no-proxy]
#
# Run it from a checkout of a tagged framework release (this directory). `install` is
# idempotent and never replaces what it did not create: an existing Docker keeps its
# configuration and containers, and a foreign Caddyfile is left alone unless
# --replace-caddyfile is given (the old file is kept as a dated backup). `check` changes
# nothing and exits 1 when something is missing.
#
# Installs: Docker Engine with the Compose plugin and Caddy from their signed apt
# repositories (key fingerprints pinned below), cosign as a checksum-pinned binary, the
# `af` deploy user, the engine in /opt/af, /etc/af, the deploy root, and the af-agent and
# af-monitor timers. Project files (host configs, keys, placement, secrets) are added per
# project afterwards (§11 steps 3 to 7).
set -euo pipefail

DOCKER_KEY_FINGERPRINT=9DC858229FC7DD38854AE2D88D81803C0EBFCD88
CADDY_KEY_FINGERPRINT=65760C51EDEA2017CEA2CA15155B6D79CA56EA34
CADDY_KEY_URL=https://dl.cloudsmith.io/public/caddy/stable/gpg.155B6D79CA56EA34.key
COSIGN_VERSION=v3.1.3
COSIGN_SHA256_AMD64=4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71
COSIGN_SHA256_ARM64=c5d324e091826b0d7a78eb16fef316450b4eb9aaec045611c08ba06f5e73220a
ENGINE_FILES="af_engine.py af_monitor.py af_notify.py af_oci.py af_release.py af_settings.py af_trigger.py contracts.py"
UNITS="af-agent.service af-agent.timer af-monitor.service af-monitor.timer"
DOCKER_LOG_CONFIG='{"log-driver": "local", "log-opts": {"max-size": "20m", "max-file": "5"}}'

HERE=$(cd "$(dirname "$0")" && pwd)
COMMAND=${1:-}
TRIGGER=pull
DEPLOY_ROOT=/srv/deploy
PROXY=1
REPLACE_CADDYFILE=0
FRESH_DOCKER=0
FRESH_CADDY=0
FAILURES=0

say() { printf 'af-bootstrap: %s\n' "$*"; }
die() { say "error: $*" >&2; exit 2; }

usage() {
  sed -n '4,7p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

parse_arguments() {
  [[ "$COMMAND" == install || "$COMMAND" == check ]] || usage
  shift
  while (($#)); do
    case "$1" in
      --trigger) TRIGGER=${2:-}; shift 2 ;;
      --deploy-root) DEPLOY_ROOT=${2:-}; shift 2 ;;
      --no-proxy) PROXY=0; shift ;;
      --replace-caddyfile) REPLACE_CADDYFILE=1; shift ;;
      *) usage ;;
    esac
  done
  [[ "$TRIGGER" == pull || "$TRIGGER" == push ]] || die "--trigger must be pull or push"
  [[ "$DEPLOY_ROOT" =~ ^/[A-Za-z0-9._/-]+$ && "$DEPLOY_ROOT" != / ]] || die "--deploy-root must be an absolute path"
}

# -- host facts ------------------------------------------------------------------------

require_host() {
  [[ $(id -u) -eq 0 ]] || die "run as root (sudo)"
  [[ -r /etc/os-release ]] || die "no /etc/os-release"
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_ID=$ID
  OS_CODENAME=${VERSION_CODENAME:-}
  [[ "$OS_ID" == debian || "$OS_ID" == ubuntu ]] || die "supported: Debian and Ubuntu (found $OS_ID)"
  [[ -n "$OS_CODENAME" ]] || die "VERSION_CODENAME missing from /etc/os-release"
  ARCH=$(dpkg --print-architecture)
  [[ "$ARCH" == amd64 || "$ARCH" == arm64 ]] || die "supported: amd64 and arm64 (found $ARCH)"
  command -v systemctl >/dev/null || die "systemd is required"
  for file in $ENGINE_FILES; do [[ -f "$HERE/$file" ]] || die "missing $file next to this script"; done
}

port_owner() {
  # The process listening on a TCP port, or empty.
  ss -Hltnp "sport = :$1" 2>/dev/null | sed -n 's/.*users:(("\([^"]*\)".*/\1/p' | head -1
}

# -- apt repositories --------------------------------------------------------------------

add_apt_key() {
  # Download a repository key, refuse it unless its primary fingerprint matches the pin.
  local url=$1 fingerprint=$2 target=$3 download
  download=$(mktemp)
  curl -fsSL --proto '=https' --tlsv1.2 "$url" -o "$download"
  local found
  found=$(gpg --show-keys --with-colons "$download" 2>/dev/null | awk -F: '$1 == "fpr" { print $10; exit }')
  if [[ "$found" != "$fingerprint" ]]; then
    rm -f "$download"
    die "key from $url has fingerprint ${found:-none}, expected $fingerprint"
  fi
  install -d -m 0755 /etc/apt/keyrings
  gpg --dearmor --yes -o "$target" "$download"
  chmod 0644 "$target"
  rm -f "$download"
}

install_base_packages() {
  say "base packages"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates curl gnupg python3 iproute2 >/dev/null
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "python3 3.11 or newer is required"
}

install_docker() {
  if docker compose version >/dev/null 2>&1 && systemctl is-active --quiet docker; then
    say "docker present: $(docker version --format '{{.Server.Version}}'), compose $(docker compose version --short)"
    return
  fi
  command -v dockerd >/dev/null && die "docker is installed but not running with the compose plugin; fix it first"
  say "installing Docker Engine from download.docker.com"
  add_apt_key "https://download.docker.com/linux/$OS_ID/gpg" "$DOCKER_KEY_FINGERPRINT" /etc/apt/keyrings/docker.gpg
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/%s %s stable\n' \
    "$ARCH" "$OS_ID" "$OS_CODENAME" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
  # Bounded container logs; only on a fresh install, so existing daemons keep their settings.
  if [[ ! -e /etc/docker/daemon.json ]]; then
    install -d -m 0755 /etc/docker
    printf '%s\n' "$DOCKER_LOG_CONFIG" > /etc/docker/daemon.json
  fi
  systemctl enable --quiet docker
  systemctl restart docker
  FRESH_DOCKER=1
}

install_caddy() {
  if command -v caddy >/dev/null; then
    say "caddy present: $(caddy version | awk '{print $1}')"
    return
  fi
  for port in 80 443; do
    local owner
    owner=$(port_owner "$port")
    [[ -z "$owner" ]] || die "port $port is used by $owner; free it or use --no-proxy"
  done
  say "installing Caddy from the caddy/stable repository"
  add_apt_key "$CADDY_KEY_URL" "$CADDY_KEY_FINGERPRINT" /etc/apt/keyrings/caddy.gpg
  printf 'deb [signed-by=/etc/apt/keyrings/caddy.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main\n' \
    > /etc/apt/sources.list.d/caddy.list
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy >/dev/null
  FRESH_CADDY=1
}

configure_caddy() {
  local changed=0
  install -d -m 0755 /etc/systemd/system/caddy.service.d
  # The admin API lives on a unix socket in /run/caddy; the caddy group (af) may reload.
  local dropin=/etc/systemd/system/caddy.service.d/af.conf
  local wanted
  wanted=$(printf '%s\n' \
    '# agent-framework: admin API on a unix socket the caddy group may use.' \
    '[Service]' \
    'RuntimeDirectory=caddy' \
    'RuntimeDirectoryMode=0750' \
    'ExecReload=' \
    'ExecReload=/usr/bin/caddy reload --config /etc/caddy/Caddyfile --address unix//run/caddy/admin.sock --force')
  if [[ ! -f "$dropin" ]] || [[ "$(cat "$dropin")" != "$wanted" ]]; then
    printf '%s\n' "$wanted" > "$dropin"
    changed=1
  fi
  if ! cmp -s "$HERE/caddy/Caddyfile" /etc/caddy/Caddyfile; then
    if ((FRESH_CADDY)) || ((REPLACE_CADDYFILE)); then
      [[ -f /etc/caddy/Caddyfile ]] && cp -p /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak-$(date -u +%Y%m%dT%H%M%SZ)"
      install -m 0644 "$HERE/caddy/Caddyfile" /etc/caddy/Caddyfile
      changed=1
    else
      die "/etc/caddy/Caddyfile was not written by this script; review it, then rerun with --replace-caddyfile"
    fi
  fi
  install -d -m 0755 -o af -g caddy /etc/caddy/sites
  usermod -aG caddy af
  if ((changed)); then
    systemctl daemon-reload
    systemctl enable --quiet caddy
    systemctl restart caddy
  fi
}

cosign_checksum() {
  if [[ "$ARCH" == amd64 ]]; then echo "$COSIGN_SHA256_AMD64"; else echo "$COSIGN_SHA256_ARM64"; fi
}

install_cosign() {
  local expected
  expected=$(cosign_checksum)
  if [[ -x /usr/local/bin/cosign ]] && echo "$expected  /usr/local/bin/cosign" | sha256sum --check --status; then
    say "cosign $COSIGN_VERSION present"
    return
  fi
  say "installing cosign $COSIGN_VERSION"
  local download
  download=$(mktemp)
  curl -fsSL --proto '=https' --tlsv1.2 -o "$download" \
    "https://github.com/sigstore/cosign/releases/download/$COSIGN_VERSION/cosign-linux-$ARCH"
  echo "$expected  $download" | sha256sum --check --strict --status || { rm -f "$download"; die "cosign checksum mismatch"; }
  install -m 0755 "$download" /usr/local/bin/cosign
  rm -f "$download"
}

# -- the deploy user, engine, and timers ------------------------------------------------

create_user() {
  if ! id af >/dev/null 2>&1; then
    say "creating the af deploy user"
    # A login shell is needed for the push trigger's forced command; the password stays locked.
    useradd --system --create-home --home-dir /var/lib/af --shell /bin/sh af
  fi
  usermod -aG docker af
  install -d -m 0700 -o af -g af /var/lib/af/.ssh
}

install_engine() {
  say "installing the engine in /opt/af"
  install -d -m 0755 /opt/af
  for file in $ENGINE_FILES; do install -m 0644 "$HERE/$file" "/opt/af/$file"; done
  local version=unknown
  [[ -f "$HERE/../../../../VERSION" ]] && version=$(tr -d '[:space:]' < "$HERE/../../../../VERSION")
  printf '%s\n' "$version" > /opt/af/VERSION
  # shellcheck disable=SC2086  # ENGINE_FILES is a word list
  (cd /opt/af && sha256sum $ENGINE_FILES > SHA256SUMS)
  install -d -m 0750 -o root -g af /etc/af /etc/af/hosts /etc/af/keys
  install -d -m 0700 -o af -g af /etc/af/secrets
  install -d -m 0750 -o af -g af "$DEPLOY_ROOT"
}

install_units() {
  for unit in $UNITS; do install -m 0644 "$HERE/systemd/$unit" "/etc/systemd/system/$unit"; done
  systemctl daemon-reload
  systemctl enable --now --quiet af-monitor.timer
  if [[ "$TRIGGER" == pull ]]; then
    systemctl enable --now --quiet af-agent.timer
  else
    systemctl disable --now --quiet af-agent.timer 2>/dev/null || true
  fi
}

# -- check ------------------------------------------------------------------------------

result() {
  # result <ok?> <item> <detail>
  if [[ "$1" == 0 ]]; then
    printf '  ok    %-22s %s\n' "$2" "$3"
  else
    printf '  FAIL  %-22s %s\n' "$2" "$3"
    FAILURES=$((FAILURES + 1))
  fi
}

passes() {
  # 0 when the command succeeds, 1 otherwise; safe under `set -e`.
  if "$@" >/dev/null 2>&1; then echo 0; else echo 1; fi
}

af_in_group() { id -nG af 2>/dev/null | tr ' ' '\n' | grep -qx "$1"; }
docker_ready() { docker compose version && systemctl is-active --quiet docker; }
python_ready() { python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; }
cosign_pinned() { echo "$(cosign_checksum)  /usr/local/bin/cosign" | sha256sum --check --status; }
engine_intact() { (cd /opt/af && sha256sum --check --status SHA256SUMS); }
directories_ready() { [[ -d /etc/af/hosts && -d /etc/af/keys && -d /etc/af/secrets && -d "$DEPLOY_ROOT" ]]; }
agent_disabled() { ! systemctl is-enabled --quiet af-agent.timer; }
admin_socket_ready() { [[ "$(stat -c '%a %G' /run/caddy/admin.sock)" == "660 caddy" ]]; }
sites_ready() { af_in_group caddy && [[ "$(stat -c %U /etc/caddy/sites)" == af ]]; }
logs_bounded() { grep -q '"max-size"' /etc/docker/daemon.json; }

check() {
  say "checking this host"
  result "$(passes docker_ready)" docker \
    "$(docker version --format 'engine {{.Server.Version}}' 2>/dev/null) compose $(docker compose version --short 2>/dev/null)"
  result "$(passes python_ready)" python3 "$(python3 --version 2>&1)"
  result "$(passes cosign_pinned)" cosign "$COSIGN_VERSION, checksum pinned"
  result "$(passes af_in_group docker)" "af user" "$(id af 2>/dev/null || echo missing)"
  result "$(passes engine_intact)" /opt/af "version $(cat /opt/af/VERSION 2>/dev/null || echo none), files match SHA256SUMS"
  result "$(passes directories_ready)" directories "/etc/af/{hosts,keys,secrets}, $DEPLOY_ROOT"
  result "$(passes systemctl is-enabled af-monitor.timer)" af-monitor.timer "$(systemctl is-active af-monitor.timer 2>/dev/null || true)"
  if [[ "$TRIGGER" == pull ]]; then
    result "$(passes systemctl is-enabled af-agent.timer)" af-agent.timer "$(systemctl is-active af-agent.timer 2>/dev/null || true)"
  else
    result "$(passes agent_disabled)" af-agent.timer "disabled (push trigger)"
  fi
  if ((PROXY)); then
    result "$(passes systemctl is-active caddy)" caddy "$(caddy version 2>/dev/null | awk '{print $1}')"
    result "$(passes admin_socket_ready)" "caddy admin socket" \
      "$(stat -c '%a %U:%G' /run/caddy/admin.sock 2>/dev/null || echo missing)"
    result "$(passes sites_ready)" "caddy sites" "/etc/caddy/sites owned by af, af in the caddy group"
  fi
  result "$(passes logs_bounded)" "container logs" "size limit in /etc/docker/daemon.json"
  if ((FAILURES)); then
    say "$FAILURES check(s) failed"
    return 1
  fi
  say "host ready"
}

main() {
  parse_arguments "$@"
  require_host
  if [[ "$COMMAND" == install ]]; then
    install_base_packages
    install_docker
    install_cosign
    create_user
    if ((PROXY)); then
      install_caddy
      configure_caddy
    fi
    install_engine
    install_units
    ((FRESH_DOCKER)) || say "note: an existing Docker keeps its daemon.json; add a log size limit there if 'container logs' fails"
  fi
  check
}

main "$@"
