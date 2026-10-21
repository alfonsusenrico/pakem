#!/bin/sh
# Verify Pakem pinned blocks in a project's AGENTS.md without the framework
# checkout (for CI in repositories that cannot access it). Blocks written before
# the rename carry the former marker name agent-framework and are still accepted.
#
# Usage: check_pinned_blocks.sh [REPO_DIR | AGENTS_FILE]
#
# Checks only block integrity (each block's sha256 matches its body, markers
# balanced) and AGENTS.md size. The sha256 covers every line between the opening
# and closing marker lines, each terminated by LF; this must stay in agreement
# with tools/pakem_lib/pinned.py. Size is measured in bytes here, while
# `pakem project check` counts characters; they are equal for ASCII files.

set -u

ERROR_BYTES=12000
WARN_BYTES=10000

target=${1:-.}
if [ -d "$target" ]; then
    agents_file=$target/AGENTS.md
else
    agents_file=$target
fi
if [ ! -f "$agents_file" ]; then
    echo "ERROR: $agents_file not found"
    exit 1
fi

if command -v sha256sum >/dev/null 2>&1; then
    sha_command="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
    sha_command="shasum -a 256"
else
    echo "ERROR: neither sha256sum nor shasum is available"
    exit 2
fi

work_dir=$(mktemp -d "${TMPDIR:-/tmp}/pinned-blocks.XXXXXX") || exit 2
trap 'rm -rf "$work_dir"' EXIT HUP INT TERM

# Emits "BLOCK|<n>|<kind>|<name>|<sha>|<line>" per block (body written to
# $work_dir/block.<n>) and "ERROR|<message>" for malformed markers.
scan=$(awk -v dir="$work_dir" '
    {
        sub(/\r$/, "")
        line = $0
        marker = line
        sub(/[ \t]+$/, "", marker)
        if (marker ~ /^<!-- (pakem|agent-framework):(digest|profile)( [a-z0-9_]+=[^ ]*)* -->$/) {
            if (open) {
                print "ERROR|line " NR ": block opened before the previous block was closed"
                next
            }
            open = 1
            count++
            kind = marker
            sub(/^<!-- [a-z-]+:/, "", kind)
            sub(/ .*/, "", kind)
            sha = ""
            if (match(marker, /sha256=[0-9a-f]*/)) sha = substr(marker, RSTART + 7, RLENGTH - 7)
            name = ""
            if (match(marker, / name=[^ ]*/)) name = substr(marker, RSTART + 6, RLENGTH - 6)
            body = dir "/block." count
            printf "" > body
            print "BLOCK|" count "|" kind "|" name "|" sha "|" NR
            next
        }
        if (marker ~ /^<!-- \/(pakem|agent-framework):(digest|profile) -->$/) {
            if (!open) {
                print "ERROR|line " NR ": closing marker without an opening marker"
                next
            }
            closing_kind = marker
            sub(/^<!-- \/[a-z-]+:/, "", closing_kind)
            sub(/ .*/, "", closing_kind)
            if (closing_kind != kind) print "ERROR|line " NR ": closing " closing_kind " marker for an open " kind " block"
            close(body)
            open = 0
            next
        }
        if (open) print line > body
    }
    END {
        if (open) print "ERROR|" kind " block " count " is never closed"
    }
' "$agents_file")

errors=0
warnings=0
blocks=0

while IFS='|' read -r tag field1 field2 field3 field4 field5; do
    case "$tag" in
        ERROR)
            echo "ERROR: AGENTS.md $field1"
            errors=$((errors + 1))
            ;;
        BLOCK)
            blocks=$((blocks + 1))
            actual=$($sha_command < "$work_dir/block.$field1" | awk '{ print $1 }')
            label=$field2
            if [ -n "$field3" ]; then
                label="$field2 $field3"
            fi
            if [ "$actual" != "$field4" ]; then
                echo "ERROR: $label block (line $field5) body does not match its sha256; it was edited by hand"
                errors=$((errors + 1))
            fi
            ;;
    esac
done <<EOF
$scan
EOF

if [ "$blocks" -eq 0 ]; then
    echo "WARNING: AGENTS.md has no Pakem blocks"
    warnings=$((warnings + 1))
fi

size=$(wc -c < "$agents_file" | tr -d ' ')
if [ "$size" -gt "$ERROR_BYTES" ]; then
    echo "ERROR: AGENTS.md is $size bytes (limit $ERROR_BYTES)"
    errors=$((errors + 1))
elif [ "$size" -gt "$WARN_BYTES" ]; then
    echo "WARNING: AGENTS.md is $size bytes (warning above $WARN_BYTES)"
    warnings=$((warnings + 1))
fi

echo "$errors error(s), $warnings warning(s)"
if [ "$errors" -gt 0 ]; then
    exit 1
fi
exit 0
