"""Unit tests for the deploy asset release_host.py with an in-memory Docker.

FakeDocker answers the docker and docker compose commands release_host.py issues and
simulates images, labels, running containers, and HTTP health answers; hooks in the
fixture release are real shell scripts. Nothing here contacts Docker or the network.
"""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from support import DEPLOY_ASSETS, load_deploy_script

release_host = load_deploy_script("release_host.py")

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
OWNER_MODE = "root" if os.geteuid() == 0 else "self"
TOKEN = "test-token-0123456789abcdef"
SECRET_VALUE = "dummy-secret-value-$HOME-not-printed"

RELEASE_TOML = textwrap.dedent(
    """\
    app = "demo"
    compose_file = "compose.yaml"
    service = "web"
    candidate_port = 8000
    health_path = "/release.json"
    preflight = ["sh", "-c", "true"]
    migrate = ["sh", "-c", "echo migrate"]
    health_attempts = 2
    health_interval = 1

    [[images]]
    name = "web"

    [environments.staging]
    local_url = "http://127.0.0.1:18080"
    keep = 2

    [environments.production]
    local_url = "http://127.0.0.1:18081"
    """
)

HOOK = '#!/bin/sh\necho "$(basename "$0") $AF_RELEASE_SHA" >> "$AF_BASE/hooks.log"\n'
BACKUP_HOOK = '#!/bin/sh\nset -eu\necho dump > "$AF_BACKUP_DIR/db.dump"\n'


def release_files(
    message: str = "one", backup: bool = True, release_toml: str = RELEASE_TOML
) -> dict[str, tuple[str, int]]:
    files = {
        "deploy/release.toml": (release_toml, 0o644),
        "compose.yaml": ("services:\n  web:\n    image: ${AF_IMAGE_WEB}\n", 0o644),
        "Dockerfile": ("FROM scratch\n", 0o644),
        "message.txt": (message + "\n", 0o644),
        "deploy/hooks/pre_build": (HOOK, 0o755),
        "deploy/hooks/pre_migrate": (HOOK, 0o755),
        "deploy/hooks/post_promote": (HOOK, 0o755),
    }
    if backup:
        files["deploy/hooks/backup"] = (BACKUP_HOOK, 0o755)
    return files


def tar_bytes(files: dict[str, tuple[str, int]], extra: list[tarfile.TarInfo] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        directories = sorted({str(Path(name).parent) for name in files if "/" in name})
        for directory in directories:
            parts = Path(directory).parts
            for index in range(1, len(parts) + 1):
                name = "/".join(parts[:index]) + "/"
                if name not in tar.getnames() and name.rstrip("/") not in tar.getnames():
                    info = tarfile.TarInfo(name)
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    tar.addfile(info)
        for name, (content, mode) in sorted(files.items()):
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = mode
            tar.addfile(info, io.BytesIO(data))
        for info in extra or []:
            tar.addfile(info, io.BytesIO(b"x" * info.size) if info.isreg() else None)
    return buffer.getvalue()


class FakeDocker:
    """Minimal model of the Docker commands release_host.py runs."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.images: dict[str, dict] = {}
        self.live: dict[str, str] = {}
        self.candidates: dict[str, str] = {}
        self.unhealthy: set[str] = set()
        self.preflight_exit = 0
        self.candidate_revision_override: str | None = None
        self.image_rm_fails: set[str] = set()
        self.extra_running: list[tuple[str, str]] = []
        self.counter = 0

    # -- runner ----------------------------------------------------------------
    def run(self, argv, *, env=None, cwd=None, capture=True, timeout=None):
        self.calls.append(list(argv))
        if argv[0] != "docker":
            return subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, check=False)
        if argv[1] == "compose":
            return self.compose(argv, env or {})
        handler = {"image": self.image, "build": self.build, "port": self.port, "rm": self.rm, "ps": self.ps}[argv[1]]
        return handler(argv)

    @staticmethod
    def done(argv, code=0, stdout=""):
        return subprocess.CompletedProcess(argv, code, stdout, "")

    def image(self, argv):
        if argv[2] == "inspect":
            details = self.images.get(argv[3])
            if details is None:
                return self.done(argv, 1)
            return self.done(argv, 0, json.dumps([details]))
        if argv[2] == "rm":
            if argv[3] in self.image_rm_fails:
                return self.done(argv, 1)
            self.images.pop(argv[3], None)
            return self.done(argv)
        raise AssertionError(argv)

    def build(self, argv):
        tag = argv[argv.index("--tag") + 1]
        labels = dict(argv[index + 1].split("=", 1) for index, value in enumerate(argv) if value == "--label")
        self.counter += 1
        self.images[tag] = {"Id": f"sha256:{self.counter:064x}", "Config": {"Labels": labels}}
        return self.done(argv)

    def port(self, argv):
        if argv[2] in self.candidates:
            return self.done(argv, 0, "127.0.0.1:40000\n")
        return self.done(argv, 1)

    def rm(self, argv):
        existed = self.candidates.pop(argv[-1], None)
        return self.done(argv, 0 if existed else 1)

    def ps(self, argv):
        filters = [argv[index + 1] for index, value in enumerate(argv) if value == "--filter"]
        prefix = "label=com.docker.compose.project="
        project = next((item.removeprefix(prefix) for item in filters if item.startswith(prefix)), None)
        rows = [(name, sha) for name, sha in self.live.items()] + self.extra_running
        lines = [sha for name, sha in rows if project is None or name == project]
        return self.done(argv, 0, "".join(line + "\n" for line in lines))

    def compose(self, argv, env):
        project = argv[argv.index("--project-name") + 1]
        rest = argv[argv.index("--env-file") + 2 :]
        sha = env["AF_RELEASE_SHA"]
        if rest[0] == "run" and "--detach" in rest:
            self.candidates[rest[rest.index("--name") + 1]] = sha
            return self.done(argv)
        if rest[0] == "run":
            if rest[1:4] == ["--rm", "--no-deps", "-T"]:
                return self.done(argv, self.preflight_exit)
            return self.done(argv)
        if rest[0] == "up":
            self.live[project] = sha
            return self.done(argv)
        if rest[0] == "down":
            self.live.pop(project, None)
            return self.done(argv)
        raise AssertionError(argv)

    # -- HTTP --------------------------------------------------------------------
    def fetch(self, url, timeout):
        if url.startswith("http://127.0.0.1:40000"):
            revision = self.candidate_revision_override or next(iter(self.candidates.values()))
            return 200, json.dumps({"revision": revision}).encode()
        project = "demo-staging" if ":18080" in url else "demo-production"
        sha = self.live.get(project)
        if sha is None or sha in self.unhealthy:
            raise urllib.error.URLError("connection refused")
        return 200, json.dumps({"revision": sha}).encode()

    def subcommands(self, name: str) -> list[list[str]]:
        return [call for call in self.calls if call[:2] == ["docker", "compose"] and name in call]


class ReleaseHostTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="af-release-host-")
        self.root = Path(os.path.realpath(self._temporary.name)) / "srv"
        self.root.mkdir(mode=0o700)
        self.docker = FakeDocker()
        self.clock = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.deps = release_host.Deps(
            run=self.docker.run,
            fetch=self.docker.fetch,
            sleep=lambda seconds: None,
            now=self.tick,
            stdin_wait=lambda: None,
        )
        self.lock_descriptors: list[tuple[object, int]] = []

    def tearDown(self) -> None:
        for host, descriptor in self.lock_descriptors:
            host.release_lock(descriptor)
        for directory, _dirs, _files in os.walk(self._temporary.name):
            os.chmod(directory, 0o700)
        self._temporary.cleanup()

    def tick(self) -> datetime:
        self.clock += timedelta(minutes=1)
        return self.clock

    def base(self, environment: str = "staging") -> Path:
        return self.root / environment

    def main(self, *args: str, environment: str = "staging") -> tuple[int, str]:
        output = io.StringIO()
        argv = [args[0], "--base", str(self.base(environment)), "--owner", OWNER_MODE, *args[1:]]
        previous_umask = os.umask(0o077)
        try:
            with redirect_stdout(output), redirect_stderr(output):
                code = release_host.main(argv, self.deps)
        finally:
            os.umask(previous_umask)
        return code, output.getvalue()

    def hold_lock(self, environment: str = "staging") -> None:
        host = release_host.Host(str(self.base(environment)), OWNER_MODE, self.deps)
        self.lock_descriptors.append((host, host.acquire_lock(0, TOKEN)))

    def upload(self, environment: str, name: str, data: bytes) -> Path:
        incoming = self.base(environment) / "incoming"
        path = incoming / name
        path.write_bytes(data)
        os.chmod(path, 0o600)
        return path

    def runtime_text(self, host_generated: str = "") -> str:
        header = f"# af:host-generated {host_generated}\n" if host_generated else ""
        return f"# test runtime\n{header}APP_SECRET='{SECRET_VALUE}'\nHTTP_PORT='18080'\n"

    def prepare(
        self, sha: str, files=None, environment: str = "staging", runtime: str | None = None
    ) -> tuple[int, str]:
        if not self.lock_descriptors:
            self.hold_lock(environment)
        archive = self.upload(environment, "release.tar", tar_bytes(files or release_files()))
        runtime_file = self.upload(environment, "runtime.env", (runtime or self.runtime_text()).encode())
        return self.main(
            "prepare",
            "--environment",
            environment,
            "--sha",
            sha,
            "--archive",
            str(archive),
            "--runtime",
            str(runtime_file),
            "--lock-token",
            TOKEN,
            environment=environment,
        )

    def deploy(self, sha: str, files=None, environment: str = "staging") -> int:
        code, output = self.prepare(sha, files, environment)
        self.assertEqual(code, 0, output)
        code, output = self.main(
            "activate", "--environment", environment, "--sha", sha, "--lock-token", TOKEN, environment=environment
        )
        if code != 0:
            return code
        code, output = self.main(
            "promote", "--environment", environment, "--sha", sha, "--lock-token", TOKEN, environment=environment
        )
        self.assertEqual(code, 0, output)
        return 0

    def current(self, environment: str = "staging") -> str | None:
        link = self.base(environment) / "current"
        return os.readlink(link).split("/")[-1] if link.is_symlink() else None


class SafeExtractionTests(ReleaseHostTestCase):
    def extract(self, files=None, extra=None) -> tuple[Path, int]:
        work = Path(tempfile.mkdtemp(dir=self._temporary.name))
        archive = work / "archive.tar"
        archive.write_bytes(tar_bytes(files or {}, extra))
        destination = work / "out"
        destination.mkdir()
        return destination, release_host.safe_extract(archive, destination, 10_000_000)

    def assert_rejected(self, info: tarfile.TarInfo, files=None) -> None:
        with self.assertRaises(release_host.DeployError) as caught:
            self.extract(files, [info])
        self.assertEqual(caught.exception.exit_code, release_host.EXIT_INVALID)

    def test_rejects_parent_traversal_and_absolute_paths(self) -> None:
        for name in ("../evil", "a/../../evil", "/etc/passwd"):
            with self.subTest(name=name):
                info = tarfile.TarInfo(name)
                info.size = 1
                self.assert_rejected(info)

    def test_rejects_links_devices_and_symlinks_leaving_the_release(self) -> None:
        hardlink = tarfile.TarInfo("hard")
        hardlink.type = tarfile.LNKTYPE
        hardlink.linkname = "message.txt"
        fifo = tarfile.TarInfo("pipe")
        fifo.type = tarfile.FIFOTYPE
        outside = tarfile.TarInfo("escape")
        outside.type = tarfile.SYMTYPE
        outside.linkname = "../../etc"
        absolute = tarfile.TarInfo("absolute")
        absolute.type = tarfile.SYMTYPE
        absolute.linkname = "/etc/passwd"
        for info in (hardlink, fifo, outside, absolute):
            with self.subTest(member=info.name):
                self.assert_rejected(info, {"message.txt": ("x", 0o644)})

    def test_refuses_to_write_through_a_symlinked_directory(self) -> None:
        link = tarfile.TarInfo("sub")
        link.type = tarfile.SYMTYPE
        link.linkname = "."
        through = tarfile.TarInfo("sub/file")
        through.size = 1
        with self.assertRaises(release_host.DeployError):
            self.extract({}, [link, through])

    def test_skips_env_files_and_keeps_only_the_executable_bit(self) -> None:
        files = {
            ".env": ("SECRET=1", 0o644),
            "config/.env.production": ("SECRET=2", 0o644),
            "run.sh": ("#!/bin/sh\n", 0o4777),
            "readme.txt": ("hi", 0o666),
        }
        destination, skipped = self.extract(files)
        self.assertEqual(skipped, 2)
        self.assertFalse((destination / ".env").exists())
        self.assertFalse((destination / "config" / ".env.production").exists())
        self.assertEqual(stat.S_IMODE(os.lstat(destination / "run.sh").st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.lstat(destination / "readme.txt").st_mode), 0o600)

    def test_rejects_duplicate_members(self) -> None:
        first = tarfile.TarInfo("same")
        first.size = 1
        second = tarfile.TarInfo("same")
        second.size = 1
        with self.assertRaises(release_host.DeployError):
            self.extract({}, [first, second])


class PrepareTests(ReleaseHostTestCase):
    def test_prepare_creates_an_immutable_release_and_installs_the_runtime(self) -> None:
        code, output = self.prepare(SHA_A)
        self.assertEqual(code, 0, output)
        release = self.base() / "releases" / SHA_A
        meta = json.loads((release / "release.json").read_text())
        self.assertEqual(meta["sha"], SHA_A)
        self.assertEqual(meta["tree_sha256"], release_host.tree_digest(release / "source"))
        self.assertEqual(stat.S_IMODE(os.lstat(release / "source").st_mode), 0o500)
        self.assertEqual(stat.S_IMODE(os.lstat(release / "source" / "message.txt").st_mode), 0o400)
        runtime = release / "runtime.env.next"
        self.assertEqual(stat.S_IMODE(os.lstat(runtime).st_mode), 0o600)
        self.assertIn(f"APP_SECRET='{SECRET_VALUE}'", runtime.read_text())
        self.assertFalse((self.base() / "incoming" / "release.tar").exists())
        self.assertFalse((self.base() / "incoming" / "runtime.env").exists())
        self.assertNotIn(SECRET_VALUE, output)

    def test_redeploying_the_same_commit_reuses_the_verified_release(self) -> None:
        self.assertEqual(self.prepare(SHA_A)[0], 0)
        code, output = self.prepare(SHA_A)
        self.assertEqual(code, 0, output)
        self.assertIn("already prepared; content verified", output)

    def test_same_commit_with_different_content_is_refused(self) -> None:
        self.assertEqual(self.prepare(SHA_A)[0], 0)
        before = (self.base() / "releases" / SHA_A / "source" / "message.txt").read_text()
        code, output = self.prepare(SHA_A, release_files(message="changed"))
        self.assertEqual(code, release_host.EXIT_INVALID)
        self.assertIn("releases are immutable", output)
        self.assertEqual((self.base() / "releases" / SHA_A / "source" / "message.txt").read_text(), before)

    def test_a_modified_release_on_disk_is_detected(self) -> None:
        self.assertEqual(self.prepare(SHA_A)[0], 0)
        target = self.base() / "releases" / SHA_A / "source" / "message.txt"
        os.chmod(target, 0o600)
        target.write_text("tampered\n")
        code, output = self.prepare(SHA_A)
        self.assertEqual(code, release_host.EXIT_INVALID)
        self.assertIn("modified on disk", output)

    def test_prepare_needs_the_held_lock(self) -> None:
        self.base().mkdir(mode=0o700)
        (self.base() / "incoming").mkdir(mode=0o700)
        archive = self.upload("staging", "release.tar", tar_bytes(release_files()))
        runtime = self.upload("staging", "runtime.env", self.runtime_text().encode())
        common = [
            "prepare",
            "--environment",
            "staging",
            "--sha",
            SHA_A,
            "--archive",
            str(archive),
            "--runtime",
            str(runtime),
        ]
        self.assertEqual(self.main(*common)[0], release_host.EXIT_INVALID)
        self.assertEqual(self.main(*common, "--lock-token", TOKEN)[0], release_host.EXIT_INVALID)
        self.hold_lock()
        self.assertEqual(self.main(*common, "--lock-token", "wrong-token-0123456789")[0], release_host.EXIT_INVALID)
        self.assertEqual(self.main(*common, "--lock-token", TOKEN)[0], 0)

    def test_host_generated_secrets_are_created_once_and_never_printed(self) -> None:
        code, output = self.prepare(SHA_A, runtime=self.runtime_text("APP_SIGNING_KEY"))
        self.assertEqual(code, 0, output)
        store = self.base() / "shared" / "host-generated.env"
        self.assertEqual(stat.S_IMODE(os.lstat(store).st_mode), 0o600)
        generated = dict(release_host.parse_runtime(store.read_text(), "store")[0])["APP_SIGNING_KEY"]
        self.assertGreaterEqual(len(generated), 40)
        self.assertNotIn(generated, output)
        self.assertIn(
            f"APP_SIGNING_KEY='{generated}'", (self.base() / "releases" / SHA_A / "runtime.env.next").read_text()
        )
        code, output = self.prepare(SHA_B, runtime=self.runtime_text("APP_SIGNING_KEY"))
        self.assertEqual(code, 0, output)
        self.assertIn(
            f"APP_SIGNING_KEY='{generated}'", (self.base() / "releases" / SHA_B / "runtime.env.next").read_text()
        )

    def test_runtime_supplying_a_host_generated_name_is_refused(self) -> None:
        runtime = "# af:host-generated APP_SECRET\nAPP_SECRET='x'\n"
        code, output = self.prepare(SHA_A, runtime=runtime)
        self.assertEqual(code, release_host.EXIT_INVALID)
        self.assertIn("host-generated names must not be supplied", output)

    def test_malformed_runtime_lines_are_refused_without_showing_values(self) -> None:
        code, output = self.prepare(SHA_A, runtime=f"APP_SECRET={SECRET_VALUE}\n")
        self.assertEqual(code, release_host.EXIT_INVALID)
        self.assertNotIn(SECRET_VALUE, output)

    def test_a_base_used_by_another_environment_is_refused(self) -> None:
        self.assertEqual(self.prepare(SHA_A)[0], 0)
        (self.base() / "environment").chmod(0o600)
        (self.base() / "environment").write_text("production\n")
        code, output = self.prepare(SHA_B)
        self.assertEqual(code, release_host.EXIT_INVALID)
        self.assertIn("belongs to environment 'production'", output)


class OwnerTests(ReleaseHostTestCase):
    def test_group_writable_deploy_root_is_refused(self) -> None:
        os.chmod(self.root, 0o770)
        code, output = self.main("status")
        self.assertEqual(code, 0)
        host = release_host.Host(str(self.base()), OWNER_MODE, self.deps)
        with self.assertRaises(release_host.DeployError) as caught:
            host.ensure_structure()
        self.assertIn("group- or world-writable", str(caught.exception))

    def test_symlinked_base_is_refused(self) -> None:
        real = self.root / "real"
        real.mkdir(mode=0o700)
        (self.root / "staging").symlink_to(real)
        host = release_host.Host(str(self.base()), OWNER_MODE, self.deps)
        with self.assertRaises(release_host.DeployError):
            host.ensure_structure()

    @unittest.skipIf(os.geteuid() == 0, "root may use --owner root")
    def test_owner_root_requires_root(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            code = release_host.main(["status", "--base", str(self.base()), "--owner", "root"], self.deps)
        self.assertEqual(code, release_host.EXIT_INVALID)
        self.assertIn("must run as root", output.getvalue())

    def test_owner_self_refuses_root(self) -> None:
        deps = release_host.Deps(run=self.docker.run, fetch=self.docker.fetch, euid=0, egid=0)
        with self.assertRaises(release_host.DeployError):
            release_host.Host(str(self.base()), "self", deps)

    def test_secure_file_moves_an_upload_into_custody(self) -> None:
        self.hold_lock()
        upload_dir = Path(self._temporary.name) / "upload"
        upload_dir.mkdir(mode=0o700)
        upload = upload_dir / "runtime.env"
        upload.write_text("A='1'\n")
        code, output = self.main("secure-file", "--lock-token", TOKEN, str(upload))
        self.assertEqual(code, 0, output)
        destination = self.base() / "incoming" / "runtime.env"
        self.assertEqual(output.strip(), str(destination))
        self.assertFalse(upload.exists())
        self.assertEqual(stat.S_IMODE(os.lstat(destination).st_mode), 0o600)

    def test_secure_file_refuses_symlinks_and_shared_directories(self) -> None:
        self.hold_lock()
        shared = Path(self._temporary.name) / "shared-upload"
        shared.mkdir(mode=0o755)
        os.chmod(shared, 0o755)
        loose = shared / "runtime.env"
        loose.write_text("A='1'\n")
        self.assertEqual(self.main("secure-file", "--lock-token", TOKEN, str(loose))[0], release_host.EXIT_INVALID)
        private = Path(self._temporary.name) / "private-upload"
        private.mkdir(mode=0o700)
        (private / "link.env").symlink_to(loose)
        self.assertEqual(
            self.main("secure-file", "--lock-token", TOKEN, str(private / "link.env"))[0], release_host.EXIT_INVALID
        )


class LockTests(ReleaseHostTestCase):
    def test_second_holder_gets_the_lock_busy_exit_code(self) -> None:
        self.hold_lock()
        code, output = self.main("lock", "--wait", "0", "--hold", "--token", "another-token-0123456789")
        self.assertEqual(code, release_host.EXIT_LOCK_BUSY)
        self.assertIn("another deployment holds the lock", output)

    def test_hold_mode_acquires_announces_and_releases(self) -> None:
        seen = {}

        def wait_for_eof() -> None:
            seen["owner"] = json.loads((self.base() / "deploy.lock.owner").read_text())

        self.deps.stdin_wait = wait_for_eof
        code, output = self.main("lock", "--wait", "0", "--hold", "--token", TOKEN)
        self.assertEqual(code, 0, output)
        self.assertIn("AF-LOCK-ACQUIRED", output)
        self.assertIn("token_sha256", seen["owner"])
        self.assertFalse((self.base() / "deploy.lock.owner").exists())
        code, _ = self.main("lock", "--wait", "0", "--hold", "--token", TOKEN)
        self.assertEqual(code, 0)

    def test_command_mode_passes_the_token_to_the_command(self) -> None:
        script = "import os, sys; sys.exit(0 if len(os.environ.get('AF_LOCK_TOKEN', '')) >= 16 else 9)"
        code, output = self.main("lock", "--wait", "0", "--", sys.executable, "-c", script)
        self.assertEqual(code, 0, output)


class ActivationTests(ReleaseHostTestCase):
    def test_first_deploy_runs_every_step_in_order(self) -> None:
        self.assertEqual(self.deploy(SHA_A), 0)
        self.assertEqual(self.current(), SHA_A)
        build = next(call for call in self.docker.calls if call[:2] == ["docker", "build"])
        self.assertIn(f"org.opencontainers.image.revision={SHA_A}", build)
        self.assertIn("io.agent-framework.app=demo", build)
        self.assertIn(f"AF_REVISION={SHA_A}", build)
        self.assertIn(f"demo-staging/web:{SHA_A}", build)
        order = []
        for call in self.docker.calls:
            if call[:2] == ["docker", "build"]:
                order.append("build")
            elif call[:2] == ["docker", "compose"]:
                rest = call[call.index("--env-file") + 2 :]
                if rest[0] == "run" and "--detach" in rest:
                    order.append("candidate")
                elif rest[0] == "run" and "--no-deps" in rest:
                    order.append("preflight")
                elif rest[0] == "run":
                    order.append("migrate")
                elif rest[0] == "up":
                    order.append("up")
            elif call[0].endswith("/backup"):
                order.append("backup")
        self.assertEqual(order, ["build", "preflight", "candidate", "backup", "migrate", "up"])
        hooks = (self.base() / "hooks.log").read_text().split()
        self.assertEqual(hooks[0::2], ["pre_build", "pre_migrate", "post_promote"])
        release = self.base() / "releases" / SHA_A
        self.assertTrue((release / "runtime.env").exists())
        self.assertFalse((release / "runtime.env.next").exists())
        meta = json.loads((release / "release.json").read_text())
        self.assertEqual(meta["image_tags"], {"web": f"demo-staging/web:{SHA_A}"})
        self.assertTrue(meta["promoted_at"])
        self.assertTrue(meta["last_backup"].startswith(str(self.base() / "backups")))

    def test_an_existing_image_is_reused_only_with_its_recorded_id(self) -> None:
        self.assertEqual(self.deploy(SHA_A), 0)
        builds = len([call for call in self.docker.calls if call[:2] == ["docker", "build"]])
        self.assertEqual(self.deploy(SHA_A), 0)
        self.assertEqual(builds, len([call for call in self.docker.calls if call[:2] == ["docker", "build"]]))
        self.docker.images[f"demo-staging/web:{SHA_A}"]["Id"] = "sha256:" + "f" * 64
        self.assertEqual(self.prepare(SHA_A)[0], 0)
        code, output = self.main("activate", "--environment", "staging", "--sha", SHA_A, "--lock-token", TOKEN)
        self.assertEqual(code, 1)
        self.assertIn("differs from the id recorded", output)

    def test_failed_local_health_restores_the_previous_release(self) -> None:
        self.assertEqual(self.deploy(SHA_A), 0)
        self.docker.unhealthy.add(SHA_B)
        code = self.deploy(SHA_B)
        self.assertEqual(code, release_host.EXIT_RESTORED)
        self.assertEqual(self.current(), SHA_A)
        self.assertEqual(self.docker.live["demo-staging"], SHA_A)

    def test_failed_restore_reports_manual_action(self) -> None:
        self.assertEqual(self.deploy(SHA_A), 0)
        self.docker.unhealthy.update({SHA_A, SHA_B})
        self.assertEqual(self.deploy(SHA_B), release_host.EXIT_RESTORE_FAILED)
        self.assertEqual(self.current(), SHA_A)

    def test_failure_without_a_previous_release_stops_the_new_one(self) -> None:
        self.docker.unhealthy.add(SHA_A)
        self.assertEqual(self.deploy(SHA_A), release_host.EXIT_FAILED)
        self.assertNotIn("demo-staging", self.docker.live)
        self.assertIsNone(self.current())

    def test_failed_preflight_changes_nothing(self) -> None:
        self.assertEqual(self.deploy(SHA_A), 0)
        self.docker.preflight_exit = 3
        self.assertEqual(self.deploy(SHA_B), release_host.EXIT_FAILED)
        self.assertEqual(self.docker.live["demo-staging"], SHA_A)
        self.assertEqual(len(self.docker.subcommands("up")), 1)

    def test_candidate_with_the_wrong_revision_stops_before_migrations(self) -> None:
        self.docker.candidate_revision_override = SHA_C
        self.assertEqual(self.deploy(SHA_A), release_host.EXIT_FAILED)
        self.assertEqual(self.docker.subcommands("up"), [])
        self.assertFalse((self.base() / "hooks.log").read_text().count("pre_migrate"))
        self.assertEqual(self.docker.candidates, {})

    def test_production_migrations_need_a_backup_hook(self) -> None:
        code, output = self.prepare(SHA_A, release_files(backup=False), environment="production")
        self.assertEqual(code, 0, output)
        code, output = self.main(
            "activate", "--environment", "production", "--sha", SHA_A, "--lock-token", TOKEN, environment="production"
        )
        self.assertEqual(code, release_host.EXIT_INVALID)
        self.assertIn("pre-migration backup", output)
        self.assertFalse(any(call[:2] == ["docker", "build"] for call in self.docker.calls))

    def test_promote_refuses_a_release_that_is_not_running(self) -> None:
        self.assertEqual(self.deploy(SHA_A), 0)
        self.assertEqual(self.prepare(SHA_B)[0], 0)
        code, output = self.main("promote", "--environment", "staging", "--sha", SHA_B, "--lock-token", TOKEN)
        self.assertEqual(code, 1)
        self.assertIn("refusing to promote", output)
        self.assertEqual(self.current(), SHA_A)


class RollbackCleanupRecordTests(ReleaseHostTestCase):
    def test_rollback_defaults_to_the_previous_release_without_migrations(self) -> None:
        self.assertEqual(self.deploy(SHA_A), 0)
        self.assertEqual(self.deploy(SHA_B), 0)
        migrations_before = len([call for call in self.docker.subcommands("run") if "echo migrate" in call])
        code, output = self.main("rollback", "--environment", "staging", "--lock-token", TOKEN)
        self.assertEqual(code, 0, output)
        self.assertEqual(self.current(), SHA_A)
        self.assertEqual(self.docker.live["demo-staging"], SHA_A)
        self.assertEqual(
            migrations_before, len([call for call in self.docker.subcommands("run") if "echo migrate" in call])
        )
        self.assertIn("skipping backup and migrations", output)

    def test_rollback_to_a_release_that_is_not_retained_is_refused(self) -> None:
        self.assertEqual(self.deploy(SHA_A), 0)
        code, output = self.main("rollback", "--environment", "staging", "--sha", SHA_C, "--lock-token", TOKEN)
        self.assertEqual(code, release_host.EXIT_INVALID)
        self.assertIn("not retained", output)

    def test_cleanup_keeps_retention_current_and_running_releases(self) -> None:
        for sha in (SHA_A, SHA_B, SHA_C):
            self.assertEqual(self.deploy(sha), 0)
        older = "d" * 40
        self.docker.unhealthy.add(older)
        self.assertEqual(self.deploy(older), release_host.EXIT_RESTORED)
        code, output = self.main("cleanup", "--environment", "staging", "--lock-token", TOKEN)
        self.assertEqual(code, 0, output)
        remaining = sorted(path.name for path in (self.base() / "releases").iterdir())
        self.assertEqual(remaining, [SHA_B, SHA_C])
        self.assertNotIn(f"demo-staging/web:{SHA_A}", self.docker.images)
        self.assertNotIn(f"demo-staging/web:{older}", self.docker.images)
        self.assertIn(f"demo-staging/web:{SHA_C}", self.docker.images)

    def test_cleanup_never_removes_a_running_release_or_one_whose_image_stays(self) -> None:
        for sha in (SHA_A, SHA_B, SHA_C):
            self.assertEqual(self.deploy(sha), 0)
        self.docker.extra_running.append(("other-project", SHA_A))
        code, output = self.main("cleanup", "--environment", "staging", "--keep", "1", "--lock-token", TOKEN)
        self.assertEqual(code, 0, output)
        self.assertEqual(sorted(path.name for path in (self.base() / "releases").iterdir()), [SHA_A, SHA_C])
        self.docker.extra_running.clear()
        self.docker.image_rm_fails.add(f"demo-staging/web:{SHA_A}")
        code, output = self.main("cleanup", "--environment", "staging", "--keep", "1", "--lock-token", TOKEN)
        self.assertEqual(code, 0, output)
        self.assertIn("could not remove image", output)
        self.assertTrue((self.base() / "releases" / SHA_A).is_dir())

    def test_record_writes_the_deployment_record_and_history(self) -> None:
        self.assertEqual(self.deploy(SHA_A), 0)
        args = [
            "record",
            "--environment",
            "staging",
            "--sha",
            SHA_A,
            "--result",
            "succeeded",
            "--actor",
            "ci-bot",
            "--pipeline-url",
            "https://ci.example.invalid/p/1",
            "--lock-token",
            TOKEN,
        ]
        self.assertEqual(self.main(*args)[0], 0)
        self.assertEqual(self.main(*args)[0], 0)
        record = json.loads((self.base() / "deployment.json").read_text())
        self.assertEqual(record["sha"], SHA_A)
        self.assertEqual(record["result"], "succeeded")
        self.assertEqual(record["images"]["web"].startswith("sha256:"), True)
        self.assertEqual(len((self.base() / "deployments.jsonl").read_text().splitlines()), 2)
        self.assertEqual(stat.S_IMODE(os.lstat(self.base() / "deployment.json").st_mode), 0o600)
        bad = list(args)
        bad[bad.index("ci-bot")] = "bad actor; rm -rf"
        self.assertEqual(self.main(*bad)[0], release_host.EXIT_INVALID)

    def test_status_reports_current_and_never_runtime_values(self) -> None:
        self.assertEqual(self.deploy(SHA_A), 0)
        code, output = self.main("status", "--json")
        self.assertEqual(code, 0, output)
        status = json.loads(output)
        self.assertEqual(status["current"], SHA_A)
        self.assertEqual(status["running_revisions"], [SHA_A])
        self.assertTrue(status["lock_held"])
        self.assertNotIn(SECRET_VALUE, output)
        self.assertEqual(self.main("status", "--field", "current")[1].strip(), SHA_A)


class ConfigAndCliTests(unittest.TestCase):
    def test_example_release_config_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "deploy").mkdir()
            (source / "deploy" / "release.toml").write_text((DEPLOY_ASSETS / "release.example.toml").read_text())
            config = release_host.load_release_config(source)
        self.assertEqual(config.environment("production").keep, 6)
        self.assertEqual(config.environment("staging").keep, 3)
        self.assertEqual(config.image_variable(config.images[0]), "AF_IMAGE_WEB")

    def test_release_config_rejects_unknown_keys_and_public_local_urls(self) -> None:
        cases = {
            "unknown key": RELEASE_TOML + "\nrestart = true\n",
            "public local url": RELEASE_TOML.replace("http://127.0.0.1:18080", "http://0.0.0.0:18080"),
            "path escape": RELEASE_TOML.replace('compose_file = "compose.yaml"', 'compose_file = "../compose.yaml"'),
        }
        for label, text in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary)
                (source / "deploy").mkdir()
                (source / "deploy" / "release.toml").write_text(text)
                with self.assertRaises(release_host.DeployError):
                    release_host.load_release_config(source)

    def test_cli_runs_as_a_script(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(DEPLOY_ASSETS / "release_host.py"), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for command in (
            "prepare",
            "secure-file",
            "lock",
            "activate",
            "promote",
            "cleanup",
            "rollback",
            "record",
            "status",
        ):
            self.assertIn(command, completed.stdout)


if __name__ == "__main__":
    unittest.main()
