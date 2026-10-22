#!/usr/bin/env bash
# Prepare a Debian or Ubuntu host for the deployment standard (ADR 0009; contracts v1 §11).
#
#   sudo ./af_bootstrap.sh install [--trigger pull|push] [--deploy-root /srv/deploy]
#                                  [--no-proxy] [--replace-caddyfile] [--release-port PORT]
#   sudo ./af_bootstrap.sh check   [--trigger pull|push] [--deploy-root /srv/deploy] [--no-proxy]
#                                  [--release-port PORT]
#
# Run it from a checkout of a tagged framework release (this directory). `install` is
# idempotent and never replaces what it did not create: an existing Docker keeps its
# configuration and containers, and a foreign Caddyfile is left alone unless
# --replace-caddyfile is given (the old file is kept as a dated backup). `check` changes
# nothing and exits 1 when something is missing.
#
# Installs: Docker Engine with the Compose plugin and Caddy from their signed apt
# repositories (key fingerprints pinned below), cosign as a checksum-pinned binary, the
# `af` deploy user, the engine and the direct delivery receiver in /opt/af, /etc/af, the
# deploy root, the af-agent and af-monitor timers, and with --release-port a second OpenSSH
# port where only `af` logs in with a key (for hosts whose port 22 is taken over by Tailscale
# SSH or similar, which skip authorized_keys forced commands). Project files (host configs,
# keys, placement, secrets) are added per project afterwards: `pakem setup host
# --project-dir` for direct delivery, contracts §11 steps 3 to 7 for a registry.
set -euo pipefail

DOCKER_KEY_FINGERPRINT=9DC858229FC7DD38854AE2D88D81803C0EBFCD88
CADDY_KEY_FINGERPRINT=65760C51EDEA2017CEA2CA15155B6D79CA56EA34
CADDY_KEY_URL=https://dl.cloudsmith.io/public/caddy/stable/gpg.155B6D79CA56EA34.key
COSIGN_VERSION=v3.1.3
COSIGN_SHA256_AMD64=4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71
COSIGN_SHA256_ARM64=c5d324e091826b0d7a78eb16fef316450b4eb9aaec045611c08ba06f5e73220a
ENGINE_FILES="af_engine.py af_monitor.py af_notify.py af_oci.py af_publish.py af_receive.py af_release.py af_settings.py af_trigger.py contracts.py af_runner.sh"
UNITS="af-agent.service af-agent.timer af-monitor.service af-monitor.timer"
DOCKER_LOG_CONFIG='{"log-driver": "local", "log-opts": {"max-size": "20m", "max-file": "5"}}'

HERE=$(cd "$(dirname "$0")" && pwd)
COMMAND=${1:-}
TRIGGER=pull
DEPLOY_ROOT=/srv/deploy
PROXY=1
REPLACE_CADDYFILE=0
RELEASE_PORT=0
SSHD_DROPIN=/etc/ssh/sshd_config.d/pakem-release.conf
FRESH_DOCKER=0
FRESH_CADDY=0
FAILURES=0

say() { printf 'af-bootstrap: %s\n' "$*"; }
die() { say "error: $*" >&2; exit 2; }

usage() {
  sed -n '4,8p' "$0" | sed 's/^# \{0,1\}//'
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
      --release-port) RELEASE_PORT=${2:-}; shift 2 ;;
      *) usage ;;
    esac
  done
  [[ "$TRIGGER" == pull || "$TRIGGER" == push ]] || die "--trigger must be pull or push"
  [[ "$DEPLOY_ROOT" =~ ^/[A-Za-z0-9._/-]+$ && "$DEPLOY_ROOT" != / ]] || die "--deploy-root must be an absolute path"
  [[ "$RELEASE_PORT" =~ ^[0-9]{1,5}$ ]] && ((RELEASE_PORT <= 65535)) || die "--release-port must be a TCP port"
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

reload_systemd() {
  # Only after a unit file changed. On GPU hosts a reload strips GPU access from containers
  # started with the legacy NVIDIA device request (`deploy.devices` / `--gpus`); containers
  # on `runtime: nvidia` (toolkit 1.18+, CDI) keep it. NVIDIA Container Toolkit troubleshooting.
  if [[ -e /dev/nvidiactl ]]; then
    say "warning: reloading systemd on a GPU host; GPU containers not on runtime: nvidia lose GPU access until recreated"
  fi
  say "reloading systemd ($1)"
  systemctl daemon-reload
}

port_owner() {
  # The process listening on a TCP port, or empty.
  ss -Hltnp "sport = :$1" 2>/dev/null | sed -n 's/.*users:(("\([^"]*\)".*/\1/p' | head -1
}

port_users() {
  # Every process listening on a TCP port, IPv4 and IPv6, one name per line.
  ss -Hltnp "sport = :$1" 2>/dev/null | grep -oE '"[^"]+",pid=' | cut -d'"' -f2 | sort -u
}

sshd_only_on() {
  # Something listens on the port, and all of it is sshd.
  local users
  users=$(port_users "$1")
  [[ -n "$users" ]] && ! grep -qvxE 'sshd|sshd-session' <<<"$users"
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
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates curl gnupg python3 iproute2 openssh-client >/dev/null
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
    reload_systemd "caddy drop-in"
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
  chmod 0755 /opt/af/af_runner.sh
  local version=unknown
  [[ -f "$HERE/../../../../VERSION" ]] && version=$(tr -d '[:space:]' < "$HERE/../../../../VERSION")
  printf '%s\n' "$version" > /opt/af/VERSION
  # shellcheck disable=SC2086  # ENGINE_FILES is a word list
  (cd /opt/af && sha256sum $ENGINE_FILES > SHA256SUMS)
  install -d -m 0750 -o root -g af /etc/af /etc/af/hosts /etc/af/keys
  install -d -m 0700 -o af -g af /etc/af/secrets
  install -d -m 0750 -o af -g af "$DEPLOY_ROOT"
  # Device keys that may sign direct releases, written by `af_receive.py authorize`.
  [[ -e /etc/af/allowed_signers ]] || install -m 0640 -o root -g af /dev/null /etc/af/allowed_signers
}

install_units() {
  local changed=()
  for unit in $UNITS; do
    if ! cmp -s "$HERE/systemd/$unit" "/etc/systemd/system/$unit"; then
      install -m 0644 "$HERE/systemd/$unit" "/etc/systemd/system/$unit"
      changed+=("$unit")
    fi
  done
  if ((${#changed[@]})); then
    reload_systemd "${changed[*]}"
  fi
  systemctl enable --now --quiet af-monitor.timer
  if [[ "$TRIGGER" == pull ]]; then
    systemctl enable --now --quiet af-agent.timer
  else
    systemctl disable --now --quiet af-agent.timer 2>/dev/null || true
  fi
}

# -- the release port ---------------------------------------------------------------------

sshd_ports() {
  # With a Match LocalPort block, `sshd -T` needs a connection spec; ports are global anyway.
  sshd -T -C "lport=1,user=af,host=localhost,addr=127.0.0.1" | awk '$1 == "port" { print $2 }'
}

release_port_text() {
  # A Port line in a drop-in replaces the default 22, so the ports sshd listens on now are
  # kept explicitly unless the main file names its own; an earlier release port ($2) goes.
  local port
  printf '%s\n' "# Written by af_bootstrap.sh (Pakem ADR 0010): a second OpenSSH port for releases." \
    "# Only the deploy user, with a key, so its authorized_keys forced commands always apply."
  if ! grep -qsE '^[[:space:]]*Port[[:space:]]' /etc/ssh/sshd_config; then
    for port in $1; do [[ "$port" == "$RELEASE_PORT" || "$port" == "$2" ]] || printf 'Port %s\n' "$port"; done
  fi
  printf '%s\n' "Port $RELEASE_PORT" "Match LocalPort $RELEASE_PORT" "  AllowUsers af" \
    "  AuthenticationMethods publickey" "  PasswordAuthentication no" "  KbdInteractiveAuthentication no" \
    "  PermitRootLogin no"
}

reload_sshd() {
  # Socket activation (Ubuntu) generates its listeners from sshd_config on daemon-reload.
  if systemctl is-active --quiet ssh.socket; then
    reload_systemd "ssh.socket listeners"
    systemctl restart ssh.socket
  else
    systemctl reload ssh 2>/dev/null || systemctl reload sshd
  fi
}

configure_release_port() {
  ((RELEASE_PORT)) || return 0
  command -v sshd >/dev/null || die "--release-port needs the OpenSSH server (sshd)"
  local before wanted saved="" port problem="" previous="" others
  others=$(port_users "$RELEASE_PORT" | grep -vxE 'sshd|sshd-session' || true)
  [[ -z "$others" ]] || die "port $RELEASE_PORT is already used by $(echo $others); choose another --release-port (nothing changed)"
  before=$(sshd_ports) || die "sshd -T could not read the current configuration; nothing changed"
  [[ -f "$SSHD_DROPIN" ]] && previous=$(sed -n 's/^Match LocalPort \([0-9]*\)$/\1/p' "$SSHD_DROPIN")
  wanted=$(release_port_text "$before" "$previous")
  if [[ -f "$SSHD_DROPIN" && "$(cat "$SSHD_DROPIN")" == "$wanted" ]]; then
    say "sshd release port $RELEASE_PORT present"
    return
  fi
  if [[ -f "$SSHD_DROPIN" ]]; then saved=$(mktemp); cp -p "$SSHD_DROPIN" "$saved"; fi
  install -d -m 0755 /etc/ssh/sshd_config.d
  printf '%s\n' "$wanted" > "$SSHD_DROPIN"
  chmod 0644 "$SSHD_DROPIN"
  if ! sshd -t; then
    problem="sshd rejected the release port settings"
  elif ! sshd_ports 2>/dev/null | grep -x "$RELEASE_PORT" >/dev/null; then
    problem="sshd does not read /etc/ssh/sshd_config.d (no Include line); add the port by hand"
  else
    for port in $before; do
      [[ "$port" == "$previous" ]] && continue
      sshd_ports 2>/dev/null | grep -x "$port" >/dev/null || problem="the settings would drop port $port"
    done
  fi
  if [[ -n "$problem" ]]; then
    if [[ -n "$saved" ]]; then mv "$saved" "$SSHD_DROPIN"; else rm -f "$SSHD_DROPIN"; fi
    die "$problem; sshd was left as it was"
  fi
  say "sshd: release port $RELEASE_PORT for the deploy user (keys only); other ports stay"
  reload_sshd
  local attempt
  for attempt in 1 2 3 4 5; do
    sshd_only_on "$RELEASE_PORT" && { rm -f "$saved"; return; }
    sleep 1
  done
  # sshd could not take the port on every address: put the old settings back.
  if [[ -n "$saved" ]]; then mv "$saved" "$SSHD_DROPIN"; else rm -f "$SSHD_DROPIN"; fi
  reload_sshd
  die "sshd did not take port $RELEASE_PORT on every address ($(echo $(port_users "$RELEASE_PORT"))); the old settings are back"
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
buildx_ready() { docker buildx version; }
receiver_ready() { [[ "$(stat -c '%a %U:%G' /etc/af/allowed_signers)" == "640 root:af" ]] && command -v ssh-keygen; }
python_ready() { python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; }
cosign_pinned() { echo "$(cosign_checksum)  /usr/local/bin/cosign" | sha256sum --check --status; }
engine_intact() { (cd /opt/af && sha256sum --check --status SHA256SUMS); }
directories_ready() { [[ -d /etc/af/hosts && -d /etc/af/keys && -d /etc/af/secrets && -d "$DEPLOY_ROOT" ]]; }
agent_disabled() { ! systemctl is-enabled --quiet af-agent.timer; }
admin_socket_ready() { [[ "$(stat -c '%a %G' /run/caddy/admin.sock)" == "660 caddy" ]]; }
sites_ready() { af_in_group caddy && [[ "$(stat -c %U /etc/caddy/sites)" == af ]]; }
logs_bounded() { grep -q '"max-size"' /etc/docker/daemon.json; }
release_port_listening() { sshd_only_on "$RELEASE_PORT"; }

check() {
  say "checking this host"
  result "$(passes docker_ready)" docker \
    "$(docker version --format 'engine {{.Server.Version}}' 2>/dev/null) compose $(docker compose version --short 2>/dev/null)"
  result "$(passes buildx_ready)" "docker buildx" "$(docker buildx version 2>/dev/null | awk '{print $2}')"
  result "$(passes python_ready)" python3 "$(python3 --version 2>&1)"
  result "$(passes cosign_pinned)" cosign "$COSIGN_VERSION, checksum pinned"
  result "$(passes af_in_group docker)" "af user" "$(id af 2>/dev/null || echo missing)"
  result "$(passes engine_intact)" /opt/af "version $(cat /opt/af/VERSION 2>/dev/null || echo none), files match SHA256SUMS"
  result "$(passes directories_ready)" directories "/etc/af/{hosts,keys,secrets}, $DEPLOY_ROOT"
  result "$(passes receiver_ready)" "direct delivery" "/etc/af/allowed_signers, ssh-keygen"
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
  if ((RELEASE_PORT)); then
    result "$(passes release_port_listening)" "release port" \
      "only sshd listens on $RELEASE_PORT ($(echo $(port_users "$RELEASE_PORT")); $SSHD_DROPIN)"
  fi
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
    configure_release_port
    ((FRESH_DOCKER)) || say "note: an existing Docker keeps its daemon.json; add a log size limit there if 'container logs' fails"
  fi
  check
}

main "$@"
