"""Deployment and health notifications (ADR 0009, host services).

A host config may name a channels file (`notify = "/etc/af/notify.toml"`):

    [[channels]]
    kind = "telegram"                                # telegram, slack, discord, or webhook
    secret_file = "/etc/af/secrets/telegram-token"   # 0600: the bot token, or the webhook URL
    chat_id = "-1001234567890"                       # telegram only
    events = ["failed", "restored", "restore_failed", "stopped", "refused", "unhealthy", "recovered"]

`events` is optional (default: every event except `unchanged` and `busy`). Messages carry
project, environment, result, version, commit, trigger, actor, and the engine's reason;
never settings values, secrets, or content. Delivery is best effort: a failure is logged by
channel kind and never changes a deployment result.
"""

from __future__ import annotations

import json
import re
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

KINDS = {"telegram", "slack", "discord", "webhook"}
EVENTS = {
    "deployed", "unchanged", "refused", "failed", "restored", "restore_failed", "stopped", "busy",
    "unhealthy", "recovered",
}
DEFAULT_EVENTS = frozenset(EVENTS - {"unchanged", "busy"})
TIMEOUT_SECONDS = 10
MAX_SECRET_BYTES = 4096
TELEGRAM_TOKEN = re.compile(r"[0-9]{1,20}:[A-Za-z0-9_-]{20,100}")


class NotifyError(Exception):
    pass


@dataclass(frozen=True)
class Channel:
    kind: str
    secret_file: Path
    chat_id: str | None = None
    events: frozenset[str] = DEFAULT_EVENTS


def load_channels(path: Path) -> list[Channel]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise NotifyError(f"channels file unreadable: {error}") from None
    entries = data.get("channels")
    if set(data) - {"channels"} or not isinstance(entries, list):
        raise NotifyError("channels file must hold only [[channels]] tables")
    channels = []
    for number, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise NotifyError(f"channel {number} must be a table")
        unknown = set(entry) - {"kind", "secret_file", "chat_id", "events"}
        if unknown:
            raise NotifyError(f"channel {number} has unknown keys: {', '.join(sorted(unknown))}")
        kind = entry.get("kind")
        if kind not in KINDS:
            raise NotifyError(f"channel {number}: kind must be one of {', '.join(sorted(KINDS))}")
        secret_file = entry.get("secret_file")
        if not isinstance(secret_file, str) or not Path(secret_file).is_absolute():
            raise NotifyError(f"channel {number}: secret_file must be an absolute path")
        chat_id = entry.get("chat_id")
        if (kind == "telegram") != isinstance(chat_id, str) or (chat_id is not None and not chat_id):
            raise NotifyError(f"channel {number}: chat_id is required for telegram and only for telegram")
        events = entry.get("events", sorted(DEFAULT_EVENTS))
        if not isinstance(events, list) or not events or not set(events) <= EVENTS:
            raise NotifyError(f"channel {number}: events must be a non-empty list of {', '.join(sorted(EVENTS))}")
        channels.append(Channel(kind, Path(secret_file), chat_id, frozenset(events)))
    return channels


def read_secret(path: Path) -> str:
    info = path.stat()
    if info.st_mode & 0o077:
        raise NotifyError("secret file must not be readable by others")
    value = path.read_bytes()[: MAX_SECRET_BYTES + 1]
    if not value.strip() or len(value) > MAX_SECRET_BYTES:
        raise NotifyError("secret file is empty or too large")
    return value.decode("utf-8").strip()


Post = Callable[[str, dict], None]


def post_json(url: str, payload: dict) -> None:
    """POST JSON; errors name the status only, never the URL (it can hold a token)."""
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            response.read(1024)
    except urllib.error.HTTPError as error:
        error.close()
        raise NotifyError(f"HTTP {error.code}") from None
    except (urllib.error.URLError, OSError):
        raise NotifyError("channel unreachable") from None


class Notifier:
    def __init__(self, channels: list[Channel], *, post: Post = post_json, log: Callable[[str], None] = print) -> None:
        self.channels = channels
        self.post = post
        self.log = log

    def send(self, event: str, text: str, record: dict | None = None) -> None:
        for channel in self.channels:
            if event not in channel.events:
                continue
            try:
                self._deliver(channel, event, text, record)
            except (NotifyError, OSError, UnicodeDecodeError) as error:
                self.log(f"notification via {channel.kind} failed: {error}")

    def _deliver(self, channel: Channel, event: str, text: str, record: dict | None) -> None:
        secret = read_secret(channel.secret_file)
        if channel.kind == "telegram":
            if not TELEGRAM_TOKEN.fullmatch(secret):
                raise NotifyError("telegram token has an unexpected format")
            url = f"https://api.telegram.org/bot{secret}/sendMessage"
            self.post(url, {"chat_id": channel.chat_id, "text": text, "link_preview_options": {"is_disabled": True}})
            return
        if not secret.startswith("https://"):
            raise NotifyError("webhook URL must use https")
        if channel.kind == "slack":
            self.post(secret, {"text": text})
        elif channel.kind == "discord":
            self.post(secret, {"content": text, "allowed_mentions": {"parse": []}})
        else:
            self.post(secret, {"event": event, "text": text, "deployment": record})


def from_file(path: Path | None, log: Callable[[str], None] = print) -> Notifier:
    """The host's notifier; a broken channels file disables notifications loudly, never a deploy."""
    if path is None:
        return Notifier([], log=log)
    try:
        return Notifier(load_channels(path), log=log)
    except NotifyError as error:
        log(f"notifications disabled: {error}")
        return Notifier([], log=log)


def deployment_text(record: dict, reason: str | None = None) -> str:
    version = record["version"] or "unversioned"
    text = (
        f"{record['project']} {record['environment']}: {record['result']} {version} "
        f"({record['commit'][:12]}) via {record['trigger']} by {record['actor']}"
    )
    if record["exit_code"]:
        text += f", exit {record['exit_code']}"
    if reason:
        text += f": {reason}"
    return text
