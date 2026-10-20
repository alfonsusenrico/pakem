"""A sandbox for the release engine: a local registry, cosign keys, and a tiny web image.

Shared by the opt-in (AF_SANDBOX=1) end-to-end tests. `publish` builds, pushes, and signs a
release the way CI will; `make_host` prepares a deploy base for one environment. Every
container, Compose project, and image is removed afterwards.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
import unittest
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path

from support import DEPLOY_ASSETS

sys.path.insert(0, str(DEPLOY_ASSETS))
import af_engine  # noqa: E402
import af_release  # noqa: E402
import af_settings  # noqa: E402

ENABLED = os.environ.get("AF_SANDBOX") == "1"
SKIP_REASON = "set AF_SANDBOX=1 to run the release engine end to end with Docker"

APP = {
    "app/__init__.py": "",
    "app/server.py": textwrap.dedent(
        """\
        import json, os
        from http.server import BaseHTTPRequestHandler, HTTPServer

        REVISION = open("/revision").read().strip()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"revision": REVISION}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
        """
    ),
    "app/preflight.py": textwrap.dedent(
        """\
        import os, sys
        path = os.environ.get("DATABASE_PASSWORD_FILE", "")
        sys.exit(0 if path and open(path).read() else 1)
        """
    ),
    "app/migrate.py": "",
}
DOCKERFILE = textwrap.dedent(
    """\
    FROM python:3.13-alpine
    ARG REVISION
    RUN printf '%s' "$REVISION" > /revision
    COPY app /app
    WORKDIR /
    USER nobody
    CMD ["python", "-m", "app.server"]
    """
)
COMPOSE = textwrap.dedent(
    """\
    services:
      web:
        image: ${AF_IMAGE_WEB}
        env_file: ${AF_RUNTIME_FILE}
        ports: ["127.0.0.1:${HTTP_PORT}:8000"]
        secrets: [database_password]
    secrets:
      database_password:
        file: ${AF_SECRETS_DIR}/DATABASE_PASSWORD
    """
)
RELEASE_TOML = textwrap.dedent(
    """\
    af_standard = 1
    project = "sandbox"
    compose_files = ["compose.yaml"]
    preflight = ["python", "-m", "app.preflight"]
    migrate = ["python", "-m", "app.migrate"]
    migrate_service = "web"
    schema_version = { glob = "migrations/*.sql" }
    drain_seconds = 5

    [health]
    service = "web"
    path = "/health/release"
    revision_key = "revision"
    port_setting = "HTTP_PORT"

    [[images]]
    name = "web"
    dockerfile = "Dockerfile"
    context = "."

    [environments.staging]
    keep = 3

    [environments.production]
    keep = 3
    backup = "required"
    """
)
SETTINGS_TOML = textwrap.dedent(
    """\
    af_standard = 1
    required = ["HTTP_PORT", "DATABASE_PASSWORD", "LOG_LEVEL"]
    placement = ["HTTP_PORT"]
    secrets = ["DATABASE_PASSWORD"]

    [defaults]
    staging = "deploy/env/staging.env"
    production = "deploy/env/production.env"
    """
)
BACKUP_HOOK = '#!/bin/sh\nprintf "backup of %s\\n" "$AF_COMPOSE_PROJECT" > "$AF_BACKUP_DIR/database.dump"\n'


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def run(*arguments: str, cwd: Path | None = None, env: dict | None = None) -> str:
    result = subprocess.run(arguments, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"{' '.join(arguments[:3])} failed: {result.stderr[-600:]}")
    return result.stdout + result.stderr


class EngineSandbox(unittest.TestCase):
    """Registry, keys, and image source; deploy bases are added per environment with make_host."""

    def setUp(self) -> None:
        for tool in ("docker", "oras", "cosign"):
            if shutil.which(tool) is None:
                self.skipTest(f"{tool} is not installed")
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory()))
        registry_port = free_port()
        self.registry_name = f"af-engine-e2e-registry-{registry_port}"
        run("docker", "run", "-d", "--name", self.registry_name, "-p", f"127.0.0.1:{registry_port}:5000", "registry:3")
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", self.registry_name], capture_output=True, check=False)
        self.repository = f"127.0.0.1:{registry_port}/owner/sandbox"
        # Cleanups run last-in first-out: images go after `compose down` has removed containers.
        self.addCleanup(self.remove_images)
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{registry_port}/v2/", timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        self.env = {"PATH": os.environ["PATH"], "HOME": str(Path.home()), "COSIGN_PASSWORD": ""}
        keys = self.work / "keys"
        keys.mkdir()
        run("cosign", "generate-key-pair", cwd=keys, env=self.env)
        run("cosign", "signing-config", "create", "--out", "signing-config.json", cwd=keys, env=self.env)
        self.keys = keys
        self.source = self.work / "source"
        for name, content in APP.items():
            (self.source / name).parent.mkdir(parents=True, exist_ok=True)
            (self.source / name).write_text(content)
        (self.source / "Dockerfile").write_text(DOCKERFILE)
        self.ports: dict[str, int] = {}

    def make_host(self, environment: str) -> af_engine.HostConfig:
        """A deploy base for one environment with its port, secret, and Compose cleanup."""
        base = self.work / f"base-{environment}"
        base.mkdir(mode=0o700)
        (base / "secrets").mkdir(mode=0o700)
        self.ports[environment] = free_port()
        placement = base / "placement.env"
        placement.write_text(f"HTTP_PORT={self.ports[environment]}\n")
        placement.chmod(0o600)
        af_settings.set_secret(base / "secrets", "DATABASE_PASSWORD", b"sandbox-secret")
        self.addCleanup(
            subprocess.run,
            ["docker", "compose", "--project-name", f"sandbox-{environment}", "down", "--volumes", "--remove-orphans"],
            capture_output=True,
            check=False,
        )
        return af_engine.HostConfig(
            project="sandbox",
            environment=environment,
            base=base,
            pointer=f"{self.repository}:release-{environment}",
            public_key=self.keys / "cosign.pub",
        )

    @staticmethod
    def write_config(config: af_engine.HostConfig, directory: Path) -> Path:
        directory.mkdir(exist_ok=True)
        path = directory / f"{config.project}-{config.environment}.toml"
        path.write_text(
            f'project = "{config.project}"\nenvironment = "{config.environment}"\nbase = "{config.base}"\n'
            f'pointer = "{config.pointer}"\npublic_key = "{config.public_key}"\n'
        )
        return path

    def remove_images(self) -> None:
        """Remove every local image of the sandbox repository, tagged or pulled by digest."""
        listing = subprocess.run(
            ["docker", "images", "--quiet", "--filter", f"reference={self.repository}/*"],
            capture_output=True, text=True, check=False,
        )
        for image in sorted(set(listing.stdout.split())):
            subprocess.run(["docker", "rmi", "-f", image], capture_output=True, check=False)

    def sign(self, reference: str) -> None:
        run(
            "cosign", "sign", "--yes", "--key", "cosign.key", "--signing-config", "signing-config.json",
            "--allow-http-registry", reference, cwd=self.keys, env=self.env,
        )

    def push_artifact(self, tags: str, artifact_type: str, content: bytes) -> str:
        """Push one artifact under one or more comma-separated tags; all name the same digest."""
        path = self.work / f"{tags.split(',')[0]}.bin"
        path.write_bytes(content)
        output = run(
            "oras", "push", "--plain-http", f"{self.repository}:{tags}", "--artifact-type", artifact_type,
            f"{path.name}:{artifact_type}", cwd=self.work,
        )
        digest = next(line.split()[-1] for line in output.splitlines() if line.startswith("Digest:"))
        return f"{self.repository}@{digest}"

    def publish(
        self,
        commit: str,
        created: str,
        *,
        image_revision: str | None = None,
        environments: tuple[str, ...] = ("production",),
    ) -> None:
        """Build, push, and sign a release the way CI will, and point the environments at it."""
        tag = f"{self.repository}/web:{commit[:12]}"
        run("docker", "build", "-q", "--build-arg", f"REVISION={image_revision or commit}", "-t", tag, str(self.source))
        run("docker", "push", "-q", tag)
        image = run("docker", "inspect", "--format", "{{index .RepoDigests 0}}", tag).strip()
        files = {
            "compose.yaml": COMPOSE,
            "deploy/release.toml": RELEASE_TOML,
            "deploy/settings.toml": SETTINGS_TOML,
            "deploy/env/staging.env": "LOG_LEVEL=debug\n",
            "deploy/env/production.env": "LOG_LEVEL=info\n",
            "deploy/hooks/backup": BACKUP_HOOK,
        }
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for name in sorted(files):
                data = files[name].encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o755 if "/hooks/" in name else 0o644
                archive.addfile(info, io.BytesIO(data))
        bundle = self.push_artifact(f"bundle-{commit[:12]}", af_release.BUNDLE_MEDIA, buffer.getvalue())
        record = {
            "schema": "af.release/v1", "project": "sandbox", "commit": commit, "version": "v1.0.0",
            "created": created, "source": "https://example.test/owner/sandbox", "ci_run": "https://example.test/run/1",
            "images": {"web": image}, "bundle": bundle, "schema_version": "0001", "rollback": False,
        }
        tags = ",".join(f"release-{environment}" for environment in environments)
        pointer = self.push_artifact(tags, af_release.RELEASE_MEDIA, json.dumps(record).encode())
        for reference in (pointer, image, bundle):
            self.sign(reference)

    def deploy(self, config: af_engine.HostConfig) -> dict:
        output = io.StringIO()
        with redirect_stdout(output):
            record = af_engine.Engine(config, health_attempts=30).deploy()
        self.assertNotIn("sandbox-secret", output.getvalue())
        self.last_output = output.getvalue()
        return record

    def served_revision(self, environment: str) -> str | None:
        body = af_engine.probe_health(f"http://127.0.0.1:{self.ports[environment]}/health/release")
        return body.get("revision") if body else None
