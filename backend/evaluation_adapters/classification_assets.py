"""Trusted, bounded model downloads. Contestant URLs never reach private networks."""
from __future__ import annotations

import fnmatch
import hashlib
import http.client
import ipaddress
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit


def public_target(url: str, hosts: list[str]):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.fragment
            or not any(fnmatch.fnmatchcase(parsed.hostname.lower(), host) for host in hosts)):
        raise ValueError("model URL must use HTTPS and an operator-approved host")
    addresses = sorted({row[4][0] for row in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("model URL resolves to a non-public address")
    return parsed, addresses[0]


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Pin the validated IP while retaining TLS certificate/SNI verification.
        sock = socket.create_connection((self.address, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def download_weights(url: str, digest: str, size: int, destination: Path, hosts: list[str], deadline: float):
    temporary = destination.with_suffix(".partial")
    try:
        for _ in range(6):
            parsed, address = public_target(url, hosts)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("model download time limit exceeded")
            connection = PinnedHTTPS(parsed.hostname, address, min(30, remaining))
            try:
                connection.request("GET", parsed.path + ("?" + parsed.query if parsed.query else ""),
                                   headers={"User-Agent": "UESTC-Evaluation/1", "Accept-Encoding": "identity"})
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        raise ValueError("model redirect has no destination")
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise ValueError("model download was rejected by the upstream")
                declared = response.getheader("Content-Length")
                if declared is not None and int(declared) != size:
                    raise ValueError("model download size differs from declared size")
                sha, received = hashlib.sha256(), 0
                with temporary.open("wb") as target:
                    while chunk := response.read(1024 * 1024):
                        received += len(chunk)
                        if received > size or time.monotonic() >= deadline:
                            raise ValueError("model download exceeded its size or time limit")
                        sha.update(chunk)
                        target.write(chunk)
                if received != size or sha.hexdigest() != digest:
                    raise ValueError("model weights failed size/SHA256 verification")
                temporary.replace(destination)
                return
            finally:
                connection.close()
        raise ValueError("model download has too many redirects")
    finally:
        temporary.unlink(missing_ok=True)
