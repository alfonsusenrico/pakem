"""`pakem propose`: turn a case from any project into a framework change the owner reviews.

The agent that discussed a case with the owner (in whatever project it came up) writes it
down while it still has the context, with the exact change drafted against the framework's
current files. A fresh session in the framework never needs that project's context: the
proposal carries it.

- `new` checks the drafted change applies to the framework's `main`, looks for earlier
  proposals on the same subject, and opens a GitHub issue labelled `proposal` on the
  framework repository (the owner's own `gh` login).
- `revise` replaces a proposal's text after the owner asked for changes.
- `accept`, only after the owner accepted it, applies the change on a branch in a separate
  worktree (the framework checkout itself stays on `main`), adds its change fragment, runs
  lint and tests, pushes, and opens a pull request that closes the issue.
- `reject` records the owner's reason and closes the issue, so the idea is not raised again.

Each accepted proposal goes live when the owner merges its pull request and devices run
`pakem update` (ADR 0007 as amended 2026-10-22); version releases still batch the CHANGELOG.
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

from .common import EXIT_DONE, CheckFailed, CommandRefused, Framework, PakemError, run_git
from .requirements import ensure

LABEL = "proposal"
LABELS = {"proposal": "0e8a16", "proposal:accepted": "1d76db", "proposal:rejected": "b60205"}
MARKER = "<!-- pakem:proposal v1 -->"
HOMES = ("kernel", "skill", "reference", "template", "tooling", "adr")
GITHUB_REMOTE = re.compile(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/(.+?)(?:\.git)?/?$")
DIFF_BLOCK = re.compile(r"^```diff\n(.*?)^```$", re.MULTILINE | re.DOTALL)
SLUG = re.compile(r"[^a-z0-9]+")
TEST_COMMAND = (sys.executable, "-m", "unittest", "discover", "-s", "tests")


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

    def body(self) -> str:
        evidence = "\n".join(f"- {item}" for item in self.evidence) or "- (none given)"
        return "\n".join([
            MARKER,
            f"Proposed from **{self.project}** on {date.today().isoformat()} with `pakem propose`.",
            "",
            "## Rule", "", self.rule, "",
            "## Case", "", self.case, "",
            "## Why it holds beyond this project", "", self.why_general, "",
            "## Home", "", self.home, "",
            "## Evidence", "", evidence, "",
            "## Replaces or makes obsolete", "", self.replaces or "nothing", "",
            "## Could a check enforce it instead?", "", self.enforcement or "no", "",
            "## Change", "",
            "Drafted against the framework's `main`; `pakem propose accept` applies exactly this.", "",
            "```diff", self.diff.rstrip("\n"), "```", "",
            "## Review", "",
            "Accept, ask for changes, or reject (with the reason) in the session that proposed it or here.",
        ]) + "\n"


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


def check_applies(framework: Framework, diff: str) -> None:
    """The drafted change must apply to the framework's main as it is now."""
    run_git(framework.root, "fetch", "--quiet", "origin")
    with tempfile.TemporaryDirectory(prefix="pakem-propose-") as directory:
        worktree = Path(directory) / "main"
        run_git(framework.root, "worktree", "add", "--quiet", "--detach", str(worktree), "origin/main")
        try:
            applied = subprocess.run(["git", "-C", str(worktree), "apply", "--check", "-"], input=diff,
                                     capture_output=True, text=True)
        finally:
            run_git(framework.root, "worktree", "remove", "--force", str(worktree))
    if applied.returncode != 0:
        raise CommandRefused(f"the drafted change does not apply to the framework's main: {applied.stderr.strip()}")


def read_diff(path: str) -> str:
    diff = Path(path).read_text(encoding="utf-8")
    if not diff.strip() or "+++ " not in diff:
        raise PakemError(f"{path} is not a unified diff (draft it with `git diff` in a framework worktree)")
    return diff


def proposal_from_args(args) -> Proposal:
    if args.home not in HOMES:
        raise PakemError(f"--home must be one of {', '.join(HOMES)}")
    project = args.project or Path.cwd().name
    return Proposal(
        title=args.title, rule=args.rule, case=args.case, why_general=args.why_general, home=args.home,
        evidence=args.evidence or [], replaces=args.replaces or "", enforcement=args.enforcement or "",
        project=project, diff=read_diff(args.change_file),
    )


def similar(repository: str, title: str) -> list[str]:
    words = [word for word in re.findall(r"[a-z]{4,}", title.lower())][:4]
    if not words:
        return []
    found = gh("issue", "list", "--repo", repository, "--label", LABEL, "--state", "all", "--search",
               " ".join(words), "--json", "number,title,state", "--limit", "5")
    return [f"#{item['number']} {item['title']} ({item['state'].lower()})" for item in json.loads(found.stdout or "[]")]


def run_propose_new(args) -> int:
    framework = Framework(Path(args.root))
    ensure(framework, ("git", "gh", "github-login"), args, "pakem propose new")
    repository = framework_repository(framework)
    proposal = proposal_from_args(args)
    check_applies(framework, proposal.diff)
    earlier = similar(repository, proposal.title)
    if earlier and not args.new_subject:
        raise CommandRefused(
            "earlier proposals may cover this already: " + "; ".join(earlier)
            + ". Revise one of them (`pakem propose revise <number>`), or pass --new-subject if it is different"
        )
    if args.plan:
        print(proposal.body())
        print("Plan only: nothing was sent.")
        return EXIT_DONE
    ensure_labels(repository)
    created = gh("issue", "create", "--repo", repository, "--label", LABEL, "--title", f"proposal: {proposal.title}",
                 "--body-file", "-", data=proposal.body())
    print(f"Proposal opened for the owner's review: {created.stdout.strip()}")
    return EXIT_DONE


def run_propose_revise(args) -> int:
    framework = Framework(Path(args.root))
    ensure(framework, ("git", "gh", "github-login"), args, "pakem propose revise")
    repository = framework_repository(framework)
    proposal = proposal_from_args(args)
    check_applies(framework, proposal.diff)
    gh("issue", "edit", str(args.number), "--repo", repository, "--title", f"proposal: {proposal.title}",
       "--body-file", "-", data=proposal.body())
    gh("issue", "comment", str(args.number), "--repo", repository, "--body", "Revised after the owner's review.")
    print(f"Proposal #{args.number} revised.")
    return EXIT_DONE


def load_issue(repository: str, number: int) -> dict:
    found = gh("issue", "view", str(number), "--repo", repository, "--json", "number,title,body,state,labels,url")
    issue = json.loads(found.stdout)
    if MARKER not in (issue.get("body") or ""):
        raise CommandRefused(f"#{number} is not a pakem proposal")
    return issue


def run_propose_list(args) -> int:
    framework = Framework(Path(args.root))
    repository = framework_repository(framework)
    found = gh("issue", "list", "--repo", repository, "--label", LABEL, "--state", args.state,
               "--json", "number,title,state,labels", "--limit", "100")
    items = json.loads(found.stdout or "[]")
    if args.json:
        print(json.dumps(items, indent=2))
        return EXIT_DONE
    for item in items:
        labels = {label["name"] for label in item.get("labels", [])}
        decision = "accepted" if "proposal:accepted" in labels else "rejected" if "proposal:rejected" in labels else "open"
        print(f"#{item['number']:<4} {decision:<9} {item['title']}")
    if not items:
        print("No proposals.")
    return EXIT_DONE


def run_propose_show(args) -> int:
    framework = Framework(Path(args.root))
    issue = load_issue(framework_repository(framework), args.number)
    print(f"#{issue['number']} {issue['title']} ({issue['state'].lower()}) {issue['url']}\n")
    print(issue["body"])
    return EXIT_DONE


def run_propose_reject(args) -> int:
    framework = Framework(Path(args.root))
    repository = framework_repository(framework)
    load_issue(repository, args.number)
    ensure_labels(repository)
    gh("issue", "comment", str(args.number), "--repo", repository, "--body", f"Rejected by the owner: {args.reason}")
    gh("issue", "edit", str(args.number), "--repo", repository, "--add-label", "proposal:rejected")
    gh("issue", "close", str(args.number), "--repo", repository, "--reason", "not planned")
    print(f"Proposal #{args.number} rejected and closed; the reason is on the issue.")
    return EXIT_DONE


def _slug(title: str) -> str:
    return SLUG.sub("-", title.lower()).strip("-")[:40].rstrip("-") or "change"


def run_propose_accept(args) -> int:
    """Only on the owner's acceptance: the drafted change becomes a pull request."""
    framework = Framework(Path(args.root))
    ensure(framework, ("git", "gh", "github-login"), args, "pakem propose accept")
    repository = framework_repository(framework)
    issue = load_issue(repository, args.number)
    if issue["state"].lower() != "open":
        raise CommandRefused(f"#{args.number} is {issue['state'].lower()}; only an open proposal can be accepted")
    match = DIFF_BLOCK.search(issue["body"])
    if not match:
        raise CommandRefused(f"#{args.number} has no drafted change (a ```diff block)")
    diff = match[1]
    title = issue["title"].removeprefix("proposal: ").strip()
    slug = _slug(title)
    branch = f"docs/proposal-{args.number}-{slug}"
    worktree = framework.root.parent / f"{framework.root.name}-proposal-{args.number}"
    if worktree.exists():
        raise CommandRefused(f"{worktree} already exists; remove it or finish that proposal there")
    print(f"pakem propose accept #{args.number}: {title}")
    print(f"  branch {branch} from origin/main in {worktree}")
    print("  apply the drafted change, add its change fragment, run lint and tests, push, open a pull request")
    if args.plan:
        print("Plan only: nothing changed.")
        return EXIT_DONE
    run_git(framework.root, "fetch", "--quiet", "origin")
    run_git(framework.root, "worktree", "add", "--quiet", "-b", branch, str(worktree), "origin/main")
    try:
        applied = subprocess.run(["git", "-C", str(worktree), "apply", "-"], input=diff, capture_output=True, text=True)
        if applied.returncode != 0:
            raise CheckFailed(f"the drafted change no longer applies to main: {applied.stderr.strip()}; "
                              f"revise the proposal with `pakem propose revise {args.number}`")
        fragment = worktree / "changes" / f"changed-proposal-{args.number}-{slug}.md"
        fragment.write_text(f"- {title} (proposal #{args.number}).\n", encoding="utf-8")
        checks = [[sys.executable, str(worktree / "tools" / "pakem"), "--root", str(worktree), "lint"],
                  [*TEST_COMMAND]]
        for command in checks:
            completed = subprocess.run(command, cwd=worktree, capture_output=True, text=True)
            if completed.returncode != 0:
                tail = "\n".join((completed.stdout + completed.stderr).strip().splitlines()[-15:])
                raise CheckFailed(f"`{shlex.join(command[-2:])}` failed on the proposal branch:\n{tail}")
        run_git(worktree, "add", "-A")
        run_git(worktree, "commit", "--quiet", "-m", f"docs: {title[:60]}")
        run_git(worktree, "push", "--quiet", "-u", "origin", branch)
        body = (f"Accepted by the owner: proposal #{args.number} ({issue['url']}).\n\nCloses #{args.number}.\n\n"
                "Checks on this branch: `pakem lint` 0 errors, unit tests passed.")
        created = gh("pr", "create", "--repo", repository, "--base", "main", "--head", branch,
                     "--title", f"docs: {title[:60]}", "--body", body)
    except PakemError:
        print(f"The branch stays in {worktree} for a look; remove it with `git worktree remove {worktree}`.")
        raise
    url = created.stdout.strip()
    ensure_labels(repository)
    gh("issue", "edit", str(args.number), "--repo", repository, "--add-label", "proposal:accepted")
    gh("issue", "comment", str(args.number), "--repo", repository, "--body", f"Accepted by the owner; pull request {url}")
    run_git(framework.root, "worktree", "remove", "--force", str(worktree))
    print(f"Pull request for the owner to merge: {url}")
    print("After it merges, run `pakem update` so every agent loads the rule on its next turn.")
    return EXIT_DONE


def add_parser(commands) -> None:
    propose = commands.add_parser("propose", help="propose a framework change from a case in any project")
    sub = propose.add_subparsers(dest="propose_command", required=True)
    for name, text in (("new", "open a proposal for the owner's review"), ("revise", "replace a proposal's text")):
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
        parser.add_argument("--project", help="the project it came from (default: this directory's name)")
        parser.add_argument("--plan", action="store_true", help="print the proposal without sending it")
        parser.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
        if name == "new":
            parser.add_argument("--new-subject", action="store_true", help="it differs from the similar proposals found")
    listing = sub.add_parser("list", help="proposals and their decisions")
    listing.add_argument("--state", choices=("open", "closed", "all"), default="open")
    listing.add_argument("--json", action="store_true")
    show = sub.add_parser("show", help="print one proposal")
    show.add_argument("number", type=int)
    accept = sub.add_parser("accept", help="on the owner's acceptance: turn the proposal into a pull request")
    accept.add_argument("number", type=int)
    accept.add_argument("--plan", action="store_true", help="print the steps without doing them")
    accept.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    accept.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    reject = sub.add_parser("reject", help="on the owner's rejection: record the reason and close it")
    reject.add_argument("number", type=int)
    reject.add_argument("--reason", required=True)


COMMANDS = {
    "new": run_propose_new,
    "revise": run_propose_revise,
    "list": run_propose_list,
    "show": run_propose_show,
    "accept": run_propose_accept,
    "reject": run_propose_reject,
}


def run_propose(args) -> int:
    return COMMANDS[args.propose_command](args)
