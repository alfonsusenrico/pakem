"""The device key: one dedicated SSH key per device that connects to hosts and signs releases.

It is never the login key. By default it has a passphrase and is used through
ssh-agent, so pakem never handles the passphrase and the same code signs on every
system; ssh-keygen and ssh-add ask for it on the owner's terminal. How the agent gets
the key differs per system:

- macOS with Apple's ssh-add: the passphrase is stored in the login Keychain
  (`ssh-add --apple-use-keychain`) and reloaded without a prompt (`--apple-load-keychain`);
- Linux and WSL: `ssh-add` once per login or boot.

A device nobody can unlock (a headless machine) is created with `--no-passphrase`, an
explicit choice recorded in device.toml. The private key is never read or printed by
pakem; only ssh-keygen and ssh-add touch it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .common import PakemError, home_dir

KEY_NAME = "pakem_device_ed25519"
# Only Apple's ssh-add has the Keychain options; the ssh-add on PATH is used either way,
# so it always talks to the agent the shell's ssh uses.
APPLE_SSH_ADD = "/usr/bin/ssh-add"

IN_AGENT = "in-agent"
NOT_IN_AGENT = "not-in-agent"
NO_AGENT = "no-agent"


def private_path() -> Path:
    return home_dir() / ".ssh" / KEY_NAME


def public_path() -> Path:
    return home_dir() / ".ssh" / f"{KEY_NAME}.pub"


def fingerprint(public_key: Path) -> str | None:
    completed = subprocess.run(["ssh-keygen", "-l", "-f", str(public_key)], capture_output=True, text=True)
    fields = completed.stdout.split()
    if completed.returncode != 0 or len(fields) < 2:
        return None
    return fields[1]


def has_passphrase(private_key: Path) -> bool:
    """True when the empty passphrase does not open the key; ssh-keygen never prompts with -P."""
    completed = subprocess.run(
        ["ssh-keygen", "-y", "-P", "", "-f", str(private_key)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    return completed.returncode != 0


def agent_state(public_key: Path) -> str:
    """Whether the running ssh-agent holds this key, compared by its public key blob."""
    blob = public_key.read_text(encoding="utf-8").split()[1]
    completed = subprocess.run(["ssh-add", "-L"], capture_output=True, text=True)
    # ssh-add -L exits 1 when the agent holds no identity and 2 when no agent answers.
    if completed.returncode == 2:
        return NO_AGENT
    listed = {line.split()[1] for line in completed.stdout.splitlines() if len(line.split()) > 1}
    if blob in listed:
        return IN_AGENT
    return NOT_IN_AGENT


def _uses_keychain(system_is_macos: bool) -> bool:
    return system_is_macos and shutil.which("ssh-add") == APPLE_SSH_ADD


def load_from_keychain() -> None:
    """Reload Keychain-stored passphrases into the macOS agent; never prompts."""
    if not _uses_keychain(True):
        return
    subprocess.run(["ssh-add", "--apple-load-keychain"], stdin=subprocess.DEVNULL, capture_output=True)


def add_command(system_is_macos: bool) -> list[str]:
    """The command that puts the key into the agent; it asks for the passphrase."""
    if _uses_keychain(system_is_macos):
        return ["ssh-add", "--apple-use-keychain", str(private_path())]
    return ["ssh-add", str(private_path())]


def create(comment: str, passphrase: bool) -> None:
    """Create the key pair; with a passphrase, ssh-keygen asks for it on this terminal."""
    private = private_path()
    if private.exists() or public_path().exists():
        raise PakemError(f"{private} or its .pub already exists; pakem never replaces a device key")
    if shutil.which("ssh-keygen") is None:
        raise PakemError("ssh-keygen is not installed")
    private.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    command = ["ssh-keygen", "-q", "-t", "ed25519", "-C", comment, "-f", str(private)]
    if not passphrase:
        command += ["-N", ""]
    completed = subprocess.run(command)
    if completed.returncode != 0:
        raise PakemError(f"ssh-keygen failed with exit {completed.returncode}")


def sign(data_file: Path, namespace: str) -> bytes:
    """An `ssh-keygen -Y sign` signature of a file with the device key, as the file's bytes.

    A key with a passphrase signs through ssh-agent (given its public half, ssh-keygen asks
    the agent), so the passphrase is never needed here.
    """
    private = private_path()
    key = public_path() if has_passphrase(private) else private
    signature = data_file.with_name(f"{data_file.name}.sig")
    signature.unlink(missing_ok=True)
    completed = subprocess.run(
        ["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", namespace, str(data_file)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        raise PakemError(f"the device key could not sign: {detail[-1] if detail else completed.returncode}")
    try:
        return signature.read_bytes()
    finally:
        signature.unlink()
