"""`pakem project init` and `pakem project adopt` (ADR 0011, area 3).

Both ask the project's fixed facts once (forge, visibility, release kind, delivery,
environments, stack, capabilities), record them in .pakem/project.toml, and write the
standard files: AGENTS.md, CI for the forge and runner, deploy files for a service we
release, agent files, OpenSpec, and the stack's formatter, linter, and test setup.
Everything a project decides for itself is left as a gap (gaps.py), never guessed.

- `init` makes a new repository: files, hooks, the stack's dev tools, the bootstrap
  commit, a GitHub repository (private unless --public), and the first push of `main`
  (the pre-push hook allows a new repository's single bootstrap commit).
- `adopt` works in an existing repository on a topic branch: it writes only missing
  files and reports every difference from the standard as a finding to resolve, never
  overwriting what is there.
"""

from __future__ import annotations

import filecmp
import importlib.util
import json
import re
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .capabilities import declarable_names, load_capabilities
from .common import (
    EXIT_DONE,
    EXIT_GAPS,
    EXIT_OWNER_ACTION,
    CommandRefused,
    Framework,
    PakemError,
    confirm_steps,
    display_path,
    is_git_work_tree,
    read_text,
    run_git,
    write_text_atomically,
)
from .gaps import (
    DELIVERIES,
    KINDS,
    STUB_MARKER,
    Facts,
    decisions_path,
    decisions_text,
    evaluate,
    facts_path,
    facts_text,
    load_facts,
    write_open_decisions,
)
from .githooks import HOOKS_DIR_NAME
from .host import load_hosts
from .release import owner_setup_action
from .requirements import ensure

NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[a-z][a-z0-9-]{0,62}$")
GITHUB_REMOTE = re.compile(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/(.+?)(?:\.git)?/?$")
STACKS = ("python", "node")
VISIBILITIES = ("private", "public")
ENVIRONMENT_SETS = {"production": ["production"], "staging,production": ["staging", "production"]}
BOOTSTRAP_SUBJECT = "chore: bootstrap project"
ADOPT_BRANCH = "chore/adopt-pakem"
# CI jobs on private repositories run on the owner's self-hosted runner (ADR 0010).
SELF_HOSTED = "[self-hosted, linux, x64, pakem]"
HOSTED = "ubuntu-24.04"
PYTHON_DEV_TOOLS = ("ruff", "pyright", "pytest")
NODE_DEV_TOOLS = (
    "prettier", "eslint", "@eslint/js", "typescript-eslint", "eslint-config-prettier", "typescript@~6.0",
)
PRIVATE_FILES = ("PROJECT_STATE.md", "JOURNAL.md")


# -- the facts --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    choices: tuple[str, ...] = ()
    default: str | None = None


def _questions(capability_names: list[str]) -> list[Question]:
    return [
        Question("description", "One line: what does the project do, and for whom?"),
        Question("visibility", "Repository visibility", VISIBILITIES, "private"),
        Question("kind", "Release kind: service (runs on a host), artifact (downloads), none", KINDS, "service"),
        Question("delivery", "Who releases it: us, others (a team or client), nobody", DELIVERIES, "us"),
        Question("delivery_note", "Who releases it, and where does our work end?"),
        Question("environments", "Environments", tuple(ENVIRONMENT_SETS), "production"),
        Question("stack", "Stack", STACKS, "python"),
        Question("capabilities", f"Capabilities, comma-separated ({', '.join(capability_names)}; or none)", (), "none"),
    ]


def _ask(question: Question) -> str:
    suffix = f" [{'/'.join(question.choices)}]" if question.choices else ""
    default = f" (default {question.default})" if question.default else ""
    while True:
        answer = input(f"{question.prompt}{suffix}{default}: ").strip() or (question.default or "")
        if answer and (not question.choices or answer in question.choices):
            return answer
        print("  please choose one of the listed values" if question.choices else "  an answer is needed")


def gather_answers(args, known: dict[str, str], capability_names: list[str]) -> dict[str, str]:
    """Flags first, then what the repository shows, then a prompt on a terminal; without a
    terminal every missing fact is a usage error naming its flag."""
    answers: dict[str, str] = {}
    missing = []
    for question in _questions(capability_names):
        if question.key == "delivery_note" and answers.get("delivery") != "others":
            continue
        value = getattr(args, question.key, None) or known.get(question.key)
        if value is None and question.key == "visibility" and getattr(args, "public", False):
            value = "public"
        if value is None:
            if sys.stdin.isatty():
                value = _ask(question)
            else:
                missing.append(f"--{question.key.replace('_', '-')}")
                continue
        if question.choices and value not in question.choices:
            raise PakemError(f"--{question.key.replace('_', '-')} must be one of {', '.join(question.choices)}")
        answers[question.key] = value
    if missing:
        raise PakemError(f"without a terminal, pass every fact as a flag; missing: {' '.join(missing)}")
    return answers


def build_facts(name: str, repository: str, answers: dict[str, str], capability_names: list[str]) -> Facts:
    if answers["kind"] == "artifact":
        raise CommandRefused("the artifact kind comes with its own area (GitLab and artifacts); not built yet")
    capabilities = [] if answers["capabilities"] == "none" else [
        part.strip() for part in answers["capabilities"].split(",") if part.strip()
    ]
    unknown = sorted(set(capabilities) - set(capability_names))
    if unknown:
        raise PakemError(f"unknown capabilities: {', '.join(unknown)} (choose from {', '.join(capability_names)})")
    if answers["kind"] == "service" and "deployed" not in capabilities:
        capabilities.append("deployed")
    return Facts(
        name=name, forge="github", repository=repository, visibility=answers["visibility"], kind=answers["kind"],
        delivery=answers["delivery"], stack=answers["stack"], environments=ENVIRONMENT_SETS[answers["environments"]],
        capabilities=sorted(capabilities), delivery_note=answers.get("delivery_note", ""),
    )


# -- the files -------------------------------------------------------------------------------------


@dataclass
class Files:
    """Path (relative to the repository) to content; mode 0o755 for executables."""

    texts: dict[str, str] = field(default_factory=dict)
    executable: set[str] = field(default_factory=set)
    # Starter code for a new repository only; adopt never adds it to an existing project.
    starters: set[str] = field(default_factory=set)

    def add(self, path: str, text: str, executable: bool = False, starter: bool = False) -> None:
        self.texts[path] = text
        if executable:
            self.executable.add(path)
        if starter:
            self.starters.add(path)


def _template(framework: Framework, relative: str) -> str:
    return read_text(framework.root / relative)


def agents_text(framework: Framework, facts: Facts, description: str) -> str:
    text = _template(framework, "skills/project-bootstrap/assets/project/AGENTS.md.tmpl")
    text = text.replace("{{project_name}}", facts.name)
    text = text.replace(
        "<!-- Two to five lines: what this project does, for whom, and what it must not do. -->", description
    )
    text = text.replace("Capabilities: <!-- e.g. database, deployed, money -->",
                        f"Capabilities: {', '.join(facts.capabilities) or 'none'}")
    delivery = facts.delivery if facts.delivery != "others" else f"others ({facts.delivery_note})"
    text = text.replace("Delivery: <!-- us | others: who releases it and where our work ends | nobody -->",
                        f"Delivery: {delivery}")
    commands = {
        "python": ["- Setup: `uv sync --locked`", "- Format: `uv run ruff format`", "- Lint: `uv run ruff check`",
                   "- Type check: `uv run pyright`", "- Test: `uv run pytest`"],
        "node": ["- Setup: `npm ci`", "- Format: `npm run format`", "- Lint: `npm run lint`",
                 "- Type check: `npm run typecheck`", "- Test: `npm test`", "- Build: `npm run build`"],
    }[facts.stack]
    if facts.releases_here:
        commands.append("- Release: `pakem release publish` (staging), `pakem release promote vX.Y.Z --production`")
    return text.replace("<!-- Exact commands an agent runs: setup, format, lint, typecheck, test, build, run. -->",
                        "\n".join(commands))


def ci_text(facts: Facts) -> str:
    runner = SELF_HOSTED if facts.visibility == "private" else HOSTED
    directory = {"python": "python_dir", "node": "node_dir"}[facts.stack]
    lines = [
        "# CI for every push, pull request, and release tag (Pakem CI templates; ADR 0010).",
        "name: ci",
        "",
        "on:",
        "  push:",
        "    branches: [main]",
        '    tags: ["v*"]',
        "  pull_request:",
        "",
        "permissions:",
        "  contents: read",
        "",
        "# One run per ref; a newer push cancels an older pull request run.",
        "concurrency:",
        "  group: ci-${{ github.ref }}",
        "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
        "",
        "jobs:",
        f"  verify-{facts.stack}:",
        f"    uses: ./.github/workflows/verify-{facts.stack}.yml",
        "    with:",
        f'      {directory}: "."',
        f"      runner: {json.dumps(runner)}",
    ]
    if "deployed" in facts.capabilities:
        lines += ["", "  security:", "    uses: ./.github/workflows/security.yml", "    with:",
                  f'      {directory}: "."', f"      runner: {json.dumps(runner)}"]
    if "database" in facts.capabilities:
        lines += ["", "  database:", "    uses: ./.github/workflows/database.yml", "    with:",
                  f"      stack: {facts.stack}", f"      runner: {json.dumps(runner)}"]
    return "\n".join(lines) + "\n"


def ci_workflows(facts: Facts) -> list[str]:
    names = [f"verify-{facts.stack}.yml"]
    if "deployed" in facts.capabilities:
        names.append("security.yml")
    if "database" in facts.capabilities:
        names.append("database.yml")
    return names


def deploy_files(framework: Framework, facts: Facts, files: Files) -> None:
    def service(name: str) -> str:
        return _template(framework, f"skills/project-bootstrap/assets/service/{name}").replace("{{name}}", facts.name)

    release = service("release.toml.tmpl")
    for environment in facts.environments:
        release += f"\n[environments.{environment}]\nkeep = {6 if environment == 'production' else 3}\n"
        if environment == "production" and "database" in facts.capabilities:
            release += 'backup = "required"\n'
    files.add("deploy/release.toml", release)
    settings = service("settings.toml.tmpl")
    settings += "".join(f'{environment} = "deploy/env/{environment}.env"\n' for environment in facts.environments)
    files.add("deploy/settings.toml", settings)
    for environment in facts.environments:
        level = "info" if environment == "production" else "debug"
        files.add(f"deploy/env/{environment}.env", f"LOG_LEVEL={level}\n")
    files.add("compose.yaml", service("compose.yaml.tmpl"))
    files.add("Dockerfile", service(f"Dockerfile.{facts.stack}.tmpl"))
    files.add("docs/deployment.md", service("deployment.md.tmpl").replace("{{environments}}", ", ".join(facts.environments)))
    if "database" in facts.capabilities:
        files.add("deploy/hooks/backup", service("backup.tmpl"), executable=True)


def stack_files(framework: Framework, facts: Facts, description: str, files: Files) -> None:
    tooling = "skills/project-bootstrap/assets/tooling"
    files.add(".editorconfig", _template(framework, f"{tooling}/.editorconfig"))
    if facts.stack == "python":
        package = facts.name.replace("-", "_")
        fragment = _template(framework, f"{tooling}/python/pyproject.fragment.toml")
        files.add("pyproject.toml", "\n".join([
            "[project]", f"name = {json.dumps(facts.name)}", 'version = "0.1.0"',
            f"description = {json.dumps(description)}", 'requires-python = ">=3.12"', "dependencies = []", "",
            "[build-system]", 'requires = ["hatchling"]', 'build-backend = "hatchling.build"', "",
        ]) + "\n" + fragment)
        files.add(f"src/{package}/__init__.py", f'"""{description}"""\n', starter=True)
        files.add("tests/test_package.py", f"import {package}\n\n\ndef test_imports() -> None:\n    assert {package}.__doc__\n",
                  starter=True)
    else:
        files.add("package.json", json.dumps({
            "name": facts.name, "version": "0.1.0", "private": True, "type": "module", "description": description,
            "scripts": {
                "format": "prettier --write .", "format:check": "prettier --check .", "lint": "eslint .",
                "typecheck": "tsc --noEmit", "test": "node --test test/", "build": "tsc",
            },
        }, indent=2) + "\n")
        files.add("tsconfig.json", "\n".join([
            "{",
            '  "compilerOptions": {',
            '    "target": "ES2023",',
            '    "module": "NodeNext",',
            '    "moduleResolution": "NodeNext",',
            '    "strict": true,',
            '    "outDir": "dist",',
            '    "rootDir": "src"',
            "  },",
            '  "include": ["src"]',
            "}",
        ]) + "\n")
        files.add("eslint.config.mjs", _template(framework, f"{tooling}/node/eslint.config.mjs"))
        files.add(".prettierrc.json", "{}\n")
        files.add(".prettierignore", "dist/\ncoverage/\nPROJECT_STATE.md\nJOURNAL.md\n")
        files.add("src/index.ts", f"// {description}\nexport const name = {json.dumps(facts.name)};\n", starter=True)
        files.add("test/smoke.test.mjs", 'import test from "node:test";\n\ntest("runs", () => {});\n', starter=True)


def standard_files(framework: Framework, facts: Facts, description: str) -> Files:
    files = Files()
    files.add("AGENTS.md", agents_text(framework, facts, description))
    files.add(".gitignore", _template(framework, "skills/project-bootstrap/assets/project/gitignore.snippet"))
    files.add(str(facts_path(Path())), facts_text(facts))
    files.add(str(decisions_path(Path())), decisions_text({}))
    openspec = _template(framework, "skills/project-bootstrap/assets/project/openspec-config.yaml.tmpl")
    openspec = openspec.replace("<name> - <one-line purpose>", f"{facts.name} - {description}")
    openspec = openspec.replace("Capabilities: <list>.", f"Capabilities: {', '.join(facts.capabilities) or 'none'}.")
    openspec = openspec.replace("Stack: <languages, frameworks, database>.", f"Stack: {facts.stack}.")
    files.add("openspec/config.yaml", openspec)
    for directory in ("openspec/specs", "openspec/changes", "research/notes", "research/prototypes", "research/evidence"):
        files.add(f"{directory}/.gitkeep", "")
    if facts.kind != "none":
        files.add("CHANGELOG.md", _template(framework, "skills/project-bootstrap/assets/project/CHANGELOG.md.tmpl"))
    if facts.delivery != "others":
        files.add(".github/workflows/ci.yml", ci_text(facts))
        for name in ci_workflows(facts):
            files.add(f".github/workflows/{name}",
                      _template(framework, f"skills/release-delivery/assets/ci/github/workflows/{name}"))
    if facts.releases_here:
        deploy_files(framework, facts, files)
        files.add(".env.example", _template(framework, "skills/project-bootstrap/assets/project/env.example.tmpl"))
    stack_files(framework, facts, description, files)
    return files


def write_files(repo: Path, files: Files, only_missing: bool) -> list[str]:
    written = []
    for relative, text in files.texts.items():
        path = repo / relative
        if only_missing and path.exists():
            continue
        write_text_atomically(path, text, mode=0o755 if relative in files.executable else None)
        written.append(relative)
    return written


def exclude_private_files(repo: Path) -> None:
    exclude = Path(run_git(repo, "rev-parse", "--git-path", "info/exclude").stdout.strip())
    if not exclude.is_absolute():
        exclude = repo / exclude
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    missing = [name for name in PRIVATE_FILES if name not in text.split()]
    if missing:
        write_text_atomically(exclude, text.rstrip("\n") + ("\n" if text else "") + "\n".join(missing) + "\n")


def write_state_file(framework: Framework, repo: Path, facts: Facts) -> None:
    path = repo / "PROJECT_STATE.md"
    if path.exists():
        return
    template = framework.root / "skills" / "state-and-handoff" / "assets" / "PROJECT_STATE.md.tmpl"
    text = read_text(template) if template.is_file() else "# PROJECT_STATE (local, untracked)\n"
    write_text_atomically(path, text.replace("{{project_name}}", facts.name))


def install_hooks(framework: Framework, repo: Path) -> None:
    """The framework hooks, as `pakem hooks install --apply` writes them."""
    for name in ("commit-msg", "pre-push"):
        write_text_atomically(repo / HOOKS_DIR_NAME / name, read_text(framework.hooks_dir / name), mode=0o755)
    run_git(repo, "config", "core.hooksPath", HOOKS_DIR_NAME)


def install_dev_tools(repo: Path, stack: str) -> None:
    if stack == "python":
        command = ["uv", "add", "--dev", *PYTHON_DEV_TOOLS]
    else:
        command = ["npm", "install", "--save-dev", *NODE_DEV_TOOLS]
    completed = subprocess.run(command, cwd=repo, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        raise PakemError(f"`{shlex.join(command)}` failed: {detail[-1] if detail else completed.returncode}")


# -- owner actions and the result ---------------------------------------------------------------


def _gh_api(endpoint: str) -> tuple[bool, object]:
    """A read-only GitHub API call with the owner's gh login: (ok, JSON or the error text)."""
    completed = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True)
    if completed.returncode != 0:
        return False, completed.stderr.strip()
    try:
        return True, json.loads(completed.stdout)
    except ValueError:
        return False, "gh answered with something other than JSON"


def runner_registered(repository: str) -> bool:
    """Whether GitHub lists a Pakem self-hosted runner for the repository."""
    ok, data = _gh_api(f"repos/{repository}/actions/runners")
    if not ok or not isinstance(data, dict):
        return False
    return any(
        "pakem" in {label.get("name") for label in runner.get("labels", [])} for runner in data.get("runners", [])
    )


def tag_protection(repository: str) -> str:
    """protected, unprotected, or unavailable (rulesets need a paid plan on private repositories)."""
    ok, data = _gh_api(f"repos/{repository}/rulesets")
    if not ok:
        return "unavailable" if "Upgrade to GitHub Pro" in str(data) else "unprotected"
    if isinstance(data, list) and any(ruleset.get("target") == "tag" for ruleset in data):
        return "protected"
    return "unprotected"


def follow_up_actions(facts: Facts, repo: Path) -> list[str]:
    """Owner-only steps still missing; what GitHub or this device already shows is skipped."""
    actions = []
    runner_hosts = [record.name for record in load_hosts().values() if record.runner]
    needs_runner = facts.delivery != "others" and facts.visibility == "private"
    if needs_runner and not runner_registered(facts.repository):
        if runner_hosts:
            actions.append(
                f"OWNER ACTION REQUIRED: run `pakem host runner add {runner_hosts[0]} {facts.repository}` in your "
                "terminal, so CI of this private repository runs on your self-hosted runner"
            )
        else:
            actions.append(
                "OWNER ACTION REQUIRED: this private repository's CI needs a self-hosted runner; set one up with "
                "`pakem setup host <target> --runner`, then `pakem host runner add <host> "
                f"{facts.repository}`"
            )
    # Rulesets on a private repository need a paid plan (default:no-billed): then only the
    # owner's own pushes create tags, and nothing is asked.
    if facts.kind != "none" and tag_protection(facts.repository) == "unprotected":
        actions.append(
            f"OWNER ACTION REQUIRED: protect release tags `v*` on {facts.repository} (Settings > Rules > Rulesets)"
        )
    allowed = any(facts.name in record.projects for record in load_hosts().values())
    if facts.releases_here and not allowed:
        actions.append(f"before the first release, {owner_setup_action(repo)}")
    return actions


def finish(args, repo: Path, facts: Facts, done: list[str], actions: list[str], findings: list[str]) -> int:
    statuses = evaluate(repo, facts)
    write_open_decisions(repo, statuses)
    open_gaps = [status for status in statuses if status.gap.required and not status.settled]
    if args.json:
        print(json.dumps({
            "status": "owner-action" if actions else ("gaps" if open_gaps else "done"),
            "done": done, "owner_actions": actions, "findings": findings,
            "gaps": [status.as_dict() for status in statuses if not status.settled],
            "checks": [],
        }, indent=2))
    else:
        for line in done:
            print(f"  done  {line}")
        for finding in findings:
            print(f"  FINDING  {finding}")
        for action in actions:
            print(action)
        if open_gaps:
            names = ", ".join(status.gap.id for status in open_gaps)
            print(f"Open gaps ({len(open_gaps)}): {names}. Next: `pakem project check --gaps --json`, then the "
                  "gap protocol (docs/pakem-command-map.md).")
    if actions:
        return EXIT_OWNER_ACTION
    return EXIT_GAPS if open_gaps else EXIT_DONE


def _capability_names(framework: Framework) -> list[str]:
    return declarable_names(load_capabilities(framework.root / "standards" / "capabilities.toml"))


def _plan(args, title: str, steps: list[str]) -> None:
    out = sys.stderr if args.json else sys.stdout
    print(title, file=out)
    for step in steps:
        print(f"  {step}", file=out)


# -- init ---------------------------------------------------------------------------------------------


def github_login() -> str:
    completed = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True)
    login = completed.stdout.strip()
    if completed.returncode != 0 or not login:
        raise PakemError("gh could not tell which GitHub account is logged in; pass --repository owner/name")
    return login


def run_project_init(args) -> int:
    framework = Framework(Path(args.root))
    if args.forge != "github":
        raise CommandRefused("GitLab projects come with the GitLab area; not built yet")
    repo = Path(args.directory).resolve()
    name = args.name or repo.name
    if not NAME.match(name):
        raise PakemError(f"invalid project name {name!r}: lowercase letters, digits, and hyphens, starting with a letter")
    if repo.exists() and any(repo.iterdir()):
        raise CommandRefused(f"{display_path(repo)} is not empty; use `pakem project adopt` for an existing project")
    capability_names = _capability_names(framework)
    answers = gather_answers(args, {}, capability_names)
    stack_tool = "uv" if answers["stack"] == "python" else "npm"
    ensure(framework, ("git", "gh", "github-login", stack_tool), args, "pakem project init")
    repository = args.repository or ("<your login>/" + name if args.plan else f"{github_login()}/{name}")
    if not args.plan and not REPOSITORY.match(repository):
        raise PakemError(f"--repository must look like owner/{name}")
    facts = build_facts(name, repository, answers, capability_names)
    files = standard_files(framework, facts, answers["description"])
    steps = [
        f"create {display_path(repo)} and `git init -b main`",
        f"write {len(files.texts)} files: AGENTS.md, .pakem/, CI, "
        + ("deploy files, " if facts.releases_here else "") + f"{facts.stack} tooling, OpenSpec, research/",
        "install the framework git hooks; keep PROJECT_STATE.md and JOURNAL.md untracked",
        f"add the {facts.stack} dev tools ({'uv add --dev' if facts.stack == 'python' else 'npm install --save-dev'})",
        f"commit `{BOOTSTRAP_SUBJECT}`",
        f"create the {facts.visibility} GitHub repository {repository} with your gh login and push `main`",
    ]
    _plan(args, f"pakem project init {facts.name} ({facts.kind}, delivery {facts.delivery}, {facts.stack})", steps)
    if args.plan:
        print("Plan only: nothing changed.", file=sys.stderr if args.json else sys.stdout)
        return EXIT_DONE
    confirm_steps(args.yes, "pakem project init")
    repo.mkdir(parents=True, exist_ok=True)
    run_git(repo, "init", "-q", "-b", "main")
    done = [f"git repository {display_path(repo)}"]
    write_files(repo, files, only_missing=False)
    done.append(f"wrote {len(files.texts)} files")
    exclude_private_files(repo)
    write_state_file(framework, repo, facts)
    install_hooks(framework, repo)
    done.append("git hooks installed")
    install_dev_tools(repo, facts.stack)
    done.append(f"{facts.stack} dev tools added")
    write_open_decisions(repo, evaluate(repo, facts))
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", BOOTSTRAP_SUBJECT)
    done.append(f"committed `{BOOTSTRAP_SUBJECT}`")
    created = subprocess.run(
        ["gh", "repo", "create", repository, f"--{facts.visibility}", "--source", str(repo), "--remote", "origin",
         "--description", answers["description"]],
        capture_output=True, text=True,
    )
    actions = []
    if created.returncode != 0:
        detail = created.stderr.strip().splitlines()
        actions.append(
            f"OWNER ACTION REQUIRED: create the {facts.visibility} repository {repository} "
            f"({detail[-1] if detail else 'gh failed'}), add it as origin, then run "
            f"`git -C {shlex.quote(str(repo))} push -u origin main`"
        )
    else:
        done.append(f"created {facts.visibility} GitHub repository {repository}")
        run_git(repo, "push", "-q", "-u", "origin", "main")
        done.append("pushed main")
    actions += follow_up_actions(facts, repo)
    return finish(args, repo, facts, done, actions, [])


# -- adopt ----------------------------------------------------------------------------------------------


def known_facts(repo: Path) -> tuple[dict[str, str], str | None]:
    """What the repository already shows, and its GitHub owner/name when origin is on GitHub."""
    known: dict[str, str] = {}
    remote = run_git(repo, "config", "--get", "remote.origin.url", check=False).stdout.strip()
    match = GITHUB_REMOTE.match(remote)
    repository = f"{match[1]}/{match[2]}" if match else None
    if (repo / "pyproject.toml").is_file():
        known["stack"] = "python"
    elif (repo / "package.json").is_file():
        known["stack"] = "node"
    agents = repo / "AGENTS.md"
    text = read_text(agents) if agents.is_file() else ""
    delivery = re.search(r"^(?:[-*+]\s+)?Delivery:\s*(us|others|nobody)\b(?:\s*\((.*)\))?", text, re.MULTILINE)
    if delivery:
        known["delivery"] = delivery[1]
        if delivery[2]:
            known["delivery_note"] = delivery[2]
    capabilities = re.search(r"^(?:[-*+]\s+)?Capabilities:\s*([a-z, -]+)$", text, re.MULTILINE)
    if capabilities:
        known["capabilities"] = capabilities[1].strip()
    release = repo / "deploy" / "release.toml"
    if release.is_file():
        known["kind"] = "service"
        try:
            environments = sorted(tomllib.loads(release.read_text(encoding="utf-8")).get("environments", {}))
        except tomllib.TOMLDecodeError:
            environments = []
        joined = ",".join(sorted(environments, key=lambda name: name != "staging"))
        if joined in ENVIRONMENT_SETS:
            known["environments"] = joined
    return known, repository


def deploy_contracts(framework: Framework):
    """The deployment contracts' own checks (contracts.py), as shipped with this framework."""
    path = framework.root / "skills" / "release-delivery" / "assets" / "deploy" / "contracts.py"
    spec = importlib.util.spec_from_file_location("pakem_deploy_contracts", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project_name(repo: Path, existing: Facts | None) -> str:
    if existing is not None:
        return existing.name
    release = repo / "deploy" / "release.toml"
    if release.is_file():
        try:
            name = tomllib.loads(read_text(release)).get("project")
        except tomllib.TOMLDecodeError:
            name = None
        if isinstance(name, str):
            return name
    return repo.name


REGISTRY_MARKERS = ("af_publish.py", "release-production", "release-staging")


def registry_findings(repo: Path) -> list[str]:
    """What the registry mode (ADR 0009) left behind once direct delivery replaces it."""
    findings = []
    workflows = repo / ".github" / "workflows"
    for path in sorted(workflows.glob("*.y*ml")) if workflows.is_dir() else []:
        if any(marker in read_text(path) for marker in REGISTRY_MARKERS):
            findings.append(
                f".github/workflows/{path.name} publishes releases to a registry (ADR 0009); direct delivery "
                "(`pakem release`) replaces it: remove it; `pakem setup host` with this project switches the host"
            )
    # deploy/af/contracts.py stays useful: a project's own checks may validate its deploy files with it.
    for relative in ("deploy/af/af_publish.py", "deploy/release-signing.pub"):
        if (repo / relative).exists():
            findings.append(f"{relative} belongs to the registry mode's signing and publishing; remove it with the workflows")
    release = repo / "deploy" / "release.toml"
    if release.is_file():
        try:
            build = tomllib.loads(read_text(release)).get("build")
        except tomllib.TOMLDecodeError:
            build = None
        if not isinstance(build, dict) or build.get("runner") != "local":
            findings.append('deploy/release.toml has no `[build] runner = "local"` (the host builds direct releases)')
    return findings


def adopt_findings(framework: Framework, repo: Path, files: Files) -> list[str]:
    """Differences from the standard in files adopt does not overwrite."""
    findings = registry_findings(repo)
    for relative in sorted(files.texts):
        path = repo / relative
        if not path.exists() or relative.endswith(".gitkeep") or relative.startswith(".pakem/"):
            continue
        if relative.startswith(".github/workflows/") and relative != ".github/workflows/ci.yml":
            source = framework.root / "skills/release-delivery/assets/ci/github/workflows" / Path(relative).name
            if not filecmp.cmp(source, path, shallow=False):
                findings.append(f"{relative} differs from the Pakem CI template; review it against the template")
    agents = read_text(repo / "AGENTS.md") if (repo / "AGENTS.md").is_file() else ""
    for line in ("Capabilities:", "Delivery:", "Language:"):
        if not re.search(rf"^(?:[-*+]\s+)?{line}", agents, re.MULTILINE):
            findings.append(f"AGENTS.md has no `{line}` line")
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file() and "[tool.ruff" not in read_text(pyproject):
        findings.append("pyproject.toml lacks the Python tooling defaults (ruff, pyright, pytest); merge the fragment")
    release = repo / "deploy" / "release.toml"
    if release.is_file():
        try:
            config = tomllib.loads(read_text(release))
        except tomllib.TOMLDecodeError as exc:
            findings.append(f"deploy/release.toml is not valid TOML: {exc}")
        else:
            problems = deploy_contracts(framework).validate_project_config(config)
            findings += [f"deploy/release.toml: {problem}" for problem in problems]
    for path in sorted(repo.rglob("*")):
        if path.is_file() and ".git" not in path.parts and path.stat().st_size < 1_000_000:
            relative = str(path.relative_to(repo))
            if relative in files.texts and STUB_MARKER in read_text(path):
                findings.append(f"{relative} is still a starter stub")
    return findings


def run_project_adopt(args) -> int:
    framework = Framework(Path(args.root))
    repo = Path(args.directory).resolve()
    if not is_git_work_tree(repo):
        raise PakemError(f"not a git repository: {display_path(repo)}")
    capability_names = _capability_names(framework)
    known, repository = known_facts(repo)
    existing = load_facts(repo)
    if existing is not None:
        known.update({
            "visibility": existing.visibility, "kind": existing.kind, "delivery": existing.delivery,
            "stack": existing.stack, "environments": ",".join(existing.environments),
            "capabilities": ",".join(existing.capabilities) or "none", "delivery_note": existing.delivery_note,
        })
        repository = repository or existing.repository
    if args.repository:
        repository = args.repository
    if repository is None or not REPOSITORY.match(repository):
        raise PakemError("origin is not a GitHub repository; pass --repository owner/name (GitLab comes later)")
    name = project_name(repo, existing)
    if not NAME.match(name):
        raise PakemError(f"project name {name!r} is not lowercase letters, digits, and hyphens; pass it in "
                         "deploy/release.toml or rename the directory")
    answers = gather_answers(args, known, capability_names)
    facts = build_facts(name, repository, answers, capability_names)
    files = standard_files(framework, facts, answers["description"])
    for starter in files.starters:
        files.texts.pop(starter, None)
    if (repo / ".github" / "workflows" / "ci.yml").exists():
        # The project's own CI stays; the reusable templates come only with a Pakem ci.yml.
        for name in ci_workflows(facts):
            files.texts.pop(f".github/workflows/{name}", None)
    missing = [relative for relative in files.texts if not (repo / relative).exists()]
    branch = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    steps = []
    if branch in ("main", "master"):
        steps.append(f"create and switch to branch {ADOPT_BRANCH} (adoption is its own change)")
    steps.append(f"write {len(missing)} missing files, keep every existing file as it is")
    steps.append("list each difference from the standard as a finding, and the project's gaps")
    _plan(args, f"pakem project adopt {facts.name} ({facts.kind}, delivery {facts.delivery}, {facts.stack})", steps)
    for relative in missing:
        print(f"    + {relative}", file=sys.stderr if args.json else sys.stdout)
    if args.plan:
        print("Plan only: nothing changed.", file=sys.stderr if args.json else sys.stdout)
        return EXIT_DONE
    confirm_steps(args.yes, "pakem project adopt")
    done = []
    if branch in ("main", "master"):
        run_git(repo, "switch", "-q", "-c", ADOPT_BRANCH)
        done.append(f"on branch {ADOPT_BRANCH}")
    if existing is not None:
        files.texts.pop(str(facts_path(Path())), None)
    if decisions_path(repo).exists():
        files.texts.pop(str(decisions_path(Path())), None)
    written = write_files(repo, files, only_missing=True)
    write_text_atomically(facts_path(repo), facts_text(facts))
    done.append(f"wrote {len(written)} missing files and {display_path(facts_path(repo))}")
    exclude_private_files(repo)
    findings = adopt_findings(framework, repo, files)
    return finish(args, repo, facts, done, follow_up_actions(facts, repo), findings)
