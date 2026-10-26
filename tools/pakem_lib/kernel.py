"""Rendering of the always-on kernel that is installed as each host's global instructions."""

from __future__ import annotations

from .common import Framework, normalize_newlines, read_text, sha256_text
from .hosts import HostTarget


def kernel_body(framework: Framework) -> str:
    return normalize_newlines(read_text(framework.kernel_path))


def render_kernel(framework: Framework) -> str:
    body = kernel_body(framework)
    header = (
        f"<!-- pakem kernel v{framework.version()} sha256={sha256_text(body)}"
        " | managed by pakem; edit the framework repo, not this file -->"
    )
    if not body.endswith("\n"):
        body += "\n"
    return f"{header}\n\n{body}"


def size_violations(rendered: str, host: HostTarget) -> list[str]:
    violations: list[str] = []
    char_count = len(rendered)
    byte_count = len(rendered.encode("utf-8"))
    if host.max_chars is not None and char_count > host.max_chars:
        violations.append(f"rendered kernel is {char_count} chars, but {host.host_id} allows {host.max_chars}")
    if host.max_bytes is not None and byte_count > host.max_bytes:
        violations.append(f"rendered kernel is {byte_count} bytes, but {host.host_id} allows {host.max_bytes}")
    return violations
