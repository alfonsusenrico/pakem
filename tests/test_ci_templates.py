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

USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$")
PINNED_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@([0-9a-f]{40})$")
VERSION_COMMENT = re.compile(r"^\s+# v\d+\.\d+\.\d+$")
RUN_LINE = re.compile(r"^(\s*)(?:-\s+)?run:\s*(.*)$")
TOP_LEVEL_KEY = re.compile(r"^([A-Za-z0-9_.:-]+):")
JOB_KEY = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
SHA256_ENV = re.compile(r"^\s+[A-Z_]+_SHA256:\s*([0-9a-f]{64})\s*$")
IMAGE_VARIABLE = re.compile(r"^\s+AF_[A-Z_]+_IMAGE:\s*(\S+)\s*$")


def github_workflow_files() -> list[Path]:
    return sorted((GITHUB_ROOT / "workflows").glob("*.yml")) + [GITHUB_ROOT / "ci.example.yml"]


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
        self.assertEqual(names, {"verify-python.yml", "verify-node.yml", "security.yml", "database.yml"})
        self.assertTrue((GITHUB_ROOT / "ci.example.yml").is_file())

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
            {"verify-python.yml", "verify-node.yml", "security.yml", "database.yml", "ci.example.yml"},
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
            if path.name == "ci.example.yml":
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


if __name__ == "__main__":
    unittest.main()
