"""An in-process OCI registry for engine tests: bearer tokens, manifests, blobs.

Loopback HTTP only. `push_artifact` stores a single-layer OCI 1.1 artifact the way
`oras push --artifact-type` does and returns its manifest digest.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EMPTY_CONFIG = b"{}"
EMPTY_MEDIA = "application/vnd.oci.empty.v1+json"
MANIFEST_MEDIA = "application/vnd.oci.image.manifest.v1+json"


def digest_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class FakeRegistry:
    def __init__(self, *, username: str | None = None, password: str | None = None) -> None:
        self.blobs: dict[str, bytes] = {}
        self.manifests: dict[str, bytes] = {}
        self.tags: dict[tuple[str, str], str] = {}
        self.username = username
        self.password = password
        self.token = "test-token"
        self.requests: list[str] = []
        self.tamper: set[str] = set()
        registry = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # noqa: ANN002
                pass

            def do_GET(self) -> None:  # noqa: N802
                registry.requests.append(self.path)
                registry.handle(self)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    def __enter__(self) -> FakeRegistry:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()

    def push_artifact(self, repository: str, tag: str | None, artifact_type: str, content: bytes) -> str:
        self.blobs[digest_of(content)] = content
        self.blobs[digest_of(EMPTY_CONFIG)] = EMPTY_CONFIG
        manifest = json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": MANIFEST_MEDIA,
                "artifactType": artifact_type,
                "config": {"mediaType": EMPTY_MEDIA, "digest": digest_of(EMPTY_CONFIG), "size": 2},
                "layers": [{"mediaType": artifact_type, "digest": digest_of(content), "size": len(content)}],
            },
            sort_keys=True,
        ).encode()
        digest = digest_of(manifest)
        self.manifests[digest] = manifest
        if tag:
            self.tags[(repository, tag)] = digest
        return digest

    def handle(self, request: BaseHTTPRequestHandler) -> None:
        if request.path.startswith("/token"):
            self._token(request)
            return
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            request.send_response(401)
            realm = f"http://{self.host}/token"
            request.send_header("WWW-Authenticate", f'Bearer realm="{realm}",service="fake"')
            request.end_headers()
            return
        match = re.fullmatch(r"/v2/(?P<repo>.+)/(?P<kind>manifests|blobs)/(?P<ref>[^/]+)", request.path)
        if match is None:
            self._send(request, 404, b"")
            return
        repo, kind, ref = match["repo"], match["kind"], match["ref"]
        if kind == "manifests":
            digest = ref if ref.startswith("sha256:") else self.tags.get((repo, ref))
            body = self.manifests.get(digest or "")
            if body is None:
                self._send(request, 404, b"")
                return
            if digest in self.tamper:
                body = body.replace(b'"schemaVersion": 2', b'"schemaVersion": 2 ')
            self._send(request, 200, body, MANIFEST_MEDIA)
        else:
            body = self.blobs.get(ref)
            if body is None:
                self._send(request, 404, b"")
                return
            if ref in self.tamper:
                body = body + b" "
            self._send(request, 200, body, "application/octet-stream")

    def _token(self, request: BaseHTTPRequestHandler) -> None:
        if self.username is not None:
            expected = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            if request.headers.get("Authorization") != f"Basic {expected}":
                self._send(request, 401, b"")
                return
        self._send(request, 200, json.dumps({"token": self.token}).encode(), "application/json")

    @staticmethod
    def _send(request: BaseHTTPRequestHandler, code: int, body: bytes, media: str = "text/plain") -> None:
        request.send_response(code)
        request.send_header("Content-Type", media)
        request.send_header("Content-Length", str(len(body)))
        request.end_headers()
        request.wfile.write(body)
