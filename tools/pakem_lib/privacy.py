"""Text that must not reach a public repository: private names, the owner's servers, paths.

The sources are device-local, never the public tree (owner decision 2026-10-23): client and
other private names in `~/.config/pakem/private-names.txt` (one per line, `#` comments),
and the hosts recorded in `~/.config/pakem/hosts.toml`. Findings name the kind of text and
where it is, never the value, so a report can be pasted anywhere.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path

from .device_config import config_dir
from .host import load_hosts

PRIVATE_NAMES_FILE = "private-names.txt"
NAME = "a private name (private-names.txt)"
HOST = "a recorded host name or address (hosts.toml)"
PORT = "a recorded SSH or release port (hosts.toml)"
ADDRESS = "a non-documentation IP address"
HOME = "a home directory path"
# Example addresses from RFC 5737, loopback, and the unspecified address may appear anywhere.
DOCUMENTATION = [ipaddress.ip_network(network) for network in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
HOME_PATH = re.compile(r"(?:/Users|/home)/[A-Za-z0-9._-]+")


def private_names_path() -> Path:
    return config_dir() / PRIVATE_NAMES_FILE


def private_names() -> list[str]:
    path = private_names_path()
    if not path.is_file():
        return []
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip()
        if name:
            names.append(name)
    return names


def _word(text: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w.-]){re.escape(text)}(?![\w-])", re.IGNORECASE)


@dataclass(frozen=True)
class Scanner:
    """Patterns for the chosen kinds; an empty scanner finds nothing."""

    patterns: tuple[tuple[str, re.Pattern[str]], ...]
    addresses: bool = False

    @classmethod
    def for_kinds(cls, *, names: bool = True, hosts: bool = False, addresses: bool = False,
                  homes: bool = False) -> "Scanner":
        patterns: list[tuple[str, re.Pattern[str]]] = []
        if names:
            patterns += [(NAME, _word(name)) for name in private_names()]
        if hosts:
            for record in load_hosts().values():
                for word in sorted({record.name, record.target, record.address}):
                    if len(word) >= 3:
                        patterns.append((HOST, _word(word)))
                for port in (record.port, record.release_port):
                    if port is not None and port != 22:
                        patterns.append((PORT, re.compile(rf"(?:\bport\s*|:){port}\b", re.IGNORECASE)))
        if homes:
            patterns.append((HOME, HOME_PATH))
        return cls(tuple(patterns), addresses)

    def kinds_in(self, line: str) -> list[str]:
        kinds = {kind for kind, pattern in self.patterns if pattern.search(line)}
        if self.addresses and any(_reportable_address(match) for match in IPV4.findall(line)):
            kinds.add(ADDRESS)
        return sorted(kinds)

    def findings(self, text: str) -> list[tuple[int, list[str]]]:
        """(line number, kinds) for each line that holds private text."""
        found = []
        for number, line in enumerate(text.splitlines(), start=1):
            kinds = self.kinds_in(line)
            if kinds:
                found.append((number, kinds))
        return found


def _reportable_address(text: str) -> bool:
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    if address.is_loopback or address.is_unspecified or address.is_reserved or address.is_multicast:
        return False
    return not any(address in network for network in DOCUMENTATION)
