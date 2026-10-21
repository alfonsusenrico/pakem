#!/usr/bin/env bash
# A free, isolated self-hosted GitHub Actions runner for a project's checks (ADR 0010).
#
#   sudo ./af_runner.sh install
#   sudo ./af_runner.sh add-repo <owner>/<repo>      (registration token on standard input)
#   sudo ./af_runner.sh remove-repo <owner>/<repo>   (removal token on standard input)
#   sudo ./af_runner.sh check
#
# The runner runs as its own user `ci-runner` (no sudo, not in the `docker` or `af` groups)
# with its own rootless Docker, so jobs never reach the production Docker socket, the
# deploy user, or release secrets. Everything it runs shares one systemd slice capped at
# RUNNER_CPU_QUOTA and RUNNER_MEMORY_MAX. One runner process per repository (GitHub
# registers runners per repository for personal accounts); each takes one job at a time.
# Tokens come from the repository owner's own GitHub login, for example:
#   gh api -X POST repos/<owner>/<repo>/actions/runners/registration-token --jq .token |
#     ssh <host> sudo ./af_runner.sh add-repo <owner>/<repo>
# and are never printed or passed as arguments. Debian's docker.io and Docker's docker-ce
# both work: the rootless pieces come from the same source as the installed Docker, so
# the production Docker is never replaced.
set -euo pipefail

RUNNER_VERSION=2.338.0
RUNNER_SHA256_X64=af4b794c1bc41d73d40535e3fe092a39f9679cd8d965954c2aca25a05ca41d32
RUNNER_SHA256_ARM64=628b4a7258487b80c1d3c221095a7ded349f5ae0175fd3dfab708d07428f041b
RUNNER_USER=ci-runner
RUNNER_HOME=/var/lib/ci-runner
RUNNER_LABEL=pakem
RUNNER_CPU_QUOTA=${RUNNER_CPU_QUOTA:-200%}
RUNNER_MEMORY_MAX=${RUNNER_MEMORY_MAX:-4G}
RUNNER_TASKS_MAX=${RUNNER_TASKS_MAX:-4096}
SUBID_COUNT=65536
# Paths the runner must never read or write; checked by `check`.
PROTECTED_PATHS=(/var/run/docker.sock /etc/af/secrets /srv/deploy /etc/af/hosts)

COMMAND=${1:-}
FAILURES=0

say() { printf 'af-runner: %s\n' "$*"; }
die() { say "error: $*" >&2; exit 2; }

usage() {
  sed -n '4,7p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

require_root() { [[ $EUID -eq 0 ]] || die "run as root (sudo)"; }

runner_uid() { id -u "$RUNNER_USER"; }

# Job containers may reach the host's loopback (from inside at 10.0.2.2, slirp4netns): a job's
# own steps already run as the runner user on the host and reach the same addresses, so this
# adds no exposure, and it lets a job's container talk to a server the job started. What
# stays isolated is the production Docker socket, the deploy user, and secret files.
LOOPBACK_DROPIN="[Service]
Environment=DOCKERD_ROOTLESS_ROOTLESSKIT_DISABLE_HOST_LOOPBACK=false"

# Run a command as the runner user inside its systemd user session.
as_runner() {
  local uid
  uid=$(runner_uid)
  runuser -u "$RUNNER_USER" -- env HOME="$RUNNER_HOME" XDG_RUNTIME_DIR="/run/user/$uid" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" DOCKER_HOST="unix:///run/user/$uid/docker.sock" \
    PATH="$RUNNER_HOME/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" "$@"
}

architecture() {
  case "$(dpkg --print-architecture)" in
    amd64) echo x64 ;;
    arm64) echo arm64 ;;
    *) die "unsupported architecture $(dpkg --print-architecture)" ;;
  esac
}

runner_checksum() {
  case "$(architecture)" in
    x64) echo "$RUNNER_SHA256_X64" ;;
    arm64) echo "$RUNNER_SHA256_ARM64" ;;
  esac
}

repo_slug() {
  [[ "${1:-}" =~ ^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$ ]] || die "repository must look like owner/repo"
  echo "${1//\//-}"
}

read_token() {
  local token
  [[ ! -t 0 ]] || die "pipe the token on standard input; never pass it as an argument"
  IFS= read -r token || true
  [[ "$token" =~ ^[A-Za-z0-9_-]{20,200}$ ]] || die "standard input does not hold a runner token"
  printf '%s' "$token"
}

# The rootless setup tool from the same source as the installed Docker.
rootless_tool() {
  local candidate
  for candidate in /usr/bin/dockerd-rootless-setuptool.sh /usr/share/docker.io/contrib/dockerd-rootless-setuptool.sh; do
    if [[ -x "$candidate" ]]; then echo "$candidate"; return; fi
  done
  return 1
}

install_packages() {
  local packages=(uidmap slirp4netns dbus-user-session curl ca-certificates)
  if dpkg -s docker.io >/dev/null 2>&1; then
    packages+=(rootlesskit)
  elif dpkg -s docker-ce >/dev/null 2>&1; then
    packages+=(docker-ce-rootless-extras)
  else
    die "Docker is not installed; run af_bootstrap.sh install first"
  fi
  say "installing ${packages[*]}"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${packages[@]}" >/dev/null
  rootless_tool >/dev/null || die "the installed Docker has no rootless setup tool"
}

next_subid_start() {
  # The first 65536-sized range above every range already in /etc/subuid and /etc/subgid.
  awk -F: 'NF == 3 { end = $2 + $3; if (end > top) top = end } END { start = (top > 100000 ? top : 100000); print start }' \
    /etc/subuid /etc/subgid 2>/dev/null
}

create_user() {
  if ! id "$RUNNER_USER" >/dev/null 2>&1; then
    say "creating user $RUNNER_USER"
    useradd --system --home-dir "$RUNNER_HOME" --create-home --shell /bin/bash "$RUNNER_USER"
  fi
  local member
  for member in sudo docker af adm; do
    if id -nG "$RUNNER_USER" | tr ' ' '\n' | grep -qx "$member"; then
      die "$RUNNER_USER is in the $member group; remove it before installing"
    fi
  done
  if ! grep -q "^$RUNNER_USER:" /etc/subuid || ! grep -q "^$RUNNER_USER:" /etc/subgid; then
    local start
    start=$(next_subid_start)
    say "adding subordinate ids $start-$((start + SUBID_COUNT - 1)) for $RUNNER_USER"
    grep -q "^$RUNNER_USER:" /etc/subuid || usermod --add-subuids "$start-$((start + SUBID_COUNT - 1))" "$RUNNER_USER"
    grep -q "^$RUNNER_USER:" /etc/subgid || usermod --add-subgids "$start-$((start + SUBID_COUNT - 1))" "$RUNNER_USER"
  fi
  install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0750 "$RUNNER_HOME"
  install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0700 "$RUNNER_HOME/runners" "$RUNNER_HOME/dist" "$RUNNER_HOME/hooks"
  loginctl enable-linger "$RUNNER_USER"
  local uid tries=0
  uid=$(runner_uid)
  until [[ -S "/run/user/$uid/bus" ]]; do
    tries=$((tries + 1))
    ((tries < 30)) || die "the user session for $RUNNER_USER did not start"
    sleep 1
  done
}

# Rootless Docker's networking (NAT from job containers to the internet) needs ip_tables.
ensure_ip_tables() {
  if grep -q '^ip_tables ' /proc/modules || grep -qs ip_tables "/lib/modules/$(uname -r)/modules.builtin"; then
    return
  fi
  modprobe ip_tables 2>/dev/null || die "the ip_tables kernel module is not available; load it, then rerun"
  echo ip_tables > /etc/modules-load.d/ci-runner.conf
  say "loaded ip_tables and kept it loaded after reboots (/etc/modules-load.d/ci-runner.conf)"
}

install_rootless_docker() {
  if as_runner systemctl --user is-active --quiet docker 2>/dev/null; then
    say "rootless Docker for $RUNNER_USER is already running"
    return
  fi
  local tool binaries="$RUNNER_HOME/bin"
  tool=$(rootless_tool)
  # The setup tool expects dockerd-rootless.sh and docker beside itself; Debian's docker.io
  # keeps the scripts in a contrib folder, so give it one folder of links to all three.
  install -d -o root -g "$RUNNER_USER" -m 0755 "$binaries"
  ln -sfn "$tool" "$binaries/dockerd-rootless-setuptool.sh"
  ln -sfn "$(dirname "$tool")/dockerd-rootless.sh" "$binaries/dockerd-rootless.sh"
  ln -sfn "$(command -v docker)" "$binaries/docker"
  ln -sfn "$(command -v dockerd)" "$binaries/dockerd"
  say "setting up rootless Docker for $RUNNER_USER"
  as_runner "$binaries/dockerd-rootless-setuptool.sh" install >/dev/null
  as_runner systemctl --user enable --now docker >/dev/null 2>&1
}

allow_host_loopback() {
  local directory="$RUNNER_HOME/.config/systemd/user/docker.service.d"
  local dropin="$directory/host-loopback.conf"
  if [[ -f "$dropin" ]] && [[ "$(cat "$dropin")" == "$LOOPBACK_DROPIN" ]]; then
    return
  fi
  install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0755 "$directory"
  printf '%s\n' "$LOOPBACK_DROPIN" > "$dropin"
  chown "$RUNNER_USER:$RUNNER_USER" "$dropin"
  as_runner systemctl --user daemon-reload
  as_runner systemctl --user restart docker
  say "job containers reach the host's loopback at 10.0.2.2"
}

limit_resources() {
  local uid slice
  uid=$(runner_uid)
  slice="user-$uid.slice"
  systemctl set-property "$slice" CPUQuota="$RUNNER_CPU_QUOTA" MemoryMax="$RUNNER_MEMORY_MAX" TasksMax="$RUNNER_TASKS_MAX"
  say "capped $slice at CPU $RUNNER_CPU_QUOTA, memory $RUNNER_MEMORY_MAX, tasks $RUNNER_TASKS_MAX"
}

download_runner() {
  local archive="$RUNNER_HOME/dist/actions-runner-linux-$(architecture)-$RUNNER_VERSION.tar.gz"
  if [[ -f "$archive" ]] && echo "$(runner_checksum)  $archive" | sha256sum --check --status; then
    return
  fi
  say "downloading GitHub Actions runner $RUNNER_VERSION"
  local temporary
  temporary=$(mktemp)
  curl -fsSL -o "$temporary" \
    "https://github.com/actions/runner/releases/download/v$RUNNER_VERSION/actions-runner-linux-$(architecture)-$RUNNER_VERSION.tar.gz"
  echo "$(runner_checksum)  $temporary" | sha256sum --check --status || { rm -f "$temporary"; die "runner checksum mismatch"; }
  install -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0640 "$temporary" "$archive"
  rm -f "$temporary"
}

install_hooks_and_unit() {
  # Each finished job leaves no workspace behind; the tool cache stays.
  cat > "$RUNNER_HOME/hooks/job-completed.sh" <<'HOOK'
#!/bin/sh
# Remove the finished job's workspace; the runner recreates it for the next job.
[ -n "${GITHUB_WORKSPACE:-}" ] && [ -d "$GITHUB_WORKSPACE" ] && find "$GITHUB_WORKSPACE" -mindepth 1 -delete
exit 0
HOOK
  chown "$RUNNER_USER:$RUNNER_USER" "$RUNNER_HOME/hooks/job-completed.sh"
  chmod 0750 "$RUNNER_HOME/hooks/job-completed.sh"
  install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0755 "$RUNNER_HOME/.config" "$RUNNER_HOME/.config/systemd" "$RUNNER_HOME/.config/systemd/user"
  cat > "$RUNNER_HOME/.config/systemd/user/actions-runner@.service" <<'UNIT'
[Unit]
Description=GitHub Actions runner for %i
After=docker.service
Wants=docker.service

[Service]
WorkingDirectory=%h/runners/%i
ExecStart=%h/runners/%i/run.sh
Restart=always
RestartSec=10
KillSignal=SIGINT
TimeoutStopSec=60

[Install]
WantedBy=default.target
UNIT
  chown "$RUNNER_USER:$RUNNER_USER" "$RUNNER_HOME/.config/systemd/user/actions-runner@.service"
  as_runner systemctl --user daemon-reload
}

install_command() {
  require_root
  install_packages
  ensure_ip_tables
  create_user
  install_rootless_docker
  allow_host_loopback
  limit_resources
  download_runner
  install_hooks_and_unit
  say "installed; register repositories with: af_runner.sh add-repo <owner>/<repo>"
}

add_repo() {
  require_root
  local repo=${1:-} slug token directory uid
  slug=$(repo_slug "$repo")
  token=$(read_token)
  id "$RUNNER_USER" >/dev/null 2>&1 || die "run af_runner.sh install first"
  uid=$(runner_uid)
  directory="$RUNNER_HOME/runners/$slug"
  if [[ ! -x "$directory/run.sh" ]]; then
    install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0700 "$directory"
    as_runner tar -xzf "$RUNNER_HOME/dist/actions-runner-linux-$(architecture)-$RUNNER_VERSION.tar.gz" -C "$directory"
  fi
  say "registering $repo"
  printf '%s' "$token" | as_runner sh -c 'cd "$1" && ./config.sh --unattended --replace \
      --url "https://github.com/$2" --token "$(cat)" --name "$3" --labels "$4" --work _work >/dev/null' \
    _ "$directory" "$repo" "$(hostname -s)-${repo#*/}" "$RUNNER_LABEL"
  cat > "$directory/.env" <<ENV
DOCKER_HOST=unix:///run/user/$uid/docker.sock
ACTIONS_RUNNER_HOOK_JOB_COMPLETED=$RUNNER_HOME/hooks/job-completed.sh
ENV
  chown "$RUNNER_USER:$RUNNER_USER" "$directory/.env"
  chmod 0600 "$directory/.env"
  as_runner systemctl --user enable --now "actions-runner@$slug" >/dev/null 2>&1
  say "runner for $repo is running (labels: self-hosted, linux, $(architecture), $RUNNER_LABEL)"
}

remove_repo() {
  require_root
  local repo=${1:-} slug token directory
  slug=$(repo_slug "$repo")
  token=$(read_token)
  directory="$RUNNER_HOME/runners/$slug"
  [[ -d "$directory" ]] || die "no runner for $repo"
  as_runner systemctl --user disable --now "actions-runner@$slug" >/dev/null 2>&1 || true
  printf '%s' "$token" | as_runner sh -c 'cd "$1" && ./config.sh remove --token "$(cat)" >/dev/null' _ "$directory"
  rm -rf -- "$directory"
  say "removed the runner for $repo"
}

result() {
  if "$@" >/dev/null 2>&1; then
    printf '  ok    %s\n' "$LABEL"
  else
    printf '  FAIL  %s\n' "$LABEL"
    FAILURES=$((FAILURES + 1))
  fi
}
check_item() { LABEL=$1; shift; result "$@"; }

not_in_groups() { ! id -nG "$RUNNER_USER" | tr ' ' '\n' | grep -qxE 'sudo|docker|af|adm'; }
linger_on() { [[ -f "/var/lib/systemd/linger/$RUNNER_USER" ]]; }
rootless_running() { as_runner docker info --format '{{.SecurityOptions}}' | grep -q rootless; }
cannot_reach() { ! runuser -u "$RUNNER_USER" -- test -r "$1" && ! runuser -u "$RUNNER_USER" -- test -w "$1"; }
slice_capped() {
  local slice
  slice="user-$(runner_uid).slice"
  [[ "$(systemctl show "$slice" -p MemoryMax --value)" != infinity ]] &&
    [[ "$(systemctl show "$slice" -p CPUQuotaPerSecUSec --value)" != infinity ]]
}
runner_active() { as_runner systemctl --user is-active --quiet "actions-runner@$1"; }

check_command() {
  require_root
  FAILURES=0
  say "check"
  check_item "user $RUNNER_USER exists" id "$RUNNER_USER"
  if ! id "$RUNNER_USER" >/dev/null 2>&1; then
    say "not installed"
    exit 1
  fi
  check_item "$RUNNER_USER is not in sudo, docker, af, or adm" not_in_groups
  check_item "user session survives logout (linger)" linger_on
  check_item "rootless Docker is running for $RUNNER_USER" rootless_running
  check_item "resource caps on the runner's slice" slice_capped
  local path
  for path in "${PROTECTED_PATHS[@]}"; do
    [[ -e "$path" ]] || continue
    check_item "$RUNNER_USER cannot read or write $path" cannot_reach "$path"
  done
  local directory
  for directory in "$RUNNER_HOME"/runners/*/; do
    [[ -d "$directory" ]] || continue
    check_item "runner $(basename "$directory") is active" runner_active "$(basename "$directory")"
  done
  if ((FAILURES)); then
    say "$FAILURES check(s) failed"
    exit 1
  fi
  say "all checks passed"
}

main() {
  case "$COMMAND" in
    install) install_command ;;
    add-repo) add_repo "${2:-}" ;;
    remove-repo) remove_repo "${2:-}" ;;
    check) check_command ;;
    *) usage ;;
  esac
}

main "$@"
