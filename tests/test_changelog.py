"""pakem changelog: change fragments in changes/ and releases into CHANGELOG.md."""

from __future__ import annotations

import sys
import textwrap
import unittest

from support import REPO_ROOT, IsolatedTestCase, write

sys.path.insert(0, str(REPO_ROOT / "tools"))

from pakem_lib.changelog import parse_fragment_text  # noqa: E402

CHANGELOG_HEAD = textwrap.dedent(
    """\
    # Changelog

    All notable changes.

    ## [Unreleased]
    """
)

CHANGELOG_TAIL = textwrap.dedent(
    """\
    ## [0.1.0] - 2026-01-01

    ### Added

    - First release.
    """
)


class FragmentParsingTests(unittest.TestCase):
    def test_bullets_with_continuation_lines(self) -> None:
        entries, problems = parse_fragment_text("- One\n  continued.\n- Two.\n")
        self.assertEqual(problems, [])
        self.assertEqual(entries, ["- One\n  continued.", "- Two."])

    def test_blank_lines_separate_entries(self) -> None:
        entries, problems = parse_fragment_text("- One.\n\n- Two.\n")
        self.assertEqual(problems, [])
        self.assertEqual(entries, ["- One.", "- Two."])

    def test_rejects_text_that_is_not_a_bullet(self) -> None:
        cases = [
            "Plain paragraph.\n",
            "* Star bullet.\n",
            "-No space.\n",
            "  Continuation without a bullet.\n",
            "- One.\n\n  Continuation after a blank line.\n",
            "- One.\n continued with one space.\n",
            "# Heading\n- One.\n",
        ]
        for text in cases:
            with self.subTest(text=text):
                _, problems = parse_fragment_text(text)
                self.assertTrue(problems, text)


class ChangelogCommandTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.changelog = self.framework / "CHANGELOG.md"
        self.changes = self.framework / "changes"
        write(self.changes / "README.md", "# Change fragments\n\nNot a fragment.\n")
        self.write_changelog("")

    def write_changelog(self, unreleased_body: str) -> None:
        text = CHANGELOG_HEAD
        if unreleased_body:
            text += "\n" + unreleased_body
        write(self.changelog, text + "\n" + CHANGELOG_TAIL)

    def fragment(self, name: str, text: str) -> None:
        write(self.changes / name, text)

    def version(self) -> str:
        return (self.framework / "VERSION").read_text()

    def release(self, *extra: str):
        return self.pakem("changelog", "--release", "0.3.0", "--date", "2026-10-09", *extra)

    def test_pending_view_groups_by_type_in_order(self) -> None:
        self.fragment("security-token-redaction.md", "- Redact tokens.\n")
        self.fragment("added-zeta.md", "- Zeta feature.\n")
        self.fragment("fixed-crash.md", "- Fix crash.\n")
        self.fragment("added-alpha.md", "- Alpha feature\n  over two lines.\n")
        completed = self.pakem("changelog")
        self.assert_ok(completed)
        out = completed.stdout
        self.assertIn("4 pending entry(ies): 4 fragment file(s), 0 legacy", out)
        self.assertNotIn("README.md", out)
        order = [out.index(text) for text in ("### Added", "- Alpha", "  over two lines.", "- Zeta", "### Fixed", "### Security")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("### Changed", out)

    def test_pending_view_with_nothing_pending(self) -> None:
        completed = self.pakem("changelog")
        self.assert_ok(completed)
        self.assertIn("No pending changes.", completed.stdout)

    def test_dry_run_prints_section_and_writes_nothing(self) -> None:
        self.fragment("added-alpha.md", "- Alpha.\n")
        before = self.snapshot(self.framework)
        completed = self.release()
        self.assert_ok(completed)
        self.assertIn("## [0.3.0] - 2026-10-09\n\n### Added\n\n- Alpha.\n", completed.stdout)
        self.assertIn("VERSION: 0.2.0 -> 0.3.0", completed.stdout)
        self.assertIn("Dry run: nothing written.", completed.stdout)
        self.assertEqual(before, self.snapshot(self.framework))

    def test_apply_inserts_release_and_consumes_fragments(self) -> None:
        self.fragment("fixed-crash.md", "- Fix crash.\n")
        self.fragment("added-alpha.md", "- Alpha\n  continued.\n- Second alpha.\n")
        self.fragment("deprecated-old-flag.md", "- Old flag.\n")
        self.assert_ok(self.release("--apply"))
        expected = CHANGELOG_HEAD + textwrap.dedent(
            """\

            ## [0.3.0] - 2026-10-09

            ### Added

            - Alpha
              continued.
            - Second alpha.

            ### Deprecated

            - Old flag.

            ### Fixed

            - Fix crash.

            """
        ) + CHANGELOG_TAIL
        self.assertEqual(self.changelog.read_text(), expected)
        self.assertEqual(self.version(), "0.3.0\n")
        self.assertEqual(sorted(path.name for path in self.changes.iterdir()), ["README.md"])
        self.assertIn("No pending changes.", self.pakem("changelog").stdout)

    def test_legacy_bullets_move_into_release(self) -> None:
        self.write_changelog(
            textwrap.dedent(
                """\
                - Loose legacy entry
                  with a continuation.

                - Second loose entry.

                ### Added

                - Legacy added.

                ### Fixed

                - Legacy fix.
                """
            )
        )
        self.fragment("changed-behaviour.md", "- Fragment change.\n")
        self.fragment("added-alpha.md", "- Fragment added.\n")
        self.assert_ok(self.release("--apply"))
        text = self.changelog.read_text()
        unreleased, released = text.split("## [0.3.0] - 2026-10-09\n", 1)
        self.assertTrue(unreleased.endswith("## [Unreleased]\n\n"))
        self.assertNotIn("- ", unreleased)
        expected = textwrap.dedent(
            """\

            ### Added

            - Fragment added.
            - Legacy added.

            ### Changed

            - Fragment change.
            - Loose legacy entry
              with a continuation.
            - Second loose entry.

            ### Fixed

            - Legacy fix.

            """
        ) + CHANGELOG_TAIL
        self.assertEqual(released, expected)

    def test_legacy_bullets_alone_can_be_released(self) -> None:
        self.write_changelog("- Only legacy.\n")
        self.assert_ok(self.release("--apply"))
        self.assertIn("## [0.3.0] - 2026-10-09\n\n### Changed\n\n- Only legacy.\n", self.changelog.read_text())

    def test_release_at_end_of_file(self) -> None:
        write(self.changelog, CHANGELOG_HEAD)
        self.fragment("removed-old.md", "- Old thing.\n")
        self.assert_ok(self.release("--apply"))
        self.assertEqual(
            self.changelog.read_text(),
            CHANGELOG_HEAD + "\n## [0.3.0] - 2026-10-09\n\n### Removed\n\n- Old thing.\n",
        )

    def assert_refused(self, completed, needle: str) -> None:
        self.assert_fails(completed, code=2)
        self.assertIn(needle, completed.stderr)

    def test_refusals_write_nothing(self) -> None:
        self.fragment("added-alpha.md", "- Alpha.\n")
        before = self.snapshot(self.framework)
        cases = [
            (("--release", "0.2.0"), "not greater than VERSION 0.2.0"),
            (("--release", "0.1.9"), "not greater than VERSION 0.2.0"),
            (("--release", "0.3"), "must be X.Y.Z"),
            (("--release", "0.3.0-rc1"), "must be X.Y.Z"),
            (("--release", "0.3.0", "--date", "2026-13-01"), "is not a valid date"),
            (("--release", "0.3.0", "--date", "09-10-2026"), "must be YYYY-MM-DD"),
            (("--release", "0.3.0", "--date", "2026-1-9"), "must be YYYY-MM-DD"),
            (("--apply",), "require --release"),
            (("--date", "2026-10-09"), "require --release"),
        ]
        for arguments, needle in cases:
            with self.subTest(arguments=arguments):
                self.assert_refused(self.pakem("changelog", *arguments, "--apply"), needle)
        self.assertEqual(before, self.snapshot(self.framework))

    def test_refuses_when_nothing_to_release(self) -> None:
        self.assert_refused(self.release("--apply"), "nothing to release")
        self.assertEqual(self.version(), "0.2.0\n")

    def test_refuses_invalid_fragments(self) -> None:
        self.fragment("added-alpha.md", "- Alpha.\n")
        cases = [
            ("feature-thing.md", "- Thing.\n", "unknown type 'feature'"),
            ("added_thing.md", "- Thing.\n", "bad name"),
            ("Added-Thing.md", "- Thing.\n", "bad name"),
            ("added-thing.txt", "- Thing.\n", "bad name"),
            ("added-thing.md", "No bullet here.\n", "has no bullet line"),
            ("added-empty.md", "", "has no bullet line"),
        ]
        for name, text, needle in cases:
            with self.subTest(name=name):
                self.fragment(name, text)
                before = self.snapshot(self.framework)
                self.assert_refused(self.release("--apply"), f"changes/{name}: ")
                self.assert_refused(self.release("--apply"), needle)
                self.assert_refused(self.pakem("changelog"), needle)
                self.assertEqual(before, self.snapshot(self.framework))
                (self.changes / name).unlink()

    def test_refuses_unknown_legacy_subsection_or_text(self) -> None:
        self.fragment("added-alpha.md", "- Alpha.\n")
        self.write_changelog("### Notes\n\n- Something.\n")
        self.assert_refused(self.release("--apply"), "unknown subsection 'Notes'")
        self.write_changelog("A paragraph.\n")
        self.assert_refused(self.release("--apply"), "neither a bullet entry nor a ### subsection")

    def test_refuses_existing_release_section(self) -> None:
        write(self.framework / "VERSION", "0.0.9\n")
        self.fragment("added-alpha.md", "- Alpha.\n")
        completed = self.pakem("changelog", "--release", "0.1.0", "--date", "2026-10-09", "--apply")
        self.assert_refused(completed, "already has a ## [0.1.0] section")

    def test_release_of_prerelease_version(self) -> None:
        write(self.framework / "VERSION", "0.3.0-rc1\n")
        self.fragment("added-alpha.md", "- Alpha.\n")
        self.assert_ok(self.release("--apply"))
        self.assertEqual(self.version(), "0.3.0\n")

    def test_missing_unreleased_heading(self) -> None:
        write(self.changelog, "# Changelog\n\n" + CHANGELOG_TAIL)
        self.fragment("added-alpha.md", "- Alpha.\n")
        self.assert_refused(self.release("--apply"), "no '## [Unreleased]' section")


if __name__ == "__main__":
    unittest.main()
