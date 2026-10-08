#!/usr/bin/env python3
"""PreToolUse guard for Claude Code and Antigravity (standard library only).

Usage: guard.py --host claude|antigravity   (hook JSON on stdin)

Claude Code: deny/ask is reported as hookSpecificOutput JSON on stdout with
exit 0; when there is no objection nothing is printed so the normal permission
flow applies (https://code.claude.com/docs/en/hooks).

Antigravity: deny/ask is reported as {"decision", "reason"}. When there is no
objection nothing is printed, because {"decision": "allow"} would auto-approve
the tool call and bypass the user's normal permission prompts.

The guard never prints file contents; it only inspects command text and paths.

Secret reads: a path is secret when it matches the patterns in is_secret_path
(.env and .env.* except .env.example/.sample/.template, SSH private keys and the
~/.ssh directory, credential files, key and certificate files, secrets/*secret*).
Within a shell command the guard denies:
- reader commands (cat, grep, cp, source, ...) and `<` redirections on a secret path;
- `git show REV:PATH`, `git show REV -- PATH`, and `git cat-file ... REV:PATH` on a
  secret path;
- interpreter one-liners (python -c, node -e/--eval/-p, ruby -e, perl -e/-E, php -r,
  deno eval, bun -e/--eval/-p) whose code contains a string literal naming a secret
  path, or whose remaining arguments are secret paths;
- simple variable indirection in one command line: an assignment of a secret path
  (`VAR=...`, `export VAR=...`, `declare`/`local`/`readonly`/`typeset`) is expanded in
  later `$VAR`/`${VAR}` uses of the same command line before they are checked.
This is a guardrail against accidents, not a sandbox: scripts on disk, string
concatenation, or encoded paths are not detected.

Protected branches: for a push, the guard runs
`git config --get-all agentframework.protectedBranch` in the directory the push
runs in (the hook's cwd, adjusted for `cd` and `git -C`). When the key is unset,
or git cannot answer (not a repository, git missing, timeout), the protected set
is main and master. Pushes to a protected branch are denied, force pushes
elsewhere ask, and a push whose target cannot be determined asks.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

DENY = "deny"
ASK = "ask"
SEVERITY = {None: 0, ASK: 1, DENY: 2}
MAX_NESTING = 5

DEFAULT_PROTECTED_BRANCHES = frozenset({"main", "master"})
PROTECTED_BRANCH_CONFIG_KEY = "agentframework.protectedBranch"
SEPARATORS = {"&&", "||", ";", "|", "&", "|&", ";;", "(", ")", ";&", ";;&"}
REDIRECTIONS = {">", ">>", ">&", "&>", "&>>", ">|", "<", "<<", "<<<", "<&", "<>", "<<-"}
READ_COMMANDS = {
    "cat",
    "less",
    "more",
    "head",
    "tail",
    "bat",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "awk",
    "sed",
    "source",
    ".",
    "cp",
    "scp",
    "base64",
    "xxd",
    "strings",
    "open",
    "tac",
    "nl",
    "od",
    "hexdump",
    "jq",
    "yq",
}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
WRAPPERS = {"sudo", "command", "exec", "nohup", "time", "nice", "builtin", "xargs"}
GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
PUSH_OPTIONS_WITH_VALUE = {"--repo", "-o", "--push-option", "--receive-pack", "--exec"}
COMMIT_OPTIONS_WITH_VALUE = {
    "--file",
    "--template",
    "--author",
    "--date",
    "--reuse-message",
    "--reedit-message",
    "--fixup",
    "--squash",
    "--cleanup",
    "--pathspec-from-file",
    "--untracked-files",
}
COMMIT_SHORT_WITH_VALUE = set("mFcCt")

ENV_EXAMPLE_NAMES = {".env.example", ".env.sample", ".env.template"}
SECRET_BASENAMES = {
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "auth.json",
    "oauth_creds.json",
    "credentials",
    "credentials.json",
    "db_prod.txt",
    "runtime.env",
}
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".runtime.env")
SSH_NON_SECRET_NAMES = {"known_hosts", "known_hosts.old", "config", "authorized_keys"}
REMOTE_PREFIX = re.compile(r"^[\w.@-]+:(?=[~/.])")
ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
ASSIGNMENT_BUILTINS = {"export", "declare", "typeset", "local", "readonly"}
VARIABLE_REFERENCE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
GLOB_CHARS = set("*?[")
TRAILER_PATTERN = re.compile(r"(?i)co-authored-by|generated with")
HEREDOC_SUBSTITUTION = re.compile(
    r"^\$\(\s*cat\s+<<-?\s*(['\"]?)(\w+)\1[ \t]*\n(.*?)\n[ \t]*\2[ \t]*\n?\s*\)\s*$", re.DOTALL
)

REASON_PUSH_PROTECTED = "direct push to protected branch {branch} is blocked; push a topic branch"
REASON_PUSH_ALL = "pushing every branch includes the protected branches ({branches}); push a topic branch"
REASON_FORCE = "force push rewrites remote history; confirm with the owner first"
REASON_UNKNOWN_BRANCH = "could not determine which branch this push targets; confirm it is not a protected branch"
REASON_NO_VERIFY = "--no-verify bypasses the repository hooks; fix the reported problem instead"
REASON_SUBJECT_ONLY = "subject-only commits: one -m with a single-line conventional subject, no body or trailers (including Co-Authored-By)"
SECRET_HINT = "use skills/sensitive-operations/scripts/env_keys.py to list variable names"
REASON_SECRET_READ = f"reading secret files is blocked; {SECRET_HINT}"
REASON_ENV_DUMP = "environment dumps can expose secrets; inspect only the variable names you need"

# Last-resort patterns, used only when the structured analysis itself fails. The
# working directory is unknown then, so per-repository protected branches cannot be
# read and only the default main/master are matched.
FALLBACK_DENY_PATTERNS = (
    re.compile(r"git\s+push\b[^\n]*\b(main|master)\b"),
    re.compile(r"--no-verify"),
    re.compile(r"\bprintenv\b"),
    re.compile(r"(?<![\w.-])\.env(\.(?!example\b|sample\b|template\b)[\w.-]+)?(?![\w.-])"),
    re.compile(r"\bid_(rsa|ed25519|ecdsa)\b(?!\.pub)"),
)


class Decision:
    def __init__(self, verdict: str | None = None, reason: str = "") -> None:
        self.verdict = verdict
        self.reason = reason

    def stronger(self, other: "Decision") -> "Decision":
        if SEVERITY[other.verdict] > SEVERITY[self.verdict]:
            return other
        return self


NO_OBJECTION = Decision()


class Context:
    def __init__(self, cwd: str | None, depth: int = 0, variables: dict[str, str] | None = None) -> None:
        self.cwd = cwd or os.getcwd()
        self.depth = depth
        # Shell variables assigned a secret path earlier in the same command line.
        self.secret_variables: dict[str, str] = dict(variables or {})

    def nested(self) -> "Context":
        return Context(self.cwd, self.depth + 1, self.secret_variables)


def expand_path(text: str, cwd: str | None) -> Path:
    home = str(Path.home())
    if text == "~" or text.startswith("~/"):
        text = home + text[1:]
    elif text.startswith("$HOME/") or text == "$HOME":
        text = home + text[len("$HOME") :]
    elif text.startswith("${HOME}"):
        text = home + text[len("${HOME}") :]
    path = Path(text)
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    return Path(os.path.normpath(path))


def is_secret_path(text: str, cwd: str | None = None) -> bool:
    if not text or "\n" in text or len(text) > 4096:
        return False
    text = REMOTE_PREFIX.sub("", text)
    path = expand_path(text, cwd)
    parts = path.parts
    name = path.name

    if ".ssh" in parts:
        ssh_index = len(parts) - 1 - parts[::-1].index(".ssh")
        if ssh_index == len(parts) - 1:
            return True
        return not (name.endswith(".pub") or name in SSH_NON_SECRET_NAMES)
    if name == ".env":
        return True
    if name.startswith(".env.") and not GLOB_CHARS.intersection(name):
        return name not in ENV_EXAMPLE_NAMES
    if name.startswith(".env") and GLOB_CHARS.intersection(name):
        return True
    if name in SECRET_BASENAMES or name.endswith(SECRET_SUFFIXES):
        return True
    return "secret" in name.lower() and "secrets" in parts[:-1]


def neutralize_command(command: str) -> str:
    """Turn unquoted newlines and backticks into ';' and drop heredoc bodies (they are data)."""
    output: list[str] = []
    pending_delimiters: list[tuple[str, bool]] = []
    in_single = in_double = escaped = False
    index = 0
    length = len(command)
    while index < length:
        char = command[index]
        if escaped:
            output.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\" and not in_single:
            output.append(char)
            escaped = True
            index += 1
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if command.startswith("<<", index) and not command.startswith("<<<", index):
                index = _read_heredoc_delimiter(command, index + 2, pending_delimiters)
                output.append(" ")
                continue
            if char == "\n":
                output.append(" ; ")
                index = _skip_heredoc_bodies(command, index + 1, pending_delimiters)
                pending_delimiters = []
                continue
            if char == "`":
                output.append(" ; ")
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output)


def _read_heredoc_delimiter(command: str, index: int, pending: list[tuple[str, bool]]) -> int:
    strip_tabs = command.startswith("-", index)
    if strip_tabs:
        index += 1
    while index < len(command) and command[index] in " \t":
        index += 1
    delimiter_chars: list[str] = []
    quote = None
    while index < len(command):
        char = command[index]
        if quote:
            if char == quote:
                quote = None
            else:
                delimiter_chars.append(char)
        elif char in "'\"":
            quote = char
        elif char.isspace() or char in ";|&<>()":
            break
        else:
            delimiter_chars.append(char)
        index += 1
    if delimiter_chars:
        pending.append(("".join(delimiter_chars), strip_tabs))
    return index


def _skip_heredoc_bodies(command: str, index: int, pending: list[tuple[str, bool]]) -> int:
    for delimiter, strip_tabs in pending:
        while index < len(command):
            line_end = command.find("\n", index)
            if line_end == -1:
                line_end = len(command)
            line = command[index:line_end]
            index = line_end + 1
            if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                break
    return index


def tokenize(command: str) -> list[str]:
    lexer = shlex.shlex(neutralize_command(command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def split_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def substitutions_in(token: str) -> list[str]:
    """Inner commands of $(...) and `...` found inside a single (quoted) token."""
    inner: list[str] = []
    start = token.find("$(")
    while start != -1:
        depth = 0
        for position in range(start + 1, len(token)):
            if token[position] == "(":
                depth += 1
            elif token[position] == ")":
                depth -= 1
                if depth == 0:
                    inner.append(token[start + 2 : position])
                    break
        start = token.find("$(", start + 2)
    backtick_parts = token.split("`")
    inner.extend(backtick_parts[1::2])
    return [text for text in inner if not re.match(r"^\s*cat\s+<<", text)]


def expand_secret_variables(tokens: list[str], variables: dict[str, str]) -> list[str]:
    if not variables:
        return tokens

    def replace(match: re.Match) -> str:
        name = match.group(1) or match.group(2)
        return variables.get(name, match.group(0))

    return [VARIABLE_REFERENCE.sub(replace, token) for token in tokens]


def record_secret_assignments(tokens: list[str], context: Context) -> None:
    """Remember `VAR=<secret path>` from a bare assignment or an export/declare-style builtin."""
    if not tokens:
        return
    if tokens[0] in ASSIGNMENT_BUILTINS:
        candidates = [token for token in tokens[1:] if not token.startswith("-")]
    elif all(ASSIGNMENT.match(token) for token in tokens):
        candidates = tokens
    else:
        # `VAR=value command` sets VAR only in that command's environment.
        return
    for token in candidates:
        match = ASSIGNMENT.match(token)
        if match is None:
            continue
        name, value = match.group(1), token[match.end() :]
        if is_secret_path(value, context.cwd):
            context.secret_variables[name] = value
        else:
            context.secret_variables.pop(name, None)


def analyze_command(command: str, context: Context) -> Decision:
    decision = NO_OBJECTION
    if context.depth > MAX_NESTING:
        return decision
    for raw_segment in split_segments(tokenize(command)):
        segment = expand_secret_variables(raw_segment, context.secret_variables)
        for token in segment:
            if "$(" in token or "`" in token:
                for inner in substitutions_in(token):
                    decision = decision.stronger(analyze_command(inner, context.nested()))
        decision = decision.stronger(analyze_segment(segment, context))
        record_secret_assignments(segment, context)
    return decision


def strip_redirections(tokens: list[str]) -> tuple[list[str], list[str]]:
    arguments: list[str] = []
    input_files: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        next_is_redirection = index + 1 < len(tokens) and tokens[index + 1] in REDIRECTIONS
        if token.isdigit() and next_is_redirection:
            index += 1
            continue
        if token in REDIRECTIONS:
            if index + 1 < len(tokens):
                if token in ("<", "<>"):
                    input_files.append(tokens[index + 1])
                index += 2
            else:
                index += 1
            continue
        arguments.append(token)
        index += 1
    return arguments, input_files


def strip_assignments(arguments: list[str]) -> list[str]:
    index = 0
    while index < len(arguments) and ASSIGNMENT.match(arguments[index]):
        index += 1
    return arguments[index:]


def analyze_segment(tokens: list[str], context: Context) -> Decision:
    arguments, input_files = strip_redirections(tokens)
    for input_file in input_files:
        if is_secret_path(input_file, context.cwd):
            return Decision(DENY, REASON_SECRET_READ)
    arguments = strip_assignments(arguments)
    if not arguments:
        return NO_OBJECTION
    program = os.path.basename(arguments[0])
    rest = arguments[1:]

    if program == "cd":
        if rest:
            context.cwd = str(expand_path(rest[0], context.cwd))
        return NO_OBJECTION
    if program == "env":
        return analyze_env(rest, context)
    if program in WRAPPERS:
        # Wrapper options vary (sudo -u USER, nice -n 5), so analyze every suffix that
        # starts at a non-option word and keep the strongest objection.
        decision = NO_OBJECTION
        for position, argument in enumerate(rest):
            if not argument.startswith("-"):
                decision = decision.stronger(analyze_segment(rest[position:], context))
        return decision
    if program in SHELLS and "-c" in rest:
        position = rest.index("-c")
        if position + 1 < len(rest):
            return analyze_command(rest[position + 1], context.nested())
        return NO_OBJECTION
    if program == "git":
        return analyze_git(rest, context)
    if program in READ_COMMANDS:
        return analyze_read_command(rest, context)
    interpreter = interpreter_spec(program)
    if interpreter is not None:
        return analyze_interpreter(interpreter, rest, context)
    if program == "deno" and rest[:1] == ["eval"]:
        return analyze_interpreter(DENO_EVAL, rest[1:], context)
    return analyze_environment_listing(program, rest)


def analyze_env(rest: list[str], context: Context) -> Decision:
    index = 0
    while index < len(rest):
        argument = rest[index]
        if argument in ("-u", "--unset", "-C", "--chdir", "-S", "--split-string"):
            index += 2
            continue
        if argument.startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argument):
            index += 1
            continue
        break
    remaining = rest[index:]
    if not remaining:
        return Decision(DENY, REASON_ENV_DUMP)
    return analyze_segment(remaining, context)


def analyze_environment_listing(program: str, rest: list[str]) -> Decision:
    if program == "printenv":
        return Decision(DENY, REASON_ENV_DUMP)
    if program == "set" and not rest:
        return Decision(DENY, REASON_ENV_DUMP)
    if program == "export" and (not rest or rest == ["-p"]):
        return Decision(DENY, REASON_ENV_DUMP)
    if program in ("declare", "typeset"):
        if not rest:
            return Decision(DENY, REASON_ENV_DUMP)
        only_flags = len(rest) == 1 and rest[0].startswith("-")
        if only_flags and ("x" in rest[0] or "p" in rest[0]):
            return Decision(DENY, REASON_ENV_DUMP)
    return NO_OBJECTION


def analyze_read_command(rest: list[str], context: Context) -> Decision:
    for argument in rest:
        candidate = argument
        if argument.startswith("-"):
            if "=" not in argument:
                continue
            candidate = argument.split("=", 1)[1]
        if is_secret_path(candidate, context.cwd):
            return Decision(DENY, REASON_SECRET_READ)
    return NO_OBJECTION


def analyze_git(arguments: list[str], context: Context) -> Decision:
    git_cwd = context.cwd
    index = 0
    while index < len(arguments) and arguments[index].startswith("-"):
        option = arguments[index]
        if option == "-C" and index + 1 < len(arguments):
            git_cwd = str(expand_path(arguments[index + 1], git_cwd))
            index += 2
            continue
        index += 2 if option in GIT_OPTIONS_WITH_VALUE else 1
    if index >= len(arguments):
        return NO_OBJECTION
    subcommand = arguments[index]
    subcommand_arguments = arguments[index + 1 :]
    if subcommand == "push":
        return analyze_push(subcommand_arguments, git_cwd)
    if subcommand == "commit":
        return analyze_commit(subcommand_arguments)
    if subcommand in ("show", "cat-file"):
        return analyze_git_object_read(subcommand_arguments, git_cwd)
    return NO_OBJECTION


def analyze_git_object_read(arguments: list[str], cwd: str) -> Decision:
    """`git show REV:PATH`, `git show REV -- PATH`, `git cat-file -p REV:PATH` print file contents."""
    after_double_dash = False
    for argument in arguments:
        if after_double_dash:
            candidate = argument
        elif argument == "--":
            after_double_dash = True
            continue
        elif argument.startswith("-") or ":" not in argument:
            continue
        else:
            # REV:PATH; an empty REV (":PATH") names the index.
            candidate = argument.split(":", 1)[1]
        if is_secret_path(candidate, cwd):
            return Decision(DENY, REASON_SECRET_READ)
    return NO_OBJECTION


class InterpreterSpec:
    """How to find the inline code of an interpreter one-liner."""

    def __init__(
        self,
        code_options: set[str],
        cluster_letters: str = "",
        value_options: set[str] | None = None,
        code_is_first_argument: bool = False,
    ) -> None:
        self.code_options = code_options
        self.cluster_letters = cluster_letters
        self.value_options = value_options or set()
        self.code_is_first_argument = code_is_first_argument


PYTHON_SPEC = InterpreterSpec({"-c"}, cluster_letters="c", value_options={"-W", "-X"})
NODE_SPEC = InterpreterSpec(
    {"-e", "--eval", "-p", "--print"},
    value_options={"-r", "--require", "--import", "--loader", "--experimental-loader", "-C", "--conditions"},
)
INTERPRETERS = {
    "node": NODE_SPEC,
    "nodejs": NODE_SPEC,
    "bun": InterpreterSpec({"-e", "--eval", "-p", "--print"}, value_options={"-r", "--preload"}),
    "ruby": InterpreterSpec({"-e"}, cluster_letters="e", value_options={"-I", "-r", "-C"}),
    "perl": InterpreterSpec({"-e", "-E"}, cluster_letters="eE", value_options={"-I", "-M", "-m"}),
    "php": InterpreterSpec({"-r"}, value_options={"-c", "-d", "-z"}),
}
DENO_EVAL = InterpreterSpec(set(), code_is_first_argument=True)
PYTHON_PROGRAM = re.compile(r"^python(\d+(\.\d+)*)?$")
STRING_LITERAL = re.compile(r"""(['"`])((?:\\.|(?!\1).)*?)\1""", re.DOTALL)
LITERAL_WORD_SEPARATORS = re.compile(r"[\s,;=()<>|&]+")


def interpreter_spec(program: str) -> InterpreterSpec | None:
    if PYTHON_PROGRAM.match(program):
        return PYTHON_SPEC
    return INTERPRETERS.get(program)


def inline_code(spec: InterpreterSpec, arguments: list[str]) -> tuple[str, list[str]] | None:
    """The one-liner code and the arguments after it, or None when no inline code is given."""
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        following = arguments[index + 1 :]
        if argument in spec.code_options:
            return (following[0], following[1:]) if following else None
        for option in spec.code_options:
            if option.startswith("--") and argument.startswith(option + "="):
                return argument.split("=", 1)[1], following
        is_cluster = (
            spec.cluster_letters
            and re.fullmatch(r"-[A-Za-z]+", argument)
            and argument[-1] in spec.cluster_letters
        )
        if is_cluster:
            return (following[0], following[1:]) if following else None
        if argument in spec.value_options:
            index += 2
            continue
        if argument.startswith("-"):
            index += 1
            continue
        if spec.code_is_first_argument:
            return argument, following
        # A script path: not a one-liner.
        return None
    return None


def code_mentions_secret_path(code: str, cwd: str) -> bool:
    """True when a string literal in the code names a secret path (identifiers are ignored)."""
    for match in STRING_LITERAL.finditer(code):
        literal = match.group(2)
        words = [literal, *LITERAL_WORD_SEPARATORS.split(literal)]
        if any(is_secret_path(word, cwd) for word in words if word):
            return True
    return False


def analyze_interpreter(spec: InterpreterSpec, arguments: list[str], context: Context) -> Decision:
    found = inline_code(spec, arguments)
    if found is None:
        return NO_OBJECTION
    code, remaining = found
    if code_mentions_secret_path(code, context.cwd):
        return Decision(DENY, REASON_SECRET_READ)
    for argument in remaining:
        if is_secret_path(argument, context.cwd):
            return Decision(DENY, REASON_SECRET_READ)
    return NO_OBJECTION


def current_branch(cwd: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", cwd, "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def protected_branches(cwd: str) -> frozenset[str]:
    """Branches protected in the repository at cwd; main/master when unset or unreadable."""
    try:
        completed = subprocess.run(
            ["git", "-C", cwd, "config", "--get-all", PROTECTED_BRANCH_CONFIG_KEY],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return DEFAULT_PROTECTED_BRANCHES
    if completed.returncode != 0:
        return DEFAULT_PROTECTED_BRANCHES
    configured = set()
    for line in completed.stdout.splitlines():
        branch = line.strip().removeprefix("refs/heads/")
        if branch:
            configured.add(branch)
    if not configured:
        return DEFAULT_PROTECTED_BRANCHES
    return frozenset(configured)


def refspec_destination(refspec: str, cwd: str) -> str | None:
    spec = refspec.lstrip("+")
    destination = spec.rsplit(":", 1)[-1] if ":" in spec else spec
    if destination == "HEAD":
        return current_branch(cwd)
    return destination.removeprefix("refs/heads/")


def analyze_push(arguments: list[str], cwd: str) -> Decision:
    decision = NO_OBJECTION
    positional: list[str] = []
    force = False
    pushes_all_branches = False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            positional.extend(arguments[index + 1 :])
            break
        if argument == "--no-verify":
            decision = decision.stronger(Decision(DENY, REASON_NO_VERIFY))
        elif argument in ("--force", "--force-with-lease") or argument.startswith("--force-with-lease="):
            force = True
        elif argument in ("--all", "--mirror", "--branches"):
            pushes_all_branches = True
        elif argument in PUSH_OPTIONS_WITH_VALUE:
            index += 1
        elif argument.startswith("--"):
            pass
        elif argument.startswith("-") and len(argument) > 1:
            for position, flag in enumerate(argument[1:]):
                if flag == "f":
                    force = True
                if flag == "o":
                    # -o takes a value: the rest of the cluster, or the next argument.
                    if position == len(argument) - 2:
                        index += 1
                    break
        else:
            positional.append(argument)
        index += 1

    protected = protected_branches(cwd)
    if pushes_all_branches:
        listed = ", ".join(sorted(protected))
        return Decision(DENY, REASON_PUSH_ALL.format(branches=listed))

    refspecs = positional[1:]
    protected_target = None
    unknown_target = False
    for refspec in refspecs:
        if refspec.startswith("+"):
            force = True
        destination = refspec_destination(refspec, cwd)
        if destination is None:
            unknown_target = True
        elif destination in protected:
            protected_target = destination
    if not refspecs:
        branch = current_branch(cwd)
        if branch is None:
            unknown_target = True
        elif branch in protected:
            protected_target = branch

    if protected_target is not None:
        return Decision(DENY, REASON_PUSH_PROTECTED.format(branch=protected_target))
    if force:
        decision = decision.stronger(Decision(ASK, REASON_FORCE))
    if unknown_target:
        decision = decision.stronger(Decision(ASK, REASON_UNKNOWN_BRANCH))
    return decision


def unwrap_heredoc_message(message: str) -> str:
    match = HEREDOC_SUBSTITUTION.match(message)
    if match is None:
        return message
    return match.group(3)


def analyze_commit(arguments: list[str]) -> Decision:
    messages: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            break
        if argument == "--no-verify":
            return Decision(DENY, REASON_NO_VERIFY)
        if argument == "--trailer" or argument.startswith("--trailer="):
            return Decision(DENY, REASON_SUBJECT_ONLY)
        if argument == "--message":
            messages.append(arguments[index + 1] if index + 1 < len(arguments) else "")
            index += 1
        elif argument.startswith("--message="):
            messages.append(argument.split("=", 1)[1])
        elif argument in COMMIT_OPTIONS_WITH_VALUE:
            index += 1
        elif argument.startswith("-") and not argument.startswith("--") and len(argument) > 1:
            cluster = argument[1:]
            for position, flag in enumerate(cluster):
                if flag == "n":
                    return Decision(DENY, REASON_NO_VERIFY)
                if flag in COMMIT_SHORT_WITH_VALUE:
                    value = cluster[position + 1 :]
                    if not value:
                        value = arguments[index + 1] if index + 1 < len(arguments) else ""
                        index += 1
                    if flag == "m":
                        messages.append(value)
                    break
        index += 1

    if len(messages) > 1:
        return Decision(DENY, REASON_SUBJECT_ONLY)
    for message in messages:
        text = unwrap_heredoc_message(message).strip("\n")
        if "\n" in text or TRAILER_PATTERN.search(text):
            return Decision(DENY, REASON_SUBJECT_ONLY)
    return NO_OBJECTION


def string_values(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in string_values(item)]
    if isinstance(value, list):
        return [text for item in value for text in string_values(item)]
    return []


CLAUDE_FILE_TOOLS = {"Read", "Grep", "Edit", "Write", "MultiEdit", "NotebookEdit"}
CLAUDE_PATH_KEYS = ("file_path", "path", "notebook_path")


def evaluate_claude(payload: dict) -> Decision:
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd")
    if tool_name == "Bash":
        return analyze_command(str(tool_input.get("command", "")), Context(cwd))
    if tool_name in CLAUDE_FILE_TOOLS:
        candidates = [tool_input.get(key) for key in CLAUDE_PATH_KEYS]
        candidates += list(tool_input.get("paths") or [])
        for candidate in candidates:
            if isinstance(candidate, str) and is_secret_path(candidate, cwd):
                return Decision(DENY, f"{tool_name} on a secret file is blocked; {SECRET_HINT}")
    return NO_OBJECTION


def evaluate_antigravity(payload: dict) -> Decision:
    tool_call = payload.get("toolCall") or {}
    name = tool_call.get("name", "")
    arguments = tool_call.get("args") or {}
    workspaces = payload.get("workspacePaths") or []
    cwd = workspaces[0] if workspaces else None
    if name == "run_command":
        command = arguments.get("CommandLine") or arguments.get("command") or ""
        return analyze_command(str(command), Context(arguments.get("Cwd") or cwd))
    for value in string_values(arguments):
        if is_secret_path(value, cwd):
            return Decision(DENY, f"{name} on a secret file is blocked; {SECRET_HINT}")
    return NO_OBJECTION


def fallback_decision(raw_input: str) -> Decision:
    for pattern in FALLBACK_DENY_PATTERNS:
        if pattern.search(raw_input):
            return Decision(DENY, "guard could not analyze this call and it matches a blocked pattern")
    return NO_OBJECTION


def emit(host: str, decision: Decision) -> None:
    if decision.verdict is None:
        return
    if host == "claude":
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision.verdict,
                "permissionDecisionReason": decision.reason,
            }
        }
    else:
        output = {"decision": decision.verdict, "reason": decision.reason}
    print(json.dumps(output))


def evaluate(host: str, payload: dict) -> Decision:
    if not isinstance(payload, dict):
        raise ValueError("hook input is not a JSON object")
    if host == "claude":
        return evaluate_claude(payload)
    return evaluate_antigravity(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", required=True, choices=("claude", "antigravity"))
    args = parser.parse_args(argv)
    raw_input = sys.stdin.read()
    try:
        decision = evaluate(args.host, json.loads(raw_input))
    except Exception as exc:  # noqa: BLE001
        # Fail closed only when the raw input clearly matches a deny pattern; otherwise
        # warn and allow, so a parser bug never blocks routine agent work. Normal host
        # permission checks still apply because nothing is printed on allow.
        decision = fallback_decision(raw_input)
        if decision.verdict is None:
            print(
                f"agent-framework guard: analysis failed ({type(exc).__name__}); not blocking",
                file=sys.stderr,
            )
    emit(args.host, decision)
    return 0


if __name__ == "__main__":
    sys.exit(main())
