#!/bin/sh
# Deploy one commit to one environment over SSH (agent-framework deploy template).
#
# Runs in CI from the repository checkout. It validates every input before connecting,
# archives the exact commit with `git archive`, materializes the runtime file from the
# commit's deploy/runtime.manifest.toml, uploads both with release_host.py, and runs the
# host sequence while holding the environment lock:
#   deploy:   secure-file, prepare, activate, promote, public smoke, cleanup, record
#   rollback: secure-file, prepare, rollback (no backup, no migrations), public smoke,
#             cleanup, record
# A failed promote or public smoke restores the previous release automatically.
# Variables are documented in README.md ("Variables"); nothing here prints their values.
#
# Exit codes: 0 deployed, 1 failed with nothing to restore, 2 invalid input, 3 failed and
# the previous release was restored, 4 failed and the restore failed too, 75 lock busy.
set -eu
set -f
LC_ALL=C
export LC_ALL

log() {
    printf 'deploy: %s\n' "$*"
}

fail() {
    fail_code=$1
    shift
    printf 'deploy: ERROR: %s\n' "$*" >&2
    exit "$fail_code"
}

invalid() {
    fail 2 "$@"
}

# only_chars VALUE CLASS: true when VALUE is non-empty and uses only CLASS characters.
only_chars() {
    [ -n "$1" ] || return 1
    case $1 in
        *[!$2]*) return 1 ;;
    esac
    return 0
}

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

environment=${AF_DEPLOY_ENVIRONMENT:-}
sha=${AF_DEPLOY_SHA:-}
mode=${AF_DEPLOY_MODE:-deploy}
host=${DEPLOY_HOST:-}
user=${DEPLOY_USER:-}
port=${DEPLOY_SSH_PORT:-22}
deploy_root=${DEPLOY_ROOT:-}
owner=${DEPLOY_OWNER:-}
key_file=${DEPLOY_SSH_KEY_FILE:-}
known_hosts=${DEPLOY_KNOWN_HOSTS_FILE:-}
public_url=${DEPLOY_PUBLIC_URL:-}
host_tool=${DEPLOY_HOST_TOOL:-}
remote_python=${DEPLOY_REMOTE_PYTHON:-/usr/bin/python3}
repo_dir=${AF_REPO_DIR:-.}
python=${AF_PYTHON:-python3}
lock_wait=${AF_LOCK_WAIT_SECONDS:-600}
protected_environments=${AF_PROTECTED_ENVIRONMENTS:-production}
release_ref=${AF_RELEASE_REF:-origin/main}
actor=${AF_DEPLOY_ACTOR:-}
pipeline_url=${AF_PIPELINE_URL:-}
smoke_paths=${AF_SMOKE_PATHS:-}
smoke_attempts=${AF_SMOKE_ATTEMPTS:-30}

validate_inputs() {
    case $environment in
        [a-z]*) ;;
        *) invalid "AF_DEPLOY_ENVIRONMENT must start with a lowercase letter" ;;
    esac
    only_chars "$environment" 'a-z0-9-' && [ ${#environment} -le 32 ] ||
        invalid "AF_DEPLOY_ENVIRONMENT must match [a-z][a-z0-9-]{0,31}"
    only_chars "$sha" '0-9a-f' && [ ${#sha} -eq 40 ] ||
        invalid "AF_DEPLOY_SHA must be a full 40-character lowercase commit SHA"
    case $mode in
        deploy | rollback) ;;
        *) invalid "AF_DEPLOY_MODE must be deploy or rollback" ;;
    esac
    case $host in
        -* | .*) invalid "DEPLOY_HOST must be a host name or IPv4 address" ;;
    esac
    only_chars "$host" 'A-Za-z0-9.-' && [ ${#host} -le 253 ] ||
        invalid "DEPLOY_HOST must be a host name or IPv4 address"
    case $user in
        [a-z_]*) ;;
        *) invalid "DEPLOY_USER must be a plain user name" ;;
    esac
    only_chars "$user" 'a-z0-9_-' || invalid "DEPLOY_USER must be a plain user name"
    only_chars "$port" '0-9' && [ ${#port} -le 5 ] && [ "$port" -ge 1 ] && [ "$port" -le 65535 ] ||
        invalid "DEPLOY_SSH_PORT must be a port number"
    case $deploy_root in
        /?*) ;;
        *) invalid "DEPLOY_ROOT must be an absolute path" ;;
    esac
    only_chars "$deploy_root" 'A-Za-z0-9._/-' || invalid "DEPLOY_ROOT may use only A-Z a-z 0-9 . _ / -"
    case $deploy_root/ in
        */../* | */./* | *//*) invalid "DEPLOY_ROOT must not contain '.', '..', empty segments, or a trailing /" ;;
    esac
    case $owner in
        root | self) ;;
        *) invalid "DEPLOY_OWNER must be root (organization hosts) or self (personal server)" ;;
    esac
    for file_input in "DEPLOY_SSH_KEY_FILE:$key_file" "DEPLOY_KNOWN_HOSTS_FILE:$known_hosts"; do
        name=${file_input%%:*}
        path=${file_input#*:}
        [ -f "$path" ] && [ -r "$path" ] && [ -s "$path" ] || invalid "$name must name a readable, non-empty file"
        only_chars "$path" 'A-Za-z0-9._/@+-' || invalid "$name path may use only A-Z a-z 0-9 . _ / @ + -"
    done
    if [ -n "$(find "$key_file" -prune \( -perm -040 -o -perm -004 \) -print)" ]; then
        invalid "DEPLOY_SSH_KEY_FILE is readable by group or others; chmod 600 it"
    fi
    if [ "$port" = 22 ]; then
        known_name=$host
    else
        known_name="[$host]:$port"
    fi
    ssh-keygen -F "$known_name" -f "$known_hosts" > /dev/null 2>&1 ||
        invalid "DEPLOY_KNOWN_HOSTS_FILE has no pinned key for $known_name (host keys are never accepted on first use)"
    case $public_url in
        https://?* | http://127.0.0.1:* | http://localhost:*) ;;
        *) invalid "DEPLOY_PUBLIC_URL must be an https URL (http only on loopback)" ;;
    esac
    only_chars "$public_url" 'A-Za-z0-9._~:/-' || invalid "DEPLOY_PUBLIC_URL has unexpected characters"
    if [ -n "$host_tool" ]; then
        case $host_tool in
            /*.py) ;;
            *) invalid "DEPLOY_HOST_TOOL must be the absolute path of an installed release_host.py" ;;
        esac
        only_chars "$host_tool" 'A-Za-z0-9._/-' || invalid "DEPLOY_HOST_TOOL has unexpected characters"
    fi
    case $remote_python in
        /*) ;;
        *) invalid "DEPLOY_REMOTE_PYTHON must be an absolute path" ;;
    esac
    only_chars "$remote_python" 'A-Za-z0-9._/-' || invalid "DEPLOY_REMOTE_PYTHON has unexpected characters"
    if [ "$owner" = root ] && [ "$user" != root ] && [ -z "$host_tool" ]; then
        invalid "DEPLOY_OWNER=root with a non-root DEPLOY_USER needs DEPLOY_HOST_TOOL (installed, root-owned, named in sudoers)"
    fi
    only_chars "$lock_wait" '0-9' && [ ${#lock_wait} -le 5 ] || invalid "AF_LOCK_WAIT_SECONDS must be a number of seconds"
    only_chars "$smoke_attempts" '0-9' && [ ${#smoke_attempts} -le 3 ] || invalid "AF_SMOKE_ATTEMPTS must be a number"
    only_chars "$release_ref" 'A-Za-z0-9._/-' || invalid "AF_RELEASE_REF must be a ref name such as origin/main"
    for path in $smoke_paths; do
        case $path in
            /*) ;;
            *) invalid "AF_SMOKE_PATHS entries must start with /" ;;
        esac
        only_chars "$path" 'A-Za-z0-9._~/?=&%-' || invalid "AF_SMOKE_PATHS entries must use URL-safe characters"
    done
    if [ -n "$pipeline_url" ]; then
        case $pipeline_url in
            https://?*) ;;
            *) invalid "AF_PIPELINE_URL must be an https URL" ;;
        esac
        only_chars "$pipeline_url" 'A-Za-z0-9._~:/-' || invalid "AF_PIPELINE_URL has unexpected characters"
    fi
    # Actor names come from CI (for example dependabot[bot]); keep a safe spelling.
    actor=$(printf '%s' "${actor:-unknown}" | tr -c 'A-Za-z0-9._@-' '_' | cut -c1-100)

    command -v ssh > /dev/null 2>&1 || invalid "ssh is not installed"
    command -v git > /dev/null 2>&1 || invalid "git is not installed"
    git -C "$repo_dir" cat-file -e "$sha^{commit}" 2> /dev/null ||
        invalid "commit $sha is not in $repo_dir (check out with full history)"
    for protected in $protected_environments; do
        if [ "$protected" = "$environment" ]; then
            git -C "$repo_dir" merge-base --is-ancestor "$sha" "$release_ref" 2> /dev/null ||
                invalid "$environment deploys only commits on $release_ref; $sha is not an ancestor"
            if [ -z "$(git -C "$repo_dir" tag --points-at "$sha" --list 'v[0-9]*')" ]; then
                invalid "$environment deploys only commits tagged v*; $sha has no such tag"
            fi
        fi
    done
}

remote() {
    ssh -F /dev/null \
        -o BatchMode=yes \
        -o IdentitiesOnly=yes \
        -o IdentityAgent=none \
        -o StrictHostKeyChecking=yes \
        -o "UserKnownHostsFile=$known_hosts" \
        -o GlobalKnownHostsFile=/dev/null \
        -o UpdateHostKeys=no \
        -o ConnectTimeout=20 \
        -o ServerAliveInterval=15 \
        -o ServerAliveCountMax=4 \
        -o LogLevel=ERROR \
        -i "$key_file" -p "$port" -- "$user@$host" "$@" 3>&-
}

lock_pid=""
staging=""
work=""

release_lock() {
    if [ -n "$lock_pid" ]; then
        exec 3>&-
        wait "$lock_pid" || true
        lock_pid=""
    fi
}

finish() {
    finish_status=$?
    release_lock
    if [ -n "$staging" ]; then
        remote "rm -rf -- $staging" || log "WARNING: could not remove $staging on the host"
    fi
    case $work in
        */af-deploy.*) rm -rf -- "$work" ;;
    esac
    exit "$finish_status"
}

start_lock() {
    mkfifo "$work/lock.in"
    remote "$tool lock --base $base --owner $owner --wait $lock_wait --hold --token $token" \
        < "$work/lock.in" > "$work/lock.out" 2>&1 &
    lock_pid=$!
    exec 3> "$work/lock.in"
    waited=0
    while ! grep -q '^AF-LOCK-ACQUIRED' "$work/lock.out" 2> /dev/null; do
        if ! kill -0 "$lock_pid" 2> /dev/null; then
            lock_status=0
            wait "$lock_pid" || lock_status=$?
            lock_pid=""
            sed 's/^/deploy: host: /' "$work/lock.out" >&2
            if [ "$lock_status" -eq 75 ]; then
                fail 75 "another deployment holds the $environment lock"
            fi
            fail 1 "could not acquire the $environment lock (exit $lock_status)"
        fi
        waited=$((waited + 1))
        [ "$waited" -le $((lock_wait + 120)) ] || fail 1 "timed out waiting for the lock holder"
        sleep 1
    done
    log "lock acquired for $environment"
}

host_tool_command() {
    remote "$tool $*"
}

record() {
    host_tool_command record --base "$base" --owner "$owner" --environment "$environment" --sha "$sha" \
        --mode "$mode" --result "$1" --actor "$actor" ${pipeline_url:+--pipeline-url "$pipeline_url"} \
        ${previous:+--previous "$previous"} --lock-token "$token" ||
        log "WARNING: could not write the deployment record"
}

restore_previous() {
    reason=$1
    if [ -z "$previous" ] || [ "$previous" = "$sha" ]; then
        record failed
        fail 1 "$reason; there is no other release to restore"
    fi
    log "$reason; restoring $previous"
    restore_status=0
    host_tool_command rollback --base "$base" --owner "$owner" --environment "$environment" \
        --sha "$previous" --lock-token "$token" || restore_status=$?
    if [ "$restore_status" -eq 0 ]; then
        record restored
        fail 3 "$reason; the previous release $previous was restored"
    fi
    record restore-failed
    fail 4 "$reason; restoring $previous failed - manual action needed (release_host.py status)"
}

run_public_smoke() {
    set -- --base-url "$public_url" --revision "$sha" \
        --release-config "$work/source/deploy/release.toml" --attempts "$smoke_attempts"
    for path in $smoke_paths; do
        set -- "$@" --path "$path"
    done
    "$python" "$script_dir/smoke.py" "$@"
}

[ $# -eq 0 ] || invalid "deploy_remote.sh takes no arguments; configure it through environment variables"
validate_inputs
base="$deploy_root/$environment"
log "$mode $sha to $environment on $host (owner $owner)"

trap finish EXIT
trap 'exit 130' INT TERM
work=$(mktemp -d "${TMPDIR:-/tmp}/af-deploy.XXXXXX")

git -C "$repo_dir" archive --format=tar -o "$work/release.tar" "$sha"
mkdir "$work/source"
tar -xf "$work/release.tar" -C "$work/source" deploy 2> /dev/null ||
    invalid "commit $sha has no deploy/ directory"
[ -f "$work/source/deploy/release.toml" ] || invalid "commit $sha has no deploy/release.toml"
[ -f "$work/source/deploy/runtime.manifest.toml" ] || invalid "commit $sha has no deploy/runtime.manifest.toml"

set -- --root "$work/source" --manifest deploy/runtime.manifest.toml \
    --environment "$environment" --output "$work/runtime.env"
# GitHub passes toJSON(vars) and toJSON(secrets); GitLab variables arrive as plain env.
if [ -n "${AF_RUNTIME_VARS_JSON:-}" ]; then
    set -- "$@" --values-json-env AF_RUNTIME_VARS_JSON
fi
if [ -n "${AF_RUNTIME_SECRETS_JSON:-}" ]; then
    set -- "$@" --values-json-env AF_RUNTIME_SECRETS_JSON
fi
"$python" "$script_dir/materialize_runtime.py" "$@" || invalid "runtime configuration is invalid (see above)"

token=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')

staging=$(remote 'umask 077 && mktemp -d /tmp/af-upload.XXXXXX') || fail 1 "could not connect to $host or create an upload directory"
case $staging in
    /tmp/af-upload.*) only_chars "$staging" 'A-Za-z0-9._/-' || fail 1 "unexpected upload directory from the host" ;;
    *)
        staging=""
        fail 1 "could not create an upload directory on the host"
        ;;
esac
remote "cat > $staging/release_host.py" < "$script_dir/release_host.py"
remote "cat > $staging/release.tar" < "$work/release.tar"
remote "cat > $staging/runtime.env" < "$work/runtime.env"
rm -f -- "$work/runtime.env"

if [ -n "$host_tool" ]; then
    remote "cmp -s $staging/release_host.py $host_tool" ||
        fail 2 "$host_tool on the host differs from this release_host.py; an administrator must install the new version"
    tool="$remote_python $host_tool"
    if [ "$owner" = root ] && [ "$user" != root ]; then
        tool="sudo -n $tool"
    fi
else
    tool="$remote_python $staging/release_host.py"
fi

start_lock
previous=$(host_tool_command status --base "$base" --owner "$owner" --field current) ||
    fail 1 "could not read the current release on the host"
if [ -n "$previous" ]; then
    only_chars "$previous" '0-9a-f' && [ ${#previous} -eq 40 ] || fail 1 "unexpected current release reported by the host"
fi

archive_path=$(host_tool_command secure-file --base "$base" --owner "$owner" --lock-token "$token" "$staging/release.tar") ||
    fail 1 "the host refused the uploaded archive"
runtime_path=$(host_tool_command secure-file --base "$base" --owner "$owner" --lock-token "$token" "$staging/runtime.env") ||
    fail 1 "the host refused the uploaded runtime file"
[ "$archive_path" = "$base/incoming/release.tar" ] && [ "$runtime_path" = "$base/incoming/runtime.env" ] ||
    fail 1 "the host did not take custody of the uploaded files"

step_status=0
host_tool_command prepare --base "$base" --owner "$owner" --environment "$environment" --sha "$sha" \
    --archive "$archive_path" --runtime "$runtime_path" --lock-token "$token" || step_status=$?
if [ "$step_status" -ne 0 ]; then
    record failed
    fail "$step_status" "prepare failed; nothing was changed"
fi

if [ "$mode" = deploy ]; then
    host_tool_command activate --base "$base" --owner "$owner" --environment "$environment" --sha "$sha" \
        --lock-token "$token" || step_status=$?
    case $step_status in
        0) ;;
        3)
            record restored
            fail 3 "activation failed; the previous release was restored"
            ;;
        4)
            record restore-failed
            fail 4 "activation failed and the restore failed - manual action needed"
            ;;
        *)
            record failed
            fail "$step_status" "activation failed before anything was switched"
            ;;
    esac
    host_tool_command promote --base "$base" --owner "$owner" --environment "$environment" --sha "$sha" \
        --lock-token "$token" || restore_previous "promote failed"
else
    host_tool_command rollback --base "$base" --owner "$owner" --environment "$environment" --sha "$sha" \
        --use-prepared-runtime --lock-token "$token" || step_status=$?
    case $step_status in
        0) ;;
        3 | 4)
            record failed
            fail "$step_status" "rollback to $sha failed (exit $step_status)"
            ;;
        *)
            record failed
            fail "$step_status" "rollback to $sha failed before anything was switched"
            ;;
    esac
fi

run_public_smoke || restore_previous "the public check failed"
host_tool_command cleanup --base "$base" --owner "$owner" --environment "$environment" --lock-token "$token" ||
    log "WARNING: cleanup failed; the deployment itself succeeded"
record succeeded
release_lock
log "$mode of $sha to $environment succeeded"
