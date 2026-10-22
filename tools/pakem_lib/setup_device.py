"""`pakem setup device`, `pakem setup key`, and `pakem setup check`.

`setup device` prepares everything a device can need in one go, for a fresh machine;
other commands prepare only their own needs when they run (requirements.py).
`setup key` creates the device key on the owner's terminal, where ssh-keygen can ask
for its passphrase. `setup check` changes nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import device_key
from .common import EXIT_DONE, EXIT_OWNER_ACTION, Framework, OwnerActionRequired, display_path
from .requirements import (
    DEVICE_NEEDS,
    MACOS,
    SETUP_KEY_ACTION,
    assess,
    detect_device,
    device_file,
    device_name,
    prepare,
    report,
    result,
    write_device_settings,
)


def run_setup_device(args) -> int:
    framework = Framework(Path(args.root))
    assessment, done = prepare(framework, DEVICE_NEEDS, args, "pakem setup device")
    if not args.plan:
        return report(args, assessment, done, "Result:")
    if args.json:
        planned = [step.description for step in assessment.steps]
        print(json.dumps(result("planned", planned, assessment), indent=2))
    else:
        for action in assessment.owner_actions:
            print(action)
        print("Plan only: nothing changed.")
    return EXIT_DONE


def run_setup_check(args) -> int:
    framework = Framework(Path(args.root))
    assessment = assess(framework, DEVICE_NEEDS, sys.stdin.isatty())
    return report(args, assessment, [], f"pakem setup check ({assessment.device.label})")


def run_setup_key(args) -> int:
    return create_device_key(passphrase=not args.no_passphrase)


def create_device_key(passphrase: bool) -> int:
    """The device key, made on the owner's terminal (ssh-keygen asks for the passphrase)."""
    device = detect_device()
    private, public = device_key.private_path(), device_key.public_path()
    if private.exists() and public.exists():
        print(f"{display_path(private)} already exists ({device_key.fingerprint(public)}); nothing to do")
        return EXIT_DONE
    if passphrase and not sys.stdin.isatty():
        raise OwnerActionRequired(SETUP_KEY_ACTION)
    device_key.create(f"pakem-device {device_name()}", passphrase)
    print(f"Created {display_path(private)} ({device_key.fingerprint(public)})")
    code = EXIT_DONE
    if passphrase:
        add = device_key.add_command(device.system == MACOS)
        print(f"Adding it to ssh-agent with `{' '.join(add)}`; it asks for the passphrase once.")
        if subprocess.run(add).returncode != 0:
            print(f"OWNER ACTION REQUIRED: start an ssh-agent, then run `{' '.join(add)}`")
            code = EXIT_OWNER_ACTION
    write_device_settings(device)
    print(f"Wrote {display_path(device_file())}")
    return code
