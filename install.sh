#!/bin/sh
# Pakem installer: https://github.com/alfonsusenrico/pakem
#
#   curl -fsSL https://raw.githubusercontent.com/alfonsusenrico/pakem/main/install.sh | sh
#
# Downloads a Pakem release into ~/.local/share/pakem/source, adds the `pakem` and `pk`
# commands to ~/.local/bin, then hands over to `pakem setup`, which asks about your agents,
# profiles, and device, shows the plan, and asks before applying it. Run it again to
# reconfigure: every question offers your current answer. It never uses sudo itself.
#
# Options (after `sh -s --` when piped):
#   --dry-run        show what would happen and change nothing
#   --yes            take every default and apply the plan without asking
#   --no-agents      install the commands only (run `pakem setup` later)
#   --version vX.Y.Z install that release instead of the latest
#   --source DIR     use an existing Pakem checkout (for example one that follows main)
# Environment: PAKEM_VERSION (same as --version), PAKEM_HOME (default ~/.local/share/pakem),
# PAKEM_BIN (default ~/.local/bin), PAKEM_REPO (default the GitHub repository).
set -eu

REPO="${PAKEM_REPO:-https://github.com/alfonsusenrico/pakem.git}"
HOME_DIR="${PAKEM_HOME:-$HOME/.local/share/pakem}"
BIN_DIR="${PAKEM_BIN:-$HOME/.local/bin}"
SOURCE="$HOME_DIR/source"
OWN_SOURCE=0
VERSION="${PAKEM_VERSION:-}"
DRY_RUN=0
ASSUME_YES=0
AGENTS=1

say() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
fail() { printf 'pakem install: %s\n' "$*" >&2; exit 1; }
run() {
  if [ "$DRY_RUN" -eq 1 ]; then printf '  would run: %s\n' "$*"; else "$@"; fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --yes) ASSUME_YES=1 ;;
    --no-agents) AGENTS=0 ;;
    --source) shift; [ $# -gt 0 ] || fail "--source needs a directory"; SOURCE="$1"; OWN_SOURCE=1 ;;
    --version) shift; [ $# -gt 0 ] || fail "--version needs a value"; VERSION="$1" ;;
    -h|--help) sed -n '2,19p' "$0" 2>/dev/null || true; exit 0 ;;
    *) fail "unknown option: $1" ;;
  esac
  shift
done

step "Checking this device"
case "$(uname -s)" in
  Darwin) OS=macos ;;
  Linux) OS=linux ;;
  *) fail "unsupported system $(uname -s). Pakem runs on macOS and Linux" ;;
esac
say "  system: $OS ($(uname -m))"

missing=""
command -v git >/dev/null 2>&1 || missing="git"
PYTHON=""
for candidate in python3 python3.16 python3.15 python3.14 python3.13 python3.12 python3.11; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PYTHON="$candidate"; break
  fi
done
[ -n "$PYTHON" ] || missing="${missing:+$missing, }Python 3.11 or newer"
if [ -n "$missing" ]; then
  say "  missing: $missing"
  if [ "$OS" = macos ]; then
    say "  Install them with: xcode-select --install   (git and Python 3)"
    say "  or with Homebrew:  brew install git python"
  else
    say "  Install them with your package manager, for example:"
    say "    sudo apt-get install -y git python3"
  fi
  fail "install the missing tools, then run this installer again"
fi
say "  git: $(git --version | cut -d' ' -f3)   python: $("$PYTHON" -c 'import platform; print(platform.python_version())')"

if [ "$OWN_SOURCE" -eq 1 ]; then
  step "Using the checkout $SOURCE"
  [ -f "$SOURCE/tools/pakem" ] || fail "$SOURCE is not a Pakem checkout (no tools/pakem)"
elif [ -d "$SOURCE/.git" ] && [ "$(git -C "$SOURCE" symbolic-ref --quiet --short HEAD 2>/dev/null)" = main ]; then
  # A checkout that follows main (`pakem setup --follow main`) keeps following it.
  step "Keeping $SOURCE on main (pakem update moves it)"
else
  step "Choosing the release"
  if [ -z "$VERSION" ]; then
    VERSION="$(git ls-remote --tags --refs "$REPO" 'v*' 2>/dev/null |
      "$PYTHON" -c '
import re, sys
tags = [line.strip().rsplit("/", 1)[-1] for line in sys.stdin if line.strip()]
releases = [t for t in tags if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
releases.sort(key=lambda t: tuple(int(n) for n in t[1:].split(".")))
print(releases[-1] if releases else "")')"
    [ -n "$VERSION" ] || fail "no release found at $REPO"
  fi
  case "$VERSION" in
    v[0-9]*.[0-9]*.[0-9]*) ;;
    *) fail "version must look like v2.0.0, not $VERSION" ;;
  esac
  say "  release: $VERSION"

  step "Downloading Pakem $VERSION"
  if [ -d "$SOURCE/.git" ]; then
    say "  updating $SOURCE"
    run git -C "$SOURCE" fetch --quiet --tags origin
  else
    [ ! -e "$SOURCE" ] || fail "$SOURCE exists and is not a Pakem checkout. Move it away first"
    run mkdir -p "$HOME_DIR"
    run git clone --quiet "$REPO" "$SOURCE"
  fi
  run git -C "$SOURCE" -c advice.detachedHead=false checkout --quiet "$VERSION"
fi

step "Adding the pakem and pk commands"
run mkdir -p "$BIN_DIR"
for name in pakem pk; do
  wrapper="$BIN_DIR/$name"
  if [ -e "$wrapper" ] && ! grep -q '^# pakem-managed$' "$wrapper" 2>/dev/null; then
    fail "$wrapper exists and was not made by Pakem. Remove or rename it first"
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    say "  would write $wrapper"
  else
    printf '#!/bin/sh\n# pakem-managed\nexec "%s" "%s" "$@"\n' "$PYTHON" "$SOURCE/tools/$name" > "$wrapper"
    chmod 0755 "$wrapper"
    say "  $wrapper"
  fi
done
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) say "  $BIN_DIR is not on your PATH. Add this line to your shell profile:"
     say "    export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

if [ "$AGENTS" -eq 0 ]; then
  step "Done. Setup skipped (--no-agents). Run: pakem setup"
  exit 0
fi

step "Setting up this device"
if [ "$DRY_RUN" -eq 1 ]; then
  say "  would run: pakem setup"
  exit 0
fi
if [ "$ASSUME_YES" -eq 1 ]; then
  "$PYTHON" "$SOURCE/tools/pakem" setup --yes
elif (exec < /dev/tty) 2>/dev/null; then
  # Piped from curl, standard input is the script: the questions read the terminal.
  "$PYTHON" "$SOURCE/tools/pakem" setup < /dev/tty
else
  step "Commands installed. Without a terminal, finish with: pakem setup --yes (or run pakem setup in a terminal)"
fi
