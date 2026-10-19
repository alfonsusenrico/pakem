"""Minimal OCI registry client for the release engine (standard library only).

Reads manifests and blobs by digest and verifies every byte against its digest. Supports
anonymous and bearer-token registries (GHCR, the GitLab registry, Docker Hub style) with
optional read-only credentials. Plain HTTP is allowed only for loopback registries, which
tests and local rehearsals use. Nothing here prints credentials or content.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

MANIFEST_TYPES = (
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
CHUNK = 1024 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

_REGISTRY = r"(?P<registry>[a-z0-9.-]+(?::[0-9]+)?|\[::1\](?::[0-9]+)?)"
_REPOSITORY = r"(?P<repository>[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*)"
_REFERENCE = re.compile(
    _REGISTRY + "/" + _REPOSITORY + r"(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))?(?:@(?P<digest>sha256:[0-9a-f]{64}))?"
)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_CHALLENGE = re.compile(r'(\w+)="([^"]*)"')


class RegistryError(Exception):
    """A registry request failed or returned content that does not match its digest."""


@dataclass(frozen=True)
class Reference:
    registry: str
    repository: str
    tag: str | None
    digest: str | None

    @classmethod
    def parse(cls, text: str) -> Reference:
        match = _REFERENCE.fullmatch(text)
        if match is None or (match["tag"] is None and match["digest"] is None):
            raise RegistryError("reference must be <registry>/<repository>:<tag> or @sha256:<digest>")
        registry = match["registry"]
        if "." not in registry and ":" not in registry and registry != "localhost":
            # Docker's rule: without a dot, port, or localhost the first part is a path.
            raise RegistryError("reference must name its registry explicitly")
        return cls(match["registry"], match["repository"], match["tag"], match["digest"])

    def with_digest(self, digest: str) -> Reference:
        return Reference(self.registry, self.repository, None, digest)

    def __str__(self) -> str:
        if self.digest:
            return f"{self.registry}/{self.repository}@{self.digest}"
        return f"{self.registry}/{self.repository}:{self.tag}"


@dataclass(frozen=True)
class Manifest:
    digest: str
    media_type: str
    document: dict


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class _NoRedirectToHttp(urllib.request.HTTPRedirectHandler):
    """Follow redirects (registries send blobs to storage) but never downgrade to http."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if urllib.parse.urlsplit(newurl).scheme != "https" and not _is_loopback_url(newurl):
            raise RegistryError("registry redirected to a non-https location")
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            # Storage redirects carry their own signed query; the registry token must not leak.
            new.remove_header("Authorization")
        return new


def _is_loopback_url(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ""
    return host in {"127.0.0.1", "localhost", "::1"}


Credentials = Callable[[str], tuple[str, str] | None]


def no_credentials(_registry: str) -> None:
    return None


def credentials_file(path: Path) -> Credentials:
    """Read-only registry credentials: a private JSON file {"<registry>": {"username", "token"}}."""

    def lookup(registry: str) -> tuple[str, str] | None:
        info = path.stat()
        if info.st_mode & 0o077:
            raise RegistryError("registry credentials file must not be readable by others")
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = data.get(registry)
        if entry is None:
            return None
        return entry["username"], entry["token"]

    return lookup


class Registry:
    def __init__(
        self,
        credentials: Credentials = no_credentials,
        opener: urllib.request.OpenerDirector | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._credentials = credentials
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirectToHttp()
        )
        self._timeout = timeout
        self._tokens: dict[tuple[str, str], str] = {}

    def _base(self, registry: str) -> str:
        host = registry.split(":")[0] if not registry.startswith("[") else "[::1]"
        scheme = "http" if host in LOOPBACK_HOSTS else "https"
        return f"{scheme}://{registry}"

    def _token(self, reference: Reference, challenge: str) -> str:
        key = (reference.registry, reference.repository)
        fields = dict(_CHALLENGE.findall(challenge))
        realm = fields.get("realm", "")
        if not realm.startswith("https://") and not _is_loopback_url(realm):
            raise RegistryError("registry token realm must be https")
        query = {"scope": fields.get("scope", f"repository:{reference.repository}:pull")}
        if "service" in fields:
            query["service"] = fields["service"]
        request = urllib.request.Request(realm + "?" + urllib.parse.urlencode(query))
        login = self._credentials(reference.registry)
        if login is not None:
            basic = base64.b64encode(f"{login[0]}:{login[1]}".encode()).decode()
            request.add_header("Authorization", f"Basic {basic}")
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                body = json.loads(response.read(65536))
        except (urllib.error.URLError, ValueError) as error:
            raise RegistryError(f"registry token request failed ({type(error).__name__})") from None
        token = body.get("token") or body.get("access_token")
        if not isinstance(token, str) or not token:
            raise RegistryError("registry returned no token")
        self._tokens[key] = token
        return token

    def _open(self, reference: Reference, path: str, accept: str | None = None, method: str = "GET"):  # noqa: ANN202
        url = f"{self._base(reference.registry)}/v2/{reference.repository}/{path}"
        for attempt in (1, 2):
            request = urllib.request.Request(url, method=method)
            if accept:
                request.add_header("Accept", accept)
            token = self._tokens.get((reference.registry, reference.repository))
            if token:
                request.add_header("Authorization", f"Bearer {token}")
            try:
                return self._opener.open(request, timeout=self._timeout)
            except urllib.error.HTTPError as error:
                challenge = error.headers.get("WWW-Authenticate", "")
                error.close()
                if error.code == 401 and attempt == 1 and challenge.lower().startswith("bearer"):
                    self._token(reference, challenge)
                    continue
                raise RegistryError(f"registry answered {error.code} for {path.split('/')[0]}") from None
            except urllib.error.URLError as error:
                raise RegistryError(f"registry unreachable ({type(error.reason).__name__})") from None
        raise RegistryError("registry authentication failed")

    def manifest(self, reference: Reference) -> Manifest:
        """Fetch a manifest by tag or digest; the returned digest is computed, not trusted."""
        target = reference.digest or reference.tag
        with self._open(reference, f"manifests/{target}", accept=", ".join(MANIFEST_TYPES)) as response:
            body = response.read(MAX_MANIFEST_BYTES + 1)
            media_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
        if len(body) > MAX_MANIFEST_BYTES:
            raise RegistryError("manifest is too large")
        digest = sha256_digest(body)
        if reference.digest and digest != reference.digest:
            raise RegistryError("manifest content does not match its digest")
        try:
            document = json.loads(body)
        except ValueError:
            raise RegistryError("manifest is not JSON") from None
        if not isinstance(document, dict):
            raise RegistryError("manifest is not a JSON object")
        media_type = document.get("mediaType") or media_type
        if media_type not in MANIFEST_TYPES:
            raise RegistryError("unsupported manifest type")
        return Manifest(digest, media_type, document)

    def resolve(self, reference: Reference) -> str:
        """The digest a tag points at right now."""
        return self.manifest(reference).digest

    def blob(self, reference: Reference, digest: str, max_bytes: int, output: BinaryIO | None = None) -> bytes:
        """Download a blob, refusing anything larger than max_bytes or not matching digest.

        With output, the content is streamed there and b"" is returned.
        """
        if not _DIGEST.fullmatch(digest):
            raise RegistryError("blob digest must be sha256:<64 hex>")
        hasher = hashlib.sha256()
        size = 0
        kept = bytearray()
        with self._open(reference, f"blobs/{digest}") as response:
            while chunk := response.read(CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise RegistryError(f"blob is larger than {max_bytes} bytes")
                hasher.update(chunk)
                if output is None:
                    kept.extend(chunk)
                else:
                    output.write(chunk)
        if "sha256:" + hasher.hexdigest() != digest:
            raise RegistryError("blob content does not match its digest")
        return bytes(kept)
