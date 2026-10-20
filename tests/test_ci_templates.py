"""Structural checks for the CI templates in skills/release-delivery/assets/ci/ (no YAML parser needed).

The templates are line-oriented YAML written in a fixed style, so these checks scan lines
and indentation instead of parsing. They protect the supply-chain and least-privilege rules
in skills/release-delivery/assets/ci/README.md.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from support import REPO_ROOT

CI_ROOT = REPO_ROOT / "skills" / "release-delivery" / "assets" / "ci"
GITHUB_ROOT = CI_ROOT / "github"
GITLAB_ROOT = CI_ROOT / "gitlab"
PINS = CI_ROOT / "PINS.md"
DEPLOY_ROOT = REPO_ROOT / "skills" / "release-delivery" / "assets" / "deploy"
DEPLOY_TEMPLATES = [
    GITHUB_ROOT / "workflows" / "deploy.yml",
    GITHUB_ROOT / "deploy.example.yml",
    GITHUB_ROOT / "workflows" / "release.yml",
    GITHUB_ROOT / "release.example.yml",
    GITLAB_ROOT / "deploy.yml",
]

USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$")
PINNED_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@([0-9a-f]{40})$")
VERSION_COMMENT = re.compile(r"^\s+# v\d+\.\d+\.\d+$")
RUN_LINE = re.compile(r"^(\s*)(?:-\s+)?run:\s*(.*)$")
TOP_LEVEL_KEY = re.compile(r"^([A-Za-z0-9_.:-]+):")
JOB_KEY = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
SHA256_ENV = re.compile(r"^\s+[A-Z_]+_SHA256:\s*([0-9a-f]{64})\s*$")
IMAGE_VARIABLE = re.compile(r"^\s+AF_[A-Z_]+_IMAGE:\s*(\S+)\s*$")
SECRET_REFERENCE = re.compile(r"^\s+[A-Za-z_-]+: \$\{\{ (secrets\.[A-Z_]+|toJSON\(secrets\)) \}\}$")
IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def github_workflow_files() -> list[Path]:
    examples = [GITHUB_ROOT / "ci.example.yml", GITHUB_ROOT / "deploy.example.yml", GITHUB_ROOT / "release.example.yml"]
    return sorted((GITHUB_ROOT / "workflows").glob("*.yml")) + examples


def all_github_files() -> list[Path]:
    return sorted(GITHUB_ROOT.rglob("*.yml"))


def gitlab_files() -> list[Path]:
    return sorted(GITLAB_ROOT.glob("*.yml"))


def is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def run_blocks(text: str) -> list[tuple[int, str]]:
    """(first line number, script text) for every GitHub `run:` value, inline or block."""
    lines = text.splitlines()
    blocks: list[tuple[int, str]] = []
    index = 0
    while index < len(lines):
        match = RUN_LINE.match(lines[index])
        if match is None or is_comment(lines[index]):
            index += 1
            continue
        key_indent = len(match.group(1))
        if lines[index].lstrip().startswith("- "):
            key_indent += 2
        value = match.group(2)
        body = [value]
        start = index + 1
        index += 1
        if value.startswith(("|", ">")):
            while index < len(lines):
                line = lines[index]
                indent = len(line) - len(line.lstrip())
                if line.strip() and indent <= key_indent:
                    break
                body.append(line)
                index += 1
        blocks.append((start, "\n".join(body)))
    return blocks


def top_level_blocks(text: str) -> dict[str, str]:
    """Top-level YAML keys mapped to their text, including the key line."""
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = TOP_LEVEL_KEY.match(line)
        if match is not None:
            current = match.group(1)
            blocks[current] = [line]
        elif current is not None:
            blocks[current].append(line)
    return {key: "\n".join(value) for key, value in blocks.items()}


def job_blocks(workflow_text: str) -> dict[str, str]:
    """GitHub jobs (two-space keys under `jobs:`) mapped to their text."""
    jobs_text = top_level_blocks(workflow_text).get("jobs", "")
    jobs: dict[str, list[str]] = {}
    current: str | None = None
    for line in jobs_text.splitlines()[1:]:
        match = JOB_KEY.match(line)
        if match is not None:
            current = match.group(1)
            jobs[current] = []
        elif current is not None:
            jobs[current].append(line)
    return {name: "\n".join(lines) for name, lines in jobs.items()}


class GitHubTemplateTests(unittest.TestCase):
    def test_expected_templates_exist(self) -> None:
        names = {path.name for path in (GITHUB_ROOT / "workflows").glob("*.yml")}
        self.assertEqual(
            names, {"verify-python.yml", "verify-node.yml", "security.yml", "database.yml", "deploy.yml", "release.yml",
             "push-trigger.yml"},
        )
        self.assertTrue((GITHUB_ROOT / "release.example.yml").is_file())
        self.assertTrue((GITHUB_ROOT / "ci.example.yml").is_file())
        self.assertTrue((GITHUB_ROOT / "deploy.example.yml").is_file())

    def test_third_party_actions_are_pinned_to_full_commit_sha(self) -> None:
        checked = 0
        for path in all_github_files():
            for number, line in enumerate(path.read_text().splitlines(), start=1):
                match = USES_LINE.match(line)
                if match is None or is_comment(line):
                    continue
                target, rest = match.group(1), match.group(2)
                if target.startswith("./"):
                    continue
                location = f"{path.relative_to(REPO_ROOT)}:{number}"
                self.assertRegex(target, PINNED_ACTION, f"{location}: {target} is not pinned to a 40-hex SHA")
                self.assertRegex(rest, VERSION_COMMENT, f"{location}: pinned SHA needs a trailing '# vX.Y.Z'")
                checked += 1
        self.assertGreater(checked, 0)

    def test_pins_are_recorded_in_pins_md(self) -> None:
        pins = PINS.read_text()
        for path in all_github_files():
            for line in path.read_text().splitlines():
                sha256 = SHA256_ENV.match(line)
                if sha256 is not None:
                    self.assertIn(sha256.group(1), pins, f"{path.name}: checksum missing from PINS.md")
                match = USES_LINE.match(line)
                if match is None or is_comment(line) or match.group(1).startswith("./"):
                    continue
                action, sha = match.group(1).split("@")
                tag = match.group(2).strip().removeprefix("# ")
                with self.subTest(action=action):
                    self.assertRegex(pins, rf"`{re.escape(action)}`.*`{re.escape(tag)}`.*`{sha}`")

    def test_every_workflow_declares_top_level_permissions(self) -> None:
        for path in github_workflow_files():
            with self.subTest(path=path.name):
                self.assertRegex(path.read_text(), r"(?m)^permissions:\n  contents: read$")

    def test_reusable_workflow_jobs_have_permissions_and_timeouts(self) -> None:
        for path in sorted((GITHUB_ROOT / "workflows").glob("*.yml")):
            text = path.read_text()
            self.assertRegex(text, r"(?m)^on:\n  workflow_call:$", path.name)
            jobs = job_blocks(text)
            self.assertTrue(jobs, f"{path.name}: no jobs found")
            for name, body in jobs.items():
                with self.subTest(workflow=path.name, job=name):
                    self.assertRegex(body, r"(?m)^    permissions:\n      contents: read$")
                    self.assertRegex(body, r"(?m)^    timeout-minutes: ")

    def test_checkout_does_not_persist_credentials(self) -> None:
        for path in all_github_files():
            text = path.read_text()
            uses_count = text.count("uses: actions/checkout@")
            self.assertEqual(uses_count, text.count("persist-credentials: false"), path.name)

    def test_no_secrets_inside_run_blocks(self) -> None:
        for path in all_github_files():
            for start, script in run_blocks(path.read_text()):
                with self.subTest(path=path.name, line=start):
                    self.assertNotIn("${{ secrets.", script)
                    self.assertNotIn("${{secrets.", script)

    def test_run_blocks_take_values_from_env_not_expressions(self) -> None:
        for path in all_github_files():
            for start, script in run_blocks(path.read_text()):
                with self.subTest(path=path.name, line=start):
                    self.assertNotIn("${{", script)

    def test_run_block_scanner_sees_block_scripts(self) -> None:
        sample = "steps:\n  - run: |\n      echo ${{ secrets.TOKEN }}\n  - name: next\n    run: echo ok\n"
        blocks = run_blocks(sample)
        self.assertEqual(len(blocks), 2)
        self.assertIn("${{ secrets.TOKEN }}", blocks[0][1])
        self.assertNotIn("next", blocks[0][1])

    def test_database_workflow_requires_the_database(self) -> None:
        text = (GITHUB_ROOT / "workflows" / "database.yml").read_text()
        self.assertIn('REQUIRE_DB: "1"', text)
        self.assertIn("TEST_DATABASE_URL:", text)
        self.assertEqual(text.count('eval "$MIGRATE_CMD"'), 2)
        self.assertIn("default: postgres:17", text)


class GitLabTemplateTests(unittest.TestCase):
    def test_expected_templates_exist(self) -> None:
        names = {path.name for path in gitlab_files()}
        self.assertEqual(
            names,
            {"verify-python.yml", "verify-node.yml", "security.yml", "database.yml", "deploy.yml", "ci.example.yml"},
        )

    def test_avoids_constructs_the_org_instance_rejects(self) -> None:
        for path in gitlab_files():
            text = path.read_text()
            with self.subTest(path=path.name):
                self.assertNotIn("CI_COMMIT_BRANCH", text)
                self.assertNotRegex(text, r"(?m)^\s+files:")
                self.assertNotIn("cache:key:files", text)

    def test_hidden_jobs_are_interruptible_bounded_and_ruled(self) -> None:
        hidden_count = 0
        for path in gitlab_files():
            # Deploy jobs are never interruptible and have their own rules (DeployTemplateTests).
            if path.name in ("ci.example.yml", "deploy.yml"):
                continue
            blocks = top_level_blocks(path.read_text())
            hidden = {name: body for name, body in blocks.items() if name.startswith(".af:")}
            self.assertTrue(hidden, f"{path.name}: no .af: hidden jobs")
            for name, body in hidden.items():
                hidden_count += 1
                with self.subTest(path=path.name, job=name):
                    self.assertRegex(body, r"(?m)^  interruptible: true$")
                    self.assertRegex(body, r"(?m)^  timeout: \d+ minutes$")
                    self.assertIn('if: $CI_PIPELINE_SOURCE == "merge_request_event"', body)
                    self.assertIn("if: $CI_COMMIT_REF_NAME == $CI_DEFAULT_BRANCH", body)
        self.assertGreaterEqual(hidden_count, 8)

    def test_images_are_pinned_to_versions(self) -> None:
        images = []
        for path in gitlab_files():
            for line in path.read_text().splitlines():
                match = IMAGE_VARIABLE.match(line)
                if match is not None:
                    images.append(match.group(1))
        self.assertTrue(images)
        for image in images:
            with self.subTest(image=image):
                tag = image.rsplit(":", 1)[-1]
                self.assertNotEqual(tag, "latest")
                self.assertRegex(tag, r"\d+\.\d+")
                self.assertIn(image, PINS.read_text())

    def test_database_job_requires_the_database(self) -> None:
        text = (GITLAB_ROOT / "database.yml").read_text()
        self.assertIn('REQUIRE_DB: "1"', text)
        self.assertIn("TEST_DATABASE_URL:", text)
        self.assertIn("- name: $AF_POSTGRES_IMAGE", text)
        self.assertEqual(text.count('- eval "$MIGRATE_CMD"'), 2)

    def test_example_avoids_duplicate_pipelines_and_extends_hidden_jobs(self) -> None:
        text = (GITLAB_ROOT / "ci.example.yml").read_text()
        self.assertIn("workflow:\n  rules:", text)
        self.assertIn("if: $CI_OPEN_MERGE_REQUESTS\n      when: never", text)
        hidden = set()
        for path in gitlab_files():
            hidden.update(name for name in top_level_blocks(path.read_text()) if name.startswith(".af:"))
        extended = set(re.findall(r"extends: (\.af:[a-z-]+)", text))
        self.assertEqual(extended, hidden)


class DeployTemplateTests(unittest.TestCase):
    """The deploy workflow, the GitLab deploy jobs, and the deploy scripts."""

    def test_github_deploy_takes_environment_sha_and_mode(self) -> None:
        text = (GITHUB_ROOT / "workflows" / "deploy.yml").read_text()
        inputs = top_level_blocks(text)["on"]
        for name in ("environment", "sha", "mode"):
            self.assertRegex(inputs, rf"(?m)^      {name}:$")
        self.assertIn("default: deploy", inputs)

    def test_github_deploy_is_serialized_per_environment(self) -> None:
        body = job_blocks((GITHUB_ROOT / "workflows" / "deploy.yml").read_text())["deploy"]
        concurrency = "    concurrency:\n      group: deploy-${{ inputs.environment }}\n      cancel-in-progress: false"
        self.assertIn(concurrency, body)
        self.assertIn("    environment:\n      name: ${{ inputs.environment }}", body)
        self.assertIn("fetch-depth: 0", body)

    def test_github_deploy_secrets_arrive_only_through_env_or_with(self) -> None:
        text = (GITHUB_ROOT / "workflows" / "deploy.yml").read_text()
        references = [line for line in text.splitlines() if "secrets" in line and not is_comment(line)]
        self.assertTrue(references)
        for line in references:
            with self.subTest(line=line.strip()):
                self.assertRegex(line, SECRET_REFERENCE)

    def test_github_deploy_example_wires_staging_production_and_rollback(self) -> None:
        jobs = job_blocks((GITHUB_ROOT / "deploy.example.yml").read_text())
        self.assertEqual(set(jobs), {"staging", "production", "rollback"})
        for name, body in jobs.items():
            with self.subTest(job=name):
                self.assertIn("uses: ./.github/workflows/deploy.yml", body)
                self.assertIn("secrets: inherit", body)
        self.assertIn("github.ref == 'refs/heads/main'", jobs["staging"])
        self.assertIn("startsWith(github.ref, 'refs/tags/v')", jobs["production"])
        self.assertIn("mode: rollback", jobs["rollback"])

    def test_gitlab_deploy_hidden_jobs(self) -> None:
        blocks = top_level_blocks((GITLAB_ROOT / "deploy.yml").read_text())
        hidden = {name: body for name, body in blocks.items() if name.startswith(".af:")}
        self.assertEqual(set(hidden), {".af:deploy", ".af:rollback", ".af:smoke"})
        self.assertRegex(hidden[".af:deploy"], r"(?m)^  interruptible: false$")
        self.assertRegex(hidden[".af:smoke"], r"(?m)^  interruptible: true$")
        # .af:rollback inherits image, script, interruptible, and timeout from .af:deploy.
        self.assertIn("extends: .af:deploy", hidden[".af:rollback"])
        for name in (".af:deploy", ".af:smoke"):
            self.assertRegex(hidden[name], r"(?m)^  timeout: \d+ minutes$", name)
        for name, body in hidden.items():
            self.assertRegex(body, r"(?m)^  rules:$", name)

    def test_gitlab_production_needs_a_protected_tag_and_a_manual_start(self) -> None:
        blocks = top_level_blocks((GITLAB_ROOT / "deploy.yml").read_text())
        deploy = blocks[".af:deploy"]
        gate = (
            '    - if: $AF_DEPLOY_ENVIRONMENT == "production" && $CI_COMMIT_TAG =~ /^v\\d+\\.\\d+\\.\\d+$/'
            ' && $CI_COMMIT_REF_PROTECTED == "true"\n      when: manual\n      allow_failure: false\n'
            '    - if: $AF_DEPLOY_ENVIRONMENT == "production"\n      when: never'
        )
        self.assertIn(gate, deploy)
        rollback = blocks[".af:rollback"]
        manual = (
            '$AF_DEPLOY_ENVIRONMENT == "production" && $CI_PIPELINE_SOURCE == "web"'
            ' && $CI_COMMIT_REF_PROTECTED == "true"\n      when: manual'
        )
        self.assertIn(manual, rollback)
        self.assertIn('    - if: $AF_DEPLOY_ENVIRONMENT == "production"\n      when: never', rollback)

    def test_gitlab_deploy_relies_on_the_host_lock(self) -> None:
        for path in (GITLAB_ROOT / "deploy.yml", GITLAB_ROOT / "ci.example.yml"):
            with self.subTest(path=path.name):
                self.assertNotRegex(path.read_text(), r"(?m)^\s+resource_group:")

    def test_deploy_templates_pin_hosts_and_name_no_servers(self) -> None:
        scripts = [path for path in sorted(DEPLOY_ROOT.iterdir()) if path.suffix in (".py", ".sh", ".md", ".toml")]
        # A lower bound: the scan must cover the deploy tooling, not be emptied by a rename.
        self.assertGreaterEqual(len(scripts), 11)
        examples = sorted((DEPLOY_ROOT / "contracts").iterdir())
        for path in DEPLOY_TEMPLATES + scripts + examples:
            text = path.read_text()
            with self.subTest(path=path.name):
                self.assertNotIn("accept-new", text.replace("never `accept-new`", ""))
                self.assertNotIn("StrictHostKeyChecking=no", text)
                addresses = set(IPV4.findall(text)) - {"127.0.0.1", "0.0.0.0"}
                self.assertEqual(addresses, set())

    def test_deploy_driver_connects_only_with_pinned_keys_and_its_own_identity(self) -> None:
        text = (DEPLOY_ROOT / "deploy_remote.sh").read_text()
        options = (
            "-F /dev/null",
            "StrictHostKeyChecking=yes",
            "IdentitiesOnly=yes",
            "IdentityAgent=none",
            "BatchMode=yes",
        )
        for option in options:
            self.assertIn(option, text)
        self.assertIn('ssh-keygen -F "$known_name"', text)
        self.assertTrue(text.startswith("#!/bin/sh\n"))


class ReleaseTemplateTests(unittest.TestCase):
    """The contracts-v1 release workflow (build once, sign, move pointers)."""

    def setUp(self) -> None:
        self.workflow = (GITHUB_ROOT / "workflows" / "release.yml").read_text()
        self.example = (GITHUB_ROOT / "release.example.yml").read_text()

    def test_tools_and_images_are_pinned_and_recorded(self) -> None:
        pins = PINS.read_text()
        for line in self.workflow.splitlines():
            image = re.match(r"^\s+AF_[A-Z_]+_IMAGE:\s*(\S+)$", line)
            if image is not None:
                self.assertRegex(image.group(1), r"@sha256:[0-9a-f]{64}$")
                self.assertIn(image.group(1).split("@")[1], pins)
        self.assertEqual(self.workflow.count("sha256sum --check --strict"), 2)

    def test_only_the_release_job_may_write_packages(self) -> None:
        self.assertRegex(self.workflow, r"(?m)^    permissions:\n      contents: read\n      packages: write$")
        self.assertRegex(self.workflow, r"(?m)^permissions:\n  contents: read$")

    def test_signing_key_reaches_the_script_only_through_env(self) -> None:
        self.assertIn("COSIGN_PRIVATE_KEY: ${{ secrets.COSIGN_PRIVATE_KEY }}", self.workflow)
        self.assertIn('rm -rf "$RUNNER_TEMP/af-docker"', self.workflow)

    def test_releases_are_serialized_and_commits_come_from_the_release_branch(self) -> None:
        self.assertIn("cancel-in-progress: false", self.workflow)
        self.assertIn('git merge-base --is-ancestor "$COMMIT" "$RELEASE_REF"', self.workflow)

    def test_example_publishes_main_promotes_tags_and_rolls_back_by_hand(self) -> None:
        jobs = job_blocks(self.example)
        self.assertEqual(set(jobs), {"verify", "publish", "promote", "rollback", "push-staging", "push-production"})
        self.assertIn("needs: verify", jobs["publish"])
        self.assertIn("refs/heads/main", jobs["publish"])
        self.assertIn("startsWith(github.ref, 'refs/tags/v')", jobs["promote"])
        self.assertIn("version: ${{ github.ref_name }}", jobs["promote"])
        self.assertIn("workflow_dispatch", jobs["rollback"])

    def test_example_pushes_only_when_a_host_is_configured_and_passes_only_its_key(self) -> None:
        jobs = job_blocks(self.example)
        for environment, after in (("staging", "publish"), ("production", "promote")):
            body = jobs[f"push-{environment}"]
            with self.subTest(environment=environment):
                self.assertIn(f"needs: {after}", body)
                self.assertIn(f"if: vars.AF_PUSH_{environment.upper()}_HOST != ''", body)
                self.assertIn("uses: ./.github/workflows/af-push.yml", body)
                self.assertIn("AF_PUSH_SSH_KEY: ${{ secrets.AF_PUSH_SSH_KEY }}", body)
                self.assertNotIn("secrets: inherit", body)


class PushTriggerTemplateTests(unittest.TestCase):
    """The push trigger: a pinned SSH call whose only payload is `deploy <environment> <actor>`."""

    def setUp(self) -> None:
        self.workflow = (GITHUB_ROOT / "workflows" / "push-trigger.yml").read_text()

    def test_connects_only_with_the_pinned_host_key_and_its_own_identity(self) -> None:
        for option in ("-F /dev/null", "BatchMode=yes", "StrictHostKeyChecking=yes", "IdentitiesOnly=yes",
                       "IdentityAgent=none"):
            self.assertIn(option, self.workflow)
        self.assertIn('rm -rf "$RUNNER_TEMP/af-push"', self.workflow)
        self.assertIn("umask 077", self.workflow)

    def test_sends_only_the_deploy_request(self) -> None:
        self.assertIn('"deploy $ENVIRONMENT $ACTOR"', self.workflow)
        self.assertEqual(self.workflow.count('@$HOST"'), 1)

    def test_rejects_names_ssh_could_read_as_options(self) -> None:
        self.assertIn("^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$", self.workflow)
        self.assertIn("^[a-z_][a-z0-9_-]{0,31}$", self.workflow)

    def test_needs_only_read_permissions(self) -> None:
        self.assertRegex(self.workflow, r"(?m)^    permissions:\n      contents: read\n    #")
        self.assertNotIn(": write", self.workflow)


class AgentUnitTests(unittest.TestCase):
    """The systemd units that run the pull agent."""

    def test_service_runs_one_poll_as_the_deploy_user(self) -> None:
        service = (DEPLOY_ROOT / "systemd" / "af-agent.service").read_text()
        self.assertIn("Type=oneshot", service)
        self.assertIn("User=af", service)
        self.assertIn("ExecStart=/usr/bin/python3 /opt/af/af_trigger.py poll --config-dir /etc/af/hosts", service)
        self.assertIn("NoNewPrivileges=yes", service)

    def test_timer_repeats_after_each_pass(self) -> None:
        timer = (DEPLOY_ROOT / "systemd" / "af-agent.timer").read_text()
        self.assertIn("OnUnitInactiveSec=", timer)
        self.assertIn("WantedBy=timers.target", timer)


if __name__ == "__main__":
    unittest.main()
