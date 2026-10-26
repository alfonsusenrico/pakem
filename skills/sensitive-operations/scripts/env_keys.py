#!/usr/bin/env python3
"""List variable names from dotenv-style files without ever printing values.

Usage:
  env_keys.py FILE                 print sorted variable names
  env_keys.py FILE --compare OTHER print names missing from or extra in FILE vs OTHER

Only names that match ^[A-Za-z_][A-Za-z0-9_]*$ are printed. Values, comments,
unparsable lines, and multiline continuations are never echoed; skipped lines are
only counted. The file is parsed as text and never sourced or executed.
"""

import argparse
import re
import sys
from pathlib import Path

NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _closes_quote(text: str, quote: str) -> bool:
    """Return True when text contains an unescaped closing quote character."""
    escaped = False
    for character in text:
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
            continue
        if character == quote:
            return True
    return False


def read_names(path: Path) -> tuple[list[str], int]:
    names: set[str] = set()
    skipped_lines = 0
    open_quote: str | None = None

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if open_quote is not None:
            # Continuation of a multiline quoted value: never inspect or count as a name.
            if _closes_quote(raw_line, open_quote):
                open_quote = None
            continue

        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()

        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not NAME_PATTERN.match(name):
            skipped_lines += 1
            continue
        names.add(name)

        value = value.lstrip()
        if value[:1] in ('"', "'"):
            quote = value[0]
            if not _closes_quote(value[1:], quote):
                open_quote = quote

    return sorted(names), skipped_lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path)
    parser.add_argument("--compare", type=Path, help="reference file, for example .env.example")
    arguments = parser.parse_args()

    for path in [arguments.file, arguments.compare]:
        if path is not None and not path.is_file():
            print(f"error: not a file: {path}", file=sys.stderr)
            return 2

    names, skipped = read_names(arguments.file)
    if arguments.compare is None:
        for name in names:
            print(name)
    else:
        reference_names, reference_skipped = read_names(arguments.compare)
        skipped += reference_skipped
        missing = sorted(set(reference_names) - set(names))
        extra = sorted(set(names) - set(reference_names))
        print(f"missing from {arguments.file.name}: {', '.join(missing) or 'none'}")
        print(f"not in {arguments.compare.name}: {', '.join(extra) or 'none'}")

    if skipped:
        print(f"note: {skipped} unparsable line(s) skipped. Contents not shown", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
