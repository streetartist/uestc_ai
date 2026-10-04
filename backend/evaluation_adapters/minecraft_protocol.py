"""Bounded JSON messages exchanged over a filesystem Unix socket."""

from __future__ import annotations

import json
import socket


MAX_MESSAGE = 2 * 1024 * 1024


class JsonChannel:
    def __init__(self, connection: socket.socket):
        self.connection = connection
        self.pending = bytearray()

    def send(self, message: dict) -> None:
        encoded = json.dumps(message, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_MESSAGE:
            raise ValueError("evaluation message is too large")
        self.connection.sendall(encoded + b"\n")

    def receive(self) -> dict:
        while b"\n" not in self.pending:
            chunk = self.connection.recv(65536)
            if not chunk:
                raise ConnectionError("evaluation peer disconnected")
            self.pending.extend(chunk)
            if len(self.pending) > MAX_MESSAGE + 1:
                raise ValueError("evaluation message is too large")
        line, _, remainder = self.pending.partition(b"\n")
        self.pending = bytearray(remainder)
        message = json.loads(line)
        if not isinstance(message, dict):
            raise ValueError("evaluation message must be an object")
        return message
