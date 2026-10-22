"""Command-line interface for `pakem`."""

from __future__ import annotations

import argparse
from pathlib import Path

from .changelog import run_changelog
from .common import Framework, PakemError, eprint
from .device_config import SETTINGS, run_config
from .doctor import run_doctor
from .drift_report import DEFAULT_CONFIG, DEFAULT_KEEP, run_report
from .enforce import ENFORCE_HOSTS, run_enforce
from .evals import HOSTS as EVAL_HOSTS
from .evals import MAX_TIMEOUT_SECONDS, MIN_TIMEOUT_SECONDS, run_eval
from .githooks import run_hooks_install
from .host import run_host_check, run_host_runner, run_setup_host
from .installer import run_install, run_uninstall
from .legacy_install import migrate
from .lint import run_lint
from .gaps import STAGES, gaps_report, run_decide
from .project import run_check, run_sync
from .project_init import run_project_adopt, run_project_init
from .propose import add_parser as add_propose_parser
from .propose import run_propose
from .release import (
    run_host_allow_device,
    run_host_revoke_device,
    run_release_promote,
    run_release_publish,
    run_release_report,
    run_release_rollback,
)
from .schedule import DEFAULT_HOUR, DEFAULT_MINUTE, DEFAULT_WEEKDAY, default_context, run_schedule
from .setup_device import run_setup_check, run_setup_device, run_setup_key
from .status import HOOK_PROMPT, HOOK_SESSION_START, run_status
from .update import run_update

DEFAULT_ROOT = Path(__file__).resolve().parent.parent.parent
# Commands that write installed state migrate it from the former name first;
# install does so itself, after its source checks.
MIGRATING_COMMANDS = ("uninstall", "enforce", "schedule")
# Maturity per area (ADR 0011, decision 10); the kernel rule "use pakem" applies to stable ones.
AREAS = (
    ("status and updates", "experimental", "status, update, config"),
    ("device", "experimental", "setup device, setup key, setup check"),
    ("host", "experimental", "setup host, setup check --host, host runner add|remove"),
    ("project", "experimental", "project init, project adopt, project check --gaps, decide"),
    ("secrets", "draft", "secret"),
    ("proposals", "experimental", "propose new|revise|list|show|accept|reject"),
    ("release", "experimental", "release publish|promote|rollback|history|status, host allow-device|revoke-device"),
    (
        "framework and agents",
        "stable",
        "install, uninstall, doctor, lint, changelog, project sync|check, report, schedule, hooks, enforce, eval",
    ),
)


def _add_apply(parser: argparse.ArgumentParser, verb: str = "write") -> None:
    parser.add_argument("--apply", action="store_true", help=f"{verb} changes (default is a dry run)")


def _bounded_int(low: int, high: int):
    def parse(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"expected a whole number, got {raw!r}") from exc
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"must be between {low} and {high}, got {value}")
        return value

    return parse


def areas_text() -> str:
    width = max(len(name) for name, _, _ in AREAS)
    lines = ["areas (stage: commands; see docs/pakem-command-map.md):"]
    lines += [f"  {name.ljust(width)}  {stage}: {commands}" for name, stage, commands in AREAS]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pakem",
        description="Pakem: kernel, skills, and project checks for coding agents.",
        epilog=areas_text(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help="framework repository root (default: the checkout containing this tool)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("help", help="show commands and each area's maturity stage")

    status = commands.add_parser(
        "status", help="compare the installed framework with merged main, and this project's pins"
    )
    status.add_argument("--brief", action="store_true", help="only the summary and problems")
    status.add_argument("--json", action="store_true", help="machine-readable result")
    network = status.add_mutually_exclusive_group()
    network.add_argument(
        "--refresh", action="store_true", help="fetch origin now (default: at most every 4 hours)"
    )
    network.add_argument("--offline", action="store_true", help="never fetch; use the last check")
    status.add_argument("--hook", choices=(HOOK_SESSION_START, HOOK_PROMPT), help=argparse.SUPPRESS)
    status.add_argument("--host", default="claude", choices=ENFORCE_HOSTS, help=argparse.SUPPRESS)

    update = commands.add_parser(
        "update", help="install the framework from merged main, then list the files that changed"
    )
    update.add_argument("--plan", action="store_true", help="print the steps without doing them")
    update.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    update.add_argument("--json", action="store_true", help="machine-readable result")

    setup = commands.add_parser("setup", help="prepare this device (installs only after one confirmation)")
    setup_commands = setup.add_subparsers(dest="setup_command", required=True)
    setup_device = setup_commands.add_parser(
        "device", help="prepare everything a device can need in one go (commands also prepare their own needs)"
    )
    setup_device.add_argument("--plan", action="store_true", help="print the steps without doing them")
    setup_device.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    setup_device.add_argument("--json", action="store_true", help="machine-readable result")
    setup_key = setup_commands.add_parser(
        "key", help="create the device key on your terminal (asks for its passphrase)"
    )
    setup_key.add_argument(
        "--no-passphrase",
        action="store_true",
        help="no passphrase, only for a device nobody can unlock (recorded in device.toml)",
    )
    setup_host = setup_commands.add_parser(
        "host", help="install the release engine on a server over SSH (your own login, from your terminal)"
    )
    setup_host.add_argument("target", help="SSH target, such as admin@203.0.113.7 or an alias in ~/.ssh/config")
    setup_host.add_argument("--name", help="name for this host in ~/.config/pakem/hosts.toml (default: from target)")
    setup_host.add_argument("--port", type=_bounded_int(1, 65535), help="SSH port (default: SSH's own)")
    setup_host.add_argument("--trigger", choices=("pull", "push"), default="pull", help="deploy trigger (default: pull)")
    setup_host.add_argument("--deploy-root", default="/srv/deploy", help="deploy root on the host (default: /srv/deploy)")
    setup_host.add_argument("--no-proxy", action="store_true", help="no Caddy reverse proxy (a private host)")
    setup_host.add_argument("--runner", action="store_true", help="also install the isolated CI runner")
    setup_host.add_argument(
        "--project-dir", action="append", metavar="DIR",
        help="let this device release this service project there (repeatable; default: here, if it is one)",
    )
    setup_host.add_argument(
        "--no-passphrase", action="store_true", help="a new device key without a passphrase (a headless device only)"
    )
    setup_host.add_argument(
        "--release-port", type=_bounded_int(1, 65535), metavar="PORT",
        help="a second OpenSSH port for releases, when port 22 skips forced commands (Tailscale SSH)",
    )
    setup_host.add_argument(
        "--allow-unmerged", action="store_true", help="upload from a checkout that is not merged main (testing only)"
    )
    setup_host.add_argument(
        "--allow-dirty", action="store_true", help="upload from a checkout with uncommitted changes (testing only)"
    )
    setup_host.add_argument("--plan", action="store_true", help="print the steps without doing them")
    setup_host.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    setup_check = setup_commands.add_parser("check", help="list tools, versions, the key, and forge logins")
    setup_check.add_argument("--host", metavar="NAME", help="check a recorded host instead of this device")
    setup_check.add_argument("--json", action="store_true", help="machine-readable result")

    host = commands.add_parser("host", help="manage servers set up with `pakem setup host`")
    host_commands = host.add_subparsers(dest="host_command", required=True)
    runner = host_commands.add_parser("runner", help="register a private repository with the host's CI runner")
    runner_commands = runner.add_subparsers(dest="runner_command", required=True)
    for verb, text in (("add", "register"), ("remove", "unregister")):
        runner_parser = runner_commands.add_parser(verb, help=f"{text} a repository (token from your gh login)")
        runner_parser.add_argument("host", help="host name or target from ~/.config/pakem/hosts.toml")
        runner_parser.add_argument("repository", help="owner/name on GitHub")
        runner_parser.add_argument("--plan", action="store_true", help="print the steps without doing them")
        runner_parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
        runner_parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    allow = host_commands.add_parser(
        "allow-device", help="let this device's key release a project there (your own login, from your terminal)"
    )
    allow.add_argument("host", help="host name or target from ~/.config/pakem/hosts.toml")
    allow.add_argument("--project-dir", default=".", help="the project's repository (default: here)")
    allow.add_argument(
        "--switch-from-registry", action="store_true", help="move environments that deploy from a registry to direct"
    )
    revoke = host_commands.add_parser("revoke-device", help="remove a device's key, or one of its projects")
    revoke.add_argument("host", help="host name or target from ~/.config/pakem/hosts.toml")
    revoke.add_argument("--device", help="device name as the host records it (default: this device)")
    revoke.add_argument("--project", help="revoke only this project")
    for device_parser in (allow, revoke):
        device_parser.add_argument("--plan", action="store_true", help="print the steps without doing them")
        device_parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
        device_parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    release = commands.add_parser("release", help="release a service: signed source to its host, built there")
    release_commands = release.add_subparsers(dest="release_command", required=True)
    publish = release_commands.add_parser("publish", help="the current main commit to staging (checks must pass)")
    promote = release_commands.add_parser("promote", help="tag a release and deploy it to production")
    promote.add_argument("version", help="vMAJOR.MINOR.PATCH, newer than every release tag")
    promote.add_argument("--commit", help="promote this commit instead of the one staging runs")
    rollback = release_commands.add_parser("rollback", help="go back to an earlier release kept on the host")
    rollback.add_argument("target", help="previous, a release tag, or a commit")
    rollback.add_argument("--environment", help="default: staging when the project has it, else production")
    rollback.add_argument(
        "--allow-schema", action="store_true", help="the target has another database schema; you restored the backup"
    )
    for changing in (promote, rollback):
        changing.add_argument("--production", action="store_true", help="confirm a production change")
    for changing in (publish, promote, rollback):
        changing.add_argument("--plan", action="store_true", help="print the steps without doing them")
        changing.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    for name, text in (("history", "every release and result per environment"), ("status", "what runs where")):
        report = release_commands.add_parser(name, help=text)
        report.add_argument("environment", nargs="?", help="one environment only")
        report.add_argument("--plan", action="store_true", help=argparse.SUPPRESS)
        report.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    for release_parser in release_commands.choices.values():
        release_parser.add_argument("--project-dir", default=".", help="the project's repository (default: here)")
        release_parser.add_argument("--json", action="store_true", help="machine-readable result")

    config = commands.add_parser("config", help="device settings in ~/.config/pakem/config.toml")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_get = config_commands.add_parser("get", help="print one setting")
    config_get.add_argument("key", choices=sorted(SETTINGS))
    config_set = config_commands.add_parser("set", help="change one setting")
    config_set.add_argument("key", choices=sorted(SETTINGS))
    config_set.add_argument("value")
    config_commands.add_parser("list", help="print every setting with its allowed values")

    install = commands.add_parser("install", help="install the kernel and skills into host locations")
    install.add_argument("--hosts", help="comma-separated host ids from hosts/targets.toml (default: all)")
    install.add_argument("--profile", action="append", metavar="DIR", help="profile directory (repeatable)")
    install.add_argument(
        "--force", action="store_true", help="back up and replace locally edited managed files"
    )
    install.add_argument(
        "--allow-dirty",
        action="store_true",
        help="install from a framework tree with uncommitted changes, or one that is not a git work tree",
    )
    install.add_argument(
        "--allow-unmerged",
        action="store_true",
        help=(
            "install from a framework checkout that is not on main or differs from origin/main; "
            "independent of --allow-dirty (testing a dirty branch needs both)"
        ),
    )
    install.add_argument(
        "--strict-source",
        action="store_true",
        help="refuse --apply when a --profile git checkout is not on a clean main (default: warn)",
    )
    _add_apply(install)

    uninstall = commands.add_parser("uninstall", help="remove pakem-managed files and restore backups")
    uninstall.add_argument(
        "--force", action="store_true", help="back up and remove locally edited managed files"
    )
    _add_apply(uninstall, "remove")

    doctor = commands.add_parser("doctor", help="compare installed state with the framework")
    doctor.add_argument("--repos", action="append", metavar="DIR", help="check git repos in DIR (repeatable)")
    doctor.add_argument(
        "--profile",
        action="append",
        metavar="DIR",
        help="check that this profile's skills are installed (repeatable)",
    )

    lint = commands.add_parser("lint", help="framework self-checks")
    lint.add_argument(
        "--profile", action="append", metavar="DIR", help="also lint this profile directory (repeatable)"
    )

    changelog = commands.add_parser(
        "changelog", help="show pending changes/ fragments or release them into CHANGELOG.md"
    )
    changelog.add_argument(
        "--release", metavar="X.Y.Z", help="build the release section for this version (dry run by default)"
    )
    changelog.add_argument(
        "--date", metavar="YYYY-MM-DD", help="release date (default: today; requires --release)"
    )
    _add_apply(changelog)

    project = commands.add_parser("project", help="start, adopt, and check projects")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    sync = project_commands.add_parser("sync", help="insert or refresh pinned framework blocks")
    sync.add_argument("repo", nargs="?", help="repository directory (default: current directory)")
    sync.add_argument("--profile", action="append", metavar="DIR", help="profile directory (repeatable)")
    sync.add_argument("--replace-legacy", action="store_true", help="replace a legacy owner-baseline block")
    _add_apply(sync)
    check = project_commands.add_parser(
        "check", help="verify pinned blocks, size, overrides, capabilities, and project files"
    )
    check.add_argument("repo", nargs="?", help="repository directory (default: current directory)")
    check.add_argument("--gaps", action="store_true", help="list the project's gaps and their decisions instead")
    check.add_argument("--json", action="store_true", help="machine-readable result (with --gaps)")
    init = project_commands.add_parser("init", help="start a new project: files, CI, hooks, GitHub repository")
    init.add_argument("directory", help="where the new repository goes (its name is the project name)")
    init.add_argument("--name", help="project name (default: the directory name)")
    init.add_argument("--repository", help="GitHub owner/name (default: your gh login / the name)")
    adopt = project_commands.add_parser("adopt", help="bring an existing repository to the standard")
    adopt.add_argument("directory", nargs="?", default=".", help="the repository (default: here)")
    adopt.add_argument("--repository", help="GitHub owner/name (default: from origin)")
    for starting in (init, adopt):
        starting.add_argument("--forge", choices=("github", "gitlab"), default="github")
        starting.add_argument("--description", help="one line: what the project does, and for whom")
        starting.add_argument("--visibility", choices=("private", "public"))
        starting.add_argument("--public", action="store_true", help="same as --visibility public")
        starting.add_argument("--kind", choices=("service", "artifact", "none"), help="release kind")
        starting.add_argument("--delivery", choices=("us", "others", "nobody"), help="who releases it")
        starting.add_argument("--delivery-note", help="with --delivery others: who, and where our work ends")
        starting.add_argument("--environments", choices=("production", "staging,production"))
        starting.add_argument("--stack", choices=("python", "node"))
        starting.add_argument("--capabilities", help="comma-separated capability names, or none")
        starting.add_argument("--plan", action="store_true", help="print the steps without doing them")
        starting.add_argument("--yes", action="store_true", help="do not ask for confirmation")
        starting.add_argument("--json", action="store_true", help="machine-readable result")

    add_propose_parser(commands)

    decide = commands.add_parser("decide", help="record a decision on one of the project's gaps")
    decide.add_argument("gap", help="gap id, such as health.proof (see `pakem project check --gaps`)")
    decide.add_argument("answer", nargs="?", help="the answer")
    decide.add_argument("--reason", help="why, in one line")
    decide.add_argument("--provisional", metavar="ASSUMPTION", help="a reversible working assumption")
    decide.add_argument("--revisit", metavar="TRIGGER", help="with --provisional: when to revisit it")
    decide.add_argument("--pending", metavar="WHAT", help="waiting for outside information")
    decide.add_argument("--from", dest="provider", metavar="WHO", help="with --pending: who provides it")
    decide.add_argument("--ask-again", metavar="YYYY-MM-DD", help="with --pending: when to ask again")
    decide.add_argument("--question", help="record a new blocker no listed gap covers")
    decide.add_argument("--blocks", help=f"with --question: stages it blocks ({','.join(STAGES)})")
    decide.add_argument("--repo", default=".", help="repository directory (default: here)")

    report = commands.add_parser("report", help="write a read-only Markdown drift report")
    report.add_argument("--config", metavar="FILE", help=f"report config (default: {DEFAULT_CONFIG})")
    report.add_argument(
        "--output-dir",
        metavar="DIR",
        help="where reports are written (default: ~/.local/state/pakem/reports)",
    )
    report.add_argument(
        "--keep",
        type=_bounded_int(1, 10_000),
        metavar="N",
        help=f"dated reports to keep (default: the config's keep, else {DEFAULT_KEEP})",
    )

    schedule = commands.add_parser("schedule", help="run pakem report weekly through launchd (macOS)")
    schedule_commands = schedule.add_subparsers(dest="schedule_command", required=True)
    schedule_install = schedule_commands.add_parser("install", help="install the weekly launchd agent")
    schedule_install.add_argument(
        "--weekday",
        type=_bounded_int(0, 7),
        default=DEFAULT_WEEKDAY,
        metavar="N",
        help="launchd weekday, 0 and 7 are Sunday (default: 1, Monday)",
    )
    schedule_install.add_argument(
        "--hour", type=_bounded_int(0, 23), default=DEFAULT_HOUR, metavar="H", help="hour (default: 8)"
    )
    schedule_install.add_argument(
        "--minute", type=_bounded_int(0, 59), default=DEFAULT_MINUTE, metavar="M", help="minute (default: 0)"
    )
    _add_apply(schedule_install)
    schedule_uninstall = schedule_commands.add_parser("uninstall", help="unload and remove the launchd agent")
    _add_apply(schedule_uninstall, "remove")
    schedule_commands.add_parser("status", help="show the plist and launchd state (read-only)")

    hooks = commands.add_parser("hooks", help="manage repository git hooks")
    hooks_commands = hooks.add_subparsers(dest="hooks_command", required=True)
    hooks_install = hooks_commands.add_parser("install", help="install commit-msg and pre-push hooks")
    hooks_install.add_argument("repo", nargs="?", help="repository directory (default: current directory)")
    hooks_install.add_argument("--force", action="store_true", help="replace differing hooks in .githooks/")
    hooks_install.add_argument(
        "--protect",
        action="append",
        metavar="BRANCH",
        help=(
            "protect this branch (repeatable); replaces the repository's protected set, "
            "so list main too if it stays protected (default when never set: main, master)"
        ),
    )
    hooks_install.add_argument(
        "--protect-none",
        action="store_true",
        help=(
            "protect no branch, so code may be pushed directly to main; only for a project whose "
            "AGENTS.md records default:branching in ## Overrides"
        ),
    )
    hooks_install.add_argument(
        "--local-only",
        action="store_true",
        help="add .githooks/ to .git/info/exclude so the hooks are never committed (client repositories)",
    )
    _add_apply(hooks_install)

    enforce = commands.add_parser("enforce", help="install host-level guardrails")
    enforce_commands = enforce.add_subparsers(dest="enforce_command", required=True)
    for name in ("install", "uninstall"):
        enforce_parser = enforce_commands.add_parser(name, help=f"{name} guardrails for one host")
        enforce_parser.add_argument("--host", required=True, choices=ENFORCE_HOSTS)
        _add_apply(enforce_parser)

    evals = commands.add_parser("eval", help="measure kernel compliance per agent host")
    eval_commands = evals.add_subparsers(dest="eval_command", required=True)
    eval_run = eval_commands.add_parser(
        "run", help="run compliance scenarios against one host CLI (dry run by default)"
    )
    eval_run.add_argument("--host", required=True, choices=sorted(EVAL_HOSTS))
    eval_run.add_argument(
        "--scenario", action="append", metavar="ID", help="run only this scenario (repeatable; default: all)"
    )
    eval_run.add_argument(
        "--repeat", type=_bounded_int(1, 20), default=3, metavar="K", help="repeats per scenario (default: 3)"
    )
    eval_run.add_argument(
        "--timeout",
        type=_bounded_int(MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS),
        metavar="SECONDS",
        help="per-run timeout for every scenario (default: each scenario's timeout_seconds)",
    )
    eval_run.add_argument(
        "--keep-fixtures",
        action="store_true",
        help="keep each fixture and a redacted transcript in the system temp directory",
    )
    eval_run.add_argument(
        "--results-dir", metavar="DIR", help="where results are appended (default: evals/results in the framework)"
    )
    eval_run.add_argument(
        "--apply",
        action="store_true",
        help="run live, billed agent sessions and record results (default is a dry run)",
    )
    return parser


def migrate_before(args: argparse.Namespace) -> None:
    if args.command not in MIGRATING_COMMANDS or not hasattr(args, "apply"):
        return
    migration = migrate(Framework(Path(args.root)), args.apply, default_context())
    migration.print()
    for warning in migration.warnings:
        print(f"WARNING: {warning}")


def dispatch(args: argparse.Namespace) -> int:
    migrate_before(args)
    if args.command == "help":
        build_parser().print_help()
        return 0
    if args.command == "status":
        return run_status(args)
    if args.command == "update":
        return run_update(args)
    if args.command == "config":
        return run_config(args)
    if args.command == "setup" and args.setup_command == "device":
        return run_setup_device(args)
    if args.command == "setup" and args.setup_command == "key":
        return run_setup_key(args)
    if args.command == "setup" and args.setup_command == "host":
        return run_setup_host(args)
    if args.command == "setup" and args.setup_command == "check" and args.host:
        return run_host_check(args, args.host)
    if args.command == "setup" and args.setup_command == "check":
        return run_setup_check(args)
    if args.command == "host" and args.host_command == "runner":
        return run_host_runner(args)
    if args.command == "host" and args.host_command == "allow-device":
        return run_host_allow_device(args)
    if args.command == "host" and args.host_command == "revoke-device":
        return run_host_revoke_device(args)
    if args.command == "release":
        commands = {
            "publish": run_release_publish,
            "promote": run_release_promote,
            "rollback": run_release_rollback,
            "history": run_release_report,
            "status": run_release_report,
        }
        return commands[args.release_command](args)
    if args.command == "install":
        return run_install(args)
    if args.command == "uninstall":
        return run_uninstall(args)
    if args.command == "doctor":
        return run_doctor(args)
    if args.command == "lint":
        return run_lint(args)
    if args.command == "changelog":
        return run_changelog(args)
    if args.command == "project" and args.project_command == "sync":
        return run_sync(args)
    if args.command == "project" and args.project_command == "check" and args.gaps:
        return gaps_report(Path(args.repo or ".").resolve(), args.json)
    if args.command == "project" and args.project_command == "check":
        return run_check(args)
    if args.command == "project" and args.project_command == "init":
        return run_project_init(args)
    if args.command == "project" and args.project_command == "adopt":
        return run_project_adopt(args)
    if args.command == "decide":
        return run_decide(args)
    if args.command == "propose":
        return run_propose(args)
    if args.command == "report":
        return run_report(args)
    if args.command == "schedule":
        return run_schedule(args)
    if args.command == "hooks":
        return run_hooks_install(args)
    if args.command == "enforce":
        return run_enforce(args)
    if args.command == "eval" and args.eval_command == "run":
        return run_eval(args)
    raise PakemError(f"unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return dispatch(args)
    except PakemError as exc:
        eprint(f"pakem: error: {exc}")
        return exc.exit_code
