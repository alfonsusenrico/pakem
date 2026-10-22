"""pakem project init | adopt, pakem decide, project check --gaps, and the bootstrap push.

Runs the real CLI with the repository's own framework files (templates, hooks), a fake
gh that creates a local bare repository as `origin`, and fake uv and npm that only log.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from datetime import date, timedelta
from pathlib import Path

from support import PAKEM, REPO_ROOT, IsolatedTestCase, git, init_repo, isolated_env, write

FAKE_GH = """#!/bin/sh
echo "gh $*" >> "{log}"
case "$1 $2" in
  "auth status") echo "Logged in" ;;
  "api user") echo octo ;;
  "api repos/"*"/actions/runners") cat "{state}/runners.json" 2>/dev/null || echo '{"total_count": 0, "runners": []}' ;;
  "api repos/"*"/rulesets")
    [ -e "{state}/rulesets-paid" ] && { echo "Upgrade to GitHub Pro or make this repository public" >&2; exit 1; }
    cat "{state}/rulesets.json" 2>/dev/null || echo '[]' ;;
  "repo create")
    [ -e "{state}/gh-fails" ] && { echo "HTTP 403" >&2; exit 1; }
    source=""; while [ $# -gt 0 ]; do [ "$1" = --source ] && source="$2"; shift; done
    git init -q --bare "{state}/origin.git" && git -C "$source" remote add origin "{state}/origin.git" ;;
  *) echo "gh version 2.0" ;;
esac
"""
FAKE_TOOL = """#!/bin/sh
[ "$1" = --version ] && { echo "1.0"; exit 0; }
echo "$(basename "$0") $*" >> "{log}"
"""
SERVICE = ("--description", "Demo service", "--visibility", "private", "--kind", "service", "--delivery", "us",
           "--environments", "staging,production", "--stack", "python", "--capabilities", "database")


def load_contracts():
    sys.path.insert(0, str(REPO_ROOT / "skills" / "release-delivery" / "assets" / "deploy"))
    import contracts

    return contracts


class ProjectTestCase(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.bin = self.base / "bin"
        self.state = self.base / "fake-state"
        self.state.mkdir()
        self.log = self.base / "calls.log"
        self.log.write_text("")
        for name, script in (("gh", FAKE_GH), ("uv", FAKE_TOOL), ("npm", FAKE_TOOL)):
            text = script.replace("{log}", str(self.log)).replace("{state}", str(self.state))
            write(self.bin / name, text, mode=0o755)
        self.env = isolated_env(self.home, {"PATH": f"{self.bin}:/usr/bin:/bin"})
        self.repo = self.base / "demo"

    def run_pakem(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(PAKEM), "--root", str(REPO_ROOT), *args],
            capture_output=True, text=True, env=self.env, stdin=subprocess.DEVNULL, cwd=str(cwd or self.base),
        )

    def calls(self) -> list[str]:
        return [line for line in self.log.read_text().splitlines() if line]

    def init(self, *flags: str) -> subprocess.CompletedProcess:
        return self.run_pakem("project", "init", str(self.repo), *(flags or SERVICE), "--yes")


class InitTests(ProjectTestCase):
    def test_a_private_service_gets_every_standard_file_and_is_pushed(self) -> None:
        completed = self.init()
        self.assert_fails(completed, code=3)
        facts = tomllib.loads((self.repo / ".pakem" / "project.toml").read_text())
        self.assertEqual(
            {key: facts[key] for key in ("name", "repository", "visibility", "kind", "delivery", "stack")},
            {"name": "demo", "repository": "octo/demo", "visibility": "private", "kind": "service",
             "delivery": "us", "stack": "python"},
        )
        self.assertEqual(facts["capabilities"], ["database", "deployed"])
        contracts = load_contracts()
        release = tomllib.loads((self.repo / "deploy" / "release.toml").read_text())
        self.assertEqual(contracts.validate_project_config(release), [])
        self.assertEqual(release["environments"]["production"]["backup"], "required")
        settings = tomllib.loads((self.repo / "deploy" / "settings.toml").read_text())
        self.assertEqual(contracts.validate_settings_manifest(settings), [])
        ci = (self.repo / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn('runner: "[self-hosted, linux, x64, pakem]"', ci)
        self.assertIn('tags: ["v*"]', ci)
        for name in ("verify-python.yml", "security.yml", "database.yml"):
            self.assertTrue((self.repo / ".github" / "workflows" / name).is_file(), name)
        agents = (self.repo / "AGENTS.md").read_text()
        self.assertIn("Capabilities: database, deployed", agents)
        self.assertIn("Delivery: us", agents)
        self.assertIn("- `health.proof`: open", agents)
        self.assertTrue((self.repo / "deploy" / "hooks" / "backup").stat().st_mode & 0o100)
        self.assertIn("uv add --dev ruff pyright pytest", self.calls())
        self.assertIn("gh repo create octo/demo --private", "\n".join(self.calls()))
        pushed = git(self.state / "origin.git", "log", "--format=%s", "main", home=self.home).stdout.split("\n")
        self.assertEqual(pushed[0], "chore: bootstrap project")
        self.assertEqual(git(self.repo, "config", "core.hooksPath", home=self.home).stdout.strip(), ".githooks")
        tracked = git(self.repo, "ls-files", home=self.home).stdout.split()
        self.assertNotIn("PROJECT_STATE.md", tracked)
        self.assertTrue((self.repo / "PROJECT_STATE.md").is_file())
        self.assertIn("pakem setup host <ssh-target> --project-dir", completed.stdout)
        self.assertIn("Open gaps (5): language.text, health.proof, settings.sources, backup.restore, migrations.rule",
                      completed.stdout)

    def test_without_a_terminal_every_fact_is_a_flag(self) -> None:
        completed = self.run_pakem("project", "init", str(self.repo), "--stack", "python", "--yes")
        self.assert_fails(completed, code=2)
        self.assertIn("missing: --description --visibility --kind --delivery --environments --capabilities",
                      completed.stderr)
        self.assertFalse(self.repo.exists())

    def test_refusals(self) -> None:
        write(self.repo / "existing.txt", "x")
        self.assert_fails(self.init(), code=6)
        artifact = list(SERVICE)
        artifact[artifact.index("service")] = "artifact"
        self.repo = self.base / "other"
        completed = self.init(*artifact)
        self.assert_fails(completed, code=6)
        self.assertIn("artifact kind comes with its own area", completed.stderr)
        completed = self.init(*SERVICE, "--forge", "gitlab")
        self.assert_fails(completed, code=6)

    def test_others_deliver_so_no_ci_or_deploy_files(self) -> None:
        flags = list(SERVICE)
        flags[flags.index("us")] = "others"
        completed = self.init(*flags, "--delivery-note", "the client's team deploys; we stop at the MR")
        self.assertIn(completed.returncode, (3, 4), completed.stderr)
        self.assertFalse((self.repo / ".github").exists())
        self.assertFalse((self.repo / "deploy").exists())
        self.assertIn("Delivery: others (the client's team deploys; we stop at the MR)",
                      (self.repo / "AGENTS.md").read_text())

    def test_a_public_node_library(self) -> None:
        completed = self.init("--description", "A small library", "--public", "--kind", "none", "--delivery",
                              "nobody", "--environments", "production", "--stack", "node", "--capabilities", "none")
        self.assert_fails(completed, code=4)
        self.assertIn('runner: "ubuntu-24.04"', (self.repo / ".github" / "workflows" / "ci.yml").read_text())
        self.assertTrue((self.repo / "package.json").is_file())
        self.assertFalse((self.repo / "CHANGELOG.md").exists())
        self.assertTrue(any(call.startswith("npm install --save-dev prettier eslint") for call in self.calls()))
        self.assertIn("gh repo create octo/demo --public", "\n".join(self.calls()))
        self.assertNotIn("OWNER ACTION", completed.stdout)

    def test_gh_failure_leaves_an_owner_action(self) -> None:
        (self.state / "gh-fails").touch()
        completed = self.init()
        self.assert_fails(completed, code=3)
        self.assertIn("OWNER ACTION REQUIRED: create the private repository octo/demo (HTTP 403)", completed.stdout)
        self.assertEqual(git(self.repo, "log", "--format=%s", home=self.home).stdout.strip(), "chore: bootstrap project")

    def test_plan_changes_nothing(self) -> None:
        completed = self.run_pakem("project", "init", str(self.repo), *SERVICE, "--plan")
        self.assert_ok(completed)
        self.assertFalse(self.repo.exists())
        probes = ("gh auth", "gh --version")
        self.assertEqual([call for call in self.calls() if not call.startswith(probes)], [])


class BootstrapPushTests(ProjectTestCase):
    """The framework pre-push hook, on a repository with a bare origin."""

    def setUp(self) -> None:
        super().setUp()
        init_repo(self.repo, self.home)
        hooks = self.repo / ".githooks"
        write(hooks / "pre-push", (REPO_ROOT / "hooks" / "pre-push").read_text(), mode=0o755)
        git(self.repo, "config", "core.hooksPath", ".githooks", home=self.home)
        self.origin = self.base / "origin.git"
        git(self.base, "init", "-q", "--bare", str(self.origin), home=self.home)
        git(self.repo, "remote", "add", "origin", str(self.origin), home=self.home)

    def commit(self, name: str, subject: str) -> None:
        write(self.repo / name, "x\n")
        git(self.repo, "add", "-A", home=self.home)
        git(self.repo, "commit", "-q", "-m", subject, home=self.home)

    def push(self) -> subprocess.CompletedProcess:
        return git(self.repo, "push", "-q", "origin", "main", home=self.home, check=False)

    def test_a_new_repository_pushes_its_bootstrap_commit(self) -> None:
        self.commit("app.py", "chore: bootstrap project")
        self.assertEqual(self.push().returncode, 0)
        self.commit("more.py", "feat: more code")
        rejected = self.push()
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("only *.md and docs/** may be pushed to main", rejected.stderr)

    def test_only_a_single_root_commit_with_that_subject(self) -> None:
        self.commit("app.py", "chore: start")
        self.assertNotEqual(self.push().returncode, 0)
        git(self.repo, "commit", "-q", "--amend", "-m", "chore: bootstrap project", home=self.home)
        self.commit("more.py", "feat: more code")
        self.assertNotEqual(self.push().returncode, 0, "two commits are not a bootstrap")


class DecideTests(ProjectTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init()

    def gaps(self) -> tuple[int, dict]:
        completed = self.run_pakem("project", "check", str(self.repo), "--gaps", "--json")
        return completed.returncode, {gap["id"]: gap for gap in json.loads(completed.stdout)["gaps"]}

    def decide(self, *args: str) -> subprocess.CompletedProcess:
        return self.run_pakem("decide", *args, "--repo", str(self.repo))

    def test_three_states_and_the_finish_condition(self) -> None:
        code, gaps = self.gaps()
        self.assertEqual(code, 4)
        self.assertEqual(gaps["health.proof"]["blocks"], ["release"])
        self.assertEqual(gaps["health.proof"]["default"], "process up and database answers")
        self.assertFalse(gaps["network.shared"]["required"])

        self.assert_ok(self.decide("health.proof", "process up and database answers", "--reason", "the default"))
        self.assert_ok(self.decide("settings.sources", "--provisional", "HTTP_PORT placement only",
                                   "--revisit", "the first real setting"))
        later = (date.today() + timedelta(days=7)).isoformat()
        self.assert_ok(self.decide("backup.restore", "--pending", "the database host", "--from", "the owner",
                                   "--ask-again", later))
        self.assert_ok(self.decide("migrations.rule", "hand-written SQL, expand then contract"))
        agents = (self.repo / "AGENTS.md").read_text()
        self.assertIn("- `settings.sources`: provisional: HTTP_PORT placement only; revisit when the first real setting",
                      agents)
        self.assertIn(f"- `backup.restore`: pending: the database host from the owner; ask again {later}", agents)
        self.assertNotIn("`health.proof`", agents)

        code, gaps = self.gaps()
        self.assertEqual(code, 4, "language.text is still open")
        agents_path = self.repo / "AGENTS.md"
        agents_path.write_text(agents_path.read_text().replace(
            "Language: <!-- UI text language; generated content language; number and date formats -->",
            "Language: English; ISO dates"))
        self.assert_ok(self.decide("language.text", "English; ISO dates"))
        code, gaps = self.gaps()
        self.assertEqual(code, 0, gaps)
        decisions = tomllib.loads((self.repo / ".pakem" / "decisions.toml").read_text())["decisions"]
        self.assertEqual(decisions["backup.restore"]["state"], "pending")

    def test_an_answer_the_files_do_not_prove_stays_open(self) -> None:
        release = self.repo / "deploy" / "release.toml"
        release.write_text(release.read_text().replace("[health]", "[unused]"))
        completed = self.decide("health.proof", "process up")
        self.assert_ok(completed)
        self.assertIn("Not proven yet: deploy/release.toml [health] needs service", completed.stdout)
        code, gaps = self.gaps()
        self.assertEqual(code, 4)
        self.assertIsNotNone(gaps["health.proof"]["problem"])

    def test_due_pending_decisions_reach_status(self) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.assert_ok(self.decide("backup.restore", "--pending", "dump tool", "--from", "ops",
                                   "--ask-again", yesterday))
        status = self.run_pakem("status", "--offline", cwd=self.repo)
        self.assertIn("decisions due: backup.restore", status.stdout)
        self.assertIn("Project decisions: 4 open, 1 pending, 0 provisional", status.stdout)

    def test_unknown_gaps_need_a_question(self) -> None:
        completed = self.decide("payments.provider", "midtrans")
        self.assert_fails(completed, code=6)
        self.assert_ok(self.decide("payments.provider", "--pending", "the contract", "--from", "the client",
                                   "--ask-again", "2030-01-01", "--question", "Which payment provider?",
                                   "--blocks", "release"))
        _, gaps = self.gaps()
        self.assertEqual(gaps["payments.provider"]["blocks"], ["release"])
        self.assert_fails(self.decide("health.proof", "x", "--provisional", "y"), code=2)


class AdoptTests(ProjectTestCase):
    def setUp(self) -> None:
        super().setUp()
        init_repo(self.repo, self.home)
        write(self.repo / "AGENTS.md", "# demo\n\nCapabilities: database\nDelivery: us\n")
        write(self.repo / "pyproject.toml", '[project]\nname = "demo"\nversion = "1.0.0"\n')
        write(self.repo / "deploy" / "release.toml", 'af_standard = 1\nproject = "demo"\n\n[environments.production]\n')
        write(self.repo / ".github" / "workflows" / "verify-python.yml", "name: changed by the project\n")
        git(self.repo, "add", "-A", home=self.home)
        git(self.repo, "commit", "-q", "-m", "feat: existing", home=self.home)
        git(self.repo, "remote", "add", "origin", "git@github.com:octo/demo.git", home=self.home)
        self.before = {path: path.read_text() for path in self.repo.rglob("*") if path.is_file() and ".git" not in path.parts}

    def adopt(self, *extra: str) -> subprocess.CompletedProcess:
        return self.run_pakem("project", "adopt", str(self.repo), "--description", "Demo", "--visibility", "private",
                              *extra)

    def test_adopt_adds_only_missing_files_on_its_own_branch(self) -> None:
        completed = self.adopt("--yes")
        self.assertIn(completed.returncode, (3, 4), completed.stderr)
        self.assertEqual(git(self.repo, "branch", "--show-current", home=self.home).stdout.strip(), "chore/adopt-pakem")
        for path, text in self.before.items():
            if path.name == "AGENTS.md":
                # Only the generated Open decisions section is added; the owner's text stays.
                self.assertTrue(path.read_text().startswith(text))
                self.assertIn("<!-- pakem:decisions:start -->", path.read_text())
                continue
            self.assertEqual(path.read_text(), text, f"{path} was changed")
        facts = tomllib.loads((self.repo / ".pakem" / "project.toml").read_text())
        self.assertEqual((facts["kind"], facts["delivery"], facts["stack"], facts["repository"]),
                         ("service", "us", "python", "octo/demo"))
        self.assertEqual(facts["environments"], ["production"])
        self.assertTrue((self.repo / "deploy" / "settings.toml").is_file())
        self.assertTrue((self.repo / ".github" / "workflows" / "security.yml").is_file())
        output = completed.stdout
        self.assertIn("FINDING  .github/workflows/verify-python.yml differs from the Pakem CI template", output)
        self.assertIn("FINDING  AGENTS.md has no `Language:` line", output)
        self.assertIn("FINDING  pyproject.toml lacks the Python tooling defaults", output)
        self.assertIn("FINDING  deploy/release.toml: release.toml: compose_files must list at least one file", output)
        writes = [call for call in self.calls() if not call.startswith("gh api repos/")]
        self.assertEqual(writes, [], "adopt runs no installers and only reads the forge")
        self.assertFalse((self.repo / "tests" / "test_package.py").exists(), "no starter code in an existing project")
        self.assertFalse((self.repo / "src").exists())

    def test_own_ci_stays_and_registry_releases_are_findings(self) -> None:
        write(self.repo / ".github" / "workflows" / "ci.yml", "name: CI\n")
        write(self.repo / ".github" / "workflows" / "release.yml", "run: python3 deploy/af/af_publish.py publish\n")
        write(self.repo / "deploy" / "release-signing.pub", "cosign key\n")
        write(self.repo / "deploy" / "af" / "af_publish.py", "# registry publisher\n")
        write(self.repo / "deploy" / "af" / "contracts.py", "# contract checks\n")
        completed = self.adopt("--yes")
        self.assertFalse((self.repo / ".github" / "workflows" / "security.yml").exists())
        self.assertIn("FINDING  .github/workflows/release.yml publishes releases to a registry", completed.stdout)
        self.assertIn("FINDING  deploy/release-signing.pub belongs to the registry mode", completed.stdout)
        self.assertIn("FINDING  deploy/af/af_publish.py belongs to the registry mode", completed.stdout)
        self.assertNotIn("contracts.py", completed.stdout, "the contract checks stay")
        self.assertIn('FINDING  deploy/release.toml has no `[build] runner = "local"`', completed.stdout)

    def test_owner_actions_skip_what_github_already_shows(self) -> None:
        (self.state / "runners.json").write_text(
            '{"total_count": 1, "runners": [{"name": "r", "labels": [{"name": "self-hosted"}, {"name": "pakem"}]}]}'
        )
        (self.state / "rulesets-paid").touch()
        completed = self.adopt("--yes")
        self.assertNotIn("self-hosted runner", completed.stdout)
        self.assertNotIn("protect release tags", completed.stdout, "rulesets need a paid plan here (no-billed)")
        self.assertIn("pakem setup host <ssh-target> --project-dir", completed.stdout)
        write(self.home / ".config" / "pakem" / "hosts.toml",
              '[hosts.prod]\ntarget = "admin@203.0.113.7"\nprojects = ["demo"]\n')
        again = self.adopt("--yes")
        self.assertNotIn("setup host", again.stdout, "this device may already release it")

    def test_plan_writes_nothing(self) -> None:
        completed = self.adopt("--plan")
        self.assert_ok(completed)
        self.assertIn("+ .pakem/project.toml", completed.stdout)
        self.assertEqual(git(self.repo, "status", "--short", home=self.home).stdout, "")


class ReleaseGateTests(ProjectTestCase):
    def test_release_waits_for_decisions_that_block_it(self) -> None:
        self.init()
        completed = self.run_pakem("release", "publish", "--yes", "--project-dir", str(self.repo))
        self.assert_fails(completed, code=4)
        self.assertIn("decisions that block a release are open: health.proof (open)", completed.stderr)
