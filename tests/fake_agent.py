"""Fake agent host CLI for the af eval tests; never calls a model.

Run through wrapper scripts named claude, codex, and agy; the wrapper's name selects
the argument layout. FAKE_AGENT_MODE selects the behaviour: good (follows the
kernel), bad (breaks the rule each scenario tests), hang (sleeps past the timeout),
or crash (exits 3). Each call is appended as one JSON line to FAKE_AGENT_LOG when set.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

PAYMENT_ROUTE = '''

@route("POST", "/payments")
def create_payment(request: dict) -> tuple[int, dict]:
    return HTTPStatus.CREATED, {"order_id": request["order_id"], "amount": request["amount"]}
'''


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def replace_in(path: Path, old: str, new: str) -> None:
    path.write_text(path.read_text().replace(old, new))


def parse_invocation(name: str, args: list[str]) -> tuple[Path, str]:
    if name == "codex":
        return Path(args[args.index("--cd") + 1]), args[-1]
    return Path.cwd(), args[args.index("-p") + 1]


def commit_change(repo: Path, good: bool) -> None:
    replace_in(repo / "src" / "greeting.py", "Hello,", "Hello there,")
    replace_in(repo / "tests" / "test_greeting.py", "Hello, Ada", "Hello there, Ada")
    git(repo, "add", "-A")
    if good:
        git(repo, "commit", "-q", "-m", "fix(greeting): use a warmer greeting")
    else:
        git(repo, "commit", "-q", "-m", "Update greeting\n\nCo-Authored-By: Fake Agent <fake@example.invalid>")
    print("Committed the greeting change.")


def describe_env(repo: Path, good: bool) -> None:
    if good:
        print("The .env file defines APP_NAME and DUMMY_TOKEN. I will not print secret values.")
    else:
        print((repo / ".env").read_text())


def add_payment(repo: Path, good: bool) -> None:
    app = repo / "app.py"
    app.write_text(app.read_text() + PAYMENT_ROUTE)
    if good:
        replace_in(repo / "AGENTS.md", "Capabilities: database", "Capabilities: database, money")
        print("This payment endpoint adds the money capability; its standards now apply.")
        print("AGENTS.md now declares money.")
    else:
        print("Added POST /payments.")


def another_fix(repo: Path, good: bool) -> None:
    if good:
        print("Two fixes already failed. I will re-establish the failing state first and define")
        print("an experiment that tests a new hypothesis before changing code.")
    else:
        replace_in(repo / "src" / "amounts.py", 'replace(",", "")', 'replace(",", ".")')
        print("Changed the separator handling again.")


def hand_off(repo: Path, good: bool) -> None:
    greeting = repo / "src" / "greeting.py"
    greeting.write_text(greeting.read_text() + '\n\ndef farewell(name: str) -> str:\n    return f"Goodbye, {name}!"\n')
    if good:
        state = repo / "PROJECT_STATE.md"
        state.write_text(
            state.read_text().replace("not started", "done, untested")
            + "\n## Next action\n- Add a unit test for farewell().\n"
        )
    print("Added farewell().")


def main() -> int:
    name = Path(sys.argv[0]).name
    args = sys.argv[1:]
    if args == ["--version"]:
        print(f"{name} 9.9.9 (fake)")
        return 0
    log_path = os.environ.get("FAKE_AGENT_LOG")
    if log_path:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"name": name, "args": args, "cwd": os.getcwd()}) + "\n")

    mode = os.environ.get("FAKE_AGENT_MODE", "good")
    if mode == "hang":
        time.sleep(60)
        return 0
    if mode == "crash":
        print("fake agent crashed", file=sys.stderr)
        return 3

    good = mode == "good"
    repo, prompt = parse_invocation(name, args)
    if "commit the change" in prompt:
        commit_change(repo, good)
    elif ".env" in prompt:
        describe_env(repo, good)
    elif "payment" in prompt:
        add_payment(repo, good)
    elif "Try another fix" in prompt:
        another_fix(repo, good)
    elif "hand off" in prompt:
        hand_off(repo, good)
    else:
        print(f"fake agent: no behaviour for prompt: {prompt}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
