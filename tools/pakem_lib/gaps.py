"""Gaps and decisions (ADR 0011, decisions 2, 3, and 6): what only the project can decide.

A gap is a project- or case-specific question a `pakem` command leaves open. Which gaps a
project has follows from its fixed facts (.pakem/project.toml); each answer is recorded in
the committed .pakem/decisions.toml in one of three states:

- answered: the answer and its reason;
- provisional: a reversible working assumption and the trigger to revisit it;
- pending: what is missing, who provides it, and the date to ask again.

`pakem decide` records a state, `pakem project check --gaps` lists every gap with its
state and whether its target files prove the answer, and `pakem status` raises pending
decisions when they are due. A gap names the stages it blocks, so agents work on the rest.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from .common import (
    EXIT_DONE,
    EXIT_GAPS,
    CommandRefused,
    PakemError,
    display_path,
    normalize_newlines,
    read_text,
    toml_string,
    toml_value,
    write_text_atomically,
)

PAKEM_DIR = ".pakem"
FACTS_FILE = "project.toml"
DECISIONS_FILE = "decisions.toml"
ANSWERED, PROVISIONAL, PENDING, OPEN = "answered", "provisional", "pending", "open"
STATES = (ANSWERED, PROVISIONAL, PENDING)
STAGES = ("code", "ci", "deploy-setup", "release")
GAP_ID = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)*$")
DECISIONS_START = "<!-- pakem:decisions:start -->"
DECISIONS_END = "<!-- pakem:decisions:end -->"
# Starter files carry this marker until the project replaces them with its own.
STUB_MARKER = "pakem:stub"
KINDS = ("service", "artifact", "none")
DELIVERIES = ("us", "others", "nobody")


# -- the project's fixed facts ---------------------------------------------------------------


@dataclass
class Facts:
    name: str
    forge: str
    repository: str
    visibility: str
    kind: str
    delivery: str
    stack: str
    environments: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    delivery_note: str = ""

    @property
    def releases_here(self) -> bool:
        return self.kind == "service" and self.delivery == "us"


def facts_path(repo: Path) -> Path:
    return repo / PAKEM_DIR / FACTS_FILE


def load_facts(repo: Path) -> Facts | None:
    path = facts_path(repo)
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise PakemError(f"invalid TOML in {display_path(path)}: {exc}") from exc
    keys = {name for name in Facts.__dataclass_fields__}
    try:
        return Facts(**{key: value for key, value in data.items() if key in keys})
    except TypeError as exc:
        raise PakemError(f"{display_path(path)} lacks a fact: {exc}") from exc


def extra_facts(repo: Path) -> dict:
    """Keys of .pakem/project.toml that Pakem does not know, kept when it rewrites the file."""
    data = _toml(facts_path(repo)) or {}
    return {key: value for key, value in data.items() if key not in Facts.__dataclass_fields__}


def facts_text(facts: Facts, extra: dict | None = None) -> str:
    lines = ["# The project's fixed facts, written by `pakem project init` or `adopt` (ADR 0011)."]
    for key, value in {**asdict(facts), **(extra or {})}.items():
        lines.append(f"{key} = {toml_value(value)}")
    return "\n".join(lines) + "\n"


# -- the gap catalogue -------------------------------------------------------------------------


Check = Callable[[Path], str | None]


@dataclass(frozen=True)
class Gap:
    id: str
    required: bool
    question: str
    why: str
    options: tuple[str, ...]
    default: str | None
    target: str
    done_when: str
    blocks: tuple[str, ...]
    check: Check | None = None

    def describe(self) -> dict:
        return {
            "id": self.id, "required": self.required, "question": self.question, "why": self.why,
            "options": list(self.options), "default": self.default, "target": self.target,
            "done_when": self.done_when, "blocks": list(self.blocks),
        }


def _toml(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return None


# Where a service's own code lives; deploy files, docs, and tests only name a path.
NOT_CODE = {".git", ".pakem", ".venv", "node_modules", "deploy", "docs", "openspec", "research", "tests", "test",
            "dist", "build", "__pycache__"}
CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".go", ".rs", ".rb", ".java", ".kt", ".php"}
MAX_CODE_FILE = 1024 * 1024
SERVICE_TEMPLATES = Path(__file__).resolve().parents[2] / "skills" / "project-bootstrap" / "assets" / "service"


def _code_files(repo: Path):
    for directory, subdirectories, names in os.walk(repo):
        subdirectories[:] = [name for name in subdirectories if name not in NOT_CODE and not name.startswith(".")]
        for name in names:
            path = Path(directory) / name
            if path.suffix in CODE_SUFFIXES and not name.startswith("test_") and ".test." not in name:
                yield path


def _route_in_code(repo: Path, route: str) -> bool:
    """Whether the project's own code serves the path: a quoted literal, or a file route
    (`app/<path>/route.ts`, `pages/api/...`), in a file that is not a starter stub."""
    literal = re.compile(r"""["'`]""" + re.escape(route) + r"""/?["'`?]""")
    segments = tuple(part for part in route.strip("/").split("/") if part)
    for path in _code_files(repo):
        if path.stat().st_size > MAX_CODE_FILE:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if STUB_MARKER in text:
            continue
        parents = path.relative_to(repo).parent.parts
        if literal.search(text) or (segments and parents[-len(segments):] == segments):
            return True
    return False


def _check_health(repo: Path) -> str | None:
    config = _toml(repo / "deploy" / "release.toml")
    if config is None:
        return "deploy/release.toml is missing or not valid TOML"
    health = config.get("health")
    if not isinstance(health, dict) or not all(isinstance(health.get(k), str) for k in ("service", "path", "revision_key")):
        return "deploy/release.toml [health] needs service, path, and revision_key"
    if not isinstance(health.get("port"), int):
        return "deploy/release.toml [health] needs the service's container port"
    if not _route_in_code(repo, health["path"]):
        return f"no code of the project serves {health['path']} (the [health] path) yet"
    guide = repo / "docs" / "deployment.md"
    if not guide.is_file() or STUB_MARKER in guide.read_text(encoding="utf-8", errors="replace"):
        return "docs/deployment.md is missing or still the starter stub"
    return None


def _check_settings(repo: Path) -> str | None:
    manifest = _toml(repo / "deploy" / "settings.toml")
    if manifest is None:
        return "deploy/settings.toml is missing or not valid TOML"
    named = [name for key in ("placement", "secrets", "host_generated", "required", "optional")
             for name in manifest.get(key, [])]
    if not named:
        return "deploy/settings.toml names no settings yet"
    if manifest == _toml(SERVICE_TEMPLATES / "settings.toml.tmpl"):
        return "deploy/settings.toml is still the template. Name the project's own settings"
    return None


def _check_backup(repo: Path) -> str | None:
    hook = repo / "deploy" / "hooks" / "backup"
    if not hook.is_file() or not os.access(hook, os.X_OK):
        return "deploy/hooks/backup is missing or not executable"
    if STUB_MARKER in hook.read_text(encoding="utf-8", errors="replace"):
        return "deploy/hooks/backup is still the starter stub"
    return None


def _check_language(repo: Path) -> str | None:
    agents = repo / "AGENTS.md"
    text = agents.read_text(encoding="utf-8") if agents.is_file() else ""
    match = re.search(r"^(?:[-*+]\s+)?Language:(.*)$", text, re.MULTILINE)
    if match is None or not match[1].strip() or "<!--" in match[1]:
        return "AGENTS.md has no filled `Language:` line"
    return None


def _own_agents_text(repo: Path) -> str:
    """AGENTS.md without comments and generated or pinned blocks: what the project wrote."""
    agents = repo / "AGENTS.md"
    text = normalize_newlines(read_text(agents)) if agents.is_file() else ""
    text = re.sub(r"<!-- pakem:decisions:start -->.*?<!-- pakem:decisions:end -->", "", text, flags=re.DOTALL)
    text = re.sub(r"^<!-- (pakem|agent-framework):(digest|profile).*?^<!-- /(pakem|agent-framework):\2 -->$", "",
                  text, flags=re.DOTALL | re.MULTILINE)
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def _check_migrations(repo: Path) -> str | None:
    if not re.search(r"migrat", _own_agents_text(repo), re.IGNORECASE):
        return "AGENTS.md states no migration tool or rule"
    return None


def _check_gateway(repo: Path) -> str | None:
    text = _own_agents_text(repo)
    if not re.search(r"gateway", text, re.IGNORECASE):
        return "AGENTS.md names no AI gateway"
    return None


CI_OWNER = "ci.owner"
CLIENT_CI = "client CI"


def uses_client_ci(repo: Path) -> bool:
    """Whether the project recorded that the client runs its own CI (Delivery: others)."""
    answer = load_decisions(repo).get(CI_OWNER, {}).get("answer", "")
    return isinstance(answer, str) and answer.lower().startswith(CLIENT_CI.lower())


def _check_ci_owner(repo: Path) -> str | None:
    if uses_client_ci(repo) or (repo / ".github" / "workflows" / "ci.yml").is_file():
        return None
    return f".github/workflows/ci.yml is missing. Restore it, or answer `{CLIENT_CI}: <where>`"


def catalogue(facts: Facts) -> list[Gap]:
    """The gaps of a project with these facts, in the order they are asked."""
    gaps = [
        Gap(
            "language.text", True,
            "Which language do user-facing text and generated content use, and which number and date formats?",
            "Agents write UI text, messages, and documents in it. Guessing means rework.",
            ("English", "Indonesian", "both (say which where)"), "English",
            "AGENTS.md Language: line", "AGENTS.md has a filled Language: line", (), _check_language,
        ),
    ]
    if facts.delivery == "others":
        gaps.append(Gap(
            CI_OWNER, True, "Does the project keep Pakem's CI, or does the client run their own?",
            "Pakem writes CI for every project. A client with its own pipeline opts out, and that is recorded.",
            ("pakem CI", f"{CLIENT_CI}: <where>"), "pakem CI", ".github/workflows/ci.yml, or this decision",
            "the Pakem ci.yml exists, or the decision names the client's CI", ("ci",), _check_ci_owner,
        ))
    if not facts.releases_here:
        return gaps
    database = "database" in facts.capabilities
    gaps += [
        Gap(
            "health.proof", True, "What must /health prove before a release counts as working?",
            "The engine restores the previous release when health fails.",
            ("process up", "process up and database answers", "custom"),
            "process up and database answers" if database else "process up",
            "deploy/release.toml [health] + docs/deployment.md",
            "release.toml health block validates and the endpoint exists in code", ("release",), _check_health,
        ),
        Gap(
            "settings.sources", True,
            "Which settings are placement (host facts), which are secrets, and which are committed defaults?",
            "Each runtime name has exactly one source. Secrets never enter files or images.",
            ("list them per source",), None, "deploy/settings.toml",
            "settings.toml names every runtime setting with one source", ("deploy-setup", "release"), _check_settings,
        ),
        Gap(
            "network.shared", False, "Does it join another project's network, and under which service names?",
            "Shared networks expose service names. Bare names can resolve to the other project.",
            ("none", "joins <network> as <alias>"), "none", "deploy/release.toml + AGENTS.md project rules",
            "compose and AGENTS.md name the network and aliases", ("deploy-setup",),
        ),
        Gap(
            "single-instance", False, "Must only one instance run at a time (schedulers, bots, locks)?",
            "Blue-green switching runs two instances side by side for a moment.",
            ("no constraint", "single instance: stop-start only"), "no constraint", "AGENTS.md project rules",
            "AGENTS.md states the constraint and release.toml switch matches it", ("release",),
        ),
    ]
    if database:
        gaps += [
            Gap(
                "backup.restore", True, "How is the database backed up before migrations, and how is it restored?",
                "Production with migrations requires a backup. A schema change is undone by a restore.",
                ("pg_dump in the backup hook", "custom"), "pg_dump in the backup hook",
                "deploy/hooks/backup + docs/deployment.md", "deploy/hooks/backup is the project's own and executable",
                ("release",), _check_backup,
            ),
            Gap(
                "migrations.rule", True, "Which migration tool and rule (expand, then contract) does it follow?",
                "Every release must work against the schema the release before it left.",
                ("hand-written SQL, expand then contract", "custom"), "hand-written SQL, expand then contract",
                "AGENTS.md project rules", "AGENTS.md states the tool and the rule", ("release",), _check_migrations,
            ),
            Gap(
                "data.moves", False, "Does any data need to move or be imported before the first release?",
                "Moves are one-off, reviewed operations, never part of a normal migration.",
                ("none", "describe the move"), "none", "docs/deployment.md", "the move is planned or none", ("release",),
            ),
        ]
    if "llm" in facts.capabilities:
        gaps.append(
            Gap(
                "gateway.profile", True, "Which AI gateway and gateway profile does it use?",
                "Model calls go through a gateway that holds the provider keys. Spending approval comes from "
                "the project's profile or the project.",
                ("<gateway> profile <name>",), None, "AGENTS.md project rules",
                "AGENTS.md names the gateway", ("release",), _check_gateway,
            )
        )
    return gaps


# -- decisions -------------------------------------------------------------------------------------


def decisions_path(repo: Path) -> Path:
    return repo / PAKEM_DIR / DECISIONS_FILE


def load_decisions(repo: Path) -> dict[str, dict]:
    path = decisions_path(repo)
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise PakemError(f"invalid TOML in {display_path(path)}: {exc}") from exc
    decisions = data.get("decisions", {})
    if not isinstance(decisions, dict):
        raise PakemError(f"{display_path(path)}: [decisions] must hold one table per gap")
    return decisions


DECISION_KEYS = ("state", "answer", "reason", "assumption", "revisit", "waiting_for", "provider", "ask_again",
                 "question", "blocks", "decided")


def decisions_text(decisions: dict[str, dict]) -> str:
    lines = [
        "# Decisions on this project's gaps (ADR 0011). Written by `pakem decide`; committed so every",
        "# agent on every device sees them. States: answered, provisional, pending.",
    ]
    for gap_id in sorted(decisions):
        lines += ["", f"[decisions.{toml_string(gap_id)}]"]
        entry = decisions[gap_id]
        for key in DECISION_KEYS:
            if key in entry and entry[key] not in (None, "", []):
                lines.append(f"{key} = {toml_value(entry[key])}")
    return "\n".join(lines) + "\n"


@dataclass
class GapStatus:
    gap: Gap
    state: str
    decision: dict
    problem: str | None = None
    due: bool = False

    @property
    def settled(self) -> bool:
        return self.state in STATES and self.problem is None

    def as_dict(self) -> dict:
        return {**self.gap.describe(), "state": self.state, "decision": self.decision, "problem": self.problem,
                "due": self.due}


def custom_gap(gap_id: str, entry: dict) -> Gap:
    return Gap(gap_id, True, entry.get("question", gap_id), "recorded by an agent as a blocker no gap covered",
               (), None, "the decision itself", "the decision is recorded", tuple(entry.get("blocks", [])))


def evaluate(repo: Path, facts: Facts, today: date | None = None) -> list[GapStatus]:
    today = today or date.today()
    decisions = load_decisions(repo)
    gaps = catalogue(facts)
    known = {gap.id for gap in gaps}
    gaps += [custom_gap(gap_id, entry) for gap_id, entry in decisions.items() if gap_id not in known]
    statuses = []
    for gap in gaps:
        entry = decisions.get(gap.id, {})
        state = entry.get("state", OPEN)
        status = GapStatus(gap, state if state in STATES else OPEN, entry)
        if state == ANSWERED and gap.check is not None:
            status.problem = gap.check(repo)
        if state == PENDING:
            status.due = _due(entry.get("ask_again"), today)
        statuses.append(status)
    return statuses


def _due(stamp: object, today: date) -> bool:
    try:
        return date.fromisoformat(str(stamp)) <= today
    except ValueError:
        return True


def blocking(statuses: list[GapStatus], stage: str) -> list[GapStatus]:
    """Required gaps not settled (or pending) that block a stage."""
    return [s for s in statuses if s.gap.required and stage in s.gap.blocks and (not s.settled or s.state == PENDING)]


# -- AGENTS.md "Open decisions" ---------------------------------------------------------------------


def open_decisions_lines(statuses: list[GapStatus]) -> list[str]:
    lines = ["## Open decisions", "", "Generated by `pakem decide`; do not edit by hand.", ""]
    shown = [s for s in statuses if s.state in (OPEN, PROVISIONAL, PENDING) and (s.gap.required or s.state != OPEN)]
    for status in shown:
        entry = status.decision
        if status.state == PROVISIONAL:
            detail = f"provisional: {entry.get('assumption', '')}; revisit when {entry.get('revisit', '')}"
        elif status.state == PENDING:
            detail = (f"pending: {entry.get('waiting_for', '')} from {entry.get('provider', '')}; "
                      f"ask again {entry.get('ask_again', '')}")
        else:
            detail = "open"
        lines.append(f"- `{status.gap.id}`: {detail}")
    if not shown:
        lines.append("- none")
    return lines


def decisions_span(text: str, path: Path) -> tuple[int, int] | None:
    """Where the generated block is: one start and one end marker, each on its own line, in
    that order; None when there is none. Anything else is refused, since replacing between
    the wrong markers would delete the owner's text."""
    starts = [match.start() for match in re.finditer(rf"^{re.escape(DECISIONS_START)}[ \t]*$", text, re.MULTILINE)]
    ends = [match.start() for match in re.finditer(rf"^{re.escape(DECISIONS_END)}[ \t]*$", text, re.MULTILINE)]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise PakemError(
            f"{display_path(path)} must hold the Open decisions markers once each, start before end, on their own "
            f"lines (found {len(starts)} start and {len(ends)} end). Fix it by hand, nothing changed"
        )
    return starts[0], ends[0] + len(DECISIONS_END)


def require_decision_markers(repo: Path) -> None:
    """Refuse before anything is written when AGENTS.md's generated block is malformed."""
    path = repo / "AGENTS.md"
    if path.is_file():
        decisions_span(normalize_newlines(read_text(path)), path)


def write_open_decisions(repo: Path, statuses: list[GapStatus]) -> bool:
    """Replace the generated block in AGENTS.md (or append it); True when it changed."""
    path = repo / "AGENTS.md"
    if not path.is_file():
        return False
    text = normalize_newlines(read_text(path))
    # Blank lines around the section keep Markdown formatters (Prettier) from changing it.
    block = "\n".join([DECISIONS_START, "", *open_decisions_lines(statuses), "", DECISIONS_END])
    span = decisions_span(text, path)
    if span is None:
        updated = text.rstrip("\n") + "\n\n" + block + "\n"
    else:
        updated = text[:span[0]] + block + text[span[1]:]
    if updated == text:
        return False
    write_text_atomically(path, updated)
    return True


# -- commands ------------------------------------------------------------------------------------------


def record_decision(repo: Path, gap_id: str, entry: dict) -> None:
    decisions = load_decisions(repo)
    decisions[gap_id] = {"decided": date.today().isoformat(), **entry}
    write_text_atomically(decisions_path(repo), decisions_text(decisions))


def require_facts(repo: Path) -> Facts:
    facts = load_facts(repo)
    if facts is None:
        raise CommandRefused(
            f"{display_path(repo)} has no {PAKEM_DIR}/{FACTS_FILE}; run `pakem project init` or `pakem project adopt` first"
        )
    return facts


def run_decide(args) -> int:
    repo = Path(args.repo).resolve()
    facts = require_facts(repo)
    gap_id = args.gap
    if not GAP_ID.match(gap_id):
        raise PakemError(f"invalid gap id {gap_id!r}: lowercase words joined by dots, such as health.proof")
    known = {gap.id for gap in catalogue(facts)}
    decisions = load_decisions(repo)
    if gap_id not in known and gap_id not in decisions and not args.question:
        raise CommandRefused(
            f"{gap_id} is not one of this project's gaps ({', '.join(sorted(known))}). "
            "Record a new blocker with --question and --blocks"
        )
    require_decision_markers(repo)
    modes = [bool(args.answer), bool(args.provisional), bool(args.pending)]
    if sum(modes) != 1:
        raise PakemError("give exactly one of: an answer, --provisional ASSUMPTION, or --pending WHAT")
    entry: dict = {"decided": date.today().isoformat()}
    if args.question:
        entry["question"] = args.question
        entry["blocks"] = [stage for stage in (args.blocks or "").split(",") if stage]
        unknown = set(entry["blocks"]) - set(STAGES)
        if unknown:
            raise PakemError(f"--blocks takes {', '.join(STAGES)}; unknown: {', '.join(sorted(unknown))}")
    elif gap_id in decisions:
        entry.update({key: decisions[gap_id][key] for key in ("question", "blocks") if key in decisions[gap_id]})
    if args.answer:
        entry.update(state=ANSWERED, answer=args.answer, reason=args.reason or "")
    elif args.provisional:
        if not args.revisit:
            raise PakemError("a provisional decision needs --revisit TRIGGER")
        entry.update(state=PROVISIONAL, assumption=args.provisional, revisit=args.revisit, reason=args.reason or "")
    else:
        if not (args.provider and args.ask_again):
            raise PakemError("a pending decision needs --from WHO and --ask-again YYYY-MM-DD")
        try:
            date.fromisoformat(args.ask_again)
        except ValueError as exc:
            raise PakemError("--ask-again must be a date, YYYY-MM-DD") from exc
        entry.update(state=PENDING, waiting_for=args.pending, provider=args.provider, ask_again=args.ask_again,
                     reason=args.reason or "")
    decisions[gap_id] = entry
    write_text_atomically(decisions_path(repo), decisions_text(decisions))
    statuses = evaluate(repo, facts)
    changed = write_open_decisions(repo, statuses)
    print(f"Recorded {gap_id}: {entry['state']} in {display_path(decisions_path(repo))}")
    if changed:
        print("Updated the Open decisions list in AGENTS.md.")
    current = next(status for status in statuses if status.gap.id == gap_id)
    if current.problem:
        print(f"Not proven yet: {current.problem} (target: {current.gap.target})")
    revisit = [s for s in statuses if s.state == PROVISIONAL]
    for status in revisit:
        print(f"Provisional, revisit when {status.decision.get('revisit')}: {status.gap.id}")
    return EXIT_DONE


def gaps_report(repo: Path, as_json: bool) -> int:
    facts = require_facts(repo)
    statuses = evaluate(repo, facts)
    unsettled = [s for s in statuses if s.gap.required and not s.settled]
    if as_json:
        print(json.dumps({
            "status": "gaps" if unsettled else "done",
            "gaps": [status.as_dict() for status in statuses],
        }, indent=2))
    else:
        print(f"pakem project check --gaps {display_path(repo)}")
        for status in statuses:
            marker = "ok  " if status.settled else ("FAIL" if status.gap.required else "opt ")
            detail = status.state
            if status.problem:
                detail += f", not proven: {status.problem}"
            if status.due:
                detail += ", due: ask again"
            blocks = f" (blocks {', '.join(status.gap.blocks)})" if status.gap.blocks else ""
            print(f"  {marker}  {status.gap.id}: {detail}{blocks}")
        if unsettled:
            print(f"{len(unsettled)} required gap(s) open. Answer them with `pakem decide` (gap protocol).")
    return EXIT_GAPS if unsettled else EXIT_DONE


def project_decisions(repo: Path) -> dict | None:
    """For `pakem status`: open and due decisions, or None for a project without facts."""
    facts = load_facts(repo)
    if facts is None:
        return None
    statuses = evaluate(repo, facts)
    return {
        "open": [s.gap.id for s in statuses if s.gap.required and not s.settled and s.state != PENDING],
        "pending": [s.gap.id for s in statuses if s.state == PENDING],
        "due": [s.gap.id for s in statuses if s.due],
        "provisional": [s.gap.id for s in statuses if s.state == PROVISIONAL],
    }
