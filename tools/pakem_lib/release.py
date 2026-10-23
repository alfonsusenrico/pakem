"""`pakem release` and `pakem host allow-device|revoke-device`: releases for services.

Direct delivery (ADR 0010): the device signs a request for one commit with its device key
(`ssh-keygen -Y`, namespace pakem-release) and sends it, with the `git archive` of the
commit when the host does not keep that release yet, over SSH to the deploy user. The
key's forced command on the host (af_receive.py) verifies the request, builds the images
natively once per commit, and runs the engine. No registry is involved; promotion and
rollback reuse what the host built and kept.

Where each project is released lives in ~/.config/pakem/hosts.toml (`projects`), written
by `pakem host allow-device`, which runs with the owner's own admin login on a terminal.
Releases use only the device key, so agents can run them; production needs --production.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import device_key
from .common import (
    EXIT_DONE,
    CheckFailed,
    CommandBusy,
    CommandRefused,
    Framework,
    GapsOpen,
    OwnerActionRequired,
    PakemError,
    confirm_steps,
    display_path,
    run_git,
    version_tuple,
)
from .gaps import blocking, evaluate, load_facts
from .host import DEPLOY_USER, HOST_OK, DeviceLink
from .host import CONTROL_PARENT, HostRecord, Remote, hosts_path, load_hosts, require_terminal, resolve_host, save_host
from .host import setup_host_command
from .requirements import device_label, ensure
from .service_project import (
    INSTALLED_RECEIVER,
    PRODUCTION,
    STAGING,
    Project,
    authorize_arguments,
    read_project,
    setup_words,
)

NAMESPACE = "pakem-release"
REQUEST_SCHEMA = "pakem.release-request/v1"
MAIN = "origin/main"
VERSION = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
GITHUB_REMOTE = re.compile(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/(.+?)(?:\.git)?/?$")
DELIVERY_LINE = re.compile(r"\bDelivery:?\**\s*:?\s*\**\s*(us|others|nobody)\b", re.IGNORECASE)
PASSED_CONCLUSIONS = {"success", "neutral", "skipped"}
# The receiver's exit when a revocation matches nothing (af_receive.EXIT_NOTHING_MATCHED).
REVOKE_NOTHING_MATCHED = 6
# The trigger a host's receiver records for a rollback; it tells "previous" what was abandoned.
ROLLBACK_TRIGGER = "rollback"
SERVICE_NEEDS = ("git", "ssh", "device-key", "device-key-unlocked")
CHECK_NEEDS = ("gh", "github-login")


# -- the project's own files ---------------------------------------------------------------


def check_gaps(project: Project) -> None:
    """A release waits for every required decision that blocks it (ADR 0011, decision 6)."""
    facts = load_facts(project.root)
    if facts is None:
        return
    waiting = blocking(evaluate(project.root, facts), "release")
    if waiting:
        names = ", ".join(f"{status.gap.id} ({status.problem or status.state})" for status in waiting)
        raise GapsOpen(f"decisions that block a release are open: {names}; see `pakem project check --gaps`")


def check_delivery(project: Project) -> None:
    """A project another team or nobody delivers is never released from here (area 3)."""
    agents = project.root / "AGENTS.md"
    if not agents.is_file():
        return
    match = DELIVERY_LINE.search(agents.read_text(encoding="utf-8"))
    if match and match[1].lower() != "us":
        raise CommandRefused(f"AGENTS.md says Delivery: {match[1]}; {project.name} is not released by pakem")


def source_url(project: Project) -> str | None:
    remote = run_git(project.root, "config", "--get", "remote.origin.url", check=False).stdout.strip()
    match = GITHUB_REMOTE.match(remote)
    return f"https://github.com/{match[1]}/{match[2]}" if match else None


def github_repository(project: Project) -> str:
    remote = run_git(project.root, "config", "--get", "remote.origin.url", check=False).stdout.strip()
    match = GITHUB_REMOTE.match(remote)
    if not match:
        raise CommandRefused(
            "release checks are read from GitHub for now; GitLab comes with its own area "
            f"(origin is {remote or 'missing'})"
        )
    return f"{match[1]}/{match[2]}"


def main_commit(project: Project) -> str:
    run_git(project.root, "fetch", "--quiet", "origin")
    return run_git(project.root, "rev-parse", f"{MAIN}^{{commit}}").stdout.strip()


def resolve_commit(project: Project, revision: str) -> str:
    completed = run_git(project.root, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}", check=False)
    if completed.returncode != 0:
        raise PakemError(f"{revision!r} names no commit in {display_path(project.root)}")
    return completed.stdout.strip()


def commit_checks(repository: str, commit: str) -> tuple[list[str], list[str]]:
    """Names of passed and of not-passed checks of a commit, from GitHub's check runs and
    commit statuses (ADR 0010, decision 2: a release refuses a commit whose checks did not pass)."""
    runs = subprocess.run(
        ["gh", "api", "--paginate", f"repos/{repository}/commits/{commit}/check-runs",
         "--jq", ".check_runs[] | [.name, .status, (.conclusion // \"\")] | @tsv"],
        capture_output=True, text=True,
    )
    statuses = subprocess.run(
        ["gh", "api", f"repos/{repository}/commits/{commit}/status", "--jq", ".statuses[] | [.context, .state] | @tsv"],
        capture_output=True, text=True,
    )
    for completed in (runs, statuses):
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()
            raise PakemError(f"gh could not read the checks of {commit[:12]}: {detail[-1] if detail else 'no output'}")
    passed, other = [], []
    for line in runs.stdout.splitlines():
        name, status, conclusion = (line.split("\t") + ["", ""])[:3]
        if status == "completed" and conclusion in PASSED_CONCLUSIONS:
            passed.append(name)
        else:
            other.append(f"{name} ({conclusion or status})")
    for line in statuses.stdout.splitlines():
        name, state = (line.split("\t") + [""])[:2]
        if state == "success":
            passed.append(name)
        else:
            other.append(f"{name} ({state})")
    return passed, other


def require_checks(project: Project, commit: str) -> str:
    passed, other = commit_checks(github_repository(project), commit)
    if other:
        raise CheckFailed(f"checks of {commit[:12]} have not passed: {', '.join(other)}")
    if not passed:
        raise CheckFailed(f"{commit[:12]} has no checks; a release needs the project's CI to pass first")
    return f"checks passed: {', '.join(passed)}"


# -- where the project is released ---------------------------------------------------------


def owner_setup_action(project_root: Path) -> str:
    """The one owner step that lets this device release a project, in its short form. The
    agent records the server and the project first (`pakem host add`, local only)."""
    known = list(load_hosts().values())
    if len(known) == 1:
        name = known[0].name
        return (f"record it with `pakem host add {name} --project-dir {shlex.quote(str(project_root))}` (agents may), "
                f"then OWNER ACTION REQUIRED: run `pakem setup host {name}` in your terminal")
    return ("ask the owner which server releases it (SSH target, private or public, CI runner, whether port 22 is "
            f"Tailscale SSH), record it with `pakem host add <name> --target <ssh-target> ... --project-dir "
            f"{shlex.quote(str(project_root))}` (agents may), then OWNER ACTION REQUIRED: run `pakem setup host <name>` "
            "in your terminal")


def host_for(project: Project) -> HostRecord:
    hosts = [record for record in load_hosts().values() if project.name in record.projects]
    if not hosts:
        raise OwnerActionRequired(
            f"this device may not release {project.name} anywhere yet: {owner_setup_action(project.root)}"
        )
    if len(hosts) > 1:
        names = ", ".join(record.name for record in hosts)
        raise PakemError(f"{project.name} is recorded for several hosts ({names}) in {display_path(hosts_path())}")
    return hosts[0]


# -- history and the meaning of "previous" ----------------------------------------------------


def previous_commit(deployments: list[dict], running: str | None) -> str | None:
    """The last release that was live and working before the running one (ADR 0010, owner
    decision 2026-10-23). Failed and restored attempts never count. A rollback drops the
    releases it rolled back from, so repeated steps walk back through what was live and
    never land on an abandoned release."""
    live: list[str] = []
    for entry in deployments:
        if entry.get("result") != "deployed":
            continue
        commit = entry["commit"]
        if entry.get("trigger") == ROLLBACK_TRIGGER and commit in live:
            while live[-1] != commit:
                live.pop()
        elif not live or live[-1] != commit:
            live.append(commit)
    if running is None or running not in live:
        return None
    position = len(live) - 1 - live[::-1].index(running)
    return live[position - 1] if position > 0 else None


def running_of(entry: dict) -> str | None:
    """The commit an environment runs. A host's receiver reports it; one installed before it
    did so for registry releases is read from the last deployment record instead."""
    if entry.get("running"):
        return entry["running"]
    current = entry.get("current") or {}
    result = current.get("result")
    if result in ("deployed", "unchanged"):
        return current.get("commit")
    if result in ("failed", "restored", "refused", "busy"):
        return current.get("previous_commit")
    return None


def kept_anywhere(history: dict, commit: str) -> bool:
    return any(commit in entry.get("kept", []) for entry in history["environments"].values())


# -- requests ------------------------------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_payload(work: Path, request: dict, archive: Path | None) -> Path:
    """Request line, signature line, then the archive: what the receiver reads."""
    request_file = work / "request.json"
    request_file.write_bytes(json.dumps(request, sort_keys=True, separators=(",", ":")).encode())
    signature = device_key.sign(request_file, NAMESPACE)
    payload = work / "payload"
    with payload.open("wb") as output:
        output.write(request_file.read_bytes() + b"\n")
        output.write(base64.b64encode(signature) + b"\n")
        if archive is not None:
            with archive.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
    return payload


def git_archive(project: Project, commit: str, destination: Path) -> str:
    with destination.open("wb") as output:
        completed = subprocess.run(["git", "-C", str(project.root), "archive", "--format=tar", commit],
                                   stdout=output, stderr=subprocess.PIPE, text=False)
    if completed.returncode != 0:
        raise PakemError(f"git archive {commit[:12]} failed: {completed.stderr.decode(errors='replace').strip()}")
    digest = hashlib.sha256()
    with destination.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


@dataclass
class Plan:
    action: str
    environment: str
    commit: str
    version: str | None = None
    allow_schema: bool = False
    steps: list[str] = field(default_factory=list)


def deliver(args, project: Project, record: HostRecord, plan: Plan, history: dict) -> None:
    """Sign and send the request, with the archive when the host keeps no release of it."""
    needs_archive = not kept_anywhere(history, plan.commit)
    with tempfile.TemporaryDirectory(prefix="pakem-release-") as directory:
        work = Path(directory)
        archive = work / "source.tar" if needs_archive else None
        request = {
            "schema": REQUEST_SCHEMA, "action": plan.action, "project": project.name,
            "environment": plan.environment, "commit": plan.commit, "version": plan.version,
            "archive": git_archive(project, plan.commit, archive) if archive is not None else None,
            "created": utc_now(), "device": device_label(), "source": source_url(project),
            "allow_schema": plan.allow_schema,
        }
        payload = build_payload(work, request, archive)
        print(f"Sending the signed request{' and the source archive' if archive else ''} to {record.name}.",
              file=sys.stderr if args.json else sys.stdout, flush=True)
        link = DeviceLink(record)
        code = link.send(payload, quiet=args.json)
    if code != HOST_OK:
        raise link.failure(code)


def print_plan(args, project: Project, record: HostRecord, plan: Plan) -> None:
    title = f"pakem release {plan.action} {project.name} {plan.environment} {plan.commit[:12]}"
    if plan.version:
        title += f" {plan.version}"
    lines = [title, *[f"  {step}" for step in plan.steps]]
    lines += [
        f"  sign the request with this device's key and send it to {DEPLOY_USER}@{record.address} "
        "(with the source archive when the host keeps no release of this commit)",
        "  the host builds it once if needed, then the engine deploys it",
    ]
    print("\n".join(lines), file=sys.stderr if args.json else sys.stdout)


def finish(args, plan: Plan, project: Project, record: HostRecord, done: list[str]) -> int:
    if args.json:
        print(json.dumps({"status": "done", "done": done, "owner_actions": [], "gaps": [], "checks": []}, indent=2))
    else:
        version = f" ({plan.version})" if plan.version else ""
        print(f"Released {project.name} {plan.commit[:12]}{version} to {plan.environment} on {record.name}.")
    return EXIT_DONE


def _prepare(args, needs: tuple[str, ...]) -> tuple[Project, HostRecord]:
    framework = Framework(Path(args.root))
    project = read_project(Path(args.project_dir))
    check_delivery(project)
    if args.release_command in ("publish", "promote", "rollback"):
        check_gaps(project)
    ensure(framework, needs, args, f"pakem release {args.release_command}")
    return project, host_for(project)


def _require_production(args, environment: str) -> None:
    if environment == PRODUCTION and not args.production:
        raise CommandRefused("this changes production; pass --production (agents: only on the owner's instruction)")


def run_release_publish(args) -> int:
    project, record = _prepare(args, SERVICE_NEEDS + CHECK_NEEDS)
    if not project.has_staging:
        raise CommandRefused(
            f"{project.name} declares no staging; release it with `pakem release promote vX.Y.Z --production`"
        )
    commit = main_commit(project)
    plan = Plan("publish", STAGING, commit, steps=[f"{MAIN} is {commit[:12]}", require_checks(project, commit)])
    print_plan(args, project, record, plan)
    if args.plan:
        print("Plan only: nothing changed.")
        return EXIT_DONE
    confirm_steps(args.yes, "pakem release publish")
    history = DeviceLink(record).query(f"history {project.name}")
    deliver(args, project, record, plan, history)
    return finish(args, plan, project, record, [f"published {commit} to staging"])


def existing_tag(project: Project, version: str) -> str | None:
    run_git(project.root, "fetch", "--quiet", "--tags", "origin")
    completed = run_git(project.root, "rev-parse", "--verify", "--quiet", f"refs/tags/{version}^{{commit}}", check=False)
    return completed.stdout.strip() if completed.returncode == 0 else None


def newest_version(project: Project) -> str | None:
    tags = run_git(project.root, "tag", "--list", "v*").stdout.split()
    versions = [tag for tag in tags if VERSION.match(tag)]
    return max(versions, key=lambda tag: version_tuple(tag[1:])) if versions else None


def run_release_promote(args) -> int:
    if not VERSION.match(args.version):
        raise PakemError(f"version must look like v1.2.3, not {args.version!r}")
    _require_production(args, PRODUCTION)
    project, record = _prepare(args, SERVICE_NEEDS + CHECK_NEEDS)
    if PRODUCTION not in project.environments:
        raise CommandRefused(f"{project.name} declares no [environments.production]")
    tagged = existing_tag(project, args.version)
    history = DeviceLink(record).query(f"history {project.name}")
    steps = []
    if args.commit:
        commit = resolve_commit(project, args.commit)
    elif project.has_staging:
        commit = running_of(history["environments"].get(STAGING, {}))
        if commit is None:
            raise CommandRefused("nothing runs in staging; publish a commit first")
        steps.append(f"staging runs {commit[:12]}")
    else:
        commit = main_commit(project)
        steps.append(f"{MAIN} is {commit[:12]} (no staging)")
    if tagged is not None and tagged != commit:
        raise CommandRefused(f"tag {args.version} already names {tagged[:12]}, not {commit[:12]}")
    newest = newest_version(project)
    if tagged is None and newest is not None and version_tuple(args.version[1:]) <= version_tuple(newest[1:]):
        raise CommandRefused(f"{args.version} is not newer than the newest release tag {newest}")
    steps.append(require_checks(project, commit))
    plan = Plan("promote", PRODUCTION, commit, version=args.version, steps=steps)
    plan.steps.append(f"after it deploys, tag {commit[:12]} as {args.version} and push the tag to origin")
    print_plan(args, project, record, plan)
    if args.plan:
        print("Plan only: nothing changed.")
        return EXIT_DONE
    confirm_steps(args.yes, "pakem release promote")
    deliver(args, project, record, plan, history)
    done = [f"promoted {commit} to production as {args.version}"]
    if tagged is None:
        run_git(project.root, "tag", "--annotate", args.version, "--message", f"Release {args.version}", commit)
        pushed = run_git(project.root, "push", "--quiet", "origin", f"refs/tags/{args.version}", check=False)
        if pushed.returncode != 0:
            raise OwnerActionRequired(
                f"OWNER ACTION REQUIRED: {args.version} is live but its tag was not pushed; run "
                f"`git -C {shlex.quote(str(project.root))} push origin {args.version}`"
            )
        done.append(f"tagged {args.version} and pushed the tag")
    return finish(args, plan, project, record, done)


def run_release_rollback(args) -> int:
    project, record = _prepare(args, SERVICE_NEEDS)
    environment = args.environment or (STAGING if project.has_staging else PRODUCTION)
    if environment not in project.environments:
        raise CommandRefused(f"{project.name} declares no [environments.{environment}]")
    _require_production(args, environment)
    history = DeviceLink(record).query(f"history {project.name}")
    entry = history["environments"].get(environment)
    if entry is None:
        raise CommandRefused(f"{project.name} has no direct {environment} environment on {record.name}")
    running = running_of(entry)
    if args.target == "previous":
        commit = previous_commit(entry.get("deployments", []), running)
        if commit is None:
            raise CommandRefused(f"no release ran in {environment} before {running[:12] if running else 'now'}")
    else:
        commit = resolve_commit(project, args.target)
    if commit == running:
        raise CommandRefused(f"{commit[:12]} already runs in {environment}")
    steps = [f"{environment} runs {running[:12] if running else 'nothing'}; back to {commit[:12]}"]
    plan = Plan("rollback", environment, commit, allow_schema=args.allow_schema, steps=steps)
    print_plan(args, project, record, plan)
    if args.plan:
        print("Plan only: nothing changed.")
        return EXIT_DONE
    confirm_steps(args.yes, "pakem release rollback")
    deliver(args, project, record, plan, history)
    return finish(args, plan, project, record, [f"rolled {environment} back to {commit}"])


def _running_line(environment: str, entry: dict) -> str:
    running = running_of(entry)
    line = f"{environment}: running {running[:12] if running else 'nothing'}"
    current = entry.get("current")
    if current:
        line += f"; last attempt {current['result']} {current['commit'][:12]} at {current['finished']}"
    return line


def _deployment_line(item: dict) -> str:
    version = item.get("version") or "-"
    return f"  {item['finished']}  {item['commit'][:12]}  {version:<9} {item['result']:<14} {item['actor']}"


def run_release_report(args) -> int:
    project, record = _prepare(args, ("ssh", "device-key", "device-key-unlocked"))
    kind = "history" if args.release_command == "history" else "status"
    data = DeviceLink(record).query(f"{kind} {project.name}")
    environments = data["environments"]
    if args.environment:
        if args.environment not in environments:
            raise PakemError(f"{project.name} has no {args.environment} environment on {record.name}")
        environments = {args.environment: environments[args.environment]}
    if args.json:
        print(json.dumps({"project": project.name, "host": record.name, "environments": environments}, indent=2))
        return EXIT_DONE
    lines = [f"{project.name} on {record.name}"]
    for environment, entry in sorted(environments.items()):
        lines.append(_running_line(environment, entry))
        if kind == "history":
            lines += [_deployment_line(item) for item in reversed(entry.get("deployments", []))]
    print("\n".join(lines))
    return EXIT_DONE


# -- device registration (the owner's admin login, on a terminal) ------------------------------


def _require_receiver(remote: Remote, record: HostRecord) -> None:
    if remote.run(f"test -f {INSTALLED_RECEIVER}").returncode != 0:
        raise CheckFailed(
            f"{record.name} runs an engine without direct delivery; update it with `{setup_host_command(record)}`"
        )


def _admin(record: HostRecord, arguments: list[str]) -> int:
    with tempfile.TemporaryDirectory(prefix="pk-", dir=CONTROL_PARENT) as control_dir:
        remote = Remote(record, Path(control_dir))
        try:
            _require_receiver(remote, record)
            return remote.run(shlex.join(arguments), tty=True).returncode
        finally:
            remote.close()


def run_host_allow_device(args) -> int:
    framework = Framework(Path(args.root))
    project = read_project(Path(args.project_dir))
    record = resolve_host(args.host)
    builder_key = builder_public_key(args, project)
    ensure(framework, ("ssh",) if builder_key else ("ssh", "device-key"), args, "pakem host allow-device")
    device = args.device if builder_key else device_label()
    arguments = authorize_arguments(project, record.deploy_root, record.proxy, args.switch_from_registry,
                                    device=device, key=builder_key)
    print(f"pakem host allow-device {record.name}: device {device} may release {project.name}")
    print(f"  sign in to {record.target} with your own login")
    print(
        f"  sudo {INSTALLED_RECEIVER} authorize: direct configs and deploy bases for "
        f"{', '.join(project.environments)} under {record.deploy_root}/{project.name}, this device's key "
        "limited to the release receiver, and its signer line"
    )
    print(f"  record {project.name} for {record.name} in {display_path(hosts_path())}")
    if args.plan:
        print("Plan only: nothing changed.")
        return EXIT_DONE
    words = ["pakem", "host", "allow-device", args.host, "--project-dir", str(project.root)]
    if builder_key:
        words += ["--device", device, "--key", args.key]
    if args.switch_from_registry:
        words.append("--switch-from-registry")
    require_terminal(shlex.join(words))
    confirm_steps(args.yes, "pakem host allow-device")
    code = _admin(record, arguments)
    if code != 0:
        raise PakemError(f"allowing the device failed on {record.target} with exit {code}; see the lines above")
    if builder_key is None and project.name not in record.projects:
        # Only this device's own permissions are recorded here.
        record.projects.append(project.name)
    save_host(record)
    print(f"Device {device} may release {project.name} on {record.name}.")
    return EXIT_DONE


def builder_public_key(args, project) -> str | None:
    """A shared builder (a CI runner, a client platform) gets one key per project (ADR 0010
    decision 8), so its name is `builder-<project>`."""
    if args.key is None:
        if args.device is not None:
            raise PakemError("--device names a shared builder's key; pass its public key with --key FILE")
        return None
    expected = f"builder-{project.name}"
    if args.device != expected:
        raise PakemError(f"a shared builder's key for {project.name} is named {expected}: pass --device {expected}")
    words = Path(args.key).expanduser().read_text(encoding="utf-8").split()
    if len(words) < 2 or words[0] != "ssh-ed25519":
        raise PakemError(f"{args.key} is not an ssh-ed25519 public key")
    return " ".join(words[:2])


def run_host_revoke_device(args) -> int:
    record = resolve_host(args.host)
    this_device = device_label()
    device = args.device or this_device
    scope = args.project or "every project"
    arguments = ["sudo", "python3", INSTALLED_RECEIVER, "revoke", "--device", device]
    words = ["pakem", "host", "revoke-device", args.host, "--device", device]
    if args.project:
        arguments += ["--project", args.project]
        words += ["--project", args.project]
    print(f"pakem host revoke-device {record.name}: device {device} may no longer release {scope}")
    print(f"  sign in to {record.target} with your own login")
    print(f"  sudo {INSTALLED_RECEIVER} revoke: remove the device's key lines for {scope}")
    if args.plan:
        print("Plan only: nothing changed.")
        return EXIT_DONE
    require_terminal(shlex.join(words))
    confirm_steps(args.yes, "pakem host revoke-device")
    code = _admin(record, arguments)
    if code not in (0, REVOKE_NOTHING_MATCHED):
        raise PakemError(f"revoking the device failed on {record.target} with exit {code}; see the lines above")
    if device == this_device:
        record.projects = [] if args.project is None else [name for name in record.projects if name != args.project]
        save_host(record)
    if code == REVOKE_NOTHING_MATCHED:
        raise CommandRefused(f"{record.target} had no key line for device {device} and {scope}; nothing changed there")
    print(f"Device {device} may no longer release {scope} on {record.name}.")
    return EXIT_DONE
