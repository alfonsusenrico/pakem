#!/usr/bin/env python3
"""Read-only smoke checks: the deployed revision first, then optional endpoints.

Usage:
  smoke.py --base-url URL --revision SHA
           [--release-config deploy/release.toml | --health-path PATH [--revision-key KEY]]
           [--path PATH ...] [--attempts 30] [--interval 2] [--timeout 10]

The health path must answer HTTP 200 with a JSON object whose revision key (default
`revision`) equals the full commit SHA; the check retries until the new release answers.
Each --path is then fetched once with GET and must answer 2xx. Redirects are not
followed. Only https URLs are accepted, plus http on loopback for checks on the host.
Response bodies are never printed; only the observed revision is reported on mismatch.

Exit codes: 0 all checks passed, 1 a check failed, 2 invalid usage.
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    sys.stderr.write("smoke: ERROR: Python 3.11 or later is required\n")
    sys.exit(2)

import argparse
import http.client
import json
import re
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
PATH_PATTERN = re.compile(r"^/[A-Za-z0-9._~/?=&%-]*$")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

Fetch = Callable[[str, float], tuple[int, bytes]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003 - urllib signature
        return None


def fetch_url(url: str, timeout: float) -> tuple[int, bytes]:
    handlers: list[urllib.request.BaseHandler] = [_NoRedirect()]
    if urllib.parse.urlsplit(url).hostname in LOOPBACK_HOSTS:
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "af-smoke"})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read(65536)
    except urllib.error.HTTPError as error:
        return error.code, b""


def validate_base_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.username or parts.password:
        raise ValueError("the base URL must not contain credentials")
    if parts.query or parts.fragment or not parts.hostname:
        raise ValueError("the base URL must be scheme://host[:port][/prefix]")
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS):
        return url.rstrip("/")
    raise ValueError("the base URL must use https (http only on loopback)")


def check_revision(
    fetch: Fetch,
    url: str,
    revision: str,
    key: str,
    attempts: int,
    interval: float,
    timeout: float,
    sleep: Callable[[float], None],
) -> bool:
    observed = "no response"
    for attempt in range(1, attempts + 1):
        try:
            status, body = fetch(url, timeout)
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
            observed = f"connection error ({type(error).__name__})"
        else:
            if status == 200:
                try:
                    found = json.loads(body.decode("utf-8")).get(key)
                except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                    found = None
                if found == revision:
                    print(f"ok    revision {revision[:12]} at {url} (attempt {attempt})", flush=True)
                    return True
                observed = f"HTTP 200, revision {str(found)[:40]!r}"
            else:
                observed = f"HTTP {status}"
        if attempt < attempts:
            sleep(interval)
    print(
        f"FAIL  revision {revision[:12]} not served at {url} after {attempts} attempt(s). Last: {observed}", flush=True
    )
    return False


def check_path(fetch: Fetch, url: str, timeout: float) -> bool:
    try:
        status, _body = fetch(url, timeout)
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
        print(f"FAIL  GET {url}: connection error ({type(error).__name__})", flush=True)
        return False
    if 200 <= status < 300:
        print(f"ok    GET {url} -> {status}", flush=True)
        return True
    print(f"FAIL  GET {url} -> {status}", flush=True)
    return False


def health_settings(args: argparse.Namespace) -> tuple[str, str]:
    path, key = args.health_path, args.revision_key
    if args.release_config:
        data = tomllib.loads(Path(args.release_config).read_text(encoding="utf-8"))
        path = data.get("health_path", path)
        key = data.get("revision_key", key)
    if not isinstance(path, str) or not PATH_PATTERN.match(path):
        raise ValueError("a health path starting with / is required (--health-path or --release-config)")
    if not isinstance(key, str) or not key:
        raise ValueError("the revision key must be a non-empty string")
    return path, key


def main(argv: list[str] | None = None, fetch: Fetch = fetch_url, sleep: Callable[[float], None] = time.sleep) -> int:
    parser = argparse.ArgumentParser(
        prog="smoke.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--release-config", help="deploy/release.toml supplying health_path and revision_key")
    parser.add_argument("--health-path")
    parser.add_argument("--revision-key", default="revision")
    parser.add_argument(
        "--path", action="append", default=[], help="read-only endpoint that must answer 2xx (repeatable)"
    )
    parser.add_argument("--attempts", type=int, default=30)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    try:
        if not SHA_PATTERN.match(args.revision):
            raise ValueError("--revision must be a full 40-character lowercase commit SHA")
        base_url = validate_base_url(args.base_url)
        health_path, revision_key = health_settings(args)
        for path in args.path:
            if not PATH_PATTERN.match(path):
                raise ValueError(f"--path must start with / and use URL-safe characters: {path!r}")
        if not 1 <= args.attempts <= 600 or not 0 <= args.interval <= 60:
            raise ValueError("--attempts must be 1-600 and --interval 0-60 seconds")
    except (ValueError, OSError, tomllib.TOMLDecodeError) as error:
        print(f"smoke: ERROR: {error}", file=sys.stderr)
        return 2

    if not check_revision(
        fetch, base_url + health_path, args.revision, revision_key, args.attempts, args.interval, args.timeout, sleep
    ):
        return 1
    results = [check_path(fetch, base_url + path, args.timeout) for path in args.path]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
