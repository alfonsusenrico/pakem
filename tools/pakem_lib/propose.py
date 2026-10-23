"""`pakem propose`: turn a case from any project into a framework change the owner reviews.

The agent that discussed a case with the owner (in whatever project it came up) writes it
down while it still has the context, with the exact change drafted against the framework's
current files. A fresh session in the framework never needs that project's context: the
proposal carries it.

A proposal is a pull request on the framework repository, labelled `proposal` (owner
decision 2026-10-23: one place to review the reasoning and the change together):

- `new` looks for earlier proposals on the same subject, applies the drafted change on a
  branch in a separate worktree (the framework checkout itself stays on `main`), adds its
  change fragment, runs lint and the unit tests, pushes, and opens the pull request.
- `revise` replaces the change and the text after the owner asked for changes, as a new
  commit on the same pull request (history is never rewritten).
- `reject` records the owner's reason and closes the pull request; closed proposals stay
  searchable, so the idea is not raised again.
- The owner's merge accepts it; then `pakem update`, on the owner's instruction, loads it
  for every agent (ADR 0007 as amended 2026-10-22). Version releases still batch the CHANGELOG.

The framework repository is public, so a proposal's text and change are scanned for private
names, the owner's hosts, addresses, and home paths first, and the text is shown before
anything is pushed.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .common import EXIT_DONE, CheckFailed, CommandRefused, Framework, PakemError, confirm_steps, run_git
from .privacy import Scanner
from .requirements import ensure

LABEL = "proposal"
LABELS = {"proposal": "0e8a16", "proposal:rejected": "b60205"}
MARKER = "<!-- pakem:proposal v2 -->"
HOMES = ("kernel", "skill", "reference", "template", "tooling", "adr")
GITHUB_REMOTE = re.compile(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/(.+?)(?:\.git)?/?$")
TEST_COMMAND = (sys.executable, "-m", "unittest", "discover", "-s", "tests")
SLUG_WORDS = 6
COMMIT_TYPES = ("docs", "feat", "fix", "refactor", "ci", "chore")
SUBJECT_LIMIT = 72


@dataclass
class Proposal:
    title: str
    rule: str
    case: str
    why_general: str
    home: str
    evidence: list[str]
    replaces: str
    enforcement: str
    project: str
    diff: str
    commit_type: str = "docs"

    @property
    def slug(self) -> str:
        words = re.findall(r"[a-z0-9]+", self.title.lower())[:SLUG_WORDS]
        return "-".join(words) or "change"

    @property
    def branch(self) -> str:
        return f"docs/proposal-{self.slug}"

    def subject(self, prefix: str = "") -> str:
        """A Conventional Commits subject within 72 characters, cut at a word."""
        start = f"{self.commit_type}: {prefix}"
        return start + shorten(self.title, SUBJECT_LIMIT - len(start))

    def body(self) -> str:
        evidence = "\n".join(f"- {item}" for item in self.evidence) or "- (none given)"
        return "\n".join([
            MARKER,
            f"Proposed from {f'**{self.project}**' if self.project else 'a project'} on "
            f"{date.today().isoformat()} with `pakem propose`.",
            "",
            "## Rule", "", self.rule, "",
            "## Case", "", self.case, "",
            "## Why it holds beyond this project", "", self.why_general, "",
            "## Home", "", self.home, "",
            "## Evidence", "", evidence, "",
            "## Replaces or makes obsolete", "", self.replaces or "nothing", "",
            "## Could a check enforce it instead?", "", self.enforcement or "no", "",
            "## Review", "",
            "The change is this pull request (lint and unit tests passed on its branch). Merge to accept; "
            "ask for changes in the session that proposed it (`pakem propose revise`); or reject with the "
            "reason (`pakem propose reject`). After a merge, `pakem update` is due on each device.",
        ]) + "\n"


def shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit + 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return cut if cut else text[:limit]


def framework_repository(framework: Framework) -> str:
    remote = run_git(framework.root, "config", "--get", "remote.origin.url", check=False).stdout.strip()
    match = GITHUB_REMOTE.match(remote)
    if not match:
        raise PakemError(f"the framework checkout's origin is not on GitHub ({remote or 'none'})")
    return f"{match[1]}/{match[2]}"


def gh(*arguments: str, data: str | None = None) -> subprocess.CompletedProcess:
    completed = subprocess.run(["gh", *arguments], input=data, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        raise PakemError(f"gh {arguments[0]} {arguments[1] if len(arguments) > 1 else ''} failed: "
                         f"{detail[-1] if detail else completed.returncode}")
    return completed


def ensure_labels(repository: str) -> None:
    for name, color in LABELS.items():
        subprocess.run(["gh", "label", "create", name, "--repo", repository, "--color", color, "--force"],
                       capture_output=True, text=True)


def read_diff(path: str) -> str:
    diff = Path(path).read_text(encoding="utf-8")
    if not diff.strip() or "+++ " not in diff:
        raise PakemError(f"{path} is not a unified diff (draft it with `git diff` in a framework worktree)")
    return diff


def proposal_from_args(args) -> Proposal:
    return Proposal(
        title=args.title, rule=args.rule, case=args.case, why_general=args.why_general, home=args.home,
        evidence=args.evidence or [], replaces=args.replaces or "", enforcement=args.enforcement or "",
        project=args.project or "", diff=read_diff(args.change_file),
        commit_type=args.type or ("feat" if args.home == "tooling" else "docs"),
    )


def refuse_private_text(proposal: Proposal) -> None:
    """The framework repository is public: nothing private goes into a proposal's text or
    change (owner decision 2026-10-23). Places are named, values never."""
    scanner = Scanner.for_kinds(names=True, hosts=True, addresses=True, homes=True)
    places = [f"body line {number}: {', '.join(kinds)}" for number, kinds in scanner.findings(proposal.body())]
    for number, line in enumerate(proposal.diff.splitlines(), start=1):
        if line.startswith("+") and not line.startswith("+++"):
            kinds = scanner.kinds_in(line)
            if kinds:
                places.append(f"change line {number}: {', '.join(kinds)}")
    if places:
        raise CommandRefused(
            "the proposal would publish private text to the public framework repository; rewrite it in generic "
            "terms (\"a client project\", \"the server\"):\n  " + "\n  ".join(places)
        )


def similar(repository: str, title: str) -> list[str]:
    words = re.findall(r"[a-z]{4,}", title.lower())[:4]
    if not words:
        return []
    found = gh("pr", "list", "--repo", repository, "--label", LABEL, "--state", "all", "--search",
               " ".join(words), "--json", "number,title,state", "--limit", "5")
    return [f"#{item['number']} {item['title']} ({item['state'].lower()})" for item in json.loads(found.stdout or "[]")]


def check_change(worktree: Path) -> None:
    """Lint and unit tests on the proposal branch; nothing is pushed when one fails."""
    commands = [[sys.executable, str(worktree / "tools" / "pakem"), "--root", str(worktree), "lint"], [*TEST_COMMAND]]
    for command in commands:
        completed = subprocess.run(command, cwd=worktree, capture_output=True, text=True)
        if completed.returncode != 0:
            tail = "\n".join((completed.stdout + completed.stderr).strip().splitlines()[-15:])
            raise CheckFailed(f"`{shlex.join(command[-2:])}` failed on the proposal branch:\n{tail}")


def apply_change(worktree: Path, proposal: Proposal) -> None:
    applied = subprocess.run(["git", "-C", str(worktree), "apply", "--index", "-"], input=proposal.diff,
                             capture_output=True, text=True)
    if applied.returncode != 0:
        raise CommandRefused(f"the drafted change does not apply to the framework's main: {applied.stderr.strip()}")
    fragment = worktree / "changes" / f"changed-proposal-{proposal.slug}.md"
    fragment.write_text(f"- {proposal.title}.\n", encoding="utf-8")
    run_git(worktree, "add", str(fragment))


class Worktree:
    """A branch checked out beside the framework checkout, removed afterwards unless a check
    failed (then it stays for a look)."""

    def __init__(self, framework: Framework, name: str) -> None:
        self.framework = framework
        self.path = framework.root.parent / f"{framework.root.name}-{name}"

    def __enter__(self) -> Path:
        if self.path.exists():
            raise CommandRefused(f"{self.path} already exists; remove it or finish the proposal there")
        return self.path

    def __exit__(self, kind, error, trace) -> None:
        if isinstance(error, CheckFailed):
            print(f"The branch stays in {self.path} for a look; remove it with `git worktree remove {self.path}`.")
        elif self.path.exists():
            run_git(self.framework.root, "worktree", "remove", "--force", str(self.path))


def run_propose_new(args) -> int:
    framework = Framework(Path(args.root))
    ensure(framework, ("git", "gh", "github-login"), args, "pakem propose new")
    repository = framework_repository(framework)
    proposal = proposal_from_args(args)
    refuse_private_text(proposal)
    earlier = similar(repository, proposal.title)
    if earlier and not args.new_subject:
        raise CommandRefused(
            "earlier proposals may cover this already: " + "; ".join(earlier)
            + ". Revise one of them (`pakem propose revise <number>`), or pass --new-subject if it is different"
        )
    run_git(framework.root, "fetch", "--quiet", "origin")
    if args.plan:
        with tempfile.TemporaryDirectory(prefix="pakem-propose-") as directory:
            check = Path(directory) / "main"
            run_git(framework.root, "worktree", "add", "--quiet", "--detach", str(check), "origin/main")
            try:
                apply_change(check, proposal)
            finally:
                run_git(framework.root, "worktree", "remove", "--force", str(check))
        print(proposal.body())
        print(f"Plan only: the change applies to main; branch {proposal.branch} and the pull request were not made.")
        return EXIT_DONE
    print(proposal.body())
    print(f"Pushes branch {proposal.branch} to the public repository {repository} and opens this pull request.")
    confirm_steps(args.yes, "pakem propose new")
    with Worktree(framework, f"proposal-{proposal.slug}") as worktree:
        run_git(framework.root, "worktree", "add", "--quiet", "-B", proposal.branch, str(worktree), "origin/main")
        apply_change(worktree, proposal)
        check_change(worktree)
        run_git(worktree, "commit", "--quiet", "-m", proposal.subject())
        run_git(worktree, "push", "--quiet", "-u", "origin", proposal.branch)
        ensure_labels(repository)
        created = gh("pr", "create", "--repo", repository, "--base", "main", "--head", proposal.branch,
                     "--label", LABEL, "--title", proposal.subject(), "--body-file", "-",
                     data=proposal.body())
    print(f"Proposal for the owner's review (merge to accept): {created.stdout.strip()}")
    return EXIT_DONE


def load_proposal(repository: str, number: int) -> dict:
    found = gh("pr", "view", str(number), "--repo", repository, "--json", "number,title,body,state,headRefName,url")
    proposal = json.loads(found.stdout)
    if MARKER not in (proposal.get("body") or ""):
        raise CommandRefused(f"#{number} is not a pakem proposal")
    return proposal


def run_propose_revise(args) -> int:
    """A new commit on the proposal's branch: main merged in, the old change undone, the new
    one applied; the pull request's text replaced."""
    framework = Framework(Path(args.root))
    ensure(framework, ("git", "gh", "github-login"), args, "pakem propose revise")
    repository = framework_repository(framework)
    current = load_proposal(repository, args.number)
    if current["state"].lower() != "open":
        raise CommandRefused(f"#{args.number} is {current['state'].lower()}; only an open proposal can be revised")
    proposal = proposal_from_args(args)
    refuse_private_text(proposal)
    print(proposal.body())
    print(f"Pushes a new commit to {current['headRefName']} on the public repository {repository}.")
    confirm_steps(args.yes, "pakem propose revise")
    branch = current["headRefName"]
    run_git(framework.root, "fetch", "--quiet", "origin")
    with Worktree(framework, f"proposal-{args.number}") as worktree:
        run_git(framework.root, "worktree", "add", "--quiet", "-B", branch, str(worktree), f"origin/{branch}")
        run_git(worktree, "merge", "--quiet", "--no-edit", "origin/main")
        earlier = run_git(worktree, "diff", "origin/main", "HEAD").stdout
        if earlier.strip():
            undone = subprocess.run(["git", "-C", str(worktree), "apply", "-R", "--index", "-"], input=earlier,
                                    capture_output=True, text=True)
            if undone.returncode != 0:
                raise CheckFailed(f"the earlier change could not be undone on {branch}: {undone.stderr.strip()}")
        apply_change(worktree, proposal)
        check_change(worktree)
        run_git(worktree, "commit", "--quiet", "-m", proposal.subject("revise "))
        run_git(worktree, "push", "--quiet", "origin", branch)
    gh("pr", "edit", str(args.number), "--repo", repository, "--title", proposal.subject(),
       "--body-file", "-", data=proposal.body())
    gh("pr", "comment", str(args.number), "--repo", repository, "--body", "Revised after the owner's review.")
    print(f"Proposal #{args.number} revised: {current['url']}")
    return EXIT_DONE


def run_propose_list(args) -> int:
    framework = Framework(Path(args.root))
    repository = framework_repository(framework)
    found = gh("pr", "list", "--repo", repository, "--label", LABEL, "--state", args.state,
               "--json", "number,title,state,labels", "--limit", "100")
    items = json.loads(found.stdout or "[]")
    if args.json:
        print(json.dumps(items, indent=2))
        return EXIT_DONE
    for item in items:
        labels = {label["name"] for label in item.get("labels", [])}
        state = item["state"].lower()
        decision = "rejected" if "proposal:rejected" in labels else "accepted" if state == "merged" else state
        print(f"#{item['number']:<4} {decision:<9} {item['title']}")
    if not items:
        print("No proposals.")
    return EXIT_DONE


def run_propose_show(args) -> int:
    framework = Framework(Path(args.root))
    proposal = load_proposal(framework_repository(framework), args.number)
    print(f"#{proposal['number']} {proposal['title']} ({proposal['state'].lower()}) {proposal['url']}\n")
    print(proposal["body"])
    return EXIT_DONE


def run_propose_reject(args) -> int:
    framework = Framework(Path(args.root))
    repository = framework_repository(framework)
    proposal = load_proposal(repository, args.number)
    if proposal["state"].lower() != "open":
        raise CommandRefused(f"#{args.number} is {proposal['state'].lower()} already")
    ensure_labels(repository)
    gh("pr", "comment", str(args.number), "--repo", repository, "--body", f"Rejected by the owner: {args.reason}")
    gh("pr", "edit", str(args.number), "--repo", repository, "--add-label", "proposal:rejected")
    gh("pr", "close", str(args.number), "--repo", repository, "--delete-branch")
    print(f"Proposal #{args.number} rejected and closed; the reason is on the pull request.")
    return EXIT_DONE


def add_parser(commands) -> None:
    propose = commands.add_parser("propose", help="propose a framework change from a case in any project")
    sub = propose.add_subparsers(dest="propose_command", required=True)
    for name, text in (("new", "open a proposal pull request for the owner's review"),
                       ("revise", "replace a proposal's change and text")):
        parser = sub.add_parser(name, help=text)
        if name == "revise":
            parser.add_argument("number", type=int)
        parser.add_argument("--title", required=True, help="the rule in a few words")
        parser.add_argument("--rule", required=True, help="the proposed rule, as it should read")
        parser.add_argument("--case", required=True, help="what happened, in the project it came from")
        parser.add_argument("--why-general", required=True, help="why it holds beyond this project")
        parser.add_argument("--home", required=True, choices=HOMES, help="where in the framework it belongs")
        parser.add_argument("--change-file", required=True, help="unified diff against the framework's main")
        parser.add_argument("--evidence", action="append", help="file, commit, or link (repeatable)")
        parser.add_argument("--replaces", help="the rule it replaces or makes obsolete")
        parser.add_argument("--enforcement", help="a check that could enforce it instead of prose, if any")
        parser.add_argument("--project", help="the project it came from, if its name may be public "
                                               "(default: \"a project\")")
        parser.add_argument("--type", choices=COMMIT_TYPES,
                            help="commit type (default: feat for tooling, else docs)")
        parser.add_argument("--plan", action="store_true", help="check the change and print the text; send nothing")
        parser.add_argument("--yes", action="store_true", help="push without asking (after the owner saw the text)")
        parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
        if name == "new":
            parser.add_argument("--new-subject", action="store_true", help="it differs from the similar proposals found")
    listing = sub.add_parser("list", help="proposals and their decisions")
    listing.add_argument("--state", choices=("open", "closed", "merged", "all"), default="open")
    listing.add_argument("--json", action="store_true")
    show = sub.add_parser("show", help="print one proposal")
    show.add_argument("number", type=int)
    reject = sub.add_parser("reject", help="on the owner's rejection: record the reason and close it")
    reject.add_argument("number", type=int)
    reject.add_argument("--reason", required=True)


COMMANDS = {
    "new": run_propose_new,
    "revise": run_propose_revise,
    "list": run_propose_list,
    "show": run_propose_show,
    "reject": run_propose_reject,
}


def run_propose(args) -> int:
    return COMMANDS[args.propose_command](args)
