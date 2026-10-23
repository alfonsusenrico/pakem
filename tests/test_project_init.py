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
  "api repos/"*) cat "{state}/private" 2>/dev/null || echo true ;;
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
        self.assertIn('runner: "pakem"', ci, "one label: a bracketed string would match no runner")
        self.assertIn("migrate_cmd:", ci)
        self.assertIn("test_cmd: uv run pytest", ci)
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
        self.assertIn("then OWNER ACTION REQUIRED: run `pakem setup host <name>`", completed.stdout)
        self.assertIn("Open gaps (5): language.text, health.proof, settings.sources, backup.restore, migrations.rule",
                      completed.stdout)

    def test_ci_runs_everything_on_main_and_only_docs_checks_for_docs_pull_requests(self) -> None:
        self.init()
        ci = (self.repo / ".github" / "workflows" / "ci.yml").read_text()
        self.assertNotIn("paths-ignore", ci)
        self.assertIn("runs-on: pakem", ci)
        self.assertIn("labels: [pakem]", (self.repo / ".github" / "actionlint.yaml").read_text())
        for job in ("verify-python", "security", "database"):
            block = ci.split(f"\n  {job}:")[1].split("\n\n")[0]
            self.assertIn("if: needs.changes.outputs.code == 'true'", block)
        self.assertIn("if: needs.changes.outputs.code == 'false'", ci.split("\n  docs:")[1].split("\n\n")[0])
        rule = "(^[^/]+\\.md$|^docs/|^openspec/)"
        self.assertIn(f"grep -qvE '{rule}'", ci)
        for files, expected in (
            (["README.md", "docs/a.md", "openspec/specs/x/spec.md"], "docs"),
            (["README.md", "src/app.py"], "code"),
            (["src/prompts/system.md"], "code"),
            (["uv.lock"], "code"),
        ):
            completed = subprocess.run(["grep", "-qvE", rule], input="\n".join(files) + "\n", text=True)
            with self.subTest(files=files):
                self.assertEqual("code" if completed.returncode == 0 else "docs", expected)

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

    def test_others_deliver_so_ci_but_no_deploy_files(self) -> None:
        flags = list(SERVICE)
        flags[flags.index("us")] = "others"
        completed = self.init(*flags, "--delivery-note", "the client's team deploys; we stop at the MR")
        self.assertIn(completed.returncode, (3, 4), completed.stderr)
        self.assertTrue((self.repo / ".github" / "workflows" / "ci.yml").is_file(), "CI as for any project")
        self.assertFalse((self.repo / "deploy").exists())
        self.assertNotIn("protect release tags", completed.stdout, "the client tags its own releases")
        self.assertIn("Delivery: others (the client's team deploys; we stop at the MR)",
                      (self.repo / "AGENTS.md").read_text())
        gaps = json.loads(self.run_pakem("project", "check", str(self.repo), "--gaps", "--json").stdout)["gaps"]
        owner = next(gap for gap in gaps if gap["id"] == "ci.owner")
        self.assertEqual((owner["state"], owner["problem"]), ("open", None))

    def test_a_client_that_runs_its_own_ci_opts_out_on_record(self) -> None:
        flags = list(SERVICE)
        flags[flags.index("us")] = "others"
        completed = self.init(*flags, "--delivery-note", "the client deploys", "--client-ci", "the client's GitLab")
        self.assertIn(completed.returncode, (3, 4), completed.stderr)
        self.assertFalse((self.repo / ".github").exists())
        self.assertNotIn("self-hosted runner", completed.stdout)
        decisions = tomllib.loads((self.repo / ".pakem" / "decisions.toml").read_text())["decisions"]
        self.assertEqual(decisions["ci.owner"]["answer"], "client CI: the client's GitLab")
        self.repo = self.base / "ours"
        refused = self.init(*SERVICE, "--client-ci", "somewhere")
        self.assert_fails(refused, code=2)
        self.assertIn("--client-ci is for --delivery others", refused.stderr)

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

        proving = self.decide("health.proof", "process up and database answers", "--reason", "the default")
        self.assert_ok(proving)
        self.assertIn("Not proven yet: no code of the project serves /health/release", proving.stdout,
                      "the template's [health] block alone proves nothing")
        self.assert_ok(self.decide("settings.sources", "--provisional", "HTTP_PORT placement only",
                                   "--revisit", "the first real setting"))
        later = (date.today() + timedelta(days=7)).isoformat()
        self.assert_ok(self.decide("backup.restore", "--pending", "the database host", "--from", "the owner",
                                   "--ask-again", later))
        self.assertIn("Not proven yet: AGENTS.md states no migration tool",
                      self.decide("migrations.rule", "hand-written SQL, expand then contract").stdout)
        agents = (self.repo / "AGENTS.md").read_text()
        self.assertIn("- `settings.sources`: provisional: HTTP_PORT placement only; revisit when the first real setting",
                      agents)
        self.assertIn(f"- `backup.restore`: pending: the database host from the owner; ask again {later}", agents)
        self.assertNotIn("`health.proof`", agents)

        code, gaps = self.gaps()
        self.assertEqual(code, 4, "language.text is still open")
        self.assertIn("no code of the project serves", gaps["health.proof"]["problem"])
        write(self.repo / "src" / "demo" / "health.py",
              '@app.get("/health/release")\ndef health():\n    return {"revision": REVISION, "database": ping()}\n')
        write(self.repo / "docs" / "deployment.md", "# Deployment\n\n/health/release checks the database.\n")
        agents_path = self.repo / "AGENTS.md"
        agents_path.write_text(agents_path.read_text().replace(
            "Language: <!-- UI text language; generated content language; number and date formats -->",
            "Language: English; ISO dates").replace(
            "## Project rules\n", "## Project rules\n\n- Migrations: hand-written SQL in src/demo/migrations, expand then contract.\n", 1))
        self.assert_ok(self.decide("language.text", "English; ISO dates"))
        code, gaps = self.gaps()
        self.assertEqual(code, 0, gaps)
        decisions = tomllib.loads((self.repo / ".pakem" / "decisions.toml").read_text())["decisions"]
        self.assertEqual(decisions["backup.restore"]["state"], "pending")

    def test_the_generated_block_never_eats_the_owners_text(self) -> None:
        agents = self.repo / "AGENTS.md"
        prose = "Notes: the block starts at `<!-- pakem:decisions:start -->` and is generated.\n"
        agents.write_text(prose + agents.read_text())
        self.assert_ok(self.decide("health.proof", "process up"))
        self.assert_ok(self.decide("health.proof", "process up and database answers"))
        self.assertTrue(agents.read_text().startswith(prose), "a marker inside prose is not a marker")
        self.assertEqual(agents.read_text().count("\n<!-- pakem:decisions:start -->\n"), 1)

        cases = {
            "a second start": agents.read_text() + "\n<!-- pakem:decisions:start -->\n",
            "no end": agents.read_text().replace("<!-- pakem:decisions:end -->", ""),
            "end before start": "<!-- pakem:decisions:end -->\n" + agents.read_text().replace(
                "<!-- pakem:decisions:end -->", ""),
        }
        decisions = (self.repo / ".pakem" / "decisions.toml").read_text()
        for name, text in cases.items():
            with self.subTest(name):
                agents.write_text(text)
                refused = self.decide("data.moves", "none")
                self.assert_fails(refused, code=2)
                self.assertIn("must hold the Open decisions markers once each", refused.stderr)
                self.assertEqual(agents.read_text(), text, "nothing changed")
                self.assertEqual((self.repo / ".pakem" / "decisions.toml").read_text(), decisions)

    def test_any_text_round_trips_through_the_decisions(self) -> None:
        answer = 'Indonesian 🇮🇩 with "quotes", a back\\slash, a tab\tand DEL\x7f'
        self.assert_ok(self.decide("language.text", answer, "--reason", "owner 🙂"))
        decisions = tomllib.loads((self.repo / ".pakem" / "decisions.toml").read_text())["decisions"]
        self.assertEqual((decisions["language.text"]["answer"], decisions["language.text"]["reason"]),
                         (answer, "owner 🙂"))
        self.assert_ok(self.decide("data.moves", "none"))
        code, _ = self.gaps()
        self.assertEqual(code, 4, "the decisions file still reads")

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
        reads = ("gh api repos/", "gh --version", "gh auth status")
        writes = [call for call in self.calls() if not call.startswith(reads)]
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
        self.assertIn("then OWNER ACTION REQUIRED: run `pakem setup host <name>`", completed.stdout)
        write(self.home / ".config" / "pakem" / "hosts.toml",
              '[hosts.prod]\ntarget = "admin@203.0.113.7"\nprojects = ["demo"]\n')
        again = self.adopt("--yes")
        self.assertNotIn("setup host", again.stdout, "this device may already release it")

    def test_adopt_keeps_the_facts_file_it_does_not_need_to_change(self) -> None:
        self.adopt("--yes")
        facts = self.repo / ".pakem" / "project.toml"
        facts.write_text(facts.read_text() + 'owner_note = "kept 🙂"\n')
        before = facts.read_text()
        self.adopt("--yes")
        self.assertEqual(facts.read_text(), before, "unchanged facts are not rewritten")
        changed = self.run_pakem("project", "adopt", str(self.repo), "--description", "Demo", "--capabilities",
                                 "database,llm", "--yes")
        self.assertIn(changed.returncode, (3, 4), changed.stderr)
        data = tomllib.loads(facts.read_text())
        self.assertEqual((data["owner_note"], data["capabilities"]), ("kept 🙂", ["database", "deployed", "llm"]))

    def test_visibility_comes_from_github(self) -> None:
        (self.state / "private").write_text("false\n")
        refused = self.adopt("--yes")
        self.assert_fails(refused, code=6)
        self.assertIn("octo/demo is public on GitHub, not private", refused.stderr)
        completed = self.run_pakem("project", "adopt", str(self.repo), "--description", "Demo", "--yes")
        self.assertIn(completed.returncode, (3, 4), completed.stderr)
        facts = tomllib.loads((self.repo / ".pakem" / "project.toml").read_text())
        self.assertEqual(facts["visibility"], "public")
        self.assertNotIn("self-hosted runner", completed.stdout)

    def test_a_public_repository_never_keeps_self_hosted_jobs(self) -> None:
        (self.state / "private").write_text("false\n")
        write(self.repo / ".github" / "workflows" / "ci.yml", "jobs:\n  check:\n    runs-on: [self-hosted, linux, x64, pakem]\n")
        refused = self.run_pakem("project", "adopt", str(self.repo), "--description", "Demo", "--yes")
        self.assert_fails(refused, code=6)
        self.assertIn("a self-hosted runner would run any fork's pull request", refused.stderr)
        self.assertIn(".github/workflows/ci.yml", refused.stderr)
        self.assertFalse((self.repo / ".pakem").exists(), "nothing changed")

        check = self.run_pakem("project", "check", str(self.repo))
        self.assert_fails(check, code=6)
        self.assertIn("octo/demo is public", check.stdout)
        (self.state / "private").write_text("true\n")
        self.assertNotIn("is public", self.run_pakem("project", "check", str(self.repo)).stdout)

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
